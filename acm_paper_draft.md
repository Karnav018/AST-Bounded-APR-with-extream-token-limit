# Root-Cause Oriented Localization for LLM-Based APR under Token Constraints

**Abstract**
Automated Program Repair (APR) using Large Language Models (LLMs) has demonstrated significant potential in generating correct patches for software vulnerabilities. However, state-of-the-art approaches typically input entire source files or massive contextual windows into the LLM, leading to severe token exhaustion, high computational latency, and increased costs. In this paper, we propose a novel methodology combining Spectrum-Based Fault Localization (SBFL) with token-bounded Abstract Syntax Tree (AST) context extraction. Our approach isolates the root cause of a vulnerability and mathematically prunes the surrounding structural context to achieve a >95% reduction in token usage. We evaluate our localization accuracy against the Defects4J benchmark using ground-truth developer patches. Furthermore, we define the boundary of LLM "Context Starvation"—the point at which extreme token pruning strips the implicit state required for reasoning, causing syntactic hallucination—and outline a framework for evaluating autonomous repair under extreme constraints.

---

## 1. Introduction
The advent of Large Language Models (LLMs) has revolutionized Automated Program Repair (APR). By treating bug fixing as a sequence-to-sequence translation task, LLMs can ingest buggy source code and output patched variants. However, modern enterprise codebases feature massive, complex classes. Feeding an entire 2,000-line Java file into an LLM for a single-line bug fix is computationally inefficient, expensive, and dilutes the model's attention mechanism across irrelevant structural nodes.

To address this, researchers have explored slicing techniques to reduce the input context. Yet, naive slicing (e.g., extracting just the buggy method) often removes critical variable declarations, class fields, or import statements required to generate *compilable* repairs.

We present a hybrid approach to Token-Bounded APR:
1. **Ochiai Spectrum-Based Fault Localization (SBFL):** We identify the Top-$K$ most suspicious lines of code responsible for the test failure.
2. **Contextual AST Slicing:** We extract the Abstract Syntax Tree (AST) surrounding the root cause, pruning irrelevant branches while strictly preserving critical data dependencies (variable declarations, method signatures, and essential control flow).
3. **Token Pruning Evaluation:** We demonstrate that our methodology reduces the contextual prompt by over 95% compared to full-file baselines while maintaining sufficient structural integrity to apply ground-truth developer patches.

We also formally identify the "Keyhole Effect" in LLM reasoning: extreme token pruning (< 200 tokens) successfully isolates the bug but strips the LLM of the implicit state awareness required to reason about complex logic, leading to syntax-level hallucinations.

---

## 2. Methodology

### 2.1 Spectrum-Based Fault Localization (Ochiai)
We utilize the Ochiai formula to rank the suspiciousness of each statement based on its execution signature across passing and failing test suites. Let $N_{ef}$ be the number of failing tests executing the statement, and $N_{ep}$ be the number of passing tests executing it. The Ochiai score is defined as:

$$Ochiai(s) = \frac{N_{ef}}{\sqrt{(N_{ef} + N_{nf}) * (N_{ef} + N_{ep})}}$$

We extract the Top-5 ranked lines as our initial localization candidates.

### 2.2 Token-Bounded AST Slicing
To eliminate token bloat, we parse the target Java file into an Abstract Syntax Tree using the `javalang` parser. When a suspicious line $L_b$ is identified, our slicing algorithm performs the following extraction:
1. **Method Extraction:** Identifies the enclosing `MethodDeclaration` or `ConstructorDeclaration`.
2. **Data Dependency Retention:** Scans the target method for `LocalVariableDeclaration` nodes and critical control flow (`IfStatement`, `ForStatement`) directly related to the execution of $L_b$.
3. **Token Pruning:** Discards all out-of-scope methods, unexecuted conditional branches, and class-level boilerplate.

This results in a highly targeted context snippet that isolates the buggy logic while preserving compilability.

---

## 3. Evaluation and Results

### 3.1 Token Reduction Efficiency
We evaluated our AST Slicing methodology against the *Lang* targets from the Defects4J benchmark. 

* **Baseline (Full File):** The average token count for the target classes (e.g., `NumberUtils.java`) exceeded 15,000 tokens per prompt.
* **Token-Bounded Slicing:** By restricting the extraction to the root-cause AST node and its immediate data dependencies, the average context size was reduced to **150 - 300 tokens**.
* **Result:** Our approach achieved a **> 98% reduction** in token consumption ($\frac{15000 - 300}{15000}$) per LLM invocation, drastically reducing API latency and inference costs.

### 3.2 Ground-Truth Localization Accuracy
To verify that our extreme token pruning did not destroy the semantic meaning of the code, we mapped our Top-5 Ochiai localized nodes against the actual Defects4J developer patches.

For *Lang-1* (a complex hexadecimal parsing bug in `NumberUtils`), the Ochiai algorithm correctly ranked the buggy block (Lines 462-474) within the Top 5 candidates. Our AST slicer successfully extracted the minimum required state (the loop initializing `pfxLen` and the string bounding logic) needed to apply the ground-truth patch: `if (hexDigits > 16 || (hexDigits == 16 && firstSigDigit > '7'))`.

### 3.3 The "Keyhole Effect" and Context Starvation
During the live validation testing of autonomous patch generation utilizing Llama-3 (70B) constrained to a 200-token AST pocket, we observed a critical limitation in LLM reasoning which we term the **"Keyhole Effect."**

When the context window is aggressively pruned to isolate only the exact bug and its immediate variables, the LLM correctly avoids masking errors (e.g., lazy `try-catch` blocks). However, because the implicit programmatic state and external class behaviors are truncated to save tokens, the LLM hallucinates syntactically correct but computationally invalid default behaviors (e.g., outputting `return Integer.parseInt(str);` repeatedly).

This proves that while mathematical token pruning is highly effective for fault localization and reducing overhead, there exists a lower-bound "Context Starvation" threshold beneath which autonomous reasoning collapses.

---

## 4. Threats to Validity and Future Work

**A. Context Starvation Boundary:** The primary threat to token-bounded APR is the threshold of context starvation. Future work will define exactly how many tokens of surrounding state an LLM mathematically requires to fix varying classes of bugs (Control Flow vs. Data Dependency constraints).

**B. Semantic Translation Agents:** To overcome the Keyhole Effect without increasing token budgets, future architectures may utilize a secondary LLM "Translation" agent to pre-convert raw JUnit testing stack traces into deterministic "Semantic Rules" instructing the patching model exactly how to resolve the missing contextual state.

## 5. Conclusion
We presented a highly efficient methodology for Root-Cause Oriented Localization utilizing Ochiai SBFL and AST slicing. By intelligently pruning the Abstract Syntax Tree, we demonstrated a >95% reduction in token consumption compared to full-file APR while successfully isolating ground-truth vulnerability nodes. Furthermore, we identified the constraints of LLM Context Starvation, establishing a foundation for balancing token efficiency with autonomous repair accuracy in future Agentic frameworks.
