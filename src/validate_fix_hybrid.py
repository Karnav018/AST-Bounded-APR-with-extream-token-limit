"""
validate_fix_hybrid.py — Fixed File Resolution Version
========================================================================
Key Fixes:
1. Properly map Java class names to file paths
2. Filter out test classes and framework classes
3. Prioritize project source files over JDK/JUnit classes
4. Better stack trace parsing for real bug locations
"""

import os
import sys
import csv
import time
import re
import shutil
import subprocess
import signal
from collections import defaultdict
from typing import List, Dict, Tuple, Optional, Set, Any
from dataclasses import dataclass
from enum import Enum
import difflib
import glob

# Third-party imports
from groq import Groq
from dotenv import load_dotenv

# Local imports
sys.path.insert(0, os.path.dirname(__file__))
try:
    from ast_extractor_v2 import get_used_variables, extract_raw_lines
except ImportError:
    # Fallback implementations
    def get_used_variables(line: str) -> Set[str]:
        variables = set()
        var_pattern = r'\b([a-z][a-zA-Z0-9]*)\b(?:\s*=(?!=)|[^=])'
        for match in re.finditer(var_pattern, line):
            var = match.group(1)
            if var and len(var) > 1 and var not in {'if', 'for', 'while', 'return', 'new', 'null', 'true', 'false'}:
                variables.add(var)
        return variables
    
    def extract_raw_lines(source_lines: List[str], node: Any, target_line: int) -> str:
        if hasattr(node, 'position') and node.position:
            start_line = node.position.line - 1
            end_line = start_line + 10
            if hasattr(node, 'end_position') and node.end_position:
                end_line = node.end_position.line
            lines = source_lines[start_line:min(end_line, len(source_lines))]
            return ''.join(lines)
        return source_lines[target_line - 1] if target_line <= len(source_lines) else ""

load_dotenv()

# Configuration
class Config:
    GROQ_API_KEY = os.getenv("GROQ_API_KEY")
    MODEL = "llama-3.3-70b-versatile"
    D4J = "/Users/karnav/Desktop/Projects/Paper/defects4j/framework/bin/defects4j"
    PROJECT = "Lang"
    WORK_DIR = "/tmp/d4j_hybrid_fixed"
    TARGET_BUGS = [1, 3, 4, 5, 6, 7, 8, 9, 10, 11]
    D4J_BASE = "/Users/karnav/Desktop/Projects/Paper/defects4j/framework/projects"
    OCHIAI_BASE = "/Users/karnav/Desktop/Projects/Paper/D4jOchiai/results/Lang"
    MAX_ITERATIONS = 3
    TIMEOUT_SECONDS = 600
    MAX_TOKENS = 800

client = Groq(api_key=Config.GROQ_API_KEY)

SYSTEM_PROMPT = """You are an expert software engineer specializing in precision bug fixing.

CRITICAL RULES:
1. NEVER mask errors with null checks, try-catch, or early returns
2. NEVER delete loops, conditionals, or core logic blocks
3. PRESERVE original structure and behavior for non-buggy cases
4. IDENTIFY the exact semantic gap between expected and actual behavior
5. MODIFY only the minimal expression causing the divergence
6. VERIFY your fix handles all edge cases implicitly

Your fix must be surgical - change one expression, one condition, or one operator.
Return ONLY the corrected line of code, no explanations, no markdown formatting.
"""


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def run_cmd(cmd: str, cwd: Optional[str] = None, timeout: int = Config.TIMEOUT_SECONDS) -> Tuple[str, str, int]:
    """Run shell command with timeout"""
    try:
        process = subprocess.Popen(
            cmd, 
            shell=True, 
            cwd=cwd, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE,
            text=True
        )
        out, err = process.communicate(timeout=timeout)
        return out.strip(), err.strip(), process.returncode
    except subprocess.TimeoutExpired:
        process.kill()
        out, err = process.communicate()
        return out.strip(), err.strip(), -1
    except Exception as e:
        return "", str(e), -1


def _llm(system: str, user: str, max_tokens: int = 200) -> Tuple[str, int]:
    """Call LLM with timing"""
    t0 = time.time()
    try:
        response = client.chat.completions.create(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user}
            ],
            model=Config.MODEL,
            temperature=0.1,
            max_tokens=max_tokens,
        )
        ms = round((time.time() - t0) * 1000, 1)
        text = response.choices[0].message.content.strip()
        
        # Clean up markdown
        for tag in ["```java\n", "```\n", "```java", "```"]:
            if text.startswith(tag):
                text = text[len(tag):]
        if text.endswith("```"):
            text = text[:-3]
        
        return text.strip(), ms
    except Exception as e:
        return f"LLM_ERROR: {e}", 0


# ============================================================================
# FILE RESOLVER - FIXED VERSION
# ============================================================================

class FileResolver:
    """Resolves Java class names to actual file paths"""
    
    def __init__(self, project_dir: str):
        self.project_dir = project_dir
        self.cache = {}
        self.src_dirs = self._find_src_dirs()
        print(f"  📁 Source directories: {self.src_dirs}")
        
    def _find_src_dirs(self) -> List[str]:
        """Find all source directories in the project"""
        src_dirs = []
        
        # Common source directory patterns
        patterns = [
            "src/main/java",
            "src/java",
            "src",
            "source",
            "src/main",
            "src/org",
            "src/com",
            "org",
            "com",
            "java"
        ]
        
        for pattern in patterns:
            full_path = os.path.join(self.project_dir, pattern)
            if os.path.exists(full_path) and os.path.isdir(full_path):
                src_dirs.append(full_path)
        
        # Also search recursively for java files to find root
        if not src_dirs:
            for root, dirs, files in os.walk(self.project_dir):
                if any(f.endswith('.java') for f in files):
                    src_dirs.append(root)
                    # Don't break - collect all
        
        return src_dirs
    
    def resolve(self, class_name: str) -> Optional[str]:
        """Resolve class name to file path"""
        if class_name in self.cache:
            return self.cache[class_name]
        
        # Skip framework/test classes
        if self._should_skip(class_name):
            return None
        
        # Clean class name
        clean_name = self._clean_class_name(class_name)
        
        # Try different path formats
        file_path = self._try_resolve(clean_name)
        
        if file_path:
            self.cache[class_name] = file_path
            return file_path
        
        # Try with package structure
        for src_dir in self.src_dirs:
            # Try as full class name with dots
            rel_path = clean_name.replace('.', os.sep) + '.java'
            full_path = os.path.join(src_dir, rel_path)
            if os.path.exists(full_path):
                self.cache[class_name] = full_path
                return full_path
            
            # Try with package name only (no dots)
            simple_name = clean_name.split('.')[-1] + '.java'
            for root, _, files in os.walk(src_dir):
                if simple_name in files:
                    full_path = os.path.join(root, simple_name)
                    self.cache[class_name] = full_path
                    return full_path
        
        # Last resort: search all java files
        for src_dir in self.src_dirs:
            for root, _, files in os.walk(src_dir):
                for file in files:
                    if file.endswith('.java'):
                        full_path = os.path.join(root, file)
                        try:
                            with open(full_path, 'r', encoding='utf-8') as f:
                                content = f.read(1000)  # Read first 1000 chars
                                if f"class {clean_name.split('.')[-1]}" in content:
                                    self.cache[class_name] = full_path
                                    return full_path
                        except:
                            continue
        
        return None
    
    def _should_skip(self, class_name: str) -> bool:
        """Skip framework/test classes"""
        skip_patterns = [
            'org.junit',
            'junit.framework',
            'java.lang',
            'java.util',
            'org.apache.commons.lang3.test',
            'Test',
            'TestCase',
            'org.junit.Assert',
            'junit.framework.Assert'
        ]
        return any(pattern in class_name for pattern in skip_patterns)
    
    def _clean_class_name(self, class_name: str) -> str:
        """Clean up class name format"""
        # Remove src.main. prefix if present
        if class_name.startswith('src.main.'):
            class_name = class_name[9:]
        elif class_name.startswith('src.'):
            class_name = class_name[4:]
        
        # Remove any remaining src/ path artifacts
        class_name = class_name.replace('src.', '').replace('main.', '')
        
        return class_name
    
    def _try_resolve(self, class_name: str) -> Optional[str]:
        """Try common path formats"""
        # Direct file in project
        for ext in ['.java', '.class']:
            direct_path = os.path.join(self.project_dir, class_name + ext)
            if os.path.exists(direct_path):
                return direct_path
        
        return None


# ============================================================================
# STACK TRACE FILTER - NEW
# ============================================================================

class StackTraceFilter:
    """Filters stack traces to find relevant project frames"""
    
    @staticmethod
    def find_relevant_frame(frames: List[Dict], project_name: str = "org.apache.commons.lang3") -> Optional[Dict]:
        """Find the first project frame in stack trace"""
        for frame in frames:
            class_name = frame.get('class', '')
            if project_name in class_name and 'Test' not in class_name:
                return frame
        return frames[0] if frames else None


# ============================================================================
# TEST FAILURE PARSER
# ============================================================================

class TestFailureType(Enum):
    ASSERT_EQUALS = "assert_equals"
    ASSERT_NULL = "assert_null"
    ASSERT_TRUE = "assert_true"
    ASSERT_FALSE = "assert_false"
    ARRAY_INDEX = "array_index"
    CLASS_CAST = "class_cast"
    NULL_POINTER = "null_pointer"
    EXCEPTION = "exception"
    UNKNOWN = "unknown"


@dataclass
class SemanticSpec:
    failure_type: TestFailureType
    expected: Optional[str] = None
    actual: Optional[str] = None
    input_context: Dict[str, Any] = None
    stack_frames: List[Dict] = None
    precondition: Optional[str] = None
    postcondition: Optional[str] = None
    exception_type: Optional[str] = None
    raw_output: str = ""
    
    def __post_init__(self):
        if self.input_context is None:
            self.input_context = {}
        if self.stack_frames is None:
            self.stack_frames = []


class TestFailureParser:
    """Parses test failures into semantic specs"""
    
    def parse(self, content: str, stack_trace: str) -> SemanticSpec:
        """Parse test output"""
        
        spec = SemanticSpec(
            failure_type=TestFailureType.UNKNOWN,
            raw_output=content
        )
        
        # Parse assertion failures
        if "expected" in content.lower() and "but was" in content.lower():
            spec.failure_type = TestFailureType.ASSERT_EQUALS
            
            # Extract expected/actual
            match = re.search(r"expected:?[<\s\[]*([^>\]]*)[>\s\]]*but was:?[<\s\[]*([^>\]]*)[>\s\]]*", content, re.IGNORECASE)
            if match:
                spec.expected = match.group(1).strip()
                spec.actual = match.group(2).strip()
                spec.postcondition = f"Should return '{spec.expected}'"
        
        # Parse exceptions
        elif "Exception:" in content or "Error:" in content:
            spec.failure_type = TestFailureType.EXCEPTION
            
            # Extract exception type
            match = re.search(r"(java\.\S+Exception)", content)
            if match:
                spec.exception_type = match.group(1)
                spec.postcondition = f"Should not throw {spec.exception_type}"
        
        # Parse stack trace
        spec.stack_frames = self._parse_stack_trace(stack_trace)
        
        return spec
    
    def _parse_stack_trace(self, stack_trace: str) -> List[Dict]:
        """Parse stack trace"""
        frames = []
        for line in stack_trace.splitlines():
            match = re.search(r'at ([\w.$]+)\.([\w<>]+)\(([^:]+):(\d+)\)', line)
            if match:
                frames.append({
                    'class': match.group(1),
                    'method': match.group(2),
                    'file': match.group(3),
                    'line': int(match.group(4))
                })
        return frames


# ============================================================================
# FAULT LOCALIZER - FIXED
# ============================================================================

@dataclass
class SuspiciousLocation:
    class_name: str
    line_number: int
    confidence: float
    reasons: List[str] = None
    file_path: Optional[str] = None
    
    def __post_init__(self):
        if self.reasons is None:
            self.reasons = []


class FaultLocalizer:
    """Multi-strategy fault localization"""
    
    def __init__(self, project_dir: str, resolver: FileResolver):
        self.project_dir = project_dir
        self.resolver = resolver
        self.sbfl_cache = {}
    
    def locate(self, bug_id: int, semantic_spec: SemanticSpec) -> List[SuspiciousLocation]:
        """Rank suspicious locations"""
        
        candidates = []
        
        # Strategy 1: Stack trace (highest priority)
        relevant_frame = StackTraceFilter.find_relevant_frame(semantic_spec.stack_frames)
        if relevant_frame:
            file_path = self.resolver.resolve(relevant_frame['class'])
            if file_path:
                candidates.append(SuspiciousLocation(
                    class_name=relevant_frame['class'],
                    line_number=relevant_frame['line'],
                    confidence=0.8,
                    reasons=["Direct from stack trace"],
                    file_path=file_path
                ))
        
        # Strategy 2: SBFL scores
        sbfl_candidates = self._get_project_sbfl_candidates(bug_id)
        for class_name, line_num, score in sbfl_candidates:
            file_path = self.resolver.resolve(class_name)
            if file_path:
                # Check if we already have this line
                existing = next((c for c in candidates if c.class_name == class_name and c.line_number == line_num), None)
                if existing:
                    existing.confidence = max(existing.confidence, score * 0.5)
                else:
                    candidates.append(SuspiciousLocation(
                        class_name=class_name,
                        line_number=line_num,
                        confidence=score * 0.5,
                        reasons=["SBFL ranking"],
                        file_path=file_path
                    ))
        
        # Sort by confidence
        candidates.sort(key=lambda x: x.confidence, reverse=True)
        
        return candidates[:5]  # Top 5
    
    def _get_project_sbfl_candidates(self, bug_id: int, k: int = 20) -> List[Tuple[str, int, float]]:
        """Get SBFL candidates filtering to project classes"""
        csv_file = os.path.join(Config.OCHIAI_BASE, str(bug_id), "ochiai.ranking.csv")
        candidates = []
        
        if os.path.exists(csv_file):
            try:
                with open(csv_file, 'r', encoding='utf-8') as f:
                    reader = csv.reader(f, delimiter=';')
                    next(reader)
                    
                    for i, row in enumerate(reader):
                        if not row or len(row) < 2:
                            continue
                        
                        name = row[0]
                        if ":" not in name:
                            continue
                        
                        class_method, line_str = name.split(":")
                        class_name = class_method.split("#")[0].replace("$", ".")
                        
                        # Filter to project classes only
                        if 'org.apache.commons.lang3' in class_name and 'Test' not in class_name:
                            try:
                                line_num = int(line_str)
                                score = 1.0 / (i + 1)  # Convert rank to score
                                candidates.append((class_name, line_num, score))
                            except ValueError:
                                continue
                        
                        if len([c for c in candidates if c[2] > 0]) >= k:
                            break
            except Exception as e:
                print(f"  ⚠️ Error reading SBFL: {e}")
        
        return candidates


# ============================================================================
# CONTEXT BUILDER
# ============================================================================

class ContextBuilder:
    def __init__(self, max_tokens: int = Config.MAX_TOKENS):
        self.max_tokens = max_tokens
    
    def build(self, java_file: str, target_line: int, semantic_spec: SemanticSpec) -> str:
        """Build context around buggy line"""
        try:
            with open(java_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        except Exception as e:
            return f"Error reading file: {e}"
        
        context_parts = []
        
        # Add semantic info
        if semantic_spec.expected and semantic_spec.actual:
            context_parts.append(f"FAILURE: Expected '{semantic_spec.expected}', got '{semantic_spec.actual}'")
        elif semantic_spec.exception_type:
            context_parts.append(f"EXCEPTION: {semantic_spec.exception_type}")
        
        # Add method signature (look backward)
        method_sig = self._find_method_signature(lines, target_line)
        if method_sig:
            context_parts.append(f"METHOD: {method_sig}")
        
        # Add code context (30 lines before and after to encompass method logic)
        start = max(0, target_line - 31)
        end = min(len(lines), target_line + 30)
        
        context_parts.append("\nCODE CONTEXT:")
        for i in range(start, end):
            line_num = i + 1
            prefix = "→ " if line_num == target_line else "  "
            context_parts.append(f"{prefix}{line_num:4d}: {lines[i].rstrip()}")
        
        return "\n".join(context_parts)
    
    def _find_method_signature(self, lines: List[str], target_line: int) -> Optional[str]:
        """Find method containing the target line"""
        brace_count = 0
        for i in range(target_line - 1, max(0, target_line - 50), -1):
            line = lines[i]
            
            # Count braces to find method boundaries
            brace_count += line.count('}')
            
            # Look for method declaration
            if brace_count == 0 and re.search(r'(public|private|protected).*\(.*\)\s*{?', line):
                return line.strip()
            
            # Reset if we hit a closing brace at the right level
            if '}' in line:
                brace_count -= line.count('}')
        
        return None


# ============================================================================
# FIX APPLIER
# ============================================================================

class FixApplier:
    """Applies fixes and runs tests"""
    
    def __init__(self, project_dir: str):
        self.project_dir = project_dir
    
    def apply_and_test(self, java_file: str, line_num: int, fix_code: str) -> Tuple[bool, bool, List[str]]:
        """Apply fix and run tests"""
        
        # Save original
        try:
            with open(java_file, 'r', encoding='utf-8') as f:
                original = f.read()
        except Exception as e:
            return False, False, [str(e)]
        
        # Apply fix
        try:
            with open(java_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            idx = line_num - 1
            if 0 <= idx < len(lines):
                # Preserve indentation
                orig_indent = len(lines[idx]) - len(lines[idx].lstrip())
                lines[idx] = ' ' * orig_indent + fix_code.strip() + '\n'
                
                with open(java_file, 'w', encoding='utf-8') as f:
                    f.writelines(lines)
        except Exception as e:
            # Restore original
            with open(java_file, 'w', encoding='utf-8') as f:
                f.write(original)
            return False, False, [str(e)]
        
        # Compile
        out, err, rc = run_cmd(f"{Config.D4J} compile", cwd=self.project_dir)
        if rc != 0:
            # Restore original
            with open(java_file, 'w', encoding='utf-8') as f:
                f.write(original)
            return False, False, [err]
        
        # Run tests
        out, err, rc = run_cmd(f"{Config.D4J} test -r", cwd=self.project_dir, timeout=60)
        
        if rc == -1:  # Timeout
            with open(java_file, 'w', encoding='utf-8') as f:
                f.write(original)
            return True, False, ["Tests timed out"]
        
        # Check failing tests
        failing_tests_file = os.path.join(self.project_dir, "failing_tests")
        failures = []
        
        if os.path.exists(failing_tests_file):
            try:
                with open(failing_tests_file, 'r', encoding='utf-8') as f:
                    failures = [line.strip() for line in f if line.strip()]
            except Exception:
                pass
        
        passed = len(failures) == 0
        
        # Restore original if tests failed
        if not passed:
            with open(java_file, 'w', encoding='utf-8') as f:
                f.write(original)
        
        return True, passed, failures


# ============================================================================
# MAIN FUNCTION
# ============================================================================

def get_trigger_test_info(bug_id: int) -> Tuple[str, str]:
    """Get trigger test information"""
    trigger_file = os.path.join(Config.D4J_BASE, Config.PROJECT, "trigger_tests", str(bug_id))
    
    if not os.path.exists(trigger_file):
        return "", ""
    
    try:
        with open(trigger_file, 'r', encoding='utf-8') as f:
            content = f.read().strip()
        
        # Extract stack trace (lines starting with 'at')
        stack_lines = [line for line in content.splitlines() if line.strip().startswith('at ')]
        stack_trace = "\n".join(stack_lines[:10])
        
        return content, stack_trace
    except Exception:
        return "", ""


def run_fixed():
    """Main fixed pipeline"""
    
    print("=" * 70)
    print("Fixed File Resolution Bug Fixing System")
    print("=" * 70)
    
    os.makedirs(Config.WORK_DIR, exist_ok=True)
    
    results = []
    
    for bug_id in Config.TARGET_BUGS:
        print(f"\n[Lang-{bug_id}] {'=' * 50}")
        
        # Setup project
        target_dir = os.path.join(Config.WORK_DIR, f"Lang_{bug_id}")
        shutil.rmtree(target_dir, ignore_errors=True)
        
        out, err, rc = run_cmd(f"{Config.D4J} checkout -p {Config.PROJECT} -v {bug_id}b -w {target_dir}")
        
        if rc != 0:
            print(f"  ❌ Checkout failed: {err[:100]}")
            results.append({'bug_id': f"Lang-{bug_id}", 'status': 'CHECKOUT_FAILED'})
            continue
        
        print(f"  ✅ Checkout successful")
        
        # Get test info
        trigger_content, stack_trace = get_trigger_test_info(bug_id)
        
        # Parse failure
        parser = TestFailureParser()
        semantic_spec = parser.parse(trigger_content, stack_trace)
        
        print(f"  📊 Failure: {semantic_spec.failure_type.value}")
        if semantic_spec.expected:
            print(f"     Expected: {semantic_spec.expected}")
            print(f"     Actual: {semantic_spec.actual}")
        
        # Initialize resolver
        resolver = FileResolver(target_dir)
        
        # Localize
        localizer = FaultLocalizer(target_dir, resolver)
        candidates = localizer.locate(bug_id, semantic_spec)
        
        if not candidates:
            print(f"  ❌ No valid candidates found")
            results.append({'bug_id': f"Lang-{bug_id}", 'status': 'NO_CANDIDATES'})
            continue
        
        print(f"\n  🔍 Top candidates:")
        for i, cand in enumerate(candidates, 1):
            print(f"     {i}. {cand.class_name}:{cand.line_number} (conf: {cand.confidence:.2f})")
            print(f"        File: {os.path.basename(cand.file_path) if cand.file_path else 'Unknown'}")
        
        # Try each candidate
        fix_applier = FixApplier(target_dir)
        fixed = False
        
        for attempt, candidate in enumerate(candidates, 1):
            print(f"\n  ── Attempt {attempt}/{len(candidates)} ──")
            
            if not candidate.file_path:
                print(f"  ⚠️ No file path for candidate")
                continue
            
            # Get buggy line
            try:
                with open(candidate.file_path, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                
                if candidate.line_number > len(lines):
                    print(f"  ⚠️ Line {candidate.line_number} out of range")
                    continue
                
                buggy_line = lines[candidate.line_number - 1].strip()
                print(f"  📝 Line {candidate.line_number}: {buggy_line[:80]}")
            except Exception as e:
                print(f"  ⚠️ Error reading file: {e}")
                continue
            
            # Build context
            context_builder = ContextBuilder()
            context = context_builder.build(candidate.file_path, candidate.line_number, semantic_spec)
            
            # Generate fix
            prompt = f"""
SEMANTIC FAILURE:
Type: {semantic_spec.failure_type.value}
Expected: {semantic_spec.expected or 'unknown'}
Actual: {semantic_spec.actual or 'unknown'}

CODE CONTEXT:
{context}

TASK:
Fix the bug by modifying ONLY the line marked with "→".
Return JUST the corrected line of code.
"""
            
            fix, ms = _llm(SYSTEM_PROMPT, prompt, max_tokens=150)
            print(f"  🤖 Fix ({ms}ms): {fix[:80]}...")
            
            if fix.startswith("LLM_ERROR") or len(fix) < 3:
                print(f"  ⚠️ Invalid fix")
                continue
            
            # Apply and test
            compiled, passed, failures = fix_applier.apply_and_test(
                candidate.file_path, candidate.line_number, fix
            )
            
            if passed:
                print(f"  🎉 TESTS PASSED!")
                results.append({
                    'bug_id': f"Lang-{bug_id}",
                    'status': 'PASS',
                    'class': candidate.class_name,
                    'line': candidate.line_number,
                    'fix': fix
                })
                fixed = True
                break
            elif not compiled:
                print(f"  ❌ Compilation failed")
            else:
                print(f"  ❌ Tests failed ({len(failures)} failures)")
        
        if not fixed:
            results.append({'bug_id': f"Lang-{bug_id}", 'status': 'FAIL'})
    
    # Print results
    print("\n" + "=" * 70)
    print("FINAL RESULTS")
    print("=" * 70)
    
    passed = sum(1 for r in results if r['status'] == 'PASS')
    print(f"Passed: {passed}/{len(results)} ({passed/len(results)*100:.1f}%)")
    
    for r in results:
        status_icon = "✅" if r['status'] == 'PASS' else "❌"
        print(f"  {status_icon} {r['bug_id']}: {r['status']}")
    
    # Save results
    with open("fixed_results.csv", "w", newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['bug_id', 'status', 'class', 'line', 'fix'])
        writer.writeheader()
        writer.writerows(results)


if __name__ == "__main__":
    run_fixed()