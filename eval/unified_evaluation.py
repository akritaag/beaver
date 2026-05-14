import os
import json
import argparse
import glob
import pandas as pd
from tqdm import tqdm
import sys

# Add the eval directory to path to import unified_utils
sys.path.append(os.path.dirname(__file__))
from unified_utils import get_mysql_credentials, execute_sql_with_timeout, compare_results

def main():
    parser = argparse.ArgumentParser(description="Unified evaluation script for text-to-SQL baselines")
    parser.add_argument("--unified_dir", type=str, required=True, help="Path to the unified output directory containing generated/ and gold/ subdirectories")
    parser.add_argument("--dataset", type=str, required=True, help="Dataset name for MySQL credentials, e.g. dw")
    args = parser.parse_args()
    
    args.unified_dir = args.unified_dir.rstrip('/')
    generated_dir = os.path.join(args.unified_dir, "generated")
    gold_dir = os.path.join(args.unified_dir, "gold")
    
    if not os.path.exists(generated_dir) or not os.path.exists(gold_dir):
        print(f"Error: Could not find 'generated' or 'gold' directories inside {args.unified_dir}")
        sys.exit(1)
        
    mysql_creds = get_mysql_credentials(args.dataset)
    if not mysql_creds:
        print(f"Error: Could not load MySQL credentials for dataset {args.dataset}")
        sys.exit(1)
        
    gold_files = sorted(glob.glob(os.path.join(gold_dir, "*.sql")))
    
    total_queries = len(gold_files)
    total_attempted = total_queries
    
    total_score = 0
    nonempty_gold_total = 0
    nonempty_gold_score = 0
    
    results = []
    
    print(f"Evaluating {total_queries} queries from {args.unified_dir}...")
    
    for gold_sql_path in tqdm(gold_files):
        filename = os.path.basename(gold_sql_path)
        base_name = filename.replace(".sql", "")
        pred_sql_path = os.path.join(generated_dir, filename)
        
        # 1. Execute Gold SQL
        with open(gold_sql_path, "r") as f:
            gold_sql = f.read().strip()
        
        gold_df, gold_err = execute_sql_with_timeout(gold_sql, mysql_creds)
        if gold_df is None:
            gold_df = pd.DataFrame() # Treat error as empty for comparison? 
            # Actually, usually if gold fails, it's a gold_execution_failed
            
        # 2. Execute Predicted SQL
        pred_sql = ""
        if os.path.exists(pred_sql_path):
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
        
    print("\n" + "=" * 80)
    print("UNIFIED EVALUATION SUMMARY")
    print("=" * 80)
    print(f"Total queries evaluated: {total_attempted}")
    print(f"Exact matches (including empty-gold matches): {total_score}")
    
    acc_including_empty = (100 * total_score / total_attempted) if total_attempted > 0 else 0.0
    print(f"Accuracy including empty-gold matches: {acc_including_empty:.1f}%")

    if nonempty_gold_total > 0:
        acc_excluding_empty = 100 * nonempty_gold_score / nonempty_gold_total
        print(f"Accuracy excluding empty-gold queries: {acc_excluding_empty:.1f}% ({nonempty_gold_score}/{nonempty_gold_total})")
    else:
        print("Accuracy excluding empty-gold queries: N/A (no non-empty gold cases)")
    
    print("=" * 80)
    
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
    
    summary_path = os.path.join(args.unified_dir, "evaluation_summary.json")
    with open(summary_path, "w") as f:
        json.dump(summary_data, f, indent=4)
        
    print(f"Detailed evaluation summary saved to {summary_path}")

if __name__ == "__main__":
    main()
