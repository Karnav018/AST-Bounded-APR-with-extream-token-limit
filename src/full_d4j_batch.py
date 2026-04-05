"""
full_d4j_batch.py — Full Defects4J Lang Batch Analysis
Processes ALL 61 Lang bugs automatically:
  1. Checkout buggy version via defects4j CLI
  2. Find modified file(s) from the diff
  3. Detect faulty line from diff patch
  4. Run AST extraction (v2 multi-node)
  5. Record metrics in CSV
  6. Delete temporary checkout

Output: d4j_full_results.csv  (paper Table 1 data source)
"""

import os
import sys
import csv
import re
import json
import shutil
import subprocess
import tempfile

sys.path.insert(0, os.path.dirname(__file__))
from ast_extractor_v2 import process_bug_v2

D4J_CMD  = "/Users/karnav/Desktop/Projects/Paper/defects4j/framework/bin/defects4j"
PROJECT  = "Lang"
WORK_DIR = "/tmp/d4j_batch_checkouts"  # temp dir, cleaned between bugs
CSV_OUT  = "d4j_full_results.csv"

# Bug IDs to skip (known to be test-only, multi-file, or non-parseable)
SKIP_IDS = set()

os.makedirs(WORK_DIR, exist_ok=True)


def run(cmd, cwd=None, timeout=120):
    """Run a shell command, return (stdout, stderr, returncode)."""
    result = subprocess.run(
        cmd, shell=True, capture_output=True,
        cwd=cwd, timeout=timeout
    )
    stdout = result.stdout.decode("utf-8", errors="replace").strip()
    stderr = result.stderr.decode("utf-8", errors="replace").strip()
    return stdout, stderr, result.returncode


def get_all_bug_ids():
    """Get list of valid bug IDs from the active-bugs CSV."""
    active_csv = os.path.join(
        os.path.dirname(D4J_CMD), "..", "projects", PROJECT, "active-bugs.csv"
    )
    bug_ids = []
    with open(active_csv) as f:
        reader = csv.DictReader(f)
        for row in reader:
            bug_ids.append(int(row["bug.id"]))
    return sorted(bug_ids)


def checkout_bug(bug_id, target_dir):
    """Checkout the buggy version of a bug. Returns True on success."""
    cmd = f"{D4J_CMD} checkout -p {PROJECT} -v {bug_id}b -w {target_dir}"
    _, stderr, rc = run(cmd, timeout=180)
    if rc != 0:
        print(f"  [CHECKOUT FAILED] {stderr[:120]}")
        return False
    return True


def get_modified_files(bug_id, target_dir):
    """
    Use defects4j export to find modified source files for this bug.
    Returns list of relative paths (src/...java).
    """
    stdout, _, rc = run(
        f"{D4J_CMD} export -p dir.src.classes",
        cwd=target_dir
    )
    src_dir = stdout.strip() if rc == 0 else "src/main/java"

    # Get list of modified files from the patch
    stdout, _, rc = run(
        f"{D4J_CMD} export -p classes.modified",
        cwd=target_dir
    )
    if rc != 0 or not stdout.strip():
        return []

    modified = []
    for cls in stdout.strip().splitlines():
        # Convert class name like org.apache.commons.lang3.math.NumberUtils
        # to relative file path
        rel_path = cls.strip().replace(".", "/") + ".java"
        # Look for it in the source dir
        candidate = os.path.join(target_dir, src_dir, rel_path)
        if os.path.exists(candidate):
            modified.append(candidate)
        else:
            # Try to find it by searching
            result = subprocess.run(
                ["find", target_dir, "-name", os.path.basename(candidate), "-not", "-path", "*/test/*"],
                capture_output=True, text=True
            )
            matches = [l.strip() for l in result.stdout.strip().splitlines() if l.strip()]
            if matches:
                modified.append(matches[0])
    return modified


def get_faulty_line_from_diff(bug_id, target_dir, java_file):
    """
    Get the first modified line number in the buggy file.
    We diff the buggy HEAD vs fixed version to find what changed.
    Returns the line number (int) or None.
    """
    # Get the diff between buggy and fixed using defects4j
    stdout, _, rc = run(
        f"git diff HEAD~1 HEAD -- {os.path.relpath(java_file, target_dir)} 2>/dev/null "
        f"|| git log --oneline -2",
        cwd=target_dir
    )

    # Try unified diff parsing — look for @@ -N lines
    # Alternatively, use defects4j export -p lines.modified
    stdout2, _, rc2 = run(
        f"{D4J_CMD} export -p lines.modified",
        cwd=target_dir
    )
    if rc2 == 0 and stdout2.strip():
        # Format is usually: "file.java:line1,line2,..."
        for entry in stdout2.strip().splitlines():
            if java_file.endswith(entry.split(":")[0].split("/")[-1]):
                try:
                    lines_part = entry.split(":")[1]
                    first_line = int(lines_part.split(",")[0].strip())
                    return first_line
                except (IndexError, ValueError):
                    pass

    # Fallback: export modified_classes and try git diff to find line
    stdout3, _, _ = run(
        "git log --all --oneline | head -2",
        cwd=target_dir
    )
    commits = [l.split()[0] for l in stdout3.splitlines() if l.strip()]
    if len(commits) >= 2:
        diff_out, _, _ = run(
            f"git diff {commits[1]} {commits[0]} -- {os.path.relpath(java_file, target_dir)}",
            cwd=target_dir
        )
        for line in diff_out.splitlines():
            m = re.search(r'@@ -(\d+)', line)
            if m:
                return int(m.group(1))
    return None


def analyze_bug(bug_id):
    """
    Full pipeline for one bug: checkout, find file+line, extract, metrics.
    Returns a result dict or an error dict.
    """
    target_dir = os.path.join(WORK_DIR, f"Lang_{bug_id}")
    if os.path.exists(target_dir):
        shutil.rmtree(target_dir)
    os.makedirs(target_dir, exist_ok=True)

    print(f"\n[Lang-{bug_id}] Checking out...")

    # Step 1: Checkout
    if not checkout_bug(bug_id, target_dir):
        shutil.rmtree(target_dir, ignore_errors=True)
        return {"BugID": f"Lang-{bug_id}", "Status": "CHECKOUT_FAILED",
                "File": "", "FaultyLine": "", "OriginalTokens": 0,
                "ASTTokens": 0, "Reduction": 0, "AnchorType": ""}

    # Step 2: Find modified file(s)
    modified = get_modified_files(bug_id, target_dir)
    if not modified:
        shutil.rmtree(target_dir, ignore_errors=True)
        return {"BugID": f"Lang-{bug_id}", "Status": "NO_MODIFIED_FILE",
                "File": "", "FaultyLine": "", "OriginalTokens": 0,
                "ASTTokens": 0, "Reduction": 0, "AnchorType": ""}

    # Use first modified file (skip multi-file bugs based on count)
    if len(modified) > 2:
        shutil.rmtree(target_dir, ignore_errors=True)
        return {"BugID": f"Lang-{bug_id}", "Status": f"MULTI_FILE_SKIP ({len(modified)} files)",
                "File": "", "FaultyLine": "", "OriginalTokens": 0,
                "ASTTokens": 0, "Reduction": 0, "AnchorType": ""}

    java_file = modified[0]
    file_name = os.path.basename(java_file)

    # Step 3: Find faulty line
    faulty_line = get_faulty_line_from_diff(bug_id, target_dir, java_file)
    if not faulty_line:
        shutil.rmtree(target_dir, ignore_errors=True)
        return {"BugID": f"Lang-{bug_id}", "Status": "LINE_NOT_FOUND",
                "File": file_name, "FaultyLine": "", "OriginalTokens": 0,
                "ASTTokens": 0, "Reduction": 0, "AnchorType": ""}

    print(f"  File: {file_name}  |  Line: {faulty_line}")

    # Step 4: Run AST extraction
    try:
        result = process_bug_v2(java_file, faulty_line)
    except Exception as e:
        shutil.rmtree(target_dir, ignore_errors=True)
        return {"BugID": f"Lang-{bug_id}", "Status": f"EXTRACT_ERROR:{str(e)[:60]}",
                "File": file_name, "FaultyLine": faulty_line, "OriginalTokens": 0,
                "ASTTokens": 0, "Reduction": 0, "AnchorType": ""}

    shutil.rmtree(target_dir, ignore_errors=True)

    if not result:
        return {"BugID": f"Lang-{bug_id}", "Status": "PARSE_FAIL",
                "File": file_name, "FaultyLine": faulty_line, "OriginalTokens": 0,
                "ASTTokens": 0, "Reduction": 0, "AnchorType": ""}

    within = "✅" if result["ast_tokens"] <= 80 else "⚠️"
    print(f"  {within} Tokens: {result['original_tokens']} → {result['ast_tokens']}  ({result['reduction_percent']}% reduction)")

    return {
        "BugID": f"Lang-{bug_id}",
        "Status": "SUCCESS",
        "File": file_name,
        "FaultyLine": faulty_line,
        "OriginalTokens": result["original_tokens"],
        "ASTTokens": result["ast_tokens"],
        "Reduction": result["reduction_percent"],
        "AnchorType": result["anchor_type"],
    }


def run_batch(start=1, end=None):
    bug_ids = get_all_bug_ids()
    if end:
        bug_ids = [b for b in bug_ids if start <= b <= end]
    else:
        bug_ids = [b for b in bug_ids if b >= start]

    bug_ids = [b for b in bug_ids if b not in SKIP_IDS]

    print(f"\n{'='*65}")
    print(f"  FULL Defects4J Lang Batch — {len(bug_ids)} bugs")
    print(f"{'='*65}")

    results = []
    for bug_id in bug_ids:
        row = analyze_bug(bug_id)
        results.append(row)

    # Write CSV
    with open(CSV_OUT, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=results[0].keys())
        writer.writeheader()
        writer.writerows(results)

    # Summary
    success = [r for r in results if r["Status"] == "SUCCESS"]
    within  = [r for r in success if r["ASTTokens"] <= 80]
    avg_orig = sum(r["OriginalTokens"] for r in success) / len(success) if success else 0
    avg_ast  = sum(r["ASTTokens"]      for r in success) / len(success) if success else 0
    avg_red  = sum(r["Reduction"]      for r in success) / len(success) if success else 0

    print(f"\n{'='*65}")
    print(f"  SUMMARY")
    print(f"{'='*65}")
    print(f"  Total Bugs Attempted    : {len(results)}")
    print(f"  Successfully Extracted  : {len(success)}")
    print(f"  Within 80-Token Budget  : {len(within)}/{len(success)} ({round(len(within)/len(success)*100) if success else 0}%)")
    print(f"  Avg Original Tokens     : {avg_orig:.0f}")
    print(f"  Avg AST Tokens          : {avg_ast:.1f}")
    print(f"  Avg Token Reduction     : {avg_red:.2f}%")
    print(f"  Results saved → {CSV_OUT}")
    print(f"{'='*65}")

    # Clean up temp dir
    shutil.rmtree(WORK_DIR, ignore_errors=True)


if __name__ == "__main__":
    # Optional: pass start and end bug ID as args
    # e.g., python full_d4j_batch.py 1 20  → processes Lang-1 through Lang-20
    start = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    end   = int(sys.argv[2]) if len(sys.argv) > 2 else None
    run_batch(start, end)
