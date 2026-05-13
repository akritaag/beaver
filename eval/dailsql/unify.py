import os
import sys
import json
import argparse
import glob
import re
from tqdm import tqdm
import pandas as pd

# Add the eval directory to path to import unified_utils
eval_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(eval_dir)

from unified_utils import get_mysql_credentials, execute_sql_with_timeout

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input_dir", type=str, required=True, help="Directory containing generated outputs")
    parser.add_argument("--gold_file", type=str, required=True, help="Path to dev.json or dev_sampled.json")
    parser.add_argument("--dataset", type=str, required=True, help="Dataset name, e.g. dw")
    args = parser.parse_args()
    
    args.input_dir = args.input_dir.rstrip('/')
    run_name = os.path.basename(args.input_dir)
    
    baseline_name = os.path.basename(os.path.dirname(os.path.abspath(__file__)))
    unified_dir = os.path.join(eval_dir, "output", "unified", baseline_name, run_name)
    generated_dir = os.path.join(unified_dir, "generated")
    gold_dir = os.path.join(unified_dir, "gold")
    
    os.makedirs(generated_dir, exist_ok=True)
    os.makedirs(gold_dir, exist_ok=True)
    
    with open(args.gold_file, 'r') as f:
        gold_data = json.load(f)
        
    mysql_creds = get_mysql_credentials(args.dataset)
    if not mysql_creds:
        print("Error: Could not load MySQL credentials.")
        sys.exit(1)
        
    subdirs = sorted(glob.glob(os.path.join(args.input_dir, "*")))
    subdirs = [d for d in subdirs if os.path.isdir(d)]
    
    execution_summary = []
    
    print(f"Processing {len(subdirs)} subdirectories...")
    for subdir_path in tqdm(subdirs):
        subdir_name = os.path.basename(subdir_path)
        match = re.search(r'_(\d+)$', subdir_name)
        if not match: continue
        
        idx = int(match.group(1))
        if idx >= len(gold_data): continue
        
        gold_entry = gold_data[idx]
        gold_sql = gold_entry.get("sql", gold_entry.get("oracle_sql", gold_entry.get("gold_sql", "")))
        
        # 1. Gold SQL Execution
        gold_csv_path = os.path.join(gold_dir, f"{args.dataset}_{idx}.csv")
        gold_df = None
        gold_err = None
            
        gold_df, gold_err = execute_sql_with_timeout(gold_sql, mysql_creds)
        if gold_df is not None:
            gold_df.to_csv(gold_csv_path, index=False)
        else:
            # Save empty CSV on failure
            pd.DataFrame().to_csv(gold_csv_path, index=False)
            
        # 2. Generated SQL Execution
        pred_csv_path = os.path.join(generated_dir, f"{args.dataset}_{idx}.csv")
        pred_df = None
        pred_err = None
        
        result_sql_path = os.path.join(subdir_path, "result.sql")
        if not os.path.exists(result_sql_path):
            sql_files = glob.glob(os.path.join(subdir_path, "*.sql"))
            if sql_files:
                result_sql_path = sql_files[0]
                
        pred_sql = ""
        if os.path.exists(result_sql_path):
            with open(result_sql_path, "r") as f:
                pred_sql = f.read().strip()
                
            pred_df, pred_err = execute_sql_with_timeout(pred_sql, mysql_creds)
            
        if pred_df is not None:
            pred_df.to_csv(pred_csv_path, index=False)
        else:
            pd.DataFrame().to_csv(pred_csv_path, index=False)
            
        execution_summary.append({
            "idx": idx,
            "instance_id": gold_entry.get("instance_id", f"{args.dataset}_{idx}"),
            "gold_error": gold_err,
            "pred_error": pred_err if pred_sql else "No generated SQL found"
        })
        
    with open(os.path.join(unified_dir, "execution_summary.json"), "w") as f:
        json.dump(execution_summary, f, indent=4)
        
    print(f"Saved executed CSVs to {unified_dir}")

if __name__ == "__main__":
    main()
