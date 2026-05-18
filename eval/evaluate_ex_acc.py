import json
import argparse
import pandas as pd
from tqdm import tqdm
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv('../.env')

from utils.ex_acc import get_mysql_credentials, execute_sql_with_timeout, compare_results
from utils.utils import write_json

def main():
    parser = argparse.ArgumentParser(description="Unified evaluation script for text-to-SQL baselines")
    parser.add_argument("--dataset", type=str, required=True, help="Dataset name for MySQL credentials, e.g. dw")
    parser.add_argument("--input_dir", type=str, required=True, help="Path to the unified output directory containing generated/ and gold/ subdirectories")
    args = parser.parse_args()
    
    generated_dir =  Path(args.input_dir) / "generated"
    gold_dir = Path(args.input_dir) / "gold"
    
    if not generated_dir.exists() or not gold_dir.exists():
        print(f"Error: Could not find 'generated' or 'gold' directories inside {args.input_dir}")
        sys.exit(1)
        
    mysql_creds = get_mysql_credentials(args.dataset)
    if not mysql_creds:
        print(f"Error: Could not load MySQL credentials for dataset {args.dataset}")
        sys.exit(1)
        
    gold_files = sorted(gold_dir.glob("*.sql"))
    
    total_queries = len(gold_files)
    total_attempted = total_queries
    
    total_score = 0
    nonempty_gold_total = 0
    nonempty_gold_score = 0
    
    results = []
    
    print(f"Evaluating {total_queries} queries from {args.input_dir}...")
    
    for gold_sql_path in tqdm(gold_files):
        filename = gold_sql_path.name
        pred_sql_path = generated_dir / filename
        
        # 1. Execute Gold SQL
        with open(gold_sql_path, "r") as f:
            gold_sql = f.read().strip()
        
        gold_df, gold_err = execute_sql_with_timeout(gold_sql, mysql_creds)
        if gold_df is None:
            gold_df = pd.DataFrame() # Treat error as empty for comparison? 
            # Actually, usually if gold fails, it's a gold_execution_failed
            
        # 2. Execute Predicted SQL
        pred_sql = ""
        if pred_sql_path.exists():
            with open(pred_sql_path, "r") as f:
                pred_sql = f.read().strip()
        
        pred_df = None
        pred_err = None
        if pred_sql:
            pred_df, pred_err = execute_sql_with_timeout(pred_sql, mysql_creds)
            
        if pred_df is None:
            pred_df = pd.DataFrame()
            
        # Compare
        match, msg = compare_results(pred_df, gold_df)
        score = 1 if match else 0
        
        total_score += score
        gold_is_empty = gold_df.empty
        
        if not gold_is_empty:
            nonempty_gold_total += 1
            nonempty_gold_score += score
            
        results.append({
            "file": filename,
            "match": match,
            "score": score,
            "message": msg,
            "gold_empty": gold_is_empty,
            "pred_empty": pred_df.empty,
            "gold_error": gold_err,
            "pred_error": pred_err
        })
    
    acc_including_empty = (100 * total_score / total_attempted) if total_attempted > 0 else 0.0
    if nonempty_gold_total > 0:
        acc_excluding_empty = 100 * nonempty_gold_score / nonempty_gold_total
    
    # Save results summary
    summary_data = {
        "metrics": {
            "total_evaluated": total_attempted,
            "exact_matches": total_score,
            "accuracy_including_empty": acc_including_empty,
            "nonempty_gold_total": nonempty_gold_total,
            "nonempty_gold_score": nonempty_gold_score,
            "accuracy_excluding_empty": acc_excluding_empty if nonempty_gold_total > 0 else None
        },
        "details": sorted(results, key=lambda x: x["file"])
    }
    
    summary_path = Path(args.input_dir) / "summary_ex_acc.json"
    write_json(summary_data, summary_path)
    
    print(f"\nEvaluation Complete.")
    print(f"Summary saved to: {summary_path}")
    print(json.dumps(summary_data['metrics'], indent=2))

if __name__ == "__main__":
    main()
