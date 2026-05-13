import os
import json
import argparse
import glob
import pandas as pd
from tqdm import tqdm
import sys

# Add the eval directory to path to import unified_utils
sys.path.append(os.path.dirname(__file__))
from unified_utils import compare_results

def main():
    parser = argparse.ArgumentParser(description="Unified evaluation script for text-to-SQL baselines")
    parser.add_argument("--unified_dir", type=str, required=True, help="Path to the unified output directory containing generated/ and gold/ subdirectories")
    args = parser.parse_args()
    
    args.unified_dir = args.unified_dir.rstrip('/')
    generated_dir = os.path.join(args.unified_dir, "generated")
    gold_dir = os.path.join(args.unified_dir, "gold")
    
    if not os.path.exists(generated_dir) or not os.path.exists(gold_dir):
        print(f"Error: Could not find 'generated' or 'gold' directories inside {args.unified_dir}")
        sys.exit(1)
        
    gold_files = glob.glob(os.path.join(gold_dir, "*.csv"))
    
    total_queries = len(gold_files)
    total_attempted = total_queries
    
    total_score = 0
    nonempty_gold_total = 0
    nonempty_gold_score = 0
    
    results = []
    
    print(f"Evaluating {total_queries} queries from {args.unified_dir}...")
    
    for gold_csv_path in tqdm(gold_files):
        filename = os.path.basename(gold_csv_path)
        pred_csv_path = os.path.join(generated_dir, filename)
        
        # Load DataFrames
        try:
            gold_df = pd.read_csv(gold_csv_path)
        except Exception:
            gold_df = pd.DataFrame()
            
        try:
            pred_df = pd.read_csv(pred_csv_path)
        except Exception:
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
            "pred_empty": pred_df.empty
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
