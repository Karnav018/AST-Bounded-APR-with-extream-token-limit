"""
generate_paper_assets.py — Complete Paper Figure Generator
Produces all graphs, charts, and visualizations needed for the ACM paper.

Outputs:
  fig1_architecture.png      — Pipeline architecture diagram
  fig2_token_reduction.png   — Per-bug token reduction bar chart (61 bugs)
  fig3_ablation.png          — Ablation: token budget vs fix accuracy
  fig4_baseline_compare.png  — Baseline vs proposed comparison
  fig5_fix_accuracy.png      — Fix accuracy breakdown (easy/hard cases)
  fig6_anchor_types.png      — Distribution of AST anchor node types
"""

import csv, os
import matplotlib
matplotlib.use("Agg")  # headless rendering
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.gridspec as gridspec
import numpy as np

OUT_DIR = "paper_figures"
os.makedirs(OUT_DIR, exist_ok=True)

# ── Styling ────────────────────────────────────────────────────────────────
BLUE   = "#2563EB"
GREEN  = "#16A34A"
RED    = "#DC2626"
ORANGE = "#EA580C"
PURPLE = "#7C3AED"
GRAY   = "#6B7280"
LIGHT  = "#F3F4F6"

plt.rcParams.update({
    "font.family":  "DejaVu Sans",
    "font.size":    11,
    "axes.spines.top":   False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.alpha": 0.3,
    "figure.dpi": 150,
})


# ─────────────────────────────────────────────────────────────────────────────
# FIG 1: Pipeline Architecture (text-based diagram)
# ─────────────────────────────────────────────────────────────────────────────
def fig1_architecture():
    fig, ax = plt.subplots(figsize=(12, 4))
    ax.axis("off")

    stages = [
        ("Java File\n(6,200 tokens)", GRAY),
        ("AST Parser\n(javalang)", BLUE),
        ("Multi-Node\nSlicer", PURPLE),
        ("Token Budget\nEnforcer\n30–80 tokens", ORANGE),
        ("Two-Pass\nLLM Repair", GREEN),
        ("Fixed\nCode", GREEN),
    ]

    x_positions = np.linspace(0.05, 0.95, len(stages))
    y = 0.5

    for i, (label, color) in enumerate(stages):
        x = x_positions[i]
        box = mpatches.FancyBboxPatch(
            (x - 0.07, y - 0.25), 0.14, 0.50,
            boxstyle="round,pad=0.02",
            facecolor=color + "22", edgecolor=color, linewidth=2
        )
        ax.add_patch(box)
        ax.text(x, y, label, ha="center", va="center",
                fontsize=9, fontweight="bold", color=color)

        if i < len(stages) - 1:
            ax.annotate("", xy=(x_positions[i+1] - 0.07, y),
                        xytext=(x + 0.07, y),
                        arrowprops=dict(arrowstyle="->", color=GRAY, lw=1.5))

    # Token count annotation
    ax.annotate("99.5%\nreduction", xy=(0.42, 0.2), fontsize=9,
                color=GREEN, ha="center", style="italic")
    ax.annotate("", xy=(0.38, 0.25), xytext=(0.62, 0.25),
                arrowprops=dict(arrowstyle="<->", color=GREEN, lw=1.5))

    ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    ax.set_title("Figure 1: AST-Bounded Token-Efficient APR Pipeline",
                 fontsize=13, fontweight="bold", pad=10)

    path = f"{OUT_DIR}/fig1_architecture.png"
    plt.tight_layout()
    plt.savefig(path, bbox_inches="tight")
    plt.close()
    print(f"✅ {path}")


# ─────────────────────────────────────────────────────────────────────────────
# FIG 2: Per-Bug Token Reduction (61 Defects4J bugs)
# ─────────────────────────────────────────────────────────────────────────────
def fig2_token_reduction():
    rows = []
    with open("d4j_full_results.csv") as f:
        for r in csv.DictReader(f):
            if r["Status"] == "SUCCESS":
                rows.append(r)

    rows.sort(key=lambda r: float(r["Reduction"]))
    labels = [r["BugID"].replace("Lang-","L") for r in rows]
    reductions = [float(r["Reduction"]) for r in rows]
    orig = [int(r["OriginalTokens"]) for r in rows]
    ast  = [int(r["ASTTokens"])      for r in rows]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 8), sharex=True)

    # Top: reduction %
    colors = [GREEN if r >= 95 else ORANGE if r >= 80 else RED for r in reductions]
    bars = ax1.bar(labels, reductions, color=colors, alpha=0.85, width=0.7)
    ax1.axhline(y=98.22, color=BLUE, linestyle="--", linewidth=1.5, label="Avg 98.22%")
    ax1.axhline(y=90, color=RED, linestyle=":", linewidth=1.2, label="90% target")
    ax1.set_ylabel("Token Reduction (%)")
    ax1.set_ylim(60, 101)
    ax1.legend(fontsize=9)
    ax1.set_title("Figure 2: Token Reduction per Bug — All 61 Defects4J Lang Bugs",
                  fontsize=12, fontweight="bold")

    # Bottom: absolute tokens
    ax2.bar(labels, orig, color=GRAY, alpha=0.4, label="Full File Tokens", width=0.7)
    ax2.bar(labels, ast,  color=BLUE,  alpha=0.9, label="AST Context Tokens", width=0.7)
    ax2.set_ylabel("Token Count")
    ax2.set_xlabel("Bug ID")
    ax2.legend(fontsize=9)
    ax2.set_yscale("log")

    plt.xticks(rotation=90, fontsize=7)
    plt.tight_layout()
    path = f"{OUT_DIR}/fig2_token_reduction.png"
    plt.savefig(path, bbox_inches="tight")
    plt.close()
    print(f"✅ {path}")


# ─────────────────────────────────────────────────────────────────────────────
# FIG 3: Ablation Study — Token Budget vs Fix Accuracy
# ─────────────────────────────────────────────────────────────────────────────
def fig3_ablation():
    rows = []
    with open("ablation_graph_data.csv") as f:
        rows = list(csv.DictReader(f))

    budgets  = [int(r["TokenBudget"])      for r in rows]
    accuracy = [int(r["FixAccuracyPct"])   for r in rows]
    latency  = [float(r["AvgLatencyMs"])   for r in rows]
    avg_tok  = [float(r["AvgExtractedTokens"]) for r in rows]

    fig, ax1 = plt.subplots(figsize=(8, 5))

    # Fix accuracy line
    ax1.plot(budgets, accuracy, "o-", color=GREEN, linewidth=2.5,
             markersize=8, label="Fix Accuracy (%)", zorder=5)
    ax1.fill_between(budgets, accuracy, alpha=0.12, color=GREEN)
    ax1.set_xlabel("Token Budget (ceiling)", fontsize=12)
    ax1.set_ylabel("Fix Accuracy (%)", color=GREEN, fontsize=12)
    ax1.set_ylim(0, 100)
    ax1.set_xticks(budgets)
    ax1.tick_params(axis="y", labelcolor=GREEN)

    # Latency on secondary axis
    ax2 = ax1.twinx()
    ax2.plot(budgets, latency, "s--", color=ORANGE, linewidth=2,
             markersize=7, label="Avg Latency (ms)")
    ax2.set_ylabel("Avg Latency (ms)", color=ORANGE, fontsize=12)
    ax2.tick_params(axis="y", labelcolor=ORANGE)
    ax2.spines["right"].set_visible(True)

    # Annotate sweet spot
    ax1.axvspan(28, 52, alpha=0.08, color=GREEN, label="Sweet Spot (30–50 tokens)")
    ax1.text(40, 55, "Sweet Spot\n30–50 tokens", ha="center", fontsize=9,
             color=GREEN, fontweight="bold",
             bbox=dict(boxstyle="round,pad=0.3", facecolor="white", edgecolor=GREEN))

    # Combined legend
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="lower right", fontsize=9)

    ax1.set_title("Figure 3: Ablation Study — Token Budget vs Fix Accuracy & Latency",
                  fontsize=12, fontweight="bold")

    plt.tight_layout()
    path = f"{OUT_DIR}/fig3_ablation.png"
    plt.savefig(path, bbox_inches="tight")
    plt.close()
    print(f"✅ {path}")


# ─────────────────────────────────────────────────────────────────────────────
# FIG 4: Baseline vs Proposed — Grouped Bar Chart
# ─────────────────────────────────────────────────────────────────────────────
def fig4_baseline_compare():
    rows = []
    with open("comparison_results.csv") as f:
        rows = list(csv.DictReader(f))

    test_ids  = [r["TestID"] for r in rows]
    base_tok  = [int(r["Baseline_InputTokens"])   for r in rows]
    prop_tok  = [int(r["Proposed_InputTokens"])    for r in rows]
    base_ms   = [float(r["Baseline_LatencyMs"])    for r in rows]
    prop_ms   = [float(r["Proposed_LatencyMs"])    for r in rows]

    x = np.arange(len(test_ids))
    w = 0.35

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))

    # Tokens
    ax1.bar(x - w/2, base_tok, w, label="Baseline (Full File)", color=RED,   alpha=0.8)
    ax1.bar(x + w/2, prop_tok, w, label="Proposed (AST Slice)", color=BLUE,  alpha=0.8)
    ax1.set_xticks(x); ax1.set_xticklabels(test_ids, rotation=25, ha="right", fontsize=9)
    ax1.set_ylabel("Input Tokens")
    ax1.set_title("(a) Input Token Count: Baseline vs Proposed")
    ax1.legend(fontsize=9)

    # Avg annotation
    avg_base = sum(base_tok)/len(base_tok)
    avg_prop = sum(prop_tok)/len(prop_tok)
    ax1.axhline(avg_base, color=RED,  linestyle="--", linewidth=1.2,
                label=f"Avg Baseline: {avg_base:.0f}")
    ax1.axhline(avg_prop, color=BLUE, linestyle="--", linewidth=1.2,
                label=f"Avg Proposed: {avg_prop:.0f}")
    ax1.legend(fontsize=8)

    # Latency
    ax2.bar(x - w/2, base_ms, w, label="Baseline Latency", color=RED,  alpha=0.8)
    ax2.bar(x + w/2, prop_ms, w, label="Proposed Latency", color=BLUE, alpha=0.8)
    ax2.set_xticks(x); ax2.set_xticklabels(test_ids, rotation=25, ha="right", fontsize=9)
    ax2.set_ylabel("Latency (ms)")
    ax2.set_title("(b) LLM Latency: Baseline vs Proposed")
    ax2.legend(fontsize=9)

    fig.suptitle("Figure 4: Baseline vs Proposed Method Comparison (8 Synthetic Bugs)",
                 fontsize=12, fontweight="bold")
    plt.tight_layout()
    path = f"{OUT_DIR}/fig4_baseline_compare.png"
    plt.savefig(path, bbox_inches="tight")
    plt.close()
    print(f"✅ {path}")


# ─────────────────────────────────────────────────────────────────────────────
# FIG 5: Fix Accuracy Breakdown
# ─────────────────────────────────────────────────────────────────────────────
def fig5_fix_accuracy():
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.5))

    datasets = [
        ("Easy / Medium\n(8 Synthetic Bugs)", 6, 2, 0),
        ("Hard Cases\n(Single-Pass)", 4, 2, 0),
        ("Hard Cases\n(Hybrid Two-Pass)", 5, 1, 0),
    ]

    for ax, (title, correct, wrong, partial) in zip(axes, datasets):
        total = correct + wrong + partial
        vals   = [correct, wrong, partial]
        labels = [f"Correct\n({correct}/{total})", f"Wrong\n({wrong}/{total})", f"Partial\n({partial}/{total})"]
        colors = [GREEN, RED, ORANGE]
        # Remove 0-value slices
        vals_f   = [(v, l, c) for v, l, c in zip(vals, labels, colors) if v > 0]
        vals, labels, colors = zip(*vals_f) if vals_f else ([], [], [])

        wedges, texts, autotexts = ax.pie(
            vals, labels=labels, colors=colors,
            autopct="%1.0f%%", startangle=90,
            wedgeprops=dict(edgecolor="white", linewidth=2)
        )
        for at in autotexts:
            at.set_fontsize(10); at.set_fontweight("bold")
        ax.set_title(title, fontsize=10, fontweight="bold")

    fig.suptitle("Figure 5: Fix Accuracy Distribution — Easy, Hard Single-Pass, Hard Hybrid",
                 fontsize=12, fontweight="bold")
    plt.tight_layout()
    path = f"{OUT_DIR}/fig5_fix_accuracy.png"
    plt.savefig(path, bbox_inches="tight")
    plt.close()
    print(f"✅ {path}")


# ─────────────────────────────────────────────────────────────────────────────
# FIG 6: AST Anchor Type Distribution
# ─────────────────────────────────────────────────────────────────────────────
def fig6_anchor_types():
    from collections import Counter
    counts = Counter()
    with open("d4j_full_results.csv") as f:
        for r in csv.DictReader(f):
            if r["Status"] == "SUCCESS":
                t = r["AnchorType"].split()[0]  # strip "[trimmed]" etc.
                counts[t] += 1

    labels = list(counts.keys())
    values = list(counts.values())

    fig, ax = plt.subplots(figsize=(8, 5))
    colors_map = {
        "TryStatement":        PURPLE,
        "IfStatement":         BLUE,
        "ForStatement":        GREEN,
        "WhileStatement":      ORANGE,
        "StatementExpression": RED,
        "TextWindow":          GRAY,
        "ExpandedWindow":      GRAY,
    }
    bar_colors = [colors_map.get(l, GRAY) for l in labels]
    bars = ax.barh(labels, values, color=bar_colors, alpha=0.85, height=0.6)
    ax.bar_label(bars, padding=4, fontsize=10)
    ax.set_xlabel("Number of Bugs")
    ax.set_title("Figure 6: Distribution of AST Anchor Node Types (61 Lang Bugs)",
                 fontsize=12, fontweight="bold")
    ax.set_xlim(0, max(values) * 1.2)
    plt.tight_layout()
    path = f"{OUT_DIR}/fig6_anchor_types.png"
    plt.savefig(path, bbox_inches="tight")
    plt.close()
    print(f"✅ {path}")


# ─────────────────────────────────────────────────────────────────────────────
# Run all
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\nGenerating all paper figures...\n")
    fig1_architecture()
    fig2_token_reduction()
    fig3_ablation()
    fig4_baseline_compare()
    fig5_fix_accuracy()
    fig6_anchor_types()
    print(f"\n✅ All 6 figures saved to ./{OUT_DIR}/")
    print("   Ready to embed in your ACM paper!")
