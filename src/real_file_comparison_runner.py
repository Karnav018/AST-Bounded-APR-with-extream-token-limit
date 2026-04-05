"""
Real-File Baseline Comparison — Production Java vs AST Slice
Uses actual Defects4J buggy Java files to demonstrate 90%+ token reduction.
"""
import os, sys, csv, time
from dotenv import load_dotenv
from groq import Groq

sys.path.insert(0, os.path.dirname(__file__))
from ast_extractor import process_bug

load_dotenv()
client = Groq(api_key=os.getenv("GROQ_API_KEY"))
MODEL_NAME = "llama-3.1-8b-instant"
COST_PER_1K = 0.00005

# Real Defects4J bugs — full production Java files
REAL_TESTS = [
    {
        "id": "Lang-1",
        "file": "Lang_1_buggy/src/main/java/org/apache/commons/lang3/math/NumberUtils.java",
        "buggy_line": 464,
        "bug_type": "Logic boundary (prefix parsing)",
    },
    {
        "id": "Lang-3",
        "file": "Lang_3_buggy/src/main/java/org/apache/commons/lang3/math/NumberUtils.java",
        "buggy_line": 594,
        "bug_type": "Numeric boundary (hex literal check)",
    },
]

REPAIR_SYSTEM = (
    "You are an expert Automated Program Repair AI. Fix the logical bug in the provided Java code. "
    "Output ONLY the fixed code — no explanation, no markdown blocks."
)

def call_llm(user_content, max_tokens=512):
    tokens_est = len(user_content.split())
    start = time.time()
    resp = client.chat.completions.create(
        messages=[{"role": "system", "content": REPAIR_SYSTEM},
                  {"role": "user",   "content": user_content}],
        model=MODEL_NAME, temperature=0.2, max_tokens=max_tokens,
    )
    ms = round((time.time() - start) * 1000, 1)
    text = resp.choices[0].message.content.strip()
    for tag in ["```java", "```"]:
        if text.startswith(tag): text = text[len(tag):]
    if text.endswith("```"): text = text[:-3]
    return text.strip(), ms, tokens_est

def run_real_comparison():
    results = []
    print("\n" + "="*65)
    print("  REAL-FILE COMPARISON (Defects4J Production Java)")
    print("="*65 + "\n")

    for t in REAL_TESTS:
        if not os.path.exists(t["file"]):
            print(f"[SKIP] {t['id']} — file not found: {t['file']}")
            continue

        with open(t["file"]) as f:
            full_source = f.read()

        full_tokens = len(full_source.split())
        print(f"[{t['id']}] {t['bug_type']} — Line {t['buggy_line']}")

        # --- BASELINE: truncate to first 3000 words to stay within Groq limit ---
        safe_baseline = " ".join(full_source.split()[:3000])
        baseline_prompt = f"Buggy Java file (bug near line {t['buggy_line']}):\n\n{safe_baseline}"
        baseline_fix, baseline_ms, baseline_tokens = call_llm(baseline_prompt, max_tokens=256)

        # --- PROPOSED: AST slice ---
        extraction = process_bug(t["file"], t["buggy_line"])
        if not extraction:
            print(f"  [SKIP] AST extraction failed.\n")
            continue

        proposed_prompt = (
            f"Target Node: {extraction['anchor_type']}\n\n"
            f"Buggy Block:\n{extraction['extracted_code']}\n\n"
            "Fix the logic bug. Output ONLY code."
        )
        proposed_fix, proposed_ms, proposed_tokens = call_llm(proposed_prompt, max_tokens=128)
        ast_tokens = extraction["ast_tokens"]

        reduction = round((1 - ast_tokens / full_tokens) * 100, 2)
        baseline_cost = round(baseline_tokens / 1000 * COST_PER_1K, 7)
        proposed_cost = round(proposed_tokens / 1000 * COST_PER_1K, 7)
        cost_saved    = round((1 - proposed_cost / baseline_cost) * 100, 1) if baseline_cost else 0

        print(f"  Full file tokens    : {full_tokens:,}")
        print(f"  Baseline sent (cap) : {baseline_tokens:,} tokens | {baseline_ms:.0f}ms | ${baseline_cost:.7f}")
        print(f"  Proposed AST tokens : {ast_tokens} tokens | {proposed_ms:.0f}ms | ${proposed_cost:.7f}")
        print(f"  Token Reduction     : {reduction}%")
        print(f"  Cost Saved          : {cost_saved}%")
        print(f"  Proposed Fix Preview: {proposed_fix[:100].replace(chr(10),' ')}")
        print()

        results.append({
            "BugID": t["id"],
            "BugType": t["bug_type"],
            "FullFileTokens": full_tokens,
            "Baseline_SentTokens": baseline_tokens,
            "Baseline_LatencyMs": baseline_ms,
            "Baseline_CostUSD": baseline_cost,
            "AST_ContextTokens": ast_tokens,
            "Proposed_SentTokens": proposed_tokens,
            "Proposed_LatencyMs": proposed_ms,
            "Proposed_CostUSD": proposed_cost,
            "TokenReductionPercent": reduction,
            "CostSavedPercent": cost_saved,
            "SpeedupFactor": round(baseline_ms / proposed_ms, 1) if proposed_ms else 0,
            "Proposed_Fix": proposed_fix[:150].replace("\n", " "),
        })

    if not results:
        print("No results collected.")
        return

    out = "real_file_comparison_results.csv"
    with open(out, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=results[0].keys())
        writer.writeheader()
        writer.writerows(results)

    avg_red = sum(r["TokenReductionPercent"] for r in results) / len(results)
    avg_cost = sum(r["CostSavedPercent"] for r in results) / len(results)
    avg_spd  = sum(r["SpeedupFactor"] for r in results) / len(results)

    print("="*65)
    print("  PAPER SUMMARY (Real Files)")
    print("="*65)
    print(f"  Avg Token Reduction  : {avg_red:.2f}%")
    print(f"  Avg Cost Saved       : {avg_cost:.1f}%")
    print(f"  Avg Speedup Factor   : {avg_spd:.1f}x")
    print(f"\n  Results saved → {out}")
    print("="*65)

if __name__ == "__main__":
    run_real_comparison()
