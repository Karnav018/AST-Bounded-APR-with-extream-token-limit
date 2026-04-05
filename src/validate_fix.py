"""
validate_fix.py — Defects4J Test Validator
The critical validation script for ACM paper Section 5 (RQ2).

For each of 10 Lang bugs:
  1. Checkout buggy version
  2. Extract AST context (v2 multi-node)
  3. Call LLM to generate fix
  4. Apply the fix (patch the Java file at the extracted line range)
  5. Compile with defects4j compile
  6. Run relevant tests with defects4j test -r
  7. Record: PASS / COMPILE_FAIL / TEST_FAIL / PATCH_FAIL

Output: test_validation_results.csv
"""

import os, sys, csv, time, re, shutil, subprocess
from google import genai
from google.genai import types
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(__file__))
from ast_extractor_v2 import process_bug_v2, find_anchor_block
import javalang

load_dotenv()
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
MODEL   = "gemini-2.0-flash-lite"   # separate quota from 2.5-flash
D4J    = "/Users/karnav/Desktop/Projects/Paper/defects4j/framework/bin/defects4j"
PROJECT = "Lang"
WORK_DIR = "/tmp/d4j_validate"

TARGET_BUGS = [1, 3, 4, 5, 6, 7, 8, 9, 10, 11]

DIAGNOSE_SYSTEM = (
    "You are a Java static analysis expert. "
    "Output ONE sentence describing the exact bug type. No code."
)
# New single-line fix strategy: ask LLM for ONLY the corrected line
FIX_SYSTEM = (
    "You are an expert Java bug fixer. "
    "You will be shown a small Java code block with a bug, and the diagnosis. "
    "Output ONLY the single corrected Java line that needs to change — "
    "nothing else. No explanation, no surrounding code, no markdown. "
    "Preserve the original indentation exactly."
)

os.makedirs(WORK_DIR, exist_ok=True)


def run_cmd(cmd, cwd=None, timeout=300):
    r = subprocess.run(cmd, shell=True, capture_output=True, cwd=cwd, timeout=timeout)
    return (r.stdout.decode("utf-8", "replace").strip(),
            r.stderr.decode("utf-8", "replace").strip(),
            r.returncode)


def get_modified_java(bug_id, target_dir):
    src_out, _, rc = run_cmd(f"{D4J} export -p dir.src.classes", cwd=target_dir)
    src_dir = src_out.strip() if rc == 0 else "src/main/java"
    cls_out, _, rc = run_cmd(f"{D4J} export -p classes.modified", cwd=target_dir)
    if rc != 0 or not cls_out.strip():
        return None, None
    for cls in cls_out.strip().splitlines():
        rel_path = cls.strip().replace(".", "/") + ".java"
        candidate = os.path.join(target_dir, src_dir, rel_path)
        if os.path.exists(candidate):
            return candidate, src_dir
        result = subprocess.run(
            ["find", target_dir, "-name", os.path.basename(candidate),
             "-not", "-path", "*/test/*"],
            capture_output=True, text=True
        )
        matches = [l.strip() for l in result.stdout.strip().splitlines() if l.strip()]
        if matches:
            return matches[0], src_dir
    return None, None


def get_faulty_line(bug_id, target_dir, java_file):
    out, _, rc = run_cmd(f"{D4J} export -p lines.modified", cwd=target_dir)
    if rc == 0 and out.strip():
        for entry in out.strip().splitlines():
            fname = entry.split(":")[0].split("/")[-1]
            if java_file.endswith(fname):
                try:
                    return int(entry.split(":")[1].split(",")[0].strip())
                except (IndexError, ValueError):
                    pass
    log_out, _, _ = run_cmd("git log --all --oneline | head -2", cwd=target_dir)
    commits = [l.split()[0] for l in log_out.splitlines() if l.strip()]
    if len(commits) >= 2:
        diff_out, _, _ = run_cmd(
            f"git diff {commits[1]} {commits[0]} -- {os.path.relpath(java_file, target_dir)}",
            cwd=target_dir
        )
        for line in diff_out.splitlines():
            m = re.search(r"@@ -(\d+)", line)
            if m:
                return int(m.group(1))
    return None


def _llm(system, user, max_tokens=512):
    t0 = time.time()
    for attempt in range(4):   # up to 3 retries on rate limit
        try:
            r = client.models.generate_content(
                model=MODEL,
                contents=user,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    max_output_tokens=max_tokens,
                    temperature=0.1,
                )
            )
            ms = round((time.time() - t0) * 1000, 1)
            # gemini-2.5-flash returns via candidates, r.text may be None
            if r.text:
                txt = r.text.strip()
            elif r.candidates:
                txt = r.candidates[0].content.parts[0].text.strip()
            else:
                return "LLM_ERROR: empty response", 0
            # Strip markdown fences
            for tag in ["```java\n", "```\n", "```java", "```"]:
                if txt.startswith(tag):
                    txt = txt[len(tag):]
            if txt.endswith("```"):
                txt = txt[:-3]
            return txt.strip(), ms
        except Exception as e:
            err = str(e)
            if "429" in err or "RESOURCE_EXHAUSTED" in err:
                wait = 30 * (2 ** attempt)   # 30s, 60s, 120s
                print(f"  ⏳ Rate limit hit — waiting {wait}s...")
                time.sleep(wait)
            else:
                return f"LLM_ERROR: {e}", 0
    return "LLM_ERROR: rate limit after 3 retries", 0


def get_failing_test_info(target_dir, timeout=180):
    """
    Run defects4j test -r on the BUGGY code (before patching).
    defects4j writes results to files in the working dir — read them directly.
    Returns: short string with failing test name + error snippet.
    """
    run_cmd(f"{D4J} test -r", cwd=target_dir, timeout=timeout)

    # defects4j writes to these two files
    ft_file  = os.path.join(target_dir, "failing_tests")
    all_file = os.path.join(target_dir, "all_tests")

    failing_test = ""
    if os.path.exists(ft_file):
        with open(ft_file) as f:
            lines = [l.strip() for l in f if l.strip()]
        if lines:
            failing_test = lines[0]  # e.g. "org.apache.Foo::testBar"

    if not failing_test:
        return "(test output not captured)"

    # Convert to junit format and run to get the actual error message
    cls, _, method = failing_test.partition("::")
    # Try to get the stack trace / assertion from the surefire reports
    report_dir = os.path.join(target_dir, "target", "surefire-reports")
    error_msg = ""
    if os.path.exists(report_dir):
        for fname in os.listdir(report_dir):
            if cls.split(".")[-1] in fname and fname.endswith(".txt"):
                with open(os.path.join(report_dir, fname),
                          encoding="utf-8", errors="replace") as f:
                    content = f.read()
                for line in content.splitlines():
                    if any(k in line for k in ["AssertionError", "expected", "but was",
                                                "ComparisonFailure", "Exception"]):
                        error_msg = line.strip()[:200]
                        break

    result = f"Failing test: {failing_test}"
    if error_msg:
        result += f"\nError: {error_msg}"
    return result


def call_llm_two_pass(context, faulty_line_txt, test_error=""):
    """Two-pass: diagnose bug, then get ONLY the corrected single line.
    If test_error is provided, include it as concrete repair target.
    """
    diag, _ = _llm(DIAGNOSE_SYSTEM, f"Bug context:\n{context}", max_tokens=150)

    test_hint = (
        f"\n\nFailing test information (what the fix must satisfy):\n{test_error}"
        if test_error and "not captured" not in test_error else ""
    )
    prompt = (
        f"Diagnosis: {diag}\n\n"
        f"Buggy code context:\n{context}\n\n"
        f"The exact buggy line is:\n{faulty_line_txt}"
        f"{test_hint}\n\n"
        "Output ONLY the single corrected Java line, preserving original indentation."
    )
    fix_line, ms = _llm(FIX_SYSTEM, prompt, max_tokens=150)
    return fix_line, diag, ms


def get_anchor_line_range(source_lines, tree, buggy_line):
    try:
        anchor_node, _ = find_anchor_block(source_lines, tree, buggy_line)
        if not anchor_node or not hasattr(anchor_node, "position"):
            raise ValueError("no anchor")
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

        end = min(ml(anchor_node, start + 1) + 1, len(source_lines))
        return start, end
    except Exception:
        start = max(0, buggy_line - 3)
        return start, min(len(source_lines), buggy_line + 2)


def build_wider_context(source_lines, anchor_start_0, anchor_end_0,
                        buggy_line, padding=5, token_ceiling=80):
    """
    Build a wider context window: the AST anchor block ± `padding` lines,
    with the buggy line highlighted by a comment marker.
    Respects token_ceiling — shrinks padding if needed.
    """
    def tok_count(lines):
        return len(" ".join(l.rstrip() for l in lines).split())

    pad = padding
    while pad >= 0:
        start = max(0, anchor_start_0 - pad)
        end   = min(len(source_lines), anchor_end_0 + pad)
        window = source_lines[start:end]
        if tok_count(window) <= token_ceiling or pad == 0:
            break
        pad -= 1

    # Insert a marker on the buggy line so the LLM knows which line to fix
    result_lines = []
    for i, line in enumerate(source_lines[start:end], start=start):
        if i == buggy_line - 1:
            result_lines.append(f"// ← BUG IS ON THIS LINE\n")
            result_lines.append(line)
        else:
            result_lines.append(line)

    ctx = "".join(result_lines).strip()
    tokens = tok_count(result_lines)
    return ctx, tokens, start, end


def apply_patch_single_line(java_file, faulty_line_1idx, fix_line):
    """
    Replace the specific faulty line (1-indexed) in the file with the LLM's fix.
    Preserves original indentation if fix_line has none.
    """
    with open(java_file, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    idx = faulty_line_1idx - 1
    if idx < 0 or idx >= len(lines):
        return False

    original = lines[idx]
    orig_indent = len(original) - len(original.lstrip())
    indent_str  = original[:orig_indent]

    fix_stripped = fix_line.strip()
    # If LLM returned multiple lines, take only the first non-empty one
    for candidate in fix_line.splitlines():
        if candidate.strip():
            fix_stripped = candidate.strip()
            break

    lines[idx] = indent_str + fix_stripped + "\n"

    with open(java_file, "w", encoding="utf-8") as f:
        f.writelines(lines)
    return True


def build_row(bug_id, status, file="", line=0, ast_tok=0,
              reduction=0, diag="", fix="", ms=0):
    return {
        "BugID": f"Lang-{bug_id}",
        "Status": status,
        "File": file,
        "FaultyLine": line,
        "ASTTokens": ast_tok,
        "TokenReduction%": reduction,
        "Diagnosis": diag,
        "LLMFix": str(fix).replace("\n", " ")[:300],
        "LLMLatencyMs": ms,
        "TestPassed": status == "PASS",
    }


def validate_bug(bug_id):
    target = os.path.join(WORK_DIR, f"Lang_{bug_id}")
    if os.path.exists(target): shutil.rmtree(target)
    os.makedirs(target, exist_ok=True)

    print(f"\n{'─'*58}")
    print(f"[Lang-{bug_id}] Checking out...")

    _, _, rc = run_cmd(f"{D4J} checkout -p {PROJECT} -v {bug_id}b -w {target}",
                       timeout=180)
    if rc != 0:
        shutil.rmtree(target, ignore_errors=True)
        return build_row(bug_id, "CHECKOUT_FAIL")

    java_file, _ = get_modified_java(bug_id, target)
    if not java_file:
        shutil.rmtree(target, ignore_errors=True)
        return build_row(bug_id, "NO_FILE")

    fl = get_faulty_line(bug_id, target, java_file)
    if not fl:
        shutil.rmtree(target, ignore_errors=True)
        return build_row(bug_id, "NO_LINE", os.path.basename(java_file))

    fname = os.path.basename(java_file)
    print(f"  File: {fname}  Line: {fl}")

    extraction = process_bug_v2(java_file, fl)
    if not extraction:
        shutil.rmtree(target, ignore_errors=True)
        return build_row(bug_id, "EXTRACT_FAIL", fname, fl)

    ast_tok = extraction["ast_tokens"]
    reduct  = extraction["reduction_percent"]
    orig_tok = extraction["original_tokens"]
    print(f"  v2 slice : {orig_tok} → {ast_tok} tokens ({reduct}%)")

    with open(java_file, "r", encoding="utf-8", errors="replace") as f:
        src = f.read()
    source_lines = src.splitlines(True)

    try:
        tree = javalang.parse.parse(src)
        s0, e0 = get_anchor_line_range(source_lines, tree, fl)
    except Exception:
        s0 = max(0, fl - 3)
        e0 = min(len(source_lines), fl + 2)

    # Build wider context (Option 3: ±5 lines for richer semantic signal)
    wider_ctx, wider_tok, _, _ = build_wider_context(
        source_lines, s0, e0, fl, padding=25, token_ceiling=400
    )
    print(f"  wider ctx: {wider_tok} tokens (±25 lines, 400-token ceiling)")

    # Get the text of the faulty line for the LLM prompt
    faulty_line_txt = source_lines[fl - 1].rstrip() if fl - 1 < len(source_lines) else ""

    # ── Key step: run tests on BUGGY code FIRST to get concrete error signal ──
    print(f"  Running tests on buggy code to capture error...")
    test_error = get_failing_test_info(target, timeout=180)
    print(f"  Test signal: {test_error[:120].replace(chr(10), ' | ')}")

    fix, diag, llm_ms = call_llm_two_pass(wider_ctx, faulty_line_txt, test_error)
    print(f"  Diagnosis : {diag[:100]}")
    print(f"  Fix line  : {fix[:80].replace(chr(10), ' ')}")

    apply_patch_single_line(java_file, fl, fix)

    _, _, crc = run_cmd(f"{D4J} compile", cwd=target)
    if crc != 0:
        print(f"  ❌ COMPILE FAIL")
        shutil.rmtree(target, ignore_errors=True)
        return build_row(bug_id, "COMPILE_FAIL", fname, fl, ast_tok, reduct, diag, fix, llm_ms)

    print(f"  ✅ Compiled!")
    test_out, test_err, _ = run_cmd(f"{D4J} test -r", cwd=target, timeout=600)

    failing = 0
    for line in (test_out + test_err).splitlines():
        m = re.search(r"Failing tests:\s*(\d+)", line)
        if m:
            failing = int(m.group(1)); break

    if failing == 0 and "BUILD" not in test_err.upper():
        status = "PASS"
        print(f"  🎉 TESTS PASS!")
    else:
        status = f"TEST_FAIL ({failing} failing)"
        print(f"  ❌ {failing} tests failing")

    shutil.rmtree(target, ignore_errors=True)
    return build_row(bug_id, status, fname, fl, ast_tok, reduct, diag, fix, llm_ms)


def run():
    rows = []
    print(f"\n{'='*58}\n  Defects4J Validation — {len(TARGET_BUGS)} bugs\n{'='*58}")

    for bug_id in TARGET_BUGS:
        row = validate_bug(bug_id)
        rows.append(row)

    out = "test_validation_results.csv"
    with open(out, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader(); writer.writerows(rows)

    passed = [r for r in rows if r["TestPassed"]]
    cfail  = [r for r in rows if "COMPILE" in r["Status"]]
    tfail  = [r for r in rows if "TEST_FAIL" in r["Status"]]

    print(f"\n{'='*58}\n  SUMMARY\n{'='*58}")
    print(f"  Total     : {len(rows)}")
    print(f"  ✅ PASS   : {len(passed)}")
    print(f"  ❌ Compile: {len(cfail)}")
    print(f"  ❌ TestFail: {len(tfail)}")
    print(f"  Pass Rate : {round(len(passed)/len(rows)*100)}%")
    print(f"  Saved     → {out}")
    print(f"{'='*58}")
    shutil.rmtree(WORK_DIR, ignore_errors=True)


if __name__ == "__main__":
    run()
