import javalang
import sys

def count_tokens(node):
    """
    Recursively counts the structural tokens in an AST node.
    This mimics the token budget we will send to an LLM.
    """
    if node is None:
        return 0
    
    token_count = 1 # The node itself (e.g., 'IfStatement')
    
    if not hasattr(node, "attrs") and not hasattr(node, "children"):
         return token_count

    # Count string literal attributes (like variable names, operators)
    if hasattr(node, "attrs"):
        for attr in node.attrs:
            val = getattr(node, attr)
            if isinstance(val, str) or isinstance(val, int) or isinstance(val, float):
                token_count += 1
    
    # Recurse through children
    if hasattr(node, "children"):
        for child in node.children:
            if isinstance(child, list):
                for item in child:
                    if hasattr(item, '__dict__'):
                        token_count += count_tokens(item)
            elif hasattr(child, '__dict__'):
                token_count += count_tokens(child)
                
    return token_count

def extract_node_source(source_lines, node):
    """
    Attempts to extract the exact string representation of the AST node from the source code.
    This relies on the position (line number) of the node and its children.
    """
    if not hasattr(node, 'position') or node.position is None:
        return "<Unknown Source>"
    
    start_line = node.position.line - 1 # 0-indexed
    
    # To find the end line, we need to recursively find the highest line number among its children
    def find_max_line(n, current_max):
        if hasattr(n, 'position') and n.position:
            current_max = max(current_max, n.position.line)
        
        if hasattr(n, 'children'):
             for child in n.children:
                if isinstance(child, list):
                    for item in child:
                        if hasattr(item, '__dict__'):
                            current_max = find_max_line(item, current_max)
                elif hasattr(child, '__dict__'):
                    current_max = find_max_line(child, current_max)
        return current_max

    end_line = find_max_line(node, start_line + 1)
    
    # Extra buffer for closing braces of blocks
    end_line = min(end_line + 1, len(source_lines)) 
    
    extracted_lines = source_lines[start_line:end_line]
    return "".join(extracted_lines)


def process_bug(java_file_path, buggy_line):
    """
    Parses the Java file, finds the closest enclosing AST node for the given buggy line,
    and returns metrics about token reduction.
    """
    with open(java_file_path, "r") as f:
        source = f.read()
        
    source_lines = source.splitlines(True)

    # javalang doesn't retain comments or whitespace in the token stream,
    # but for simple word counting of the original file, we split by whitespace.
    original_token_count = len(source.split())

    try:
        tree = javalang.parse.parse(source)
    except javalang.parser.JavaSyntaxError as e:
        print(f"Error parsing {java_file_path}: {e}")
        return None

    candidates = []
    
    # 1. Traverse the AST to find nodes enclosing our buggy line
    for path, node in tree:
        if hasattr(node, 'position') and node.position:
            if node.position.line <= buggy_line:
                # We want structural blocks like MethodDeclaration, IfStatement, ForStatement, etc.
                # that provide context, not just the raw StatementExpression itself.
                allowed_types = ['IfStatement', 'ForStatement', 'WhileStatement', 'MethodDeclaration', 'CatchClause', 'TryStatement']
                
                # If the buggy line is directly inside a basic block, we want the parent.
                if type(node).__name__ in allowed_types or type(node).__name__ == "StatementExpression":
                    candidates.append((node.position.line, type(node).__name__, node))

    # 2. Sort candidates to find the closest enclosing block
    # We want the node that starts closest to (but before or on) the buggy line
    candidates.sort(key=lambda x: abs(buggy_line - x[0]))

    if not candidates:
        print(f"No suitable AST anchor found near line {buggy_line}")
        return None

    # Let's try to isolate the smallest enclosing block that fits the budget
    # Priority order: smallest block first that is >= 20 tokens AND <= 60 tokens
    anchor_node = None
    
    # First pass: find the best AST block within our [20, 60] token sweet spot
    for cand in candidates:
        if cand[1] in ['IfStatement', 'ForStatement', 'WhileStatement', 'CatchClause', 'TryStatement']:
            token_count = count_tokens(cand[2])
            if 20 <= token_count <= 60:
                anchor_node = cand[2]
                break
    
    # Second pass: if nothing in sweet spot, try any block >= 20 tokens (we'll hard cap it later)
    if not anchor_node:
        for cand in candidates:
            if cand[1] in ['IfStatement', 'ForStatement', 'WhileStatement', 'MethodDeclaration', 'CatchClause', 'TryStatement', 'StatementExpression']:
                if count_tokens(cand[2]) >= 20:
                    anchor_node = cand[2]
                    break

    # Fallback to nearest node (may be tiny, will be expanded below)
    if not anchor_node:
        anchor_node = candidates[0][2]

    ast_token_count = count_tokens(anchor_node)
    extracted_source = extract_node_source(source_lines, anchor_node)

    # CEILING ENFORCEMENT (> 60 tokens): Force a 5-line text slice
    if ast_token_count > 60 or len(extracted_source.splitlines()) > 7:
        start_line_idx = max(0, buggy_line - 3)
        end_line_idx = min(len(source_lines), buggy_line + 2)
        extracted_source = "".join(source_lines[start_line_idx:end_line_idx])
        ast_token_count = len(extracted_source.split())
        anchor_type = "StrictTextSlice (AST > 60 tokens)"
    
    # FLOOR ENFORCEMENT (< 20 tokens): Expand to 5-line window — too small means no useful context
    elif ast_token_count < 20:
        start_line_idx = max(0, buggy_line - 4)
        end_line_idx = min(len(source_lines), buggy_line + 3)
        extracted_source = "".join(source_lines[start_line_idx:end_line_idx])
        ast_token_count = len(extracted_source.split())
        anchor_type = f"ExpandedTextSlice (AST < 20 tokens, expanded)"

    else:
        anchor_type = type(anchor_node).__name__

    return {
        "file": java_file_path,
        "buggy_line": buggy_line,
        "anchor_type": anchor_type,
        "original_tokens": original_token_count,
        "ast_tokens": ast_token_count,
        "reduction_percent": round((1 - (ast_token_count / original_token_count)) * 100, 2),
        "extracted_code": extracted_source.strip()
    }

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python ast_extractor.py <path_to_java_file> <buggy_line_number>")
        sys.exit(1)
        
    file_path = sys.argv[1]
    line_num = int(sys.argv[2])
    
    print(f"Analyzing {file_path} at line {line_num}...\n")
    result = process_bug(file_path, line_num)
    
    if result:
        print("-" * 50)
        print(f"🎯 Anchor Node Found: {result['anchor_type']}")
        print(f"📊 Original File Tokens (approx): {result['original_tokens']}")
        print(f"📦 AST Bounded Context Tokens: {result['ast_tokens']}")
        print(f"📉 Token Reduction: {result['reduction_percent']}%")
        print("-" * 50)
        print("💻 Extracted Code Snippet (What the LLM sees):")
        print(result['extracted_code'])
        print("-" * 50)
