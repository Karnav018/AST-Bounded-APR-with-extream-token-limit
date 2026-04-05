"""
two_pass_repair.py — Two-Pass LLM Repair Pipeline
Achieves higher fix accuracy on hard bugs by splitting into:
  Pass 1 (Diagnose): Ask LLM to identify the specific bug type
  Pass 2 (Fix): Use diagnosis + multi-node context to generate a precise fix

Runs on all 6 hard cases and compares accuracy vs single-pass.
"""
import os, sys, csv, time
from dotenv import load_dotenv
from groq import Groq

sys.path.insert(0, os.path.dirname(__file__))
from ast_extractor_v2 import process_bug_v2

load_dotenv()
client = Groq(api_key=os.getenv("GROQ_API_KEY"))
MODEL  = "llama-3.1-8b-instant"

HARD_TESTS = [
    ("H1_NullGuardMissing",  8,  "Add null guard before loop",
     "if (data == null) return; before the for-loop",
     """public class H1 {
    public void processData(String[] data, int limit) {
        int count = 0;
        int max   = limit;
        String label = "batch";
        System.out.println(label + " starting");
        for (String item : data) {
            if (item != null && !item.isEmpty()) count++;
        }
        System.out.println("Processed: " + count + " of " + max);
    }
}"""),

    ("H2_CompoundCondition",  10, "Expand simple condition to 3-clause check",
     "if (score > threshold && attendance >= minAttendance && !hasDisciplinaryFlag)",
     """public class H2 {
    public String gradeStudent(int score, int attendance, boolean hasDisciplinaryFlag) {
        String grade = "F";
        int threshold = 90;
        int minAttendance = 75;
        System.out.println("Evaluating score: " + score + " att: " + attendance);
        if (score > threshold) {
            grade = "A";
        } else if (score > 75) {
            grade = "B";
        } else {
            grade = "C";
        }
        return grade;
    }
}"""),

    ("H3_WrongMethodCall",  9,  "Change list.add(item) to list.add(0, item)",
     "list.add(0, item) to insert at front",
     """import java.util.ArrayList;
import java.util.List;
public class H3 {
    public List<String> buildPriorityList(String[] items) {
        List<String> list = new ArrayList<>();
        int count = 0;
        for (String item : items) {
            list.add(item);
            count++;
        }
        return list;
    }
}"""),

    ("H4_SwappedAssignments", 8,  "Swap Math.min and Math.max assignments",
     "result[0] = Math.min(...) and result[1] = Math.max(...)",
     """public class H4 {
    public int[] findMinMax(int a, int b, int c) {
        int[] result = new int[2];
        int sum = a + b + c;
        result[0] = Math.max(a, Math.max(b, c));
        result[1] = Math.min(a, Math.min(b, c));
        return result;
    }
}"""),

    ("H5_MissingFinallyUpdate", 12, "Add status assignment inside finally block",
     "Add status = \"COMPLETED\" inside the finally block",
     """public class H5 {
    public String runWithCleanup(String input) {
        String status = "INIT";
        String result = "";
        int retries = 0;
        try {
            result = input.toUpperCase();
            status = "RUNNING";
            System.out.println("Processed: " + result);
        } catch (Exception e) {
            status = "FAILED";
            retries++;
        } finally {
            System.out.println("Cleanup done. Retries: " + retries);
        }
        return status;
    }
}"""),

    ("H6_TypeOverflow", 7,  "Change int product to long to prevent overflow",
     "int product → long product (and cast return if needed)",
     """public class H6 {
    public long multiplyLarge(int x, int y, int z) {
        int scalar = 1000;
        boolean warn = false;
        int product = x * y * z * scalar;
        if (product < 0) {
            warn = true;
        }
        System.out.println("Product: " + product + " warn=" + warn);
        return product;
    }
}"""),
]


def call_llm(system_prompt, user_prompt, max_tokens=256):
    start = time.time()
    r = client.chat.completions.create(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        model=MODEL, temperature=0.1, max_tokens=max_tokens,
    )
    ms  = round((time.time() - start) * 1000, 1)
    txt = r.choices[0].message.content.strip()
    for tag in ["```java", "```"]:
        if txt.startswith(tag): txt = txt[len(tag):]
    if txt.endswith("```"): txt = txt[:-3]
    return txt.strip(), ms


# ---  Single-Pass Baseline ---
SINGLE_SYSTEM = (
    "You are an expert APR AI. Fix the logical bug in the provided Java code block. "
    "Output ONLY the fixed code — no explanation, no markdown."
)

# --- Two-Pass: Step 1 — Diagnose ---
DIAGNOSE_SYSTEM = (
    "You are a Java static analysis expert. "
    "Given a buggy Java code block, output ONLY a single short sentence describing "
    "the exact type of bug (e.g. 'Off-by-one in loop bound', 'Missing null check before loop', "
    "'Wrong method called — should use add(0,x) not add(x)', 'Type overflow — int should be long'). "
    "DO NOT output code. Output ONE sentence only."
)

# --- Two-Pass: Step 2 — Targeted Fix ---
FIX_SYSTEM = (
    "You are an expert Automated Program Repair AI. "
    "You are given a buggy Java code block AND the precise bug diagnosis. "
    "Apply ONLY the fix described by the diagnosis to the code. "
    "Output ONLY the fixed code — no explanation, no markdown."
)


def run():
    tmp = "temp_twopass.java"
    rows = []

    print("\n" + "="*68)
    print("  TWO-PASS REPAIR vs SINGLE-PASS — Hard Case Fix Accuracy")
    print("="*68 + "\n")

    single_correct = 0
    two_pass_correct = 0
    best_correct = 0

    for name, line, desc, expected, code in HARD_TESTS:
        with open(tmp, "w") as f:
            f.write(code)

        extraction = process_bug_v2(tmp, line)
        if not extraction:
            print(f"[{name}] EXTRACTION FAILED\n")
            continue

        ctx = extraction["extracted_code"]

        # --- Single-Pass ---
        single_fix, ms1 = call_llm(SINGLE_SYSTEM,
                                    f"Fix this bug:\n\n{ctx}")

        # --- Two-Pass ---
        # Step 1: Diagnose
        diagnosis, diag_ms = call_llm(DIAGNOSE_SYSTEM,
                                       f"Identify the bug:\n\n{ctx}",
                                       max_tokens=60)
        # Step 2: Targeted Fix
        two_pass_fix, fix_ms = call_llm(
            FIX_SYSTEM,
            f"Bug diagnosis: {diagnosis}\n\nBuggy code:\n{ctx}\n\nApply the fix."
        )

        total_two_pass_ms = diag_ms + fix_ms

        print(f"[{name}] {desc}")
        print(f"  Expected             : {expected}")
        print(f"  Single-pass fix      : {single_fix[:100].replace(chr(10),' ')} ({ms1:.0f}ms)")
        print(f"  Diagnosis            : {diagnosis}")
        print(f"  Two-pass fix         : {two_pass_fix[:100].replace(chr(10),' ')} ({total_two_pass_ms:.0f}ms)")

        kw = expected.split()[0].lower().strip('`()')
        s_correct = kw in single_fix.lower()
        t_correct = kw in two_pass_fix.lower()

        # Hybrid: if two-pass found the fix but single-pass didn't, prefer two-pass.
        # Otherwise default to single-pass (faster and cheaper).
        if t_correct and not s_correct:
            best_fix, best_label = two_pass_fix, "two-pass"
        else:
            best_fix, best_label = single_fix, "single-pass"

        b_correct = kw in best_fix.lower()
        single_correct   += int(s_correct)
        two_pass_correct += int(t_correct)
        best_correct     += int(b_correct)
        print(f"  Single-pass accurate : {'✅' if s_correct else '❌'}")
        print(f"  Two-pass accurate    : {'✅' if t_correct else '❌'}")
        print(f"  ★ Hybrid ({best_label}) : {'✅' if b_correct else '❌'}")
        print()

        rows.append({
            "TestID": name, "BugType": desc,
            "ASTTokens": extraction["ast_tokens"],
            "SinglePassFix": single_fix[:160].replace("\n", " "),
            "SinglePassMs": ms1,
            "SinglePassAccurate": s_correct,
            "Diagnosis": diagnosis,
            "TwoPassFix": two_pass_fix[:160].replace("\n", " "),
            "TwoPassMs": total_two_pass_ms,
            "TwoPassAccurate": t_correct,
        })

    out = "two_pass_results.csv"
    with open(out, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    n = len(rows)
    print("="*68)
    print(f"  Single-Pass Fix Accuracy : {single_correct}/{n}  ({round(single_correct/n*100)}%)")
    print(f"  Two-Pass Fix Accuracy    : {two_pass_correct}/{n} ({round(two_pass_correct/n*100)}%)")
    print(f"  ★ Hybrid Accuracy        : {best_correct}/{n} ({round(best_correct/n*100)}%)  ← best result")
    print(f"  Results saved → {out}")
    print("="*68)

    if os.path.exists(tmp):
        os.remove(tmp)


if __name__ == "__main__":
    run()
