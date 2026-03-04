#!/usr/bin/env python3
"""
Evaluate LLM-Enterprise results on Beaver dataset by comparing with gold SQL execution results.
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
from itertools import permutations

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
except ImportError:
    pass

def get_mysql_credentials(dataset, creds_path=None):
    """Get MySQL credentials from JSON file or environment variables."""
    if creds_path and os.path.exists(creds_path):
        with open(creds_path, "r") as f:
            return json.load(f)
    host = os.environ.get("MYSQL_HOST")
    user = os.environ.get("MYSQL_USER")
    password = os.environ.get("MYSQL_PASSWORD")
    
    db_id = dataset
    if db_id == "neutron":
        db_id = "csail_stata_neutron"
    elif db_id == "nova":
        db_id = "csail_stata_nova"
        
    if host and user and password:
        return {"host": host, "user": user, "password": password, "database": db_id}
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

def execute_sql_with_timeout(sql, mysql_creds, timeout=QUERY_TIMEOUT):
    """Execute SQL query and return results as DataFrame using threaded timeout."""
    
    result_holder = {"df": None, "error": None, "completed": False}
    
    t = threading.Thread(target=execute_query_thread, args=(sql, mysql_creds, result_holder))
    t.daemon = True
    t.start()
    
    start_time = time.time()
    
    try:
        while t.is_alive():
            t.join(timeout=0.5)
            if time.time() - start_time > timeout:
                return None, "timeout" # Timeout treated as None in this script's logic mostly, or we could raise
        
        if result_holder["completed"]:
            return result_holder["df"], None
        else:
            # print(f"Error executing SQL: {result_holder['error']}")
            return None, result_holder['error']
            
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
    # Use map instead of applymap for pandas compatibility if possible, or fallback
    try:
        df = df.map(lambda x: x.strip() if isinstance(x, str) else x)
    except AttributeError:
        df = df.applymap(lambda x: x.strip() if isinstance(x, str) else x)

    return df.values.tolist()


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
    if not schema_tables:
        return sql
        
    # Sort tables by length desc to avoid partial replacements (e.g. replace 'room_properties' before 'room')
    sorted_tables = sorted(schema_tables, key=len, reverse=True)
    
    for table in sorted_tables:
        # regex to match table name case-insensitively, ensure word boundary
        # We replace with the lowercase version from schema_tables
        pattern = re.compile(r'\b' + re.escape(table) + r'\b', re.IGNORECASE)
        sql = pattern.sub(table, sql)
        
    return sql

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
    if pred_empty:
        return False, "Prediction is empty, Gold is not"
    if gold_empty:
        return False, "Gold is empty, Prediction is not"

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
    
    return False, "Values mismatch"

def evaluate_predictions(output_dir, gold_path, mysql_creds_path, tables_path=None, dataset=None):
    """Evaluate generated SQL predictions against gold SQL directly from output subdirs."""
    
    # Load gold data
    print(f"Loading gold data from {gold_path}...")
    with open(gold_path, "r") as f:
        gold_data = json.load(f)
        
    # Load MySQL credentials
    fallback_dataset_name = dataset if dataset else "unknown"
    if gold_data and isinstance(gold_data, list) and len(gold_data) > 0:
        db_id = gold_data[0].get("db_id", "unknown")
        if fallback_dataset_name == "unknown":
            fallback_dataset_name = db_id

    print(f"Loading MySQL credentials for dataset {fallback_dataset_name}...")
    mysql_creds = get_mysql_credentials(fallback_dataset_name, mysql_creds_path)
    
    if not mysql_creds:
        print("Error: Could not load MySQL credentials. Please provide valid path or set env vars.")
        return

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    results = []
    total_score = 0
    total_attempted = 0
    execution_errors = 0
    
    # New metrics
    nonempty_gold_total = 0
    nonempty_gold_score = 0
    error_gold_execution_failed = []
    
    print("=" * 80)
    print("Evaluating LLM-Enterprise Results")
    print("=" * 80)

    # Source file statistics
    source_stats = {}
    
    # Determine DB ID and load schema tables if available
    db_id = "unknown"
    if gold_data and isinstance(gold_data, list) and len(gold_data) > 0:
        db_id = gold_data[0].get("db_id", "unknown")
    
    if not tables_path:
        # Try to infer it from gold_path
        gold_dir = os.path.dirname(gold_path)
        inferred_tables_path = os.path.join(gold_dir, "dev_tables.json")
        if os.path.exists(inferred_tables_path):
            tables_path = inferred_tables_path
            
    schema_tables = []
    if tables_path and os.path.exists(tables_path):
        print(f"Loading tables schema from {tables_path}")
        with open(tables_path, "r") as f:
            tables_data = json.load(f)
            
        if isinstance(tables_data, list):
            # Spider format: list of databases
            for db in tables_data:
                schema_tables.extend(db.get('table_names_original', []))
        elif isinstance(tables_data, dict):
            # Beaver format: dict of tables
            for table_key, table_info in tables_data.items():
                if 'table_name_original' in table_info:
                    schema_tables.append(table_info['table_name_original'])
                else:
                     schema_tables.append(table_key)
        
        # Lowercase all for consistent matching if DB needs it
        if db_id in ["sp", "neutron", "nova", "csail_stata_neutron", "csail_stata_nova", "sp_real", "sp_easy", "dw_real"]:
            schema_tables = [str(t).lower() for t in schema_tables]
            print(f"Loaded and lowercased {len(schema_tables)} schema tables.")
        else:
            print(f"Loaded {len(schema_tables)} schema tables.")

    # and count missing predictions as failures.
    
    for idx, gold_item in enumerate(tqdm(gold_data)):
        idx_str = str(idx)
        
        # Get gold SQL
        gold_sql = gold_item.get("sql", gold_item.get("oracle_sql", gold_item.get("gold_sql", "")))
        if not gold_sql:
             # Skip or count as error? DIN-SQL skips if no gold SQL.
             continue
             
        # Clean gold SQL
        # Using robust cleaning with schema tables (lowercasing tables if DB requires it)
        gold_sql = robust_clean_gold_sql(gold_sql, schema_tables)

        fallback_dataset_name = dataset if dataset else db_id
        instance_id = gold_item.get("instance_id", f"beaver_{fallback_dataset_name}_{idx:03d}")
        source_file = gold_item.get("source_file", "unknown")

        if source_file not in source_stats:
            source_stats[source_file] = {"total": 0, "correct": 0}
            
        instance_dir = os.path.join(output_dir, instance_id)
        predicted_sql_path = os.path.join(instance_dir, "predicted_0.sql")
        
        # Check if prediction exists
        if not os.path.exists(predicted_sql_path):
            # Missing prediction
            results.append({
                "index": idx,
                "instance_id": instance_id,
                "source_file": source_file,
                "score": 0,
                "status": "no_prediction",
                "message": "No prediction generated",
                "pred_sql": "",
                "gold_sql": gold_sql
            })
            total_attempted += 1
            source_stats[source_file]["total"] += 1
            continue

        # Get prediction SQL
        with open(predicted_sql_path, "r") as f:
            pred_sql = f.read()
        # Clean prediction SQL similarly
        pred_sql = robust_clean_gold_sql(pred_sql, schema_tables)
        
        # Execute Gold SQL
        gold_df, gold_err = execute_sql_with_timeout(gold_sql, mysql_creds)
        
        if gold_err:
            # print(f"  ⚠ Gold SQL execution failed: {gold_err}")
            results.append({
                "index": idx,
                "instance_id": instance_id,
                "source_file": source_file,
                "score": 0,
                "status": "gold_error",
                "message": gold_err,
                "pred_sql": pred_sql,
                "gold_sql": gold_sql
            })
            # total_attempted += 1 # Count as attempted but failed due to gold error -> Dinsql doesn't count if gold execution fails?
            # Actually dinsql adds to error_gold_execution_failed and continues, 
            # effectively excluding it from total_attempted logic (which counts usable gold).
            # Let's align:
            error_gold_execution_failed.append(instance_id)
            source_stats[source_file]["total"] += 1
            continue
            
        # Gold execution successful
        gold_is_empty = gold_df.empty
        if not gold_is_empty:
             nonempty_gold_total += 1

        # Execute Prediction SQL
        pred_df, pred_err = execute_sql_with_timeout(pred_sql, mysql_creds)
        
        if pred_err:
            # print(f"  ✗ Prediction SQL execution failed: {pred_err}")
            results.append({
                "index": idx,
                "instance_id": instance_id,
                "source_file": source_file,
                "score": 0,
                "status": "pred_error",
                "message": pred_err,
                "pred_sql": pred_sql,
                "gold_sql": gold_sql
            })
            execution_errors += 1
            total_attempted += 1
            source_stats[source_file]["total"] += 1
            continue

        # Compare Results
        match, message = compare_results(pred_df, gold_df)
        score = 1 if match else 0
        total_score += score
        total_attempted += 1
        
        if not gold_is_empty:
            nonempty_gold_score += score
        
        source_stats[source_file]["total"] += 1
        source_stats[source_file]["correct"] += score
            
        results.append({
            "index": idx,
            "instance_id": instance_id,
            "source_file": source_file,
            "score": score,
            "status": "match" if match else "no_match",
            "message": message,
            "pred_sql": pred_sql,
            "gold_sql": gold_sql
        })

    # Summary
    print("\n" + "=" * 80)
    print("EVALUATION SUMMARY")
    print("=" * 80)
    print(f"Total queries in gold: {len(gold_data)}")
    
    fallback_dataset_name = dataset if dataset else db_id
    results_in_gold = sum(1 for idx, item in enumerate(gold_data) if os.path.exists(os.path.join(output_dir, item.get("instance_id", f"beaver_{fallback_dataset_name}_{idx:03d}"), "predicted_0.sql")))
    print(
        f"Results generated: {results_in_gold}"
        f" ({results_in_gold/len(gold_data)*100:.1f}%)"
    )
    print(f"Successfully evaluated (with usable gold): {total_attempted}")
    print(f"Execution errors (prediction): {execution_errors}")
    print(f"Exact matches (including empty-gold matches): {total_score}")
    
    acc_including_empty = (100 * total_score / total_attempted) if total_attempted > 0 else 0.0
    print(f"Accuracy including empty-gold matches: {acc_including_empty:.1f}%")

    if nonempty_gold_total > 0:
        acc_excluding_empty = 100 * nonempty_gold_score / nonempty_gold_total
        print(
            f"Accuracy excluding empty-gold queries: {acc_excluding_empty:.1f}% "
            f"({nonempty_gold_score}/{nonempty_gold_total})"
        )
    else:
        acc_excluding_empty = 0.0
        print("Accuracy excluding empty-gold queries: N/A (no non-empty gold cases)")

    
    print("-" * 80)
    print("Breakdown by Source File:")
    print(f"{'Source File':<40} | {'Correct':<10} | {'Total':<10} | {'Accuracy':<10}")
    print("-" * 80)
    
    source_stats_out = {}
    for src, stats in source_stats.items():
        src_total = stats["total"]
        src_correct = stats["correct"]
        src_acc = (100 * src_correct / src_total) if src_total > 0 else 0.0
        
        print(f"{src:<40} | {src_correct:<10} | {src_total:<10} | {src_acc:.1f}%")
        
        source_stats_out[src] = {
            "correct": src_correct,
            "total": src_total,
            "accuracy": src_acc
        }

    print("=" * 80)
    
    # Save detailed results
    results_filename = f"evaluation_results_summary.json"
    results_file = os.path.join(output_dir, results_filename)
    with open(results_file, "w") as f:
        json.dump({
            "summary": {
                "total_queries": len(gold_data),
                "total_evaluated": total_attempted,
                "execution_errors": execution_errors,
                "exact_matches_including_empty": total_score,
                "accuracy_including_empty": acc_including_empty / 100.0,
                "nonempty_gold_total": nonempty_gold_total,
                "nonempty_gold_correct": nonempty_gold_score,
                "accuracy_excluding_empty": acc_excluding_empty / 100.0,
                "error_gold_execution_failed": error_gold_execution_failed,
                "by_source_file": source_stats_out
            },
            "details": results
        }, f, indent=2)
        
    print(f"\nDetailed results saved to: {results_file}")

def main():
    parser = argparse.ArgumentParser(description="Evaluate LLM-Enterprise results")
    parser.add_argument("--output_dir", type=str, required=True, help="Directory containing prediction subdirectories")
    parser.add_argument("--gold", type=str, required=True, help="Path to gold dataset JSON (list of dicts)")
    parser.add_argument("--mysql_creds", type=str, default=None, help="Path to MySQL credentials JSON (optional, fallback to .env)")
    parser.add_argument("--dataset", type=str, default=None, help="Dataset name to use for fallback instance ID")
    parser.add_argument("--tables", type=str, default=None, help="Path to tables JSON file (optional)")
    
    args = parser.parse_args()
    
    evaluate_predictions(args.output_dir, args.gold, args.mysql_creds, args.tables, args.dataset)

if __name__ == "__main__":
    main()
