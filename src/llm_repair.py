import os
from dotenv import load_dotenv
from groq import Groq
import sys
from ast_extractor import process_bug

# Load environment variables
load_dotenv()

# Check for API Key
API_KEY = os.getenv("GROQ_API_KEY")
if not API_KEY or API_KEY == "your_groq_api_key_here":
    print("❌ Error: GROQ_API_KEY is not set. Please add it to your .env file.")
    sys.exit(1)

# Initialize Groq Client
client = Groq(api_key=API_KEY)

# Use the fastest available model, LLaMA-3 8B
MODEL_NAME = "llama-3.1-8b-instant"

def repair_bug_with_llm(java_file_path, buggy_line):
    """
    1. Extracts the minimal AST wrapper
    2. Builds a strict LLM Prompt
    3. Calls Groq API
    4. Returns the suggested fix
    """
    print(f"\n🔍 Extracting Context for {java_file_path} at line {buggy_line}...")
    extraction = process_bug(java_file_path, buggy_line)
    
    if not extraction:
        print("Failed to extract context.")
        return None

    extracted_code = extraction['extracted_code']
    anchor_type = extraction['anchor_type']
    
    print(f"✅ Context Extracted! Using {extraction['ast_tokens']} tokens instead of {extraction['original_tokens']}.")
    print("🧠 Sending prompt to Groq API...\n")

    # Strict system prompt for zero-shot repair
    system_prompt = (
        "You are an expert Automated Program Repair (APR) AI agent. "
        "You are provided with a minimal AST-bounded context representing a buggy logic block in Java. "
        "Your goal is to fix the logic bug and return ONLY the completely corrected code block. "
        "DO NOT write explanations. DO NOT wrap the code in markdown blocks (like ```java). "
        "ONLY output the raw corrected Java code so it can be directly injected back into the file."
    )
    
    user_prompt = f"""Target Node Type: {anchor_type}
    
Buggy Code Block:
{extracted_code}

Task: Provide the fixed version of this block. Output ONLY code.
"""

    chat_completion = client.chat.completions.create(
        messages=[
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": user_prompt,
            }
        ],
        model=MODEL_NAME,
        temperature=0.2, # Low temperature for more deterministic code fixing
        max_tokens=256, # We expect tiny fixes
    )
    
    response = chat_completion.choices[0].message.content.strip()
    
    # Strip markdown if the AI hallucinated it
    if response.startswith("```java"):
        response = response[7:]
    if response.startswith("```"):
        response = response[3:]
    if response.endswith("```"):
        response = response[:-3]
        
    response = response.strip()
    
    print("-" * 50)
    print("✅ LLM Suggested Fix:")
    print("-" * 50)
    print(response)
    print("-" * 50)
    
    return {
        "original_snippet": extracted_code,
        "fixed_snippet": response
    }

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python llm_repair.py <path_to_java_file> <buggy_line_number>")
        sys.exit(1)
        
    file_path = sys.argv[1]
    line_num = int(sys.argv[2])
    
    repair_bug_with_llm(file_path, line_num)
