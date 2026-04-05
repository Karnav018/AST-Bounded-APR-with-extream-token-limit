import os
from dotenv import load_dotenv
from groq import Groq
import re

# Load environment variables
load_dotenv()
API_KEY = os.getenv("GROQ_API_KEY")

if not API_KEY or API_KEY == "your_groq_api_key_here":
    print("❌ Error: GROQ_API_KEY is not set.")
    exit(1)

client = Groq(api_key=API_KEY)
MODEL_NAME = "llama-3.1-8b-instant"

def locate_bug(java_code):
    """
    Asks the LLM to analyze the provided Java code, find the logical bug, 
    and return ONLY the line number of the buggy statement.
    """
    
    # Add line numbers to the code so the LLM knows what to return
    lines = java_code.split('\n')
    numbered_code = "\n".join([f"{i+1}: {line}" for i, line in enumerate(lines)])
    
    system_prompt = (
        "You are an expert static analysis AI. You are given a piece of Java code with line numbers. "
        "There is a logical bug in this code. Your task is to ONLY output the integer line number where the bug is located. "
        "DO NOT output explanation. DO NOT output code. OUTPUT ONLY AN INTEGER."
    )
    
    user_prompt = f"Find the bug in this code:\n\n{numbered_code}"
    
    chat_completion = client.chat.completions.create(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        model=MODEL_NAME,
        temperature=0.1,  # Highly deterministic
        max_tokens=10,
    )
    
    response = chat_completion.choices[0].message.content.strip()
    
    # Extract just the number using regex in case the LLM adds text anyway like "Line 42"
    match = re.search(r'\d+', response)
    if match:
        return int(match.group())
    else:
        print(f"Failed to parse line number from LLM response: {response}")
        return None
