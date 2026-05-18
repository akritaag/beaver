import os
import sys
import json
import argparse
import glob
import re
from tqdm import tqdm

# Add the eval directory to path
eval_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(eval_dir)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input_dir", type=str, required=True, help="Directory containing generated outputs")
    parser.add_argument("--run_name", type=str, required=True, help="Run name to save the unified output")
    parser.add_argument("--gold_file", type=str, required=True, help="Path to dev.json or dev_sampled.json")
    parser.add_argument("--dataset", type=str, required=True, help="Dataset name, e.g. dw")
    args = parser.parse_args()
    
    args.input_dir = args.input_dir.rstrip('/')
    
    baseline_name = os.path.basename(os.path.dirname(os.path.abspath(__file__)))
    unified_dir = os.path.join(eval_dir, "unified-output", baseline_name, args.run_name)
    generated_dir = os.path.join(unified_dir, "generated")
    gold_dir = os.path.join(unified_dir, "gold")
    
    os.makedirs(generated_dir, exist_ok=True)
    os.makedirs(gold_dir, exist_ok=True)
    
    with open(args.gold_file, 'r') as f:
        gold_data = json.load(f)
        
    # Create ID mapping for efficient lookup
    id_to_entry = {entry['id']: entry for entry in gold_data}
    
    subdirs = sorted(glob.glob(os.path.join(args.input_dir, "*")))
    subdirs = [d for d in subdirs if os.path.isdir(d)]
    
    print(f"Processing {len(subdirs)} subdirectories for {baseline_name}...")
    for subdir_path in tqdm(subdirs):
        subdir_name = os.path.basename(subdir_path)
        
        # Use ID-based lookup
        if subdir_name in id_to_entry:
            gold_entry = id_to_entry[subdir_name]
        else:
            # Fallback for index-based directory names (e.g., ends in _001)
            match = re.search(r'_(\d+)$', subdir_name)
            if match:
                idx = int(match.group(1))
                if idx < len(gold_data):
                    gold_entry = gold_data[idx]
                else:
                    continue
            else:
                continue
        
        real_id = gold_entry['id']
        gold_sql = gold_entry.get("sql", gold_entry.get("oracle_sql", gold_entry.get("gold_sql", "")))
        
        # 1. Save Gold SQL
        gold_sql_path = os.path.join(gold_dir, f"{real_id}.sql")
        with open(gold_sql_path, "w") as f:
            f.write(gold_sql)
            
        # 2. Save Generated SQL
        pred_sql_path = os.path.join(generated_dir, f"{real_id}.sql")
        
        # Look for SQL result file in the subdirectory
        result_sql_path = os.path.join(subdir_path, "result.sql")
        if not os.path.exists(result_sql_path):
            # Check for other .sql files in the subdirectory
            sql_files = glob.glob(os.path.join(subdir_path, "*.sql"))
            if sql_files:
                # Use the first .sql file found (common in some baselines like DIN-SQL)
                result_sql_path = sql_files[0]
                
        pred_sql = ""
        if os.path.exists(result_sql_path):
            with open(result_sql_path, "r") as f:
                pred_sql = f.read().strip()
        
        with open(pred_sql_path, "w") as f:
            f.write(pred_sql)
            
    print(f"Saved SQL files to {unified_dir}")

if __name__ == "__main__":
    main()
