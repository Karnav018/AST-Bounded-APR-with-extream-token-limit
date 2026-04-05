"""
ast_extractor_v2.py — Multi-Node AST Slicing
Upgrade over v1: instead of one anchor node, extracts a CONNECTED SET of nodes:
  1. Closest enclosing structural block (the bug site)
  2. Variable declarations for variables USED in that block
  3. Return/assignment nodes that USE the same variables

This gives the LLM a semantically complete repair context without exceeding ~80 tokens.
"""
import javalang
import sys
import re

TOKEN_UPPER_LIMIT = 80   # hard ceiling
TOKEN_LOWER_LIMIT = 15   # minimum — expand if below this


def count_tokens_text(text):
    """Estimate token count from raw text by splitting on whitespace."""
    return len(text.split())


def get_used_variables(source_snippet):
    """Extract identifiers (variable names) from a code snippet using simple regex."""
    # Remove string literals and comments first
    cleaned = re.sub(r'"[^"]*"', '', source_snippet)
    cleaned = re.sub(r'//.*', '', cleaned)
    # Extract word tokens that look like identifiers
    words = re.findall(r'\b([a-zA-Z_][a-zA-Z0-9_]*)\b', cleaned)
    # Filter out Java keywords & common noise
    java_keywords = {
        'if','else','for','while','do','return','int','long','double','float',
        'boolean','String','char','byte','short','void','null','true','false',
        'new','class','public','private','protected','static','final','try',
        'catch','finally','throw','throws','import','package','this','super',
        'instanceof','switch','case','break','continue','System','out','println'
    }
    return set(w for w in words if w not in java_keywords and len(w) > 1)


def extract_declarations(source_lines, used_vars, buggy_line):
    """
    Scan lines BEFORE the buggy line for local variable declarations
    of the variables used in the buggy block.
    Prioritizes numeric primitive types (int, long, double, float) first
    because those are the most repair-relevant declarations.
    """
    numeric_types   = {'int', 'long', 'double', 'float'}
    all_types       = {'int', 'long', 'double', 'float', 'boolean', 'String', 'char'}
    numeric_decls   = []
    other_decls     = []

    for idx in range(max(0, buggy_line - 40), buggy_line - 1):
        line = source_lines[idx].strip()
        for var in used_vars:
            pattern = rf'\b({"|".join(all_types)})\s+{re.escape(var)}\b'
            m = re.search(pattern, line)
            if m:
                declared_type = m.group(1)
                raw_line = source_lines[idx].rstrip()
                if declared_type in numeric_types:
                    numeric_decls.append(raw_line)
                else:
                    other_decls.append(raw_line)
                break

    # Return numeric-type declarations first, then up to 2 others (to cap token cost)
    return numeric_decls + other_decls[:2]


def find_return_lines(source_lines, used_vars, buggy_line, end_line):
    """Find return statements after the buggy block that use the variables."""
    return_lines = []
    for idx in range(end_line, min(len(source_lines), end_line + 10)):
        line = source_lines[idx].strip()
        if line.startswith('return') or line.startswith('result['):
            for var in used_vars:
                if var in line:
                    return_lines.append(source_lines[idx].rstrip())
                    break
    return return_lines


def extract_method_signature(source_lines, buggy_line):
    """
    Scan backwards from the buggy line to find the containing method signature.
    Returns the single signature line (e.g. `public List<String> buildPriorityList(...)`).
    This is critical for intent-bearing bugs where the method name implies the fix.
    """
    method_pattern = re.compile(
        r'(public|private|protected|static)?\s*'
        r'[\w<>\[\]]+\s+\w+\s*\([^)]*\)\s*(throws\s+\w+)?\s*\{?'
    )
    for idx in range(buggy_line - 2, max(0, buggy_line - 30), -1):
        line = source_lines[idx].strip()
        if method_pattern.search(line) and '(' in line and ')' in line:
            # Skip lines that are clearly not method declarations
            if any(kw in line for kw in ['if ', 'for ', 'while ', 'catch ', 'System.']):
                continue
            return source_lines[idx].rstrip()
    return None


def find_anchor_block(source_lines, tree, buggy_line):
    """Find the best enclosing AST block within token limits."""
    candidates = []
    try_candidates = []

    for path, node in tree:
        if hasattr(node, 'position') and node.position:
            if node.position.line <= buggy_line:
                node_type = type(node).__name__
                if node_type == 'TryStatement':
                    # Always collect TryStatements separately — we prefer a full try-catch-finally
                    try_candidates.append((node.position.line, node_type, node))
                elif node_type in ['IfStatement','ForStatement','WhileStatement',
                                   'CatchClause','StatementExpression']:
                    candidates.append((node.position.line, node_type, node))

    candidates.sort(key=lambda x: abs(buggy_line - x[0]))
    try_candidates.sort(key=lambda x: abs(buggy_line - x[0]))

    # Special case: if bug is inside a try-catch-finally, ALWAYS use the full try block
    # because the LLM needs the complete flow to find a missing finally assignment.
    if try_candidates:
        node = try_candidates[0][2]
        raw  = extract_raw_lines(source_lines, node, buggy_line)
        tok  = count_tokens_text(raw)
        if tok <= TOKEN_UPPER_LIMIT:
            return node, raw

    # General case: prefer smallest structural block in [15, TOKEN_UPPER_LIMIT]
    for line_no, node_type, node in candidates:
        raw = extract_raw_lines(source_lines, node, buggy_line)
        tok = count_tokens_text(raw)
        if 15 <= tok <= TOKEN_UPPER_LIMIT:
            return node, raw

    # Fallback: 5-line text window
    start = max(0, buggy_line - 3)
    end   = min(len(source_lines), buggy_line + 2)
    raw   = "".join(source_lines[start:end])
    return None, raw


def extract_raw_lines(source_lines, node, buggy_line):
    """Extract source text for an AST node."""
    if not hasattr(node, 'position') or not node.position:
        return ""
    start = node.position.line - 1

    def find_max_line(n, cur):
        if hasattr(n, 'position') and n.position:
            cur = max(cur, n.position.line)
        if hasattr(n, 'children'):
            for child in n.children:
                if isinstance(child, list):
                    for item in child:
                        if hasattr(item, '__dict__'):
                            cur = find_max_line(item, cur)
                elif hasattr(child, '__dict__'):
                    cur = find_max_line(child, cur)
        return cur

    end = find_max_line(node, start + 1)
    end = min(end + 1, len(source_lines))
    return "".join(source_lines[start:end])


def process_bug_v2(java_file_path, buggy_line):
    """
    Multi-node AST extraction:
      1. Find the bug-site block (anchor)
      2. Extract variable declarations used in the block
      3. Extract relevant return statements after the block
      4. Assemble into one coherent, token-bounded context
    """
    with open(java_file_path, "r") as f:
        source = f.read()

    source_lines = source.splitlines(True)
    original_tokens = len(source.split())

    try:
        tree = javalang.parse.parse(source)
    except javalang.parser.JavaSyntaxError as e:
        print(f"Parse error: {e}")
        return None

    # --- Step 1: Find anchor block ---
    anchor_node, anchor_raw = find_anchor_block(source_lines, tree, buggy_line)
    anchor_type = type(anchor_node).__name__ if anchor_node else "TextWindow"

    # --- Step 2: Extract used variables from anchor ---
    used_vars = get_used_variables(anchor_raw)

    # --- Step 3: Extract method signature (exposes intent via method name) ---
    method_sig = extract_method_signature(source_lines, buggy_line)

    # --- Step 4: Fetch declaration lines for those variables ---
    decl_lines = extract_declarations(source_lines, used_vars, buggy_line)

    # --- Step 5: Fetch relevant return lines after anchor ---
    anchor_end = (anchor_node.position.line + anchor_raw.count('\n')
                  if anchor_node and hasattr(anchor_node, 'position') and anchor_node.position
                  else buggy_line + 5)
    return_lines = find_return_lines(source_lines, used_vars, buggy_line, anchor_end)

    # --- Step 6: Assemble multi-node context ---
    parts = []
    # Include method signature first if it adds semantic value
    if method_sig and len(method_sig.split()) <= 15:
        parts.append(method_sig.strip() if not method_sig.strip().endswith('{') else method_sig.strip())
        parts.append("")
    if decl_lines:
        parts.extend(decl_lines)
        parts.append("")
    parts.append(anchor_raw.strip())
    if return_lines:
        parts.append("")
        parts.extend(return_lines)

    multi_node_context = "\n".join(parts)
    ast_tokens = count_tokens_text(multi_node_context)

    # --- Hard ceiling fallback ---
    if ast_tokens > TOKEN_UPPER_LIMIT:
        # Truncate to bug-site only + declarations only
        multi_node_context = "\n".join(
            (decl_lines[:3] if decl_lines else []) + ["", anchor_raw.strip()]
        )
        ast_tokens = count_tokens_text(multi_node_context)
        anchor_type += " [trimmed]"

    # --- Hard floor fallback (expand window) ---
    if ast_tokens < TOKEN_LOWER_LIMIT:
        start = max(0, buggy_line - 5)
        end   = min(len(source_lines), buggy_line + 4)
        multi_node_context = "".join(source_lines[start:end])
        ast_tokens = count_tokens_text(multi_node_context)
        anchor_type = "ExpandedWindow"

    reduction = round((1 - ast_tokens / original_tokens) * 100, 2) if original_tokens else 0

    return {
        "file": java_file_path,
        "buggy_line": buggy_line,
        "anchor_type": anchor_type,
        "original_tokens": original_tokens,
        "ast_tokens": ast_tokens,
        "reduction_percent": reduction,
        "used_variables": list(used_vars),
        "declaration_lines_added": len(decl_lines),
        "return_lines_added": len(return_lines),
        "extracted_code": multi_node_context,
    }


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python ast_extractor_v2.py <java_file> <buggy_line>")
        sys.exit(1)

    result = process_bug_v2(sys.argv[1], int(sys.argv[2]))

    if result:
        print("-" * 55)
        print(f"🎯 Anchor    : {result['anchor_type']}")
        print(f"📊 Original  : {result['original_tokens']} tokens")
        print(f"📦 Multi-node: {result['ast_tokens']} tokens")
        print(f"📉 Reduction : {result['reduction_percent']}%")
        print(f"🔗 Decl added: {result['declaration_lines_added']} lines")
        print(f"↩️  Return added: {result['return_lines_added']} lines")
        print(f"🔍 Vars used : {result['used_variables']}")
        print("-" * 55)
        print(result['extracted_code'])
        print("-" * 55)
