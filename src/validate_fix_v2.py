"""
validate_fix_v2.py — Block Replacement + Retry Loop Validator
=============================================================
Strategy: instead of replacing a single line (which always breaks structure),
we ask the LLM to rewrite the ENTIRE extracted block. If it doesn't compile,
we feed the compiler error back. If tests fail, we feed the test error back.
Up to 3 attempts per bug.

Pipeline per bug:
  1. Checkout buggy version
  2. Get modified file + faulty line (from defects4j metadata)
  3. Extract wider context block (±5 lines, 80-token ceiling)
  4. Run tests on buggy code → capture failing test name + error
  5. ATTEMPT LOOP (max 3):
     a. Send {context, test_error, [prev_compiler_error]} to LLM
     b. LLM returns COMPLETE fixed block (not a single line)
     c. Replace anchor lines with the fixed block
     d. defects4j compile → if FAIL: feed error to LLM, retry
     e. defects4j test -r → if PASS: record + stop
                            if FAIL: feed test error to LLM, retry
  6. Record final status

Output: test_validation_v2_results.csv
"""

import os, sys, csv, time, re, shutil, subprocess, copy
from dotenv import load_dotenv
from groq import Groq

sys.path.insert(0, os.path.dirname(__file__))
from ast_extractor_v2 import process_bug_v2, find_anchor_block
import javalang

load_dotenv()
client = Groq(api_key=os.getenv("GROQ_API_KEY"))
MODEL   = "llama-3.3-70b-versatile"
D4J     = "/Users/karnav/Desktop/Projects/Paper/defects4j/framework/bin/defects4j"
PROJECT = "Lang"
WORK_DIR = "/tmp/d4j_v2"
MAX_ATTEMPTS = 3
TARGET_BUGS  = [1, 3, 4, 5, 6, 7, 8, 9, 10, 11]

os.makedirs(WORK_DIR, exist_ok=True)

# ── Prompts ──────────────────────────────────────────────────────────────────

SYSTEM_INITIAL = """You are an expert Java APR engineer doing Automated Program Repair.
You will be given:
1. A small code block with the exact buggy line marked with a comment
2. The failing test name and error message

Your job: output ONLY the corrected version of the buggy line (or the minimum lines needed).
Do NOT rewrite the surrounding code. Do NOT add surrounding braces or methods.
Preserve original indentation exactly.
Output ONLY the corrected Java line(s) — nothing else, no markdown, no explanation."""

SYSTEM_COMPILE_RETRY = """You are an expert Java APR engineer.
Your previous fix caused a compilation error. The error is shown below.
Output ONLY the corrected Java line — the SINGLE LINE that needs to change.
No surrounding code, no markdown, no explanation. Preserve indentation."""

SYSTEM_TEST_RETRY = """You are an expert Java APR engineer.
Your previous fix compiled but the test still fails. The test is shown below.
Output ONLY the corrected Java line — the SINGLE LINE that needs to change.
No surrounding code, no markdown, no explanation. Think carefully about what the test expects."""


# ── Helpers ──────────────────────────────────────────────────────────────────

def run_cmd(cmd, cwd=None, timeout=600):
    r = subprocess.run(cmd, shell=True, capture_output=True, cwd=cwd, timeout=timeout)
    return (r.stdout.decode("utf-8", "replace").strip(),
            r.stderr.decode("utf-8", "replace").strip(),
            r.returncode)


def _llm(system, user, max_tokens=512):
    t0 = time.time()
    try:
        r = client.chat.completions.create(
            messages=[{"role": "system", "content": system},
                      {"role": "user",   "content": user}],
            model=MODEL, temperature=0.1, max_tokens=max_tokens,
        )
        ms  = round((time.time() - t0) * 1000, 1)
        txt = r.choices[0].message.content.strip()
        # Strip markdown fences if present
        for fence in ["```java\n", "```\n", "```java", "```"]:
            if txt.startswith(fence):
                txt = txt[len(fence):]
        if txt.endswith("```"):
            txt = txt[:-3]
        return txt.strip(), ms
    except Exception as e:
        return f"// LLM_ERROR: {e}", 0


def get_modified_java(target_dir):
    src_out, _, rc = run_cmd(f"{D4J} export -p dir.src.classes", cwd=target_dir)
    src_dir = src_out.strip() if rc == 0 else "src/main/java"
    cls_out, _, _ = run_cmd(f"{D4J} export -p classes.modified", cwd=target_dir)
    if not cls_out.strip():
        return None
    for cls in cls_out.strip().splitlines():
        rel = cls.strip().replace(".", "/") + ".java"
        full = os.path.join(target_dir, src_dir, rel)
        if os.path.exists(full):
            return full
        res = subprocess.run(["find", target_dir, "-name", os.path.basename(full),
                              "-not", "-path", "*/test/*"],
                             capture_output=True, text=True)
        hits = [l.strip() for l in res.stdout.strip().splitlines() if l.strip()]
        if hits:
            return hits[0]
    return None


D4J_BASE = "/Users/karnav/Desktop/Projects/Paper/defects4j/framework/projects"


def get_trigger_test_info(bug_id, project="Lang"):
    """
    Read the precomputed trigger_tests/{bug_id} file from defects4j.
    Returns (test_name, error_line, stack_trace).
    This is ground-truth error info — no runtime required.
    """
    path = os.path.join(D4J_BASE, project, "trigger_tests", str(bug_id))
    if not os.path.exists(path):
        return "", "", ""
    with open(path) as f:
        content = f.read()
    lines = content.strip().splitlines()
    test_name  = lines[0].lstrip("- ").strip() if lines else ""
    error_line = lines[1].strip() if len(lines) > 1 else ""
    stack      = "\n".join(lines[:8])  # first 8 lines of stack
    return test_name, error_line, stack


def get_bug_line_from_stack(bug_id, java_file, project="Lang"):
    """
    Parse the trigger_tests stack trace to find which line of the
    production Java file is implicated. Falls back to git diff.
    """
    fname = os.path.basename(java_file).replace(".java", "")
    path  = os.path.join(D4J_BASE, project, "trigger_tests", str(bug_id))
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                # e.g. "at org.apache...NumberUtils.createNumber(NumberUtils.java:474)"
                if fname + ".java:" in line:
                    m = re.search(rf"{re.escape(fname)}\.java:(\d+)", line)
                    if m:
                        return int(m.group(1))
    return None


def get_anchor_range(source_lines, tree, buggy_line):
    """Return (start_0idx, end_0idx) of the AST anchor block."""
    try:
        anchor_node, _ = find_anchor_block(source_lines, tree, buggy_line)
        if not anchor_node or not hasattr(anchor_node, "position"):
            raise ValueError
        start = anchor_node.position.line - 1

        def ml(n, cur):
            if hasattr(n, "position") and n.position:
                cur = max(cur, n.position.line)
            if hasattr(n, "children"):
                for c in n.children:
                    if isinstance(c, list):
                        for item in c:
                            if hasattr(item, "__dict__"): cur = ml(item, cur)
                    elif hasattr(c, "__dict__"):
                        cur = ml(c, cur)
            return cur

        end = min(ml(anchor_node, start + 1) + 2, len(source_lines))
        return start, end
    except Exception:
        s = max(0, buggy_line - 4)
        return s, min(len(source_lines), buggy_line + 4)


def build_wider_context(source_lines, s0, e0, buggy_line, padding=5, ceiling=80):
    """Anchor block ± padding lines, marked at buggy line."""
    def tok(lines):
        return len(" ".join(l.rstrip() for l in lines).split())

    pad = padding
    while pad >= 0:
        start = max(0, s0 - pad)
        end   = min(len(source_lines), e0 + pad)
        if tok(source_lines[start:end]) <= ceiling or pad == 0:
            break
        pad -= 1

    result = []
    for i, line in enumerate(source_lines[start:end], start=start):
        if i == buggy_line - 1:
            result.append("    // ← BUG IS ON THIS LINE\n")
        result.append(line)

    return "".join(result).strip(), start, end


def get_failing_test_info(target_dir):
    """Read defects4j failing_tests file after running tests."""
    run_cmd(f"{D4J} test -r", cwd=target_dir, timeout=180)
    ft = os.path.join(target_dir, "failing_tests")
    if not os.path.exists(ft):
        return "(no failing_tests file)"
    with open(ft) as f:
        lines = [l.strip() for l in f if l.strip()]
    if not lines:
        return "(no failing tests recorded)"
    failing = lines[0]  # e.g. org.apache.Foo::testBar

    # Try to find surefire error message
    cls_name = failing.split("::")[0].split(".")[-1]
    error_msg = ""
    for report_dir in [
        os.path.join(target_dir, "target", "surefire-reports"),
        os.path.join(target_dir, "build", "test-results"),
    ]:
        if os.path.exists(report_dir):
            for fname in os.listdir(report_dir):
                if cls_name in fname and fname.endswith(".txt"):
                    with open(os.path.join(report_dir, fname),
                              encoding="utf-8", errors="replace") as f:
                        for line in f:
                            if any(k in line for k in [
                                "AssertionError", "expected", "but was",
                                "ComparisonFailure", "Exception"
                            ]):
                                error_msg = line.strip()[:250]
                                break
                    if error_msg:
                        break

    result = f"Failing test: {failing}"
    if error_msg:
        result += f"\nError: {error_msg}"
    return result


def apply_single_line_fix(java_file, buggy_line_1idx, fix_text):
    """
    Replace ONLY the buggy line (1-indexed) with the LLM's corrected line.
    Preserves indentation from the original line.
    If fix_text has multiple lines (LLM still returned extra), take the first non-empty one.
    """
    with open(java_file, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    idx = buggy_line_1idx - 1
    if idx < 0 or idx >= len(lines):
        return False

    original = lines[idx]
    orig_indent = len(original) - len(original.lstrip())
    indent_str  = original[:orig_indent]

    # Take only the first meaningful line from LLM output
    fix_stripped = ""
    for candidate in fix_text.strip().splitlines():
        stripped = candidate.strip()
        # Skip comment markers or empty lines
        if stripped and not stripped.startswith("//") and "←" not in stripped:
            fix_stripped = stripped
            break
    if not fix_stripped:
        fix_stripped = fix_text.strip().splitlines()[0].strip() if fix_text.strip() else original.strip()

    lines[idx] = indent_str + fix_stripped + "\n"

    with open(java_file, "w", encoding="utf-8") as f:
        f.writelines(lines)
    return True


def get_compile_error(target_dir):
    """Run compile and return a short error snippet."""
    out, err, rc = run_cmd(f"{D4J} compile", cwd=target_dir, timeout=120)
    if rc == 0:
        return None, True  # compiled
    # Extract first meaningful error line
    for line in (out + "\n" + err).splitlines():
        if "error:" in line.lower() or "ERROR" in line:
            return line.strip()[:300], False
    return (out + err)[:300], False


def get_test_result(target_dir):
    """Run relevant tests, return (failing_count, error_snippet, passed)."""
    out, err, _ = run_cmd(f"{D4J} test -r", cwd=target_dir, timeout=600)
    ft = os.path.join(target_dir, "failing_tests")
    failing = 0
    if os.path.exists(ft):
        with open(ft) as f:
            failing = len([l for l in f if l.strip()])

    error_snippet = ""
    for line in (out + err).splitlines():
        if any(k in line for k in ["AssertionError", "expected", "but was",
                                    "ComparisonFailure", "Exception"]):
            error_snippet = line.strip()[:200]
            break

    return failing, error_snippet, (failing == 0)


# ── Main validation logic ─────────────────────────────────────────────────────

def validate_bug(bug_id):
    target = os.path.join(WORK_DIR, f"Lang_{bug_id}")
    if os.path.exists(target):
        shutil.rmtree(target)
    os.makedirs(target, exist_ok=True)

    print(f"\n{'─'*60}")
    print(f"[Lang-{bug_id}] Checking out...")

    _, _, rc = run_cmd(f"{D4J} checkout -p {PROJECT} -v {bug_id}b -w {target}",
                       timeout=180)
    if rc != 0:
        shutil.rmtree(target, ignore_errors=True)
        return mk_row(bug_id, "CHECKOUT_FAIL")

    java_file = get_modified_java(target)
    if not java_file:
        shutil.rmtree(target, ignore_errors=True)
        return mk_row(bug_id, "NO_FILE")

    # Use trigger_tests stack trace to find the EXACT buggy line
    fl = get_bug_line_from_stack(bug_id, java_file)
    if not fl:
        # Fallback: use git diff first-hunk line
        log, _, _ = run_cmd("git log --all --oneline", cwd=target)
        commits   = [l.split()[0] for l in log.splitlines() if l.strip()]
        if len(commits) >= 2:
            rel = os.path.relpath(java_file, target)
            diff, _, _ = run_cmd(f"git diff {commits[1]}..{commits[0]} -- {rel}", cwd=target)
            for dl in diff.splitlines():
                m = re.search(r"@@ -(\d+)", dl)
                if m:
                    fl = int(m.group(1))
                    break
    if not fl:
        shutil.rmtree(target, ignore_errors=True)
        return mk_row(bug_id, "NO_LINE", os.path.basename(java_file))

    fname = os.path.basename(java_file)
    print(f"  File: {fname}  Bug line: {fl}")

    # Extract AST context for token metrics
    extraction = process_bug_v2(java_file, fl)
    ast_tok  = extraction["ast_tokens"]        if extraction else 0
    reduct   = extraction["reduction_percent"] if extraction else 0
    orig_tok = extraction["original_tokens"]   if extraction else 0
    print(f"  Token reduction: {orig_tok} → {ast_tok} ({reduct}%)")

    # Read source
    with open(java_file, "r", encoding="utf-8", errors="replace") as f:
        src = f.read()
    source_lines = src.splitlines(True)

    # Build context: ±12 lines around the buggy line, hard 300-token cap
    ctx_pad   = 12
    ctx_start = max(0, fl - 1 - ctx_pad)
    ctx_end   = min(len(source_lines), fl + ctx_pad)

    def tok(lines):
        return len(" ".join(l.rstrip() for l in lines).split())
    while tok(source_lines[ctx_start:ctx_end]) > 300 and ctx_pad > 4:
        ctx_pad -= 1
        ctx_start = max(0, fl - 1 - ctx_pad)
        ctx_end   = min(len(source_lines), fl + ctx_pad)

    ctx_lines = []
    for i, line in enumerate(source_lines[ctx_start:ctx_end], start=ctx_start):
        if i == fl - 1:
            ctx_lines.append(f"    // ← BUGGY LINE {fl}\n")
        ctx_lines.append(line)
    wider_ctx = "".join(ctx_lines).strip()
    print(f"  Block: lines {ctx_start+1}–{ctx_end} ({ctx_end-ctx_start} lines, {tok(source_lines[ctx_start:ctx_end])} tokens)")

    # Load ground-truth test info from defects4j trigger_tests (no live run needed)
    test_name, error_line, stack = get_trigger_test_info(bug_id)
    test_error = f"Failing test: {test_name}\nError: {error_line}\nStack:\n{stack}"
    print(f"  Test: {test_name}")
    print(f"  Error: {error_line[:80]}")



    # Save original file content for rollback between attempts
    with open(java_file, "r", encoding="utf-8", errors="replace") as f:
        original_content = f.read()

    best_status = "FAIL_ALL_ATTEMPTS"
    best_fix    = ""
    attempts_log = []

    for attempt in range(1, MAX_ATTEMPTS + 1):
        print(f"\n  ── Attempt {attempt}/{MAX_ATTEMPTS} ──")

        # Rollback to original before each attempt
        with open(java_file, "w", encoding="utf-8") as f:
            f.write(original_content)

        # Choose prompt based on attempt
        if attempt == 1:
            system = SYSTEM_INITIAL
            user = (
                f"Failing test information:\n{test_error}\n\n"
                f"Buggy code block:\n{wider_ctx}\n\n"
                "Output the COMPLETE fixed version of this code block."
            )
        elif attempt == 2 and attempts_log:
            prev = attempts_log[-1]
            system = SYSTEM_COMPILE_RETRY if "compile" in prev["result"] else SYSTEM_TEST_RETRY
            user = (
                f"Original bug context:\n{wider_ctx}\n\n"
                f"Failing test:\n{test_error}\n\n"
                f"Your previous fix:\n{prev['fix']}\n\n"
                f"Problem: {prev['error']}\n\n"
                "Output the COMPLETE corrected code block."
            )
        else:
            prev = attempts_log[-1]
            system = SYSTEM_COMPILE_RETRY if "compile" in prev["result"] else SYSTEM_TEST_RETRY
            user = (
                f"Code block:\n{wider_ctx}\n\n"
                f"Test that must pass:\n{test_error}\n\n"
                f"Last attempt:\n{prev['fix']}\n\n"
                f"Error: {prev['error']}\n\n"
                "Final attempt — output ONLY the corrected Java block."
            )

        fix_block, llm_ms = _llm(system, user, max_tokens=512)
        print(f"  LLM ({llm_ms}ms): {fix_block[:60].replace(chr(10),' ')}")

        if "LLM_ERROR" in fix_block:
            attempts_log.append({"attempt": attempt, "fix": fix_block,
                                  "result": "llm_error", "error": fix_block})
            continue

        # Apply block
        apply_single_line_fix(java_file, fl, fix_block)

        # Compile check
        compile_err, compiled = get_compile_error(target)
        if not compiled:
            print(f"  ❌ Compile fail: {(compile_err or '')[:60]}")
            attempts_log.append({"attempt": attempt, "fix": fix_block,
                                  "result": "compile_fail",
                                  "error": f"Compile error: {compile_err}"})
            continue

        print(f"  ✅ Compiled!")

        # Test check
        failing, test_err_snippet, passed = get_test_result(target)
        if passed:
            print(f"  🎉 ALL TESTS PASS on attempt {attempt}!")
            best_status = f"PASS (attempt {attempt})"
            best_fix    = fix_block
            attempts_log.append({"attempt": attempt, "fix": fix_block,
                                  "result": "pass", "error": ""})
            break
        else:
            print(f"  ❌ {failing} tests failing. Error: {test_err_snippet[:60]}")
            attempts_log.append({"attempt": attempt, "fix": fix_block,
                                  "result": f"test_fail ({failing})",
                                  "error": f"Test error: {test_err_snippet}"})

    shutil.rmtree(target, ignore_errors=True)

    return mk_row(
        bug_id, best_status, fname, fl, ast_tok, reduct,
        str(best_fix)[:300],
        len(attempts_log),
        ",".join(a["result"] for a in attempts_log)
    )


def mk_row(bug_id, status, file="", line=0, ast_tok=0,
           reduction=0, fix="", attempts=0, attempt_results=""):
    return {
        "BugID":          f"Lang-{bug_id}",
        "Status":         status,
        "File":           file,
        "FaultyLine":     line,
        "ASTTokens":      ast_tok,
        "TokenReduction%": reduction,
        "Attempts":       attempts,
        "AttemptResults": attempt_results,
        "BestFix":        str(fix).replace("\n", " ")[:300],
        "TestPassed":     "PASS" in status,
    }


def run():
    rows = []
    print(f"\n{'='*60}")
    print(f"  Defects4J Block-Replace + Retry — {len(TARGET_BUGS)} bugs")
    print(f"  Model: {MODEL}  |  Max attempts: {MAX_ATTEMPTS}")
    print(f"{'='*60}")

    for bug_id in TARGET_BUGS:
        row = validate_bug(bug_id)
        rows.append(row)
        print(f"\n  → Lang-{bug_id}: {row['Status']}")

    out = "test_validation_v2_results.csv"
    with open(out, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    passed  = [r for r in rows if r["TestPassed"]]
    cfail   = [r for r in rows if "compile" in r["Status"].lower()]
    tfail   = [r for r in rows if "test_fail" in r["Status"].lower()]

    print(f"\n{'='*60}\n  FINAL RESULTS\n{'='*60}")
    print(f"  Total       : {len(rows)}")
    print(f"  ✅ PASS     : {len(passed)}  →  {[r['BugID'] for r in passed]}")
    print(f"  ❌ Compile  : {len(cfail)}")
    print(f"  ❌ TestFail : {len(tfail)}")
    print(f"  Pass Rate   : {round(len(passed)/len(rows)*100)}%")
    print(f"  Saved       → {out}")
    print(f"{'='*60}")
    shutil.rmtree(WORK_DIR, ignore_errors=True)


if __name__ == "__main__":
    run()
