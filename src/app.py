import streamlit as st
import pandas as pd
import time
import os
from ast_extractor import process_bug
from llm_repair import repair_bug_with_llm
from llm_locator import locate_bug

st.set_page_config(layout="wide", page_title="AST-Bounded APR Demo", page_icon="✂️")

st.title("✂️ Token-Efficient Automated Program Repair")
st.markdown("""
This demonstration proves that many logical software bugs can be repaired using **minimal AST-bounded context**, solving the massive token costs associated with passing entire files to Large Language Models.
""")

st.divider()

# Load Data
@st.cache_data
def load_data():
    if os.path.exists("phase1_localization.csv"):
        return pd.read_csv("phase1_localization.csv")
    return pd.DataFrame()

df = load_data()

# --- Sidebar Controls ---
st.sidebar.header("Configuration")
mode = st.sidebar.radio("Input Method", ["Benchmark Bugs", "Custom Code Paste"])

java_file_path = None
buggy_line = None
custom_code_content = None

if mode == "Benchmark Bugs":
    if not df.empty:
        selected_bug_id = st.sidebar.selectbox("Select Benchmark Bug", df['BugID'].tolist())
        bug_row = df[df['BugID'] == selected_bug_id].iloc[0]
        
        # Resolve file path
        parts = selected_bug_id.split('-')
        folder_name = f"{parts[0]}_{parts[1]}_buggy"
        java_file_path = os.path.join(folder_name, bug_row['File'])
        buggy_line = int(bug_row['Line'])
    else:
        st.sidebar.warning("phase1_localization.csv not found.")
else:
    st.sidebar.markdown("### Paste Java Code")
    custom_code_content = st.sidebar.text_area("Paste the buggy Java code here:", height=300)
    
    if custom_code_content:
        # Create a temporary file for the processor
        java_file_path = "temp_custom_bug.java"
        with open(java_file_path, "w") as f:
            f.write(custom_code_content)

if st.sidebar.button("Analyze & Repair 🚀", type="primary"):
    
    if not java_file_path or not os.path.exists(java_file_path):
        st.error("Please select a valid benchmark bug or paste Java code.")
        st.stop()
        
    # Phase 0: Auto-Locate Bug (If Custom Mode)
    if mode == "Custom Code Paste":
        with st.spinner("🤖 AI is analyzing code to autonomously locate the bug..."):
            start_loc_time = time.time()
            buggy_line = locate_bug(custom_code_content)
            loc_latency = (time.time() - start_loc_time) * 1000
            
            if buggy_line:
                st.sidebar.success(f"Bug found at **Line {buggy_line}** ({loc_latency:.0f}ms)")
            else:
                st.error("Failed to automatically locate the bug in the provided code.")
                st.stop()

    with st.spinner("✂️ Extracting minimal AST Context around Line " + str(buggy_line) + "..."):
        time.sleep(0.5) # Slight delay for dramatic UX effect
        extraction = process_bug(java_file_path, buggy_line)
        
    if not extraction:
        st.error(f"Failed to parse {java_file_path}.")
        st.stop()
        
    # --- UI Layout ---
    col1, col2 = st.columns([1, 1], gap="large")
    
    with col1:
        st.subheader("📊 Token Phase: Context Slicing")
        
        # Metrics 
        m1, m2, m3 = st.columns(3)
        m1.metric("Original File Tokens", f"{extraction['original_tokens']:,}")
        m2.metric("AST Bounded Tokens", extraction['ast_tokens'])
        m3.metric("Reduction", f"{extraction['reduction_percent']}%", delta="Tokens Saved", delta_color="normal")
        
        st.info(f"📍 **Anchor Type:** `{extraction['anchor_type']}` found enclosing line {buggy_line}.")
        
        st.markdown("**What the LLM sees (Minimal Context):**")
        st.code(extraction['extracted_code'], language='java')
        
    with col2:
        st.subheader("🧠 Phase 2: Instant LLM Repair")
        with st.spinner("LLaMA-3 generating fix via Groq API..."):
            
            start_time = time.time()
            llm_result = repair_bug_with_llm(java_file_path, buggy_line)
            end_time = time.time()
            
            latency = (end_time - start_time) * 1000
            
        if llm_result:
            st.success(f"Fix generated in **{latency:.0f} ms**!")
            
            st.markdown("**Proposed Fix:**")
            st.code(llm_result['fixed_snippet'], language='java')
            
            # Simple Text Diff
            st.markdown("**Patch (Before vs After):**")
            diff_string = f"- {llm_result['original_snippet'].strip()}\n+ {llm_result['fixed_snippet'].strip()}"
            st.code(diff_string, language='diff')
        else:
            st.error("LLM failed to generate a response.")
