#!/usr/bin/env python3
"""
Evaluate ReFoRCE results on Beaver dataset by comparing with gold SQL execution results.

Behavior:

- Distinguishes between:
  * Gold SQL execution ERROR        -> treated as gold_execution_failed
  * Gold SQL executes but 0 rows    -> treated as an EMPTY gold result (empty DataFrame)

- If gold result is empty and ReFoRCE produced NO result.csv, this is treated as a MATCH
  (both gold and prediction are effectively empty).

Metrics tracked:

1) Accuracy including empty-gold matches:
   - Any case where we consider the prediction correct, including
     gold-empty + no-prediction, contributes to the numerator.

2) Accuracy excluding empty-gold queries:
   - Queries whose gold result is empty are excluded from the denominator.
   - Among the remaining queries (gold non-empty), we count how many are correct.
"""

import os
import json
import argparse
import pandas as pd
import mysql.connector
import re
from collections import Counter
from tqdm import tqdm
import threading
import time
import sys


def get_mysql_credentials(dataset, creds_path=None):
    """Get MySQL credentials from JSON file or environment variables.
    
    Priority: JSON file > environment variables.
    The database name is derived from db_id.
    """
    if creds_path and os.path.exists(creds_path):
        with open(creds_path, "r") as f:
            return json.load(f)
    
    # Fallback to environment variables
    host = os.environ.get("MYSQL_HOST")
    user = os.environ.get("MYSQL_USER")
    password = os.environ.get("MYSQL_PASSWORD")
    if dataset == "dw_real":
        database = "dw"
    else:
        database = dataset
    
    if host and user and password:
        return {
            "host": host,
            "user": user,
            "password": password,
            "database": database
        }
    
    return None

# Timeout for SQL execution (in seconds)
QUERY_TIMEOUT = 10  # 10 seconds
CONNECTION_TIMEOUT = 10

def execute_query_thread(sql, mysql_creds, result_holder):
    """Function to run in a separate thread."""
    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**mysql_creds, connection_timeout=CONNECTION_TIMEOUT)
        
        cursor = conn.cursor()
        cursor.execute(sql)
        
        # Some statements may not return a result set
        if cursor.description is None:
            rows = []
            columns = []
        else:
            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()
        
        cursor.close()
        conn.close()

        # 0 rows -> empty DataFrame (NOT an error)
        result_holder["df"] = pd.DataFrame(rows, columns=columns)
        result_holder["completed"] = True
        
    except mysql.connector.Error as err:
        result_holder["error"] = f"Database Error: {err}"
    except Exception as e:
        result_holder["error"] = f"Error: {str(e)}"
    finally:
        # Attempt cleanup if needed
        try:
            if cursor: cursor.close()
            if conn: conn.close()
        except:
            pass


def execute_gold_sql(gold_sql, mysql_creds, timeout=QUERY_TIMEOUT):
    """Execute gold SQL query and return results as DataFrame using threaded timeout."""
    
    result_holder = {"df": None, "error": None, "completed": False}
    
    t = threading.Thread(target=execute_query_thread, args=(gold_sql, mysql_creds, result_holder))
    t.daemon = True
    t.start()
    
    start_time = time.time()
    
    try:
        while t.is_alive():
            t.join(timeout=0.5)
            if time.time() - start_time > timeout:
                return None # Timeout treated as None in this script's logic mostly, or we could raise
        
        if result_holder["completed"]:
            return result_holder["df"]
        else:
            print(f"Error executing gold SQL: {result_holder['error']}")
            return None
            
    except KeyboardInterrupt:
        print("\n\n⚠️ Interrupted by user (Ctrl+C). Terminating...")
        sys.exit(1)



from itertools import permutations

def normalize_dataframe_values(df):
    """Normalize DataFrame values for comparison (to string, stripped).
    
    Does NOT sort rows or columns. Returns a list of lists (rows).
    """
    if df is None or df.empty:
        return []

    # Convert all columns to string for consistent comparison
    df = df.astype(str)

    # Strip whitespace
    # Use map instead of applymap for pandas compatibility if possible, or fallback
    try:
        df = df.map(lambda x: x.strip() if isinstance(x, str) else x)
    except AttributeError:
        df = df.applymap(lambda x: x.strip() if isinstance(x, str) else x)

    return df.values.tolist()



def compare_results(pred_df, gold_df):
    """Compare prediction and gold DataFrames robustly using SET logic.
    
    Checks if there exists a permutation of prediction columns such that
    the SET of rows matches the gold SET of rows.
    Ignores column names and duplicate rows.
    """
    # Handle None/Empty cases
    pred_empty = pred_df is None or pred_df.empty
    gold_empty = gold_df is None or gold_df.empty

    if pred_empty and gold_empty:
        return True, "Both empty"
    if pred_empty or gold_empty:
        return False, "One is empty, other is not"

    # Normalize values to list of lists
    pred_rows = normalize_dataframe_values(pred_df)
    gold_rows = normalize_dataframe_values(gold_df)

    # Convert gold to set of tuples for set comparison
    # This ignores duplicates in gold
    gold_rows_set = set(tuple(r) for r in gold_rows)

    # Check number of columns
    n_cols_pred = len(pred_rows[0]) if pred_rows else 0
    n_cols_gold = len(gold_rows[0]) if gold_rows else 0

    if n_cols_pred != n_cols_gold:
        return False, f"Column count mismatch: pred {n_cols_pred} vs gold {n_cols_gold}"

    # Try all column permutations of prediction
    n_cols = n_cols_pred
    
    # Fast path: Check if current order matches (ignoring column names)
    pred_rows_set_current = set(tuple(r) for r in pred_rows)
    if pred_rows_set_current == gold_rows_set:
        return True, "Match (values match, ignoring column names)"

    # if n_cols > 8:
    #     return False, f"Too many columns ({n_cols}) to check all permutations, and direct match failed."

    # # Permutation check
    # pred_cols = list(range(n_cols))
    
    # for perm in tqdm(permutations(pred_cols)):
    #     # Reorder each row according to permutation
    #     # perm is a tuple of indices, e.g., (1, 0)
    #     permuted_pred_rows_set = set()
    #     for row in pred_rows:
    #         permuted_row = tuple(row[i] for i in perm)
    #         permuted_pred_rows_set.add(permuted_row)
        
    #     # Compare sets
    #     if permuted_pred_rows_set == gold_rows_set:
    #         return True, "Match (found valid column permutation)"

    # return False, "Values mismatch (no column permutation matched)"
    
    return False, "Values mismatch"


def evaluate_beaver_results(beaver_questions_path, output_dir, mysql_creds_path, gold_result_dir=None, dataset=None):
    """Evaluate all ReFoRCE results against gold SQL."""

    # Load Beaver questions with gold SQL
    with open(beaver_questions_path, "r") as f:
        beaver_data = json.load(f)

    # Load MySQL credentials
    mysql_creds = get_mysql_credentials(dataset, mysql_creds_path)

    results = []
    error_gold_execution_failed = []  # list of question ids with gold SQL execution errors

    # Global counters
    total_score = 0                   # all matches, including empty-gold matches
    total_attempted = 0               # queries with usable gold (including empty-gold)
    total_generated = 0               # queries where ReFoRCE produced result.csv

    # For accuracy excluding empty-gold queries
    nonempty_gold_total = 0           # number of queries where gold result is non-empty
    nonempty_gold_score = 0           # number of correct queries among those

    print("=" * 80)
    print("Evaluating ReFoRCE Results on Beaver Dataset")
    print("=" * 80)

    for idx, item in enumerate(beaver_data):
        # Use the instance_id from the data file if available, otherwise construct it
        instance_id = item.get("id", f"beaver_{item['db']}_{idx:03d}")
        result_dir = os.path.join(output_dir, instance_id)
        result_csv_path = os.path.join(result_dir, "result.csv")

        print(f"\n[{idx+1}/{len(beaver_data)}] Evaluating {instance_id}")
        print(f"Question: {item['question'][:80]}...")

        # ------------------------------------------------------------------
        # 1) Obtain gold results (DataFrame), allowing empty DataFrame
        # ------------------------------------------------------------------
        gold_df = None

        # Try to load saved gold results first
        if gold_result_dir:
            gold_csv_path = os.path.join(gold_result_dir, f"{instance_id}.csv")
            if os.path.exists(gold_csv_path):
                try:
                    gold_df = pd.read_csv(gold_csv_path)
                    print(
                        f"  → Loaded gold result from saved file: "
                        f"{gold_df.shape[0]} rows, {gold_df.shape[1]} columns"
                    )
                except Exception as e:
                    print(f"  ⚠ Error reading saved gold result: {e}")

        # If gold result not found or failed to load, execute gold SQL
        if gold_df is None:
            gold_sql = item.get("sql", item.get("oracle_sql", item.get("gold_sql", "")))
            if not gold_sql:
                print("  ⚠ No gold SQL found")
                results.append(
                    {
                        "instance_id": instance_id,
                        "score": 0,
                        "status": "no_gold_sql",
                        "message": "No gold SQL in dataset",
                    }
                )
                continue

            if not mysql_creds:
                print("  ⚠ No MySQL credentials provided, cannot execute gold SQL")
                results.append(
                    {
                        "instance_id": instance_id,
                        "score": 0,
                        "status": "no_mysql_creds",
                        "message": "Cannot execute gold SQL without credentials",
                    }
                )
                continue

            print(f"  → Executing gold SQL (timeout: {QUERY_TIMEOUT}s)...")
            gold_df = execute_gold_sql(gold_sql, mysql_creds, timeout=QUERY_TIMEOUT)

            if gold_df is None:
                print("  ⚠ Gold SQL execution failed (error)")
                results.append(
                    {
                        "instance_id": instance_id,
                        "score": 0,
                        "status": "gold_execution_failed",
                        "message": "Gold SQL execution failed",
                        "gold_sql": gold_sql,
                    }
                )
                error_gold_execution_failed.append(instance_id)
                continue

            print(
                f"  → Gold result: {gold_df.shape[0]} rows, {gold_df.shape[1]} columns"
            )

        # At this point, gold_df is a valid DataFrame (possibly empty).
        total_attempted += 1
        gold_is_empty = gold_df.empty
        if not gold_is_empty:
            nonempty_gold_total += 1

        # ------------------------------------------------------------------
        # 2) Check if ReFoRCE generated a result
        # ------------------------------------------------------------------
        if not os.path.exists(result_csv_path):
            # No prediction file


            # Gold has non-empty result, but no prediction
            print(
                "  ✗ No result.csv found - Generation failed or max iterations reached "
                "(gold non-empty)"
            )
            score = 0
            total_score += score
            # Count as non-empty gold case, but incorrect
            results.append(
                {
                    "instance_id": instance_id,
                    "score": 0,
                    "status": "no_result",
                    "message": "No result.csv generated while gold is non-empty",
                }
            )
            continue

        # If we got here, we have both gold_df and a prediction CSV
        total_generated += 1

        # Load ReFoRCE result
        try:
            pred_df = pd.read_csv(result_csv_path)
            print(
                f"  → ReFoRCE result: {pred_df.shape[0]} rows, {pred_df.shape[1]} columns"
            )
        except Exception as e:
            print(f"  ✗ Error reading result.csv: {e}")
            results.append(
                {
                    "instance_id": instance_id,
                    "score": 0,
                    "status": "read_error",
                    "message": str(e),
                }
            )
            continue

        # ------------------------------------------------------------------
        # 3) Compare prediction vs gold
        # ------------------------------------------------------------------
        match, message = compare_results(pred_df, gold_df)
        score = 1 if match else 0
        total_score += score

        if not gold_is_empty:
            nonempty_gold_score += score

        if match:
            print("  ✅ MATCH! Score: 1")
        else:
            print("  ✗ No match. Score: 0")
            print(f"     Reason: {message}")

        results.append(
            {
                "instance_id": instance_id,
                "score": score,
                "status": "match" if match else "no_match",
                "message": message,
                "pred_shape": pred_df.shape,
                "gold_shape": gold_df.shape,
            }
        )

    # ----------------------------------------------------------------------
    # Summary
    # ----------------------------------------------------------------------
    print("\n" + "=" * 80)
    print("EVALUATION SUMMARY")
    print("=" * 80)
    print(f"Total queries: {len(beaver_data)}")
    print(
        f"Results generated: {total_generated} "
        f"({100 * total_generated / len(beaver_data):.1f}%)"
    )
    print(f"Successfully evaluated (with usable gold): {total_attempted}")
    print(f"Exact matches (including empty-gold matches): {total_score}")

    # Accuracy 1: of all queries with usable gold, counting empty-gold matches
    acc_including_empty = (
        100 * total_score / total_attempted if total_attempted > 0 else 0.0
    )
    print(
        f"Accuracy including empty-gold matches (over usable gold queries): "
        f"{acc_including_empty:.1f}%"
    )

    # Accuracy 2: skip queries whose gold is empty
    if nonempty_gold_total > 0:
        acc_excluding_empty = 100 * nonempty_gold_score / nonempty_gold_total
        print(
            f"Accuracy excluding empty-gold queries: {acc_excluding_empty:.1f}% "
            f"({nonempty_gold_score}/{nonempty_gold_total})"
        )
    else:
        acc_excluding_empty = 0.0
        print("Accuracy excluding empty-gold queries: N/A (no non-empty gold cases)")

    print("=" * 80)

    # Save detailed results
    results_file = os.path.join(output_dir, "evaluation_results.json")
    with open(results_file, "w") as f:
        json.dump(
            {
                "summary": {
                    "total_queries": len(beaver_data),
                    "results_generated": total_generated,
                    "exact_matches_including_empty": total_score,
                    "accuracy_including_empty": acc_including_empty / 100.0,
                    "nonempty_gold_total": nonempty_gold_total,
                    "nonempty_gold_correct": nonempty_gold_score,
                    "accuracy_excluding_empty": acc_excluding_empty / 100.0,
                    # all question id with gold SQL execution errors
                    "error_gold_execution_failed": error_gold_execution_failed
                },
                "details": results,
            },
            f,
            indent=2,
        )

    print(f"\nDetailed results saved to: {results_file}")

    # Print breakdown by status
    print("\nBreakdown by status:")
    status_counts = Counter([r["status"] for r in results])
    for status, count in status_counts.most_common():
        print(f"  {status}: {count}")

    return results


def main():
    parser = argparse.ArgumentParser(description="Evaluate ReFoRCE results on Beaver dataset")
    parser.add_argument(
        "--beaver_questions",
        type=str,
        required=True,
        help="Path to Beaver questions file with gold SQL",
    )
    parser.add_argument(
        "--output_dir",
        type=str,
        required=True,
        help="Directory containing ReFoRCE output",
    )
    parser.add_argument(
        "--gold_result_dir",
        type=str,
        default=None,
        help="Directory containing saved gold SQL execution results (optional)",
    )
    parser.add_argument(
        "--mysql_creds",
        type=str,
        default=None,
        help="Path to MySQL credentials JSON file (optional, falls back to env vars)",
    )
    parser.add_argument(
        "--dataset",
        type=str,
        default=None,
        help="Database ID / name for MySQL connection (derived from data if omitted)",
    )

    args = parser.parse_args()

    evaluate_beaver_results(
        args.beaver_questions, args.output_dir, args.mysql_creds, args.gold_result_dir, args.dataset
    )


if __name__ == "__main__":
    main()