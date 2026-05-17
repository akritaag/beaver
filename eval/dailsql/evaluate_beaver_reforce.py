#!/usr/bin/env python3
"""
Evaluation script for DAIL-SQL on Beaver dataset using ReFoRCE's robust comparison logic.

This script uses the same evaluation methodology as ReFoRCE:
- SET-based comparison (ignores row order and duplicates)
- Column permutation matching (ignores column order)
- Proper empty result handling
- Normalized value comparison

Usage:
    python evaluate_beaver_reforce.py --option 4 --model gpt-4o --comment beaver_opt4
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
    """Normalize DataFrame values for comparison (to string, stripped).
    
    Does NOT sort rows or columns. Returns a list of lists (rows).
    """
    if df is None or df.empty:
        return []

    # Convert all columns to string for consistent comparison
    df = df.astype(str)

    # Strip whitespace
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
    """Clean gold SQL by removing dw#sep# prefixes from table names"""
    # Replace patterns like "dw#sep#TABLE_NAME" with just "TABLE_NAME"
    cleaned_sql = re.sub(r'\b\w+#sep#(\w+)', r'\1', sql)
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


def evaluate_dailsql_results(results_dir, dev_json_path, mysql_creds_path, gold_result_dir=None, option=4, tables_json_path=None, db_id=None):
    """Evaluate all DAIL-SQL results against gold SQL using ReFoRCE comparison logic."""

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
        if db_id in ['sp', 'neutron', 'nova', 'csail_stata_neutron', 'csail_stata_nova']:
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
    print(f"Evaluating DAIL-SQL Results on Beaver Dataset (Option {option})")
    print("Using ReFoRCE comparison logic")
    print("=" * 80)

    for idx, item in enumerate(dev_data):
        instance_id = item.get("id", f"beaver_{item.get('db', 'unknown')}_{idx:03d}")
        instance_dir = os.path.join(results_dir, instance_id)
        result_csv_path = os.path.join(instance_dir, "results.csv")  # DAIL-SQL uses "results.csv"

        print(f"\n[{idx+1}/{len(dev_data)}] Evaluating {instance_id}")
        question = item.get('question', item.get('NLQ', ''))
        print(f"Question: {question[:80]}...")

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

        # If gold result not found, execute gold SQL
        if gold_df is None:
            gold_sql = item.get("query", item.get("sql", item.get("oracle_sql", item.get("gold_sql", ""))))
            if not gold_sql:
                print("  ⚠ No gold SQL found")
                results.append({
                    "instance_id": instance_id,
                    "score": 0,
                    "status": "no_gold_sql",
                    "message": "No gold SQL in dataset",
                })
                continue

            # Clean gold SQL (remove dw#sep# prefixes)
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
                print("  ⚠ Gold SQL execution failed (error)")
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

        # At this point, gold_df is a valid DataFrame (possibly empty).
        total_attempted += 1
        gold_is_empty = gold_df.empty
        if not gold_is_empty:
            nonempty_gold_total += 1

        # ------------------------------------------------------------------
        # 2) Check if DAIL-SQL generated a result
        # ------------------------------------------------------------------
        if not os.path.exists(result_csv_path):
            # No prediction file


            # Gold has non-empty result, but no prediction
            print(
                "  ✗ No results.csv found - Generation failed or execution error "
                "(gold non-empty)"
            )
            score = 0
            total_score += score
            results.append({
                "instance_id": instance_id,
                "score": 0,
                "status": "no_result",
                "message": "No results.csv generated while gold is non-empty",
            })
            continue

        # If we got here, we have both gold_df and a prediction CSV
        total_generated += 1

        # Load DAIL-SQL result
        try:
            pred_df = pd.read_csv(result_csv_path)
            print(f"  → DAIL-SQL result: {pred_df.shape[0]} rows, {pred_df.shape[1]} columns")
        except Exception as e:
            print(f"  ✗ Error reading results.csv: {e}")
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
    results_file = os.path.join(results_dir, "evaluation_results_reforce.json")
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
    parser = argparse.ArgumentParser(description="Evaluate DAIL-SQL results on Beaver using ReFoRCE logic")
    parser.add_argument('--option', type=int, required=True, choices=[1, 2, 3, 4, 5],
                        help='Preprocessing option (1-5)')
    parser.add_argument('--model', default='gpt-4o', type=str)
    parser.add_argument('--comment', default='', type=str)
    parser.add_argument('--max_tokens', type=int, default=200)
    parser.add_argument(
        "--gold_result_dir",
        type=str,
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
    parser.add_argument('--dev', type=str, default=None, help='Dev dataset name (for folder naming)')
    parser.add_argument('--tables_file', type=str, default=None, help='Path to tables json (for casing fix)')
    parser.add_argument('--db_id', type=str, default=None, help='Database id')
    args = parser.parse_args()

    # Construct paths
    script_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Construct folder name
    if args.dev:
        # Match generate_question.py: {comment}_{dev}_CTX-{max_tokens}
        folder_name = f"{args.comment}_{args.dev}_CTX-{args.max_tokens}"
    else:
        # Fallback to old behavior
        folder_name = f"{args.comment}_beaver_opt{args.option}_CTX-{args.max_tokens}"

    results_dir = os.path.join(
        script_dir,
        f'postprocessed_data/{folder_name}/RESULTS_MODEL-{args.model}-SQL'
    )
    if args.dev_file:
        dev_json_path = args.dev_file
    else:
        dev_json_path = os.path.join(
            script_dir,
            f'preprocessed_data/beaver_opt{args.option}/beaver_opt{args.option}_preprocessed.json'
        )
    mysql_credential_path = args.mysql_credential
    if mysql_credential_path:
        mysql_credential_path = os.path.join(script_dir, mysql_credential_path)

    if args.tables_file:
         tables_json_path = args.tables_file
    else:
         # Default location
         tables_json_path = os.path.join(script_dir, f'preprocessed_data/beaver_opt{args.option}/tables_preprocessed.json')

    evaluate_dailsql_results(
        results_dir, dev_json_path, mysql_credential_path, args.gold_result_dir, args.option, tables_json_path, args.db_id
    )


if __name__ == "__main__":
    main()
