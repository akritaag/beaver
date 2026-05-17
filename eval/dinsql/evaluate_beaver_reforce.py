#!/usr/bin/env python3
"""
Evaluation script for DIN-SQL on Beaver dataset using ReFoRCE's robust comparison logic.

This script uses the same evaluation methodology as ReFoRCE:
- SET-based comparison (ignores row order and duplicates)
- Column permutation matching (ignores column order)
- Proper empty result handling
- Normalized value comparison

Usage:
    python evaluate_beaver_reforce.py --dev beaver_opt4
"""

import os
import json
import argparse
import pandas as pd
import mysql.connector
import re
import signal
from collections import Counter
from pathlib import Path

import threading
import time
import sys


def get_mysql_credentials(db_id, creds_path=None):
    """Get MySQL credentials from JSON file or environment variables."""
    if creds_path and os.path.exists(creds_path):
        with open(creds_path, "r") as f:
            return json.load(f)
    host = os.environ.get("MYSQL_HOST")
    user = os.environ.get("MYSQL_USER")
    password = os.environ.get("MYSQL_PASSWORD")
    if db_id == "neutron":
        db_id = "csail_stata_neutron"
    elif db_id == "nova":
        db_id = "csail_stata_nova"
    if host and user and password:
        return {"host": host, "user": user, "password": password, "database": db_id}
    return None

# Timeout for SQL execution (in seconds)
QUERY_TIMEOUT = 10  # 10 seconds
CONNECTION_TIMEOUT = 10 # 10 seconds for connection

# Global execution result holder
# No longer needed as we pass local dicts



def execute_query_thread(sql, mysql_creds, result_holder):
    """Function to run in a separate thread."""
    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**mysql_creds, connection_timeout=CONNECTION_TIMEOUT)
        
        cursor = conn.cursor()
        cursor.execute(sql)
        
        if cursor.description is None:
            rows = []
            columns = []
        else:
            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()
        
        cursor.close()
        conn.close()

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


def execute_sql_with_timeout(sql, mysql_creds, timeout=QUERY_TIMEOUT):
    """Execute SQL in a background thread to stay responsive to interrupts."""
    
    # Store result in a mutable dictionary
    result_holder = {"df": None, "error": None, "completed": False}
    
    # Create a daemon thread - this will die when main program exits
    t = threading.Thread(target=execute_query_thread, args=(sql, mysql_creds, result_holder))
    t.daemon = True 
    t.start()
    
    # Wait for the thread, checking for interrupts
    start_time = time.time()
    
    try:
        while t.is_alive():
            t.join(timeout=0.5)  # Wait in short bursts to remain responsive to Ctrl+C
            
            # Check for timeout
            if time.time() - start_time > timeout:
                return None, f"Timeout: Query exceeded {timeout} seconds"
                
        if result_holder["completed"]:
            return result_holder["df"], None
        else:
            # If thread finished but completed is false, there was an error caught inside
            return None, result_holder["error"] if result_holder["error"] else "Unknown execution failure"
            
    except KeyboardInterrupt:
        print("\n\n⚠️ Interrupted by user (Ctrl+C). Terminating...")
        sys.exit(1)


def normalize_dataframe_values(df):
    """Normalize DataFrame values for comparison (to string, stripped)."""
    if df is None or df.empty:
        return []

    df = df.astype(str)

    try:
        df = df.map(lambda x: x.strip() if isinstance(x, str) else x)
    except AttributeError:
        df = df.applymap(lambda x: x.strip() if isinstance(x, str) else x)

    return df.values.tolist()


def compare_results(pred_df, gold_df):
    """Compare prediction and gold DataFrames robustly using SET logic."""
    pred_empty = pred_df is None or pred_df.empty
    gold_empty = gold_df is None or gold_df.empty

    if pred_empty and gold_empty:
        return True, "Both empty"
    if pred_empty or gold_empty:
        return False, "One is empty, other is not"

    pred_rows = normalize_dataframe_values(pred_df)
    gold_rows = normalize_dataframe_values(gold_df)

    gold_rows_set = set(tuple(r) for r in gold_rows)

    n_cols_pred = len(pred_rows[0]) if pred_rows else 0
    n_cols_gold = len(gold_rows[0]) if gold_rows else 0

    if n_cols_pred != n_cols_gold:
        return False, f"Column count mismatch: pred {n_cols_pred} vs gold {n_cols_gold}"

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


def clean_gold_sql(sql):
    """Clean gold SQL by removing dw#sep# prefixes and lowercasing table references if needed"""
    # Remove dw#sep# or db#sep# prefixes
    cleaned_sql = re.sub(r'\b\w+#sep#(\w+)', r'\1', sql)
    # Simple heuristic: lowercase known table names if they are uppercase
    # For now, just lowercase the entire SQL? No, that affects string literals.
    # Better approach which ReFoRCE uses: get table names from schema and replace specific uppercase instances
    return cleaned_sql

def robust_clean_gold_sql(sql, schema_tables):
    """
    Clean gold SQL more robustly:
    1. Remove prefixes like dw#sep#
    2. Replace Uppercase Table Names with lowercase if they exist in schema_tables (which are lowercase)
    """
    # 1. Remove prefixes
    sql = re.sub(r'\b\w+#sep#(\w+)', r'\1', sql)
    
    # 2. Case-insensitive replacement of table names
    # Sort tables by length desc to avoid partial replacements (e.g. replace 'room_properties' before 'room')
    sorted_tables = sorted(schema_tables, key=len, reverse=True)
    
    for table in sorted_tables:
        # regex to match table name case-insensitively, ensure word boundary
        # We replace with the lowercase version from schema_tables
        pattern = re.compile(r'\b' + re.escape(table) + r'\b', re.IGNORECASE)
        sql = pattern.sub(table, sql)
        
    return sql


def evaluate_dinsql_results(output_dir, dev_json_path, mysql_creds_path, gold_result_dir=None, tables_json_path=None, db_id="dw"):
    """Evaluate all DIN-SQL results against gold SQL using ReFoRCE comparison logic."""

    # Load dev data with gold SQL
    with open(dev_json_path, "r") as f:
        dev_data = json.load(f)
    
    # Load schema tables if provided to help with casing
    schema_tables = []
    if tables_json_path and os.path.exists(tables_json_path):
        with open(tables_json_path, "r") as f:
            tables_data = json.load(f)
            
        if isinstance(tables_data, list):
            # Spider format: list of databases
            for db in tables_data:
                schema_tables.extend(db['table_names_original'])
        elif isinstance(tables_data, dict):
            # Beaver format: dict of tables
            for table_key, table_info in tables_data.items():
                if 'table_name_original' in table_info:
                    schema_tables.append(table_info['table_name'])
                else:
                     # fallback to key if needed
                     schema_tables.append(table_key)
        
        # Lowercase all for consistent matching
        if db_id in ["sp", "neutron", "nova", "csail_stata_neutron", "csail_stata_nova"]:
            schema_tables = [str(t).lower() for t in schema_tables]

    # Load MySQL credentials
    mysql_creds = get_mysql_credentials(db_id, mysql_creds_path)

    results = []
    error_gold_execution_failed = []

    # Global counters
    total_score = 0
    total_attempted = 0
    total_generated = 0

    # For accuracy excluding empty-gold queries
    nonempty_gold_total = 0
    nonempty_gold_score = 0

    print("=" * 80)
    print(f"Evaluating DIN-SQL Results on Beaver Dataset")
    print("Using ReFoRCE comparison logic")
    print("=" * 80)

    for idx, item in enumerate(dev_data):
        instance_id = item.get("id", f"beaver_dw_{idx:03d}")
        # if instance_id == "beaver_dw_025":
        #     print("Skipping beaver_dw_025")
        #     continue
        instance_dir = os.path.join(output_dir, instance_id)
        
        # DIN-SQL stores predicted SQL in predicted_0.sql, we need to execute it
        predicted_sql_file = os.path.join(instance_dir, "predicted_0.sql")
        result_csv_path = os.path.join(instance_dir, "result.csv")

        print(f"\n[{idx+1}/{len(dev_data)}] Evaluating {instance_id}")
        question = item.get('question', '')[:80]
        print(f"Question: {question}...")

        # ------------------------------------------------------------------
        # 1) Obtain gold results (DataFrame)
        # ------------------------------------------------------------------
        gold_df = None

        # Try to load saved gold results first
        if gold_result_dir:
            temp_instance_id = instance_id.replace("beaver_dw_", "beaver_dw_opt5_")
            gold_csv_path = os.path.join(gold_result_dir, f"{temp_instance_id}.csv")
            if os.path.exists(gold_csv_path):
                try:
                    gold_df = pd.read_csv(gold_csv_path)
                    print(f"  → Loaded gold result from saved file: {gold_df.shape[0]} rows, {gold_df.shape[1]} columns")
                except Exception as e:
                    print(f"  ⚠ Error reading saved gold result: {e}")

        # If gold result not found, execute gold SQL
        if gold_df is None:
            gold_sql = item.get("gold_sql", item.get("query", item.get("sql", "")))
            if not gold_sql:
                print("  ⚠ No gold SQL found")
                results.append({
                    "instance_id": instance_id,
                    "score": 0,
                    "status": "no_gold_sql",
                    "message": "No gold SQL in dataset",
                })
                continue

            # Clean gold SQL
            if schema_tables:
                 gold_sql = robust_clean_gold_sql(gold_sql, schema_tables)
            else:
                 gold_sql = clean_gold_sql(gold_sql)

            if not mysql_creds:
                print("  ⚠ No MySQL credentials provided, cannot execute gold SQL")
                results.append({
                    "instance_id": instance_id,
                    "score": 0,
                    "status": "no_mysql_creds",
                    "message": "Cannot execute gold SQL without credentials",
                })
                continue

            print(f"  → Executing gold SQL (timeout: {QUERY_TIMEOUT}s)...")
            gold_df, gold_error = execute_sql_with_timeout(gold_sql, mysql_creds, timeout=QUERY_TIMEOUT)

            if gold_df is None:
                print(f"  ⚠ Gold SQL execution failed: {gold_error}")
                results.append({
                    "instance_id": instance_id,
                    "score": 0,
                    "status": "gold_execution_failed",
                    "message": gold_error or "Gold SQL execution failed",
                    "gold_sql": gold_sql,
                })
                error_gold_execution_failed.append(instance_id)
                continue

            print(f"  → Gold result: {gold_df.shape[0]} rows, {gold_df.shape[1]} columns")

        total_attempted += 1
        gold_is_empty = gold_df.empty
        if not gold_is_empty:
            nonempty_gold_total += 1

        # ------------------------------------------------------------------
        # 2) Check if DIN-SQL generated a result
        # ------------------------------------------------------------------
        # First check if predicted SQL file exists
        if not os.path.exists(predicted_sql_file):


            print("  ✗ No predicted_0.sql found - Generation failed (gold non-empty)")
            results.append({
                "instance_id": instance_id,
                "score": 0,
                "status": "no_result",
                "message": "No predicted SQL file generated while gold is non-empty",
            })
            continue

        # Read the predicted SQL
        with open(predicted_sql_file, 'r') as f:
            predicted_sql = f.read().strip()
            if not predicted_sql.endswith(";"):
                predicted_sql += ";"

        # Execute the predicted SQL if result.csv doesn't exist
        if not os.path.exists(result_csv_path):
            print(f"  → Executing DIN-SQL query (timeout: {QUERY_TIMEOUT}s)...")
            # Clean predicted SQL casing if needed
            if schema_tables:
                 predicted_sql = robust_clean_gold_sql(predicted_sql, schema_tables)
            else:
                 predicted_sql = clean_gold_sql(predicted_sql)

            pred_df, pred_error = execute_sql_with_timeout(predicted_sql, mysql_creds, timeout=QUERY_TIMEOUT)
            
            if pred_df is None:
                print(f"  ✗ DIN-SQL execution error: {pred_error}")
                # Save error
                with open(result_csv_path, 'w') as f:
                    f.write(f"ERROR,{pred_error}")
                results.append({
                    "instance_id": instance_id,
                    "score": 0,
                    "status": "execution_error",
                    "message": pred_error,
                })
                continue
            
            # Save to result.csv
            pred_df.to_csv(result_csv_path, index=False)
            print(f"  → DIN-SQL result: {pred_df.shape[0]} rows, {pred_df.shape[1]} columns")
            total_generated += 1
        else:
            # Load existing result.csv
            try:
                pred_df = pd.read_csv(result_csv_path)
                print(f"Loaded result.csv: {result_csv_path}")
                # Check if it's an error file
                if 'ERROR' in pred_df.columns:
                    print(f"  ✗ Previous execution had error")
                    results.append({
                        "instance_id": instance_id,
                        "score": 0,
                        "status": "execution_error",
                        "message": "Previous execution error",
                    })
                    continue
                print(f"  → DIN-SQL result: {pred_df.shape[0]} rows, {pred_df.shape[1]} columns")
                total_generated += 1
            except Exception as e:
                print(f"  ✗ Error reading result.csv: {e}")
                results.append({
                    "instance_id": instance_id,
                    "score": 0,
                    "status": "read_error",
                    "message": str(e),
                })
                continue

        # ------------------------------------------------------------------
        # 3) Compare prediction vs gold using ReFoRCE comparison logic
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

        results.append({
            "instance_id": instance_id,
            "score": score,
            "status": "match" if match else "no_match",
            "message": message,
            "pred_shape": tuple(pred_df.shape),
            "gold_shape": tuple(gold_df.shape),
        })

    # ----------------------------------------------------------------------
    # Summary
    # ----------------------------------------------------------------------
    # ----------------------------------------------------------------------
    # Summary
    # ----------------------------------------------------------------------
    print("\n" + "=" * 80)
    print("EVALUATION SUMMARY")
    print("=" * 80)
    print(f"Total queries: {len(dev_data)}")
    print(
        f"Results generated: {total_generated} "
        f"({100 * total_generated / len(dev_data):.1f}%)"
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
    results_file = os.path.join(output_dir, "evaluation_results_reforce.json")
    with open(results_file, "w") as f:
        json.dump(
            {
                "summary": {
                    "total_queries": len(dev_data),
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
    parser = argparse.ArgumentParser(description="Evaluate DIN-SQL results on Beaver using ReFoRCE logic")
    parser.add_argument('--dev', type=str, required=True, help='Dataset name (e.g., beaver_opt4)')
    parser.add_argument(
        "--output_dir",
        type=str,
        default=None,
        help="Custom output directory containing predictions",
    )
    parser.add_argument(
        "--gold_result_dir",
        type=str,
        # default='/shared/ssd_14T/home/weiyueli/text2sql/ReFoRCE/methods/ReFoRCE/output/gpt-5-mini-beaver-opt5-gold-20260120-014722',
        default=None,
        help="Directory containing saved gold SQL execution results (optional)",
    )
    parser.add_argument(
        "--mysql_credential",
        type=str,
        default=None,
        help="Path to MySQL credentials JSON file (optional, falls back to env vars)",
    )
    parser.add_argument('--dev_file', type=str, default=None, help='Path to preprocessed dev questions json')
    parser.add_argument('--tables_file', type=str, default=None, help='Path to tables json (for casing fix)')
    parser.add_argument('--db_id', type=str, default="dw", help='Database ID (dw or sp)')

    args = parser.parse_args()

    # Construct paths
    script_dir = Path(__file__).parent
    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        output_dir = script_dir / 'output' / args.dev
        
    if args.dev_file:
        dev_json_path = Path(args.dev_file)
    else:
        dev_json_path = script_dir / 'preprocessed_data' / args.dev / f'{args.dev}_preprocessed.json'

    # Try to find tables file if not provided
    if args.tables_file:
         tables_json_path = args.tables_file
    else:
         # Default location for preprocessed tables
         tables_json_path = script_dir / 'preprocessed_data' / args.dev / 'tables_preprocessed.json'
        
    mysql_credential_path = None
    if args.mysql_credential:
        mysql_credential_path = str(script_dir / args.mysql_credential)

    if not output_dir.exists():
        print(f"✗ Output directory not found: {output_dir}")
        print("Please run DIN-SQL-beaver-v2.py first to generate predictions.")
        return

    evaluate_dinsql_results(
        str(output_dir), str(dev_json_path), mysql_credential_path, args.gold_result_dir, str(tables_json_path), args.db_id
    )


if __name__ == "__main__":
    main()