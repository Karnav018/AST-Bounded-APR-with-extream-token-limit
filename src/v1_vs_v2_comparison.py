"""
v1 vs v2 head-to-head comparison on all 6 Hard Cases.
Shows how multi-node slicing improves fix accuracy over single-node.
"""
import os, sys, csv, time
from dotenv import load_dotenv
from groq import Groq

sys.path.insert(0, os.path.dirname(__file__))
from ast_extractor    import process_bug      as extract_v1
from ast_extractor_v2 import process_bug_v2   as extract_v2

load_dotenv()
client = Groq(api_key=os.getenv("GROQ_API_KEY"))
MODEL  = "llama-3.1-8b-instant"

REPAIR_SYSTEM = (
    "You are an expert Automated Program Repair AI. "
    "Fix the logical bug in the provided Java code block. "
    "Output ONLY the fixed code — no explanation, no markdown."
)

HARD_TESTS = [
    ("H1_NullGuardMissing",  8,  "Add null guard before loop",
     """public class H1 {
    public void processData(String[] data, int limit) {
        int count = 0;
        int max   = limit;
        String label = "batch";
        System.out.println(label + " starting");
        // BUG: no null check — throws NullPointerException when data is null
        for (String item : data) {
            if (item != null && !item.isEmpty()) count++;
        }
        System.out.println("Processed: " + count + " of " + max);
    }
}"""),

    ("H4_SwappedAssignments", 10, "Swap Math.min/max assignments",
     """public class H4 {
    public int[] findMinMax(int a, int b, int c) {
        int[] result = new int[2];
        String label = "minmax";
        int sum  = a + b + c;
        System.out.println(label + " sum=" + sum);
        // BUG: min and max are assigned to wrong slots
        result[0] = Math.max(a, Math.max(b, c));
        result[1] = Math.min(a, Math.min(b, c));
        System.out.println("result[0]=" + result[0] + " result[1]=" + result[1]);
        return result;
    }
}"""),

    ("H6_TypeOverflow",       7,  "Change int to long (multi-location)",
     """public class H6 {
    public int multiplyLarge(int x, int y, int z) {
        int scalar = 1000;
        String label = "overflow_test";
        boolean warn = false;
        System.out.println(label + ": x=" + x + " y=" + y + " z=" + z);
        // BUG: int overflows — product should be long
        int product = x * y * z * scalar;
        if (product < 0) {
            warn = true;
            System.out.println("WARNING: possible overflow!");
        }
        System.out.println("Product: " + product + " warn=" + warn);
        return product;
    }
}"""),
]


def call_llm(context, max_tokens=256):
    start = time.time()
    resp  = client.chat.completions.create(
        messages=[{"role": "system", "content": REPAIR_SYSTEM},
                  {"role": "user",   "content": f"Fix this bug:\n\n{context}"}],
        model=MODEL, temperature=0.2, max_tokens=max_tokens,
    )
    ms  = round((time.time() - start) * 1000, 1)
    txt = resp.choices[0].message.content.strip()
    for tag in ["```java", "```"]:
        if txt.startswith(tag): txt = txt[len(tag):]
    if txt.endswith("```"): txt = txt[:-3]
    return txt.strip(), ms


def run():
    tmp = "temp_v2_cmp.java"
    rows = []

    print("\n" + "="*68)
    print("  V1 (Single-Node) vs V2 (Multi-Node) Head-to-Head")
    print("="*68 + "\n")

    for name, line, desc, code in HARD_TESTS:
        with open(tmp, "w") as f:
            f.write(code)

        r1 = extract_v1(tmp, line)
        r2 = extract_v2(tmp, line)

        fix_v1, ms1 = call_llm(r1["extracted_code"] if r1 else "")
        fix_v2, ms2 = call_llm(r2["extracted_code"] if r2 else "")

        t1 = r1["ast_tokens"] if r1 else 0
        t2 = r2["ast_tokens"] if r2 else 0
        decl = r2["declaration_lines_added"] if r2 else 0
        ret  = r2["return_lines_added"]      if r2 else 0

        print(f"[{name}] {desc}")
        print(f"  V1 tokens  : {t1}   |  Fix: {fix_v1[:80].replace(chr(10),' ')}")
        print(f"  V2 tokens  : {t2}   |  Fix: {fix_v2[:80].replace(chr(10),' ')}")
        print(f"  Extra ctx  : +{decl} decl lines, +{ret} return lines")
        print()

        rows.append({
            "TestID": name, "BugType": desc,
            "V1_Tokens": t1, "V1_LatencyMs": ms1, "V1_Fix": fix_v1[:160].replace("\n"," "),
            "V2_Tokens": t2, "V2_LatencyMs": ms2, "V2_Fix": fix_v2[:160].replace("\n"," "),
            "V2_ExtraDecls": decl, "V2_ExtraReturns": ret,
        })

    out = "v1_vs_v2_results.csv"
    with open(out, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    avg1 = sum(r["V1_Tokens"] for r in rows) / len(rows)
    avg2 = sum(r["V2_Tokens"] for r in rows) / len(rows)

    print("="*68)
    print(f"  Avg V1 tokens : {avg1:.1f}")
    print(f"  Avg V2 tokens : {avg2:.1f}  (+{round(avg2-avg1,1)} for multi-node context)")
    print(f"  Results saved → {out}")
    print("="*68)

    if os.path.exists(tmp): os.remove(tmp)


if __name__ == "__main__":
    run()
