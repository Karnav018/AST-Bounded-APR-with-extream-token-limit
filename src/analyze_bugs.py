import pandas as pd
import sys
import os
from ast_extractor import process_bug

def bulk_analyze(csv_file, output_csv="analysis_results.csv"):
    df = pd.read_csv(csv_file)
    results = []
    
    for index, row in df.iterrows():
        bug_id = row['BugID'] # e.g. Lang-1
        file_path = row['File']
        line_num = int(row['Line'])
        
        # Convert bug_id to expected folder structure (e.g., Lang_1_buggy)
        # Assuming the structure is: Lang_1_buggy/src/...
        parts = bug_id.split('-')
        if len(parts) == 2:
            folder_name = f"{parts[0]}_{parts[1]}_buggy"
            full_path = os.path.join(folder_name, file_path)
        else:
            full_path = file_path # Fallback
            
        print(f"[{index+1}/{len(df)}] Analyzing {bug_id}...")
        
        if not os.path.exists(full_path):
             print(f"  -> File not found: {full_path}")
             continue
             
        res = process_bug(full_path, line_num)
        
        if res:
             results.append({
                 "BugID": bug_id,
                 "AnchorType": res["anchor_type"],
                 "OriginalTokens": res["original_tokens"],
                 "ASTTokens": res["ast_tokens"],
                 "ReductionPercent": res["reduction_percent"]
             })
             
    if results:
         results_df = pd.DataFrame(results)
         results_df.to_csv(output_csv, index=False)
         print(f"\n✅ Analysis complete! Saved to {output_csv}")
         print(results_df.to_string(index=False))
    else:
         print("\n❌ No results to save.")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python analyze_bugs.py <path_to_csv>")
        sys.exit(1)
        
    csv_path = sys.argv[1]
    bulk_analyze(csv_path)
