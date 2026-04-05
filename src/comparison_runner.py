"""
Baseline Comparison Runner — IEEE Paper Evidence
Runs each bug through the LLM TWICE:
  1. BASELINE: Full file context  (no slicing, raw tokens)
  2. PROPOSED: AST-bounded slice  (our method)

Captures: token usage, latency, estimated API cost, and fix output.
Saves everything to comparison_results.csv for the paper's Table 1.
"""

import os
import sys
import csv
import time

from dotenv import load_dotenv
from groq import Groq

sys.path.insert(0, os.path.dirname(__file__))
from ast_extractor import process_bug

load_dotenv()
API_KEY = os.getenv("GROQ_API_KEY")

if not API_KEY or API_KEY == "your_groq_api_key_here":
    print("❌ Error: GROQ_API_KEY not set in .env")
    sys.exit(1)

client = Groq(api_key=API_KEY)
MODEL_NAME = "llama-3.1-8b-instant"

# Groq LLaMA-3 8B approximate pricing (per 1K tokens)
COST_PER_1K_TOKENS = 0.00005  # $0.05 per million tokens input

# ---- Test Cases from Benchmark Suite ----
TESTS = [
    ("T1_TinyMethod", 5, "Off-by-one in tiny 5-line method",
     """public class Test1 {
    public int maxIndex(int[] arr) {
        int max = 0;
        for (int i = 0; i < arr.length; i++) {
            if (arr[i] > arr[max]) max = i;
        }
        return max;
    }
}"""),

    ("T2_SmallLoop", 7, "Wrong loop termination condition",
     """public class Test2 {
    public int sumArray(int[] arr) {
        int total = 0;
        int count = 0;
        boolean flag = true;
        String label = "sum";
        // BUG: should be i < arr.length
        for (int i = 0; i <= arr.length; i++) {
            total += arr[i];
            count++;
        }
        System.out.println(label + ":" + count);
        return total;
    }
}"""),

    ("T3_MediumIf", 14, "Wrong boundary check condition (< vs <=)",
     """public class Test3 {
    public double calcRatio(double amount, double balance) {
        double fee = 5.0;
        double tax = 0.1;
        double riskFactor = 1.05;
        double adjustedAmount = amount * riskFactor;
        double totalCost = adjustedAmount + fee;
        String status = "PENDING";
        int retries = 0;
        boolean isActive = true;

        System.out.println("Processing with balance: " + balance);
        System.out.println("Amount: " + adjustedAmount);

        // BUG: should be balance <= 0 to prevent zero-division
        if (balance < 0) {
            System.out.println("Invalid balance.");
            return -1;
        } else {
            double ratio = totalCost / balance;
            System.out.println("Ratio: " + ratio);
            return ratio;
        }
    }
}"""),

    ("T4_LargeNested", 20, "Incorrect null check inverted",
     """public class Test4 {
    public String processUser(String userId, String email, int age) {
        String result = "INIT";
        String region = "US";
        String tier = "standard";
        boolean isVerified = false;
        boolean isActive = true;
        int loginCount = 42;
        double balance = 250.00;
        double creditScore = 720.5;
        double riskScore = 0.3;
        int failedAttempts = 0;

        System.out.println("Processing user: " + userId);
        System.out.println("Email: " + email);
        System.out.println("Age: " + age);

        // BUG: should be userId != null
        if (userId == null) {
            isVerified = true;
            System.out.println("User verified.");
        } else {
            result = "INVALID_ID";
            return result;
        }

        if (age >= 18 && creditScore > 700) {
            tier = "premium";
            balance += 100;
            System.out.println("Upgraded to premium tier.");
        } else if (age < 18) {
            result = "AGE_RESTRICTED";
            return result;
        }

        if (isVerified && isActive) {
            result = "SUCCESS";
        }
        System.out.println("Final Result: " + result);
        return result;
    }
}"""),

    ("T5_TryCatch", 10, "Silent exception swallow",
     """public class Test5 {
    public int parseAndDouble(String input) {
        int result = 0;
        String tag = "parser";
        boolean success = false;
        double factor = 2.0;
        int fallback = -1;

        // BUG: result should be set to fallback on failure
        try {
            result = Integer.parseInt(input);
            result *= factor;
            success = true;
        } catch (NumberFormatException e) {
            System.out.println(tag + ": parse failed for input=" + input);
        }
        System.out.println("Result: " + result + " success=" + success);
        return result;
    }
}"""),

    ("T6_WhileLoop", 8, "Wrong while condition off-by-one",
     """public class Test6 {
    public int countDown(int start) {
        int count = 0;
        int step = 1;
        int total = start;
        double factor = 1.0;
        String log = "step";

        // BUG: should be total > 0, not total >= 0
        while (total >= 0) {
            count++;
            total -= step;
            System.out.println(log + " count=" + count + " remaining=" + total);
        }
        return count;
    }
}"""),

    ("T7_ForInit", 5, "Loop starts at 1 instead of 0",
     """public class Test7 {
    public int findMin(int[] arr) {
        int min = arr[0];
        String label = "min";
        // BUG: should start at 0
        for (int i = 1; i < arr.length; i++) {
            if (arr[i] < min) min = arr[i];
        }
        System.out.println(label + "=" + min);
        return min;
    }
}"""),

    ("T8_StringEquals", 6, "== used instead of .equals()",
     """public class Test8 {
    public boolean checkCode(String code) {
        String expected = "SECRET_007";
        boolean valid = false;
        int attempts = 1;
        // BUG: should use .equals() not ==
        if (code == expected) {
            valid = true;
        }
        System.out.println("Valid: " + valid + " (attempts: " + attempts + ")");
        return valid;
    }
}"""),
]


def call_llm(system_prompt, user_content, max_tokens=256, temperature=0.2):
    """Make an LLM call and return (response_text, latency_ms, input_tokens_est)."""
    input_token_est = len(user_content.split())
    start = time.time()
    completion = client.chat.completions.create(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ],
        model=MODEL_NAME,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    latency_ms = round((time.time() - start) * 1000, 1)
    response = completion.choices[0].message.content.strip()
    # Strip markdown if present
    if response.startswith("```java"): response = response[7:]
    if response.startswith("```"): response = response[3:]
    if response.endswith("```"): response = response[:-3]
    return response.strip(), latency_ms, input_token_est


REPAIR_SYSTEM = (
    "You are an expert Automated Program Repair AI. Fix the logical bug in the provided Java code. "
    "Output ONLY the fixed code — no explanation, no markdown blocks."
)


def run_comparison():
    results = []
    tmp = "temp_cmp_test.java"

    print("\n" + "=" * 65)
    print("  BASELINE vs. PROPOSED METHOD — Full Comparison Run")
    print("=" * 65 + "\n")

    for idx, (name, buggy_line, description, java_code) in enumerate(TESTS, 1):
        print(f"[{idx}/{len(TESTS)}] {name} — {description}")

        # Write temp Java file
        with open(tmp, "w") as f:
            f.write(java_code)

        # ── BASELINE: send the ENTIRE file to the LLM ─────────────────────
        baseline_prompt = f"Buggy Java code (line {buggy_line} is suspected):\n\n{java_code}"
        baseline_fix, baseline_ms, baseline_tokens = call_llm(
            REPAIR_SYSTEM, baseline_prompt, max_tokens=512)

        # ── PROPOSED: extract AST slice first, then send slice ────────────
        extraction = process_bug(tmp, buggy_line)
        if extraction:
            proposed_prompt = (
                f"Target Node: {extraction['anchor_type']}\n\n"
                f"Buggy Code Block:\n{extraction['extracted_code']}\n\n"
                f"Task: Fix the logic bug. Output ONLY code."
            )
            proposed_fix, proposed_ms, proposed_tokens = call_llm(
                REPAIR_SYSTEM, proposed_prompt)
            ast_tokens = extraction['ast_tokens']
        else:
            proposed_fix, proposed_ms, proposed_tokens, ast_tokens = "EXTRACTION_FAILED", 0, 0, 0

        # ── Cost estimates ─────────────────────────────────────────────────
        baseline_cost  = round(baseline_tokens  / 1000 * COST_PER_1K_TOKENS, 7)
        proposed_cost  = round(proposed_tokens  / 1000 * COST_PER_1K_TOKENS, 7)
        cost_saved_pct = round((1 - proposed_cost / baseline_cost) * 100, 1) if baseline_cost else 0.0

        print(f"  Baseline  → {baseline_tokens:4d} tokens | {baseline_ms:6.0f}ms | ${baseline_cost:.7f}")
        print(f"  Proposed  → {proposed_tokens:4d} tokens | {proposed_ms:6.0f}ms | ${proposed_cost:.7f}")
        print(f"  Savings   → {cost_saved_pct}% cheaper, {round(baseline_ms/proposed_ms,1) if proposed_ms else 'N/A'}x faster\n")

        results.append({
            "TestID": f"T{idx}",
            "Name": name,
            "BugType": description,
            # Baseline
            "Baseline_InputTokens": baseline_tokens,
            "Baseline_LatencyMs": baseline_ms,
            "Baseline_CostUSD": baseline_cost,
            "Baseline_Fix": baseline_fix[:120].replace("\n", " "),
            # Proposed
            "Proposed_ASTTokens": ast_tokens,
            "Proposed_InputTokens": proposed_tokens,
            "Proposed_LatencyMs": proposed_ms,
            "Proposed_CostUSD": proposed_cost,
            "Proposed_Fix": proposed_fix[:120].replace("\n", " "),
            # Savings
            "CostSavedPercent": cost_saved_pct,
            "SpeedupFactor": round(baseline_ms / proposed_ms, 1) if proposed_ms else 0,
        })

    # ── Write CSV ──────────────────────────────────────────────────────────
    output_csv = "comparison_results.csv"
    with open(output_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=results[0].keys())
        writer.writeheader()
        writer.writerows(results)

    # ── Summary ───────────────────────────────────────────────────────────
    avg_baseline_tok = sum(r["Baseline_InputTokens"] for r in results) / len(results)
    avg_proposed_tok = sum(r["Proposed_InputTokens"]  for r in results) / len(results)
    avg_baseline_ms  = sum(r["Baseline_LatencyMs"]    for r in results) / len(results)
    avg_proposed_ms  = sum(r["Proposed_LatencyMs"]    for r in results) / len(results)
    avg_cost_saved   = sum(r["CostSavedPercent"]      for r in results) / len(results)
    avg_speedup      = sum(r["SpeedupFactor"]          for r in results) / len(results)

    print("=" * 65)
    print("  PAPER SUMMARY")
    print("=" * 65)
    print(f"  Avg Baseline Tokens  : {avg_baseline_tok:.1f}")
    print(f"  Avg Proposed Tokens  : {avg_proposed_tok:.1f}")
    print(f"  Avg Token Reduction  : {round((1 - avg_proposed_tok/avg_baseline_tok)*100, 1)}%")
    print(f"  Avg Baseline Latency : {avg_baseline_ms:.0f}ms")
    print(f"  Avg Proposed Latency : {avg_proposed_ms:.0f}ms")
    print(f"  Avg Speedup          : {avg_speedup:.1f}x faster")
    print(f"  Avg Cost Reduction   : {avg_cost_saved:.1f}%")
    print(f"\n  Results saved to: {output_csv}")
    print("=" * 65)

    # Cleanup
    if os.path.exists(tmp):
        os.remove(tmp)


if __name__ == "__main__":
    run_comparison()
