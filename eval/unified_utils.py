import os
import json
import pandas as pd
import mysql.connector
import threading
import time
import sys

# Load environment variables if dotenv exists
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), '../.env'))
except ImportError:
    pass

# Constants
QUERY_TIMEOUT = 10
CONNECTION_TIMEOUT = 10


def get_mysql_credentials(db_id, creds_path=None):
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
    if db_id == "neutron":
        db_id = "csail_stata_neutron"
    elif db_id == "nova":
        db_id = "csail_stata_nova"
    elif db_id == "dw_real":
        db_id = "dw"
    
    if host and user and password:
        return {
            "host": host,
            "user": user,
            "password": password,
            "database": db_id
        }
    
    return None


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
    """Execute a SQL query and return results as DataFrame using threaded timeout."""
    
    result_holder = {"df": None, "error": None, "completed": False}
    
    t = threading.Thread(target=execute_query_thread, args=(sql, mysql_creds, result_holder))
    t.daemon = True
    t.start()
    
    start_time = time.time()
    
    try:
        while t.is_alive():
            t.join(timeout=0.5)
            if time.time() - start_time > timeout:
                return None, "Timeout exceeded"
        
        if result_holder["completed"]:
            return result_holder["df"], None
        else:
            return None, result_holder["error"]
            
    except KeyboardInterrupt:
        print("\n\n⚠️ Interrupted by user (Ctrl+C). Terminating...")
        sys.exit(1)


def compare_results(pred_df, gold_df):
    """Compare two DataFrames (prediction and gold).
    
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

    return False, "Values mismatch"


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
