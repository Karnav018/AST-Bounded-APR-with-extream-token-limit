"""
ablation_study.py — Token Budget Sensitivity Ablation
Tests the same bugs at 4 token ceilings: 10, 30, 50, 80 tokens.
For each budget, measures:
  - Extraction success rate (how many bugs fit within budget)
  - Avg AST tokens actually used
  - LLM fix accuracy (keyword-match heuristic)
  - Avg latency and cost

Produces: ablation_results.csv + ablation_graph_data.csv (for matplotlib)
"""
import os, sys, csv, time, re, shutil, tempfile
from dotenv import load_dotenv
from groq import Groq

sys.path.insert(0, os.path.dirname(__file__))

load_dotenv()
client = Groq(api_key=os.getenv("GROQ_API_KEY"))
MODEL  = "llama-3.1-8b-instant"
COST_PER_1K = 0.00005

# ── Representative bug set (spans easy → hard) ─────────────────────────
# Format: (name, buggy_line, expected_fix_kw, java_code)
BUGS = [
    ("B1_OffByOne", 5, "i < arr",
     """public class B1 {
    public int sumArray(int[] arr) {
        int total = 0;
        // BUG: should be i < arr.length
        for (int i = 0; i <= arr.length; i++) total += arr[i];
        return total;
    }
}"""),
    ("B2_BoundaryCheck", 8, "<= 0",
     """public class B2 {
    public double safeDivide(double val, double divisor) {
        double scale = 1.5;
        String label = "div";
        System.out.println(label);
        // BUG: should be divisor <= 0
        if (divisor < 0) return -1;
        return val / divisor;
    }
}"""),
    ("B3_NullCheck", 7, "!= null",
     """public class B3 {
    public int countWords(String text) {
        int count = 0;
        String sep = " ";
        int max = 100;
        System.out.println("counting");
        // BUG: should be text != null
        if (text == null) return count;
        return text.split(sep).length;
    }
}"""),
    ("B4_SwappedReturn", 9, "Math.min",
     """public class B4 {
    public int[] minMax(int a, int b, int c) {
        int[] res = new int[2];
        int sum = a + b + c;
        System.out.println("sum=" + sum);
        boolean ok = true;
        String tag = "mm";
        System.out.println(tag);
        // BUG: min and max swapped
        res[0] = Math.max(a, Math.max(b, c));
        res[1] = Math.min(a, Math.min(b, c));
        return res;
    }
}"""),
    ("B5_TypeOverflow", 6, "long",
     """public class B5 {
    public long bigProduct(int x, int y, int z) {
        int scale = 10000;
        boolean warn = false;
        System.out.println("computing");
        // BUG: int overflows for large x, y, z
        int product = x * y * z * scale;
        if (product < 0) warn = true;
        System.out.println("p=" + product + " w=" + warn);
        return product;
    }
}"""),
    ("B6_StringEquals", 5, "equals",
     """public class B6 {
    public boolean verify(String code) {
        String expected = "AUTH_007";
        boolean valid = false;
        // BUG: should use .equals() not ==
        if (code == expected) valid = true;
        System.out.println("valid=" + valid);
        return valid;
    }
}"""),
    ("B7_WhileExtraIter", 7, "> 0",
     """public class B7 {
    public int countdown(int n) {
        int count = 0;
        int step = 1;
        double factor = 1.0;
        String log = "tick";
        // BUG: >= 0 runs one extra iteration
        while (n >= 0) {
            count++;
            n -= step;
            System.out.println(log + count);
        }
        return count;
    }
}"""),
    ("B8_MissingElse", 8, "else if",
     """public class B8 {
    public String grade(int score) {
        String g = "F";
        int threshold = 90;
        int b = 75;
        System.out.println("grading " + score);
        System.out.println("threshold=" + threshold);
        // BUG: second condition should be else if, not if (overwrites grade A)
        if (score >= threshold) g = "A";
        if (score >= b && score < threshold) g = "B";
        System.out.println("grade=" + g);
        return g;
    }
}"""),
]

REPAIR_SYSTEM = (
    "You are an expert APR AI. Fix the logical bug in the provided Java code block. "
    "Output ONLY the fixed code — no explanations, no markdown."
)

# ── Custom extractor with configurable token ceiling ──────────────────────
import javalang

def count_tokens_text(text):
    return len(text.split())

def extract_with_budget(java_code, buggy_line, ceiling):
    """
    Extract the AST context for the given buggy line,
    enforcing a strict token ceiling (hard ceiling + fallback).
    Returns (extracted_text, actual_tokens, status).
    """
    source_lines = java_code.splitlines(True)
    try:
        tree = javalang.parse.parse(java_code)
    except Exception:
        return "", 0, "PARSE_FAIL"

    # Collect candidate structural nodes
    candidates = []
    for path, node in tree:
        if hasattr(node, 'position') and node.position:
            if node.position.line <= buggy_line:
                nt = type(node).__name__
                if nt in ['IfStatement','ForStatement','WhileStatement',
                           'TryStatement','StatementExpression']:
                    candidates.append((node.position.line, nt, node))

    candidates.sort(key=lambda x: abs(buggy_line - x[0]))

    def get_raw(node):
        start = node.position.line - 1
        def max_line(n, cur):
            if hasattr(n, 'position') and n.position: cur = max(cur, n.position.line)
            if hasattr(n, 'children'):
                for c in n.children:
                    if isinstance(c, list):
                        for item in c:
                            if hasattr(item, '__dict__'): cur = max_line(item, cur)
                    elif hasattr(c, '__dict__'): cur = max_line(c, cur)
            return cur
        end = min(max_line(node, start+1)+1, len(source_lines))
        return "".join(source_lines[start:end])

    # Try to find a block that fits within the ceiling
    anchor_raw = None
    for _, nt, node in candidates:
        raw = get_raw(node)
        tok = count_tokens_text(raw)
        if tok <= ceiling:
            anchor_raw = raw
            break

    # If no qualifying block, use a shrinking line window
    if not anchor_raw:
        # determine window size based on ceiling
        half = max(1, ceiling // 8)
        start = max(0, buggy_line - half - 1)
        end   = min(len(source_lines), buggy_line + half)
        anchor_raw = "".join(source_lines[start:end])

    tok = count_tokens_text(anchor_raw)
    if tok > ceiling:
        # Final hard truncation: take first `ceiling` words
        words = anchor_raw.split()
        anchor_raw = " ".join(words[:ceiling])
        tok = ceiling
        status = "TRUNCATED"
    else:
        status = "OK"

    return anchor_raw.strip(), tok, status


def call_llm(context, max_tokens=200):
    est = len(context.split())
    t0  = time.time()
    r   = client.chat.completions.create(
        messages=[{"role": "system", "content": REPAIR_SYSTEM},
                  {"role": "user",   "content": f"Fix this bug:\n\n{context}"}],
        model=MODEL, temperature=0.1, max_tokens=max_tokens,
    )
    ms  = round((time.time() - t0) * 1000, 1)
    txt = r.choices[0].message.content.strip()
    for tag in ["```java", "```"]:
        if txt.startswith(tag): txt = txt[len(tag):]
    if txt.endswith("```"): txt = txt[:-3]
    return txt.strip(), ms, est


def run_ablation():
    BUDGETS = [10, 30, 50, 80]
    tmp = "temp_ablation.java"
    all_rows  = []   # per bug × per budget
    graph_rows = []  # aggregated per budget (for the graph)

    print("\n" + "="*70)
    print("  ABLATION STUDY — Token Budget Sensitivity (10 / 30 / 50 / 80)")
    print("="*70)

    for budget in BUDGETS:
        print(f"\n── Budget: {budget} tokens ─────────────────────────────────")
        correct = 0
        within  = 0
        total_tok = 0
        total_ms  = 0
        total_cost = 0

        for name, buggy_line, expected_kw, code in BUGS:
            with open(tmp, "w") as f: f.write(code)

            ctx, tok, status = extract_with_budget(code, buggy_line, budget)
            total_tok += tok
            if tok <= budget: within += 1

            fix, ms, sent = call_llm(ctx, max_tokens=max(100, budget*2))
            cost  = round(sent / 1000 * COST_PER_1K, 8)
            total_ms   += ms
            total_cost  += cost

            accurate = expected_kw.lower() in fix.lower()
            if accurate: correct += 1

            icon = "✅" if accurate else "❌"
            print(f"  {icon} {name:25s} | {tok:3d}t  {status:9s} | {ms:.0f}ms | fix: {fix[:50].replace(chr(10),' ')}")

            all_rows.append({
                "Budget": budget, "Bug": name,
                "ExtractedTokens": tok, "Status": status,
                "LLMFix": fix[:100].replace("\n", " "),
                "Accurate": accurate, "LatencyMs": ms, "CostUSD": cost,
            })

        n = len(BUGS)
        acc_pct  = round(correct / n * 100)
        fit_pct  = round(within  / n * 100)
        avg_tok  = round(total_tok  / n, 1)
        avg_ms   = round(total_ms   / n, 1)
        avg_cost = round(total_cost / n, 8)

        print(f"  ── Fix Accuracy: {correct}/{n} ({acc_pct}%)  |  "
              f"Within Budget: {within}/{n} ({fit_pct}%)  |  "
              f"Avg Tokens: {avg_tok}  |  Avg Latency: {avg_ms}ms")

        graph_rows.append({
            "TokenBudget": budget,
            "FixAccuracyPct": acc_pct,
            "WithinBudgetPct": fit_pct,
            "AvgExtractedTokens": avg_tok,
            "AvgLatencyMs": avg_ms,
            "AvgCostUSD": avg_cost,
        })

    # Save CSVs
    with open("ablation_results.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=all_rows[0].keys())
        writer.writeheader(); writer.writerows(all_rows)

    with open("ablation_graph_data.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=graph_rows[0].keys())
        writer.writeheader(); writer.writerows(graph_rows)

    # Print graph table
    print("\n" + "="*70)
    print("  GRAPH DATA (Token Budget vs Fix Accuracy)")
    print("="*70)
    print(f"  {'Budget':>8}  {'Fix Acc%':>9}  {'In Budget%':>11}  {'Avg Tokens':>11}  {'Avg ms':>8}")
    print("  " + "-"*58)
    for r in graph_rows:
        print(f"  {r['TokenBudget']:>8}  {r['FixAccuracyPct']:>8}%  "
              f"{r['WithinBudgetPct']:>10}%  {r['AvgExtractedTokens']:>11}  "
              f"{r['AvgLatencyMs']:>8}ms")
    print("="*70)
    print("  Saved: ablation_results.csv  |  ablation_graph_data.csv")
    print("="*70)

    if os.path.exists(tmp): os.remove(tmp)


if __name__ == "__main__":
    run_ablation()
