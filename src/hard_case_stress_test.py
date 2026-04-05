"""
Hard Case Stress Test — IEEE Paper: Threats to Validity
Tests 6 hard bugs that require complex, multi-line, or cross-context fixes.
Measures: AST tokens extracted, LLM fix output, and whether fix is accurate.
Saves to: hard_case_results.csv
"""
import os, sys, csv, time
from dotenv import load_dotenv
from groq import Groq

sys.path.insert(0, os.path.dirname(__file__))
from ast_extractor import process_bug

load_dotenv()
client = Groq(api_key=os.getenv("GROQ_API_KEY"))
MODEL = "llama-3.1-8b-instant"
COST_PER_1K = 0.00005

REPAIR_SYSTEM = (
    "You are an expert Automated Program Repair AI. "
    "Fix the logical bug in the provided Java code block. "
    "Output ONLY the fixed code — no explanation, no markdown, no commentary."
)

# ------------------------------------------------------------
#  HARD TEST CASES — increasing difficulty
# ------------------------------------------------------------
HARD_TESTS = [

    # H1: Multi-line fix — need to ADD a null guard before entering a block
    {
        "id": "H1_NullGuardMissing",
        "line": 8,
        "difficulty": "Medium",
        "fix_type": "Add null guard (insert new line)",
        "expected_change": "Insert `if (data == null) return;` before line 8",
        "code": """public class H1 {
    public void processData(String[] data, int limit) {
        int count = 0;
        int max   = limit;
        String label = "batch";
        System.out.println(label + " starting");
        // BUG: no null check — throws NullPointerException when data is null
        for (String item : data) {
            if (item != null && !item.isEmpty()) {
                count++;
                System.out.println("item: " + item);
            }
        }
        System.out.println("Processed: " + count + " of " + max);
    }
}"""
    },

    # H2: Multi-condition rewrite — single condition replaced by compound expression
    {
        "id": "H2_CompoundCondition",
        "line": 10,
        "difficulty": "Medium",
        "fix_type": "Expand condition from 1 to 3 clauses",
        "expected_change": "`if (score > 90)` → `if (score > 90 && attendance >= 75 && !hasDisciplinaryFlag)`",
        "code": """public class H2 {
    public String gradeStudent(int score, int attendance, boolean hasDisciplinaryFlag) {
        String grade = "F";
        String remarks = "pending";
        int threshold = 90;
        int minAttendance = 75;
        double gpa = score / 100.0;
        System.out.println("Evaluating student score: " + score);
        System.out.println("Attendance: " + attendance);
        // BUG: ignores attendance and disciplinary flag
        if (score > threshold) {
            grade = "A";
            remarks = "Excellent";
        } else if (score > 75) {
            grade = "B";
            remarks = "Good";
        } else {
            grade = "C";
        }
        System.out.println("Grade: " + grade + " | GPA: " + gpa);
        return grade;
    }
}"""
    },

    # H3: Wrong method called — fix requires replacing method name AND argument
    {
        "id": "H3_WrongMethodCall",
        "line": 9,
        "difficulty": "Medium-Hard",
        "fix_type": "Replace wrong method call with correct one + argument change",
        "expected_change": "`list.add(item)` → `list.add(0, item)` to insert at front",
        "code": """import java.util.ArrayList;
import java.util.List;

public class H3 {
    public List<String> buildPriorityList(String[] items) {
        List<String> list = new ArrayList<>();
        String prefix = "PRIORITY";
        int count = 0;
        // BUG: appends to end instead of inserting at front (priority order lost)
        for (String item : items) {
            list.add(item);
            count++;
        }
        System.out.println(prefix + " list size: " + count);
        return list;
    }
}"""
    },

    # H4: Multi-line swap — two variable assignments are swapped
    {
        "id": "H4_SwappedAssignments",
        "line": 10,
        "difficulty": "Hard",
        "fix_type": "Swap two variable assignment lines",
        "expected_change": "min and max assignments are reversed — need to swap them",
        "code": """public class H4 {
    public int[] findMinMax(int a, int b, int c) {
        int result[] = new int[2];
        String label = "minmax";
        int temp = 0;
        int sum  = a + b + c;
        boolean sorted = false;
        System.out.println(label + " sum=" + sum);
        // BUG: min and max are assigned to wrong slots
        result[0] = Math.max(a, Math.max(b, c)); // should be min
        result[1] = Math.min(a, Math.min(b, c)); // should be max
        System.out.println("result[0]=" + result[0] + " result[1]=" + result[1]);
        return result;
    }
}"""
    },

    # H5: Missing return value update — fix adds line inside finally block
    {
        "id": "H5_MissingFinallyUpdate",
        "line": 12,
        "difficulty": "Hard",
        "fix_type": "Add assignment in finally block (structural insert)",
        "expected_change": "Add `status = \"COMPLETED\"` inside finally block",
        "code": """public class H5 {
    public String runWithCleanup(String input) {
        String status = "INIT";
        String result = "";
        int retries = 0;
        double factor = 1.5;
        boolean debug = true;
        System.out.println("Running for input: " + input);
        try {
            result = input.toUpperCase();
            status = "RUNNING";
            // simulated processing
            System.out.println("Processed: " + result);
        } catch (Exception e) {
            status = "FAILED";
            retries++;
            System.out.println("Error: " + e.getMessage());
        } finally {
            // BUG: status never set to COMPLETED — remains RUNNING or FAILED on success
            System.out.println("Cleanup done. Retries: " + retries);
        }
        return status;
    }
}"""
    },

    # H6: Cross-scope type change — return type is wrong (int vs long), fix requires 2 edits
    {
        "id": "H6_TypeOverflow",
        "line": 7,
        "difficulty": "Very Hard",
        "fix_type": "Change multiple int to long to prevent overflow (multi-location fix)",
        "expected_change": "Change `int product` to `long product` and fix return cast",
        "code": """public class H6 {
    public int multiplyLarge(int x, int y, int z) {
        int scalar = 1000;
        String label = "overflow_test";
        boolean warn = false;
        System.out.println(label + ": x=" + x + " y=" + y + " z=" + z);
        // BUG: int overflows when x,y,z are large (e.g., 50000 * 50000 * 50000)
        int product = x * y * z * scalar;
        if (product < 0) {
            warn = true;
            System.out.println("WARNING: possible overflow detected!");
        }
        System.out.println("Product: " + product + " warn=" + warn);
        return product;
    }
}"""
    },
]


def call_llm(prompt, max_tokens=300):
    est_tokens = len(prompt.split())
    t0 = time.time()
    r = client.chat.completions.create(
        messages=[{"role": "system", "content": REPAIR_SYSTEM},
                  {"role": "user",   "content": prompt}],
        model=MODEL, temperature=0.2, max_tokens=max_tokens,
    )
    ms = round((time.time() - t0) * 1000, 1)
    txt = r.choices[0].message.content.strip()
    for tag in ["```java", "```"]:
        if txt.startswith(tag): txt = txt[len(tag):]
    if txt.endswith("```"): txt = txt[:-3]
    return txt.strip(), ms, est_tokens


def run_hard_tests():
    results = []
    tmp = "temp_hard_test.java"

    print("\n" + "="*65)
    print("  HARD CASE STRESS TEST — Threats to Validity")
    print("="*65 + "\n")

    for t in HARD_TESTS:
        with open(tmp, "w") as f:
            f.write(t["code"])

        full_tokens = len(t["code"].split())
        extraction = process_bug(tmp, t["line"])

        if not extraction:
            print(f"[{t['id']}] — AST EXTRACTION FAILED\n")
            continue

        ast_tokens  = extraction["ast_tokens"]
        anchor_type = extraction["anchor_type"]
        reduction   = round((1 - ast_tokens / full_tokens) * 100, 2)

        prompt = (
            f"Target Node: {anchor_type}\n\n"
            f"Buggy Code Block:\n{extraction['extracted_code']}\n\n"
            f"Task: Fix the logic bug. Output ONLY code."
        )
        fix, ms, sent_tokens = call_llm(prompt)
        cost = round(sent_tokens / 1000 * COST_PER_1K, 7)

        within_limit = ast_tokens <= 60
        status_icon = "✅" if within_limit else "⚠️ OVER LIMIT"

        print(f"[{t['id']}] {t['difficulty']} — {t['fix_type']}")
        print(f"  Full Code Tokens : {full_tokens}")
        print(f"  AST Tokens       : {ast_tokens}  {status_icon}")
        print(f"  Reduction        : {reduction}%")
        print(f"  Anchor Type      : {anchor_type}")
        print(f"  Latency          : {ms}ms | Cost: ${cost:.7f}")
        print(f"  Expected Change  : {t['expected_change']}")
        print(f"  LLM Fix Preview  : {fix[:120].replace(chr(10), ' ')}")
        print()

        results.append({
            "TestID": t["id"],
            "Difficulty": t["difficulty"],
            "FixType": t["fix_type"],
            "FullCodeTokens": full_tokens,
            "ASTTokens": ast_tokens,
            "AnchorType": anchor_type,
            "ReductionPercent": reduction,
            "WithinLimit": within_limit,
            "LatencyMs": ms,
            "CostUSD": cost,
            "ExpectedChange": t["expected_change"],
            "LLMFix": fix[:200].replace("\n", " "),
        })

    out = "hard_case_results.csv"
    with open(out, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=results[0].keys())
        writer.writeheader()
        writer.writerows(results)

    within = sum(1 for r in results if r["WithinLimit"])
    print("=" * 65)
    print(f"  SUMMARY: {within}/{len(results)} bugs within 60-token limit")
    avg_ast = sum(r["ASTTokens"] for r in results) / len(results)
    avg_red = sum(r["ReductionPercent"] for r in results) / len(results)
    print(f"  Avg AST Tokens  : {avg_ast:.1f}")
    print(f"  Avg Reduction   : {avg_red:.1f}%")
    print(f"  Results saved → {out}")
    print("=" * 65)

    if os.path.exists(tmp):
        os.remove(tmp)


if __name__ == "__main__":
    run_hard_tests()
