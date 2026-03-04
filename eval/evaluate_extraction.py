import os
import sys
import json
import argparse
import glob
import re
import time
from concurrent.futures import ThreadPoolExecutor
from openai import OpenAI

from utils.llm import GPTChat

EXTRACTION_PROMPT = """
You are an expert SQL parser. Your task is to extract specific components from the provided SQL query into a JSON format.
You must extract ONLY objects that exist in the database schema. Do NOT extract intermediate tables (CTEs), subquery aliases, or derived tables.

1. "tables": A list of all DATABASE table names referenced in the query (FROM, JOIN). Ignore CTE names and subquery aliases.
2. "columns": A list of all fully qualified DATABASE columns used (SELECT, WHERE, GROUP BY, HAVING, ORDER BY, JOIN conditions). Format: "TABLE_NAME.COLUMN_NAME". Ignore aliases, map back to real table names. If table name is unknown, use the alias or inferred name, but ensure it maps to a real table.
3. "join_keys": A list of join conditions between DATABASE tables. Format: "TABLE1.COL1 = TABLE2.COL2". Order of operands does not matter. Do NOT include joins involving CTEs.
4. "domain_knowledge": A list of domain-specific predicates found in WHERE or HAVING clauses. Format: "TABLE.COL <OP> VALUE". Examples: "INSTANCES.DELETED = 0", "INSTANCES.HOST = 'prime-35'". Do NOT include join conditions here.

SQL Query:
<<<
{sql}
>>>

Output JSON format:
{{
  "tables": ["TABLE1", "TABLE2", ...],
  "columns": ["TABLE1.COL1", "TABLE2.COL2", ...],
  "join_keys": ["TABLE1.ID = TABLE2.REF_ID", ...],
  "domain_knowledge": ["TABLE.STATUS = 'active'", ...]
}}
"""

def normalize_join_key(join_str):
    """Normalize join key string to set of operands for order-independent comparison."""
    # Remove spaces
    join_str = join_str.replace(" ", "")
    parts = join_str.split("=")
    if len(parts) == 2:
        return frozenset([parts[0].upper(), parts[1].upper()])
    return frozenset([join_str.upper()])

def compute_f1(generated_set, gold_set):
    """Compute F1 score between two sets of strings."""
    # Normalize to upper case
    gen_norm = set([s.upper() for s in generated_set])
    gold_norm = set([s.upper() for s in gold_set])
    
    tp = len(gen_norm.intersection(gold_norm))
    fp = len(gen_norm - gold_norm)
    fn = len(gold_norm - gen_norm)
    
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0
    f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0
    
    return f1

def extract_predicates_from_evidence(evidence_list):
    """
    Extract predicates from evidence strings. 
    Evidence strings are often: "Host 'foo' is predicated by 'instances.host = foo'"
    We try to extract the part inside the last pair of quotes, or just the raw string if simpler.
    """
    predicates = []
    for ev in evidence_list:
        # Regex to find "table.col = val" pattern often quoted at the end
        # Example: ... predicated by "instances.host = 'blaze8-12'"
        # We need to handle both " and ' wrappers, but " seems more common in this dataset.
        match = re.search(r'predicated by "(.*?)"', ev)
        if match:
             predicates.append(match.group(1))
        else:
             match = re.search(r"predicated by '(.*?)'", ev)
             if match:
                 predicates.append(match.group(1))
    return predicates

def compute_predicate_f1(generated_set, gold_set):
    """
    Compute F1 for domain knowledge predicates, handling table name mismatches.
    Gold often uses 'TABLE.' placeholder or no table name, while generated has specific table name.
    Strategy: Strip table name prefix (everything before first dot) from both sides and compare.
    """
    def remove_table_prefix(s):
        if "." in s:
            return s.split(".", 1)[1]
        return s

    # Normalize to upper case and strip table prefix
    gen_processed = set([remove_table_prefix(s.upper().strip()) for s in generated_set])
    gold_processed = set([remove_table_prefix(s.upper().strip()) for s in gold_set])
    
    tp = len(gen_processed.intersection(gold_processed))
    fp = len(gen_processed - gold_processed)
    fn = len(gold_processed - gen_processed)
    
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0
    f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0
    
    return f1

def evaluate_single_entry(subdir_path, gold_data, chat_client, output_base_dir):
    result = {
        "subdir": os.path.basename(subdir_path),
        "status": "error",
        "generated_sql": "",
        "extraction": {},
        "scores": {}
    }

    try:
        subdir_name = os.path.basename(subdir_path)
        
        # Resume Check
        output_dir = os.path.join(output_base_dir, subdir_name)
        extraction_file = os.path.join(output_dir, "extraction.json")
        call_llm = True
        if os.path.exists(extraction_file):
            try:
                with open(extraction_file, "r") as f:
                    existing_data = json.load(f)
                if existing_data.get("status") == "success":
                    call_llm = False
            except Exception:
                pass # If error reading, re-compute

        match = re.search(r'_(\d+)$', subdir_name)
        if not match: return result
        index = int(match.group(1))
        if index >= len(gold_data): return result

        gold_entry = gold_data[index]
        
        # Gold Standards
        gold_tables = gold_entry.get("gold_tables", [])
        
        # Flatten mapping dict
        gold_columns = []
        if "mapping" in gold_entry:
            for k, v in gold_entry["mapping"].items():
                gold_columns.extend(v)
                
        # Flatten join_keys list of lists -> list of strings "A=B"
        gold_joins = []
        if "join_keys" in gold_entry:
            for jk in gold_entry["join_keys"]:
                # jk is often [col1, col2]
                if isinstance(jk, list) and len(jk) == 2:
                    gold_joins.append(f"{jk[0]} = {jk[1]}")
                elif isinstance(jk, str):
                    gold_joins.append(jk)

        # Domain knowledge: evidence lists
        gold_predicates = []
        gold_predicates.extend(extract_predicates_from_evidence(gold_entry.get("internal_evidence", [])))
        gold_predicates.extend(extract_predicates_from_evidence(gold_entry.get("external_evidence", [])))

        # Read Generated SQL
        result_sql_path = os.path.join(subdir_path, "result.sql")
        
        # Fallback: look for any .sql file
        if not os.path.exists(result_sql_path):
            sql_files = glob.glob(os.path.join(subdir_path, "*.sql"))
            if sql_files:
                result_sql_path = sql_files[0]
            else:
                result["status"] = "no_sql"
                return result
        
        with open(result_sql_path, "r") as f:
            generated_sql = f.read().strip()
            result["generated_sql"] = generated_sql

        # Extraction via LLM
        if call_llm:
            prompt = EXTRACTION_PROMPT.format(sql=generated_sql)
            extraction = chat_client.get_json_response(prompt)
        else:
            extraction = existing_data.get("extraction")
        
        if not extraction:
            result["status"] = "llm_error"
            return result
            
        result["extraction"] = extraction
        
        # Compute F1s
        # 1. Tables
        f1_tables = compute_f1(extraction.get("tables", []), gold_tables)
        
        # 2. Columns
        f1_columns = compute_f1(extraction.get("columns", []), gold_columns)
        
        # 3. Join Keys (Requires normalization)
        gen_joins_norm = set([normalize_join_key(j) for j in extraction.get("join_keys", [])])
        gold_joins_norm = set([normalize_join_key(j) for j in gold_joins])
        
        tp_j = len(gen_joins_norm.intersection(gold_joins_norm))
        fp_j = len(gen_joins_norm - gold_joins_norm)
        fn_j = len(gold_joins_norm - gen_joins_norm)
        
        prec_j = tp_j / (tp_j + fp_j) if (tp_j + fp_j) > 0 else 0
        rec_j = tp_j / (tp_j + fn_j) if (tp_j + fn_j) > 0 else 0
        f1_joins = 2 * (prec_j * rec_j) / (prec_j + rec_j) if (prec_j + rec_j) > 0 else 0

        # 4. Domain Knowledge (Predicates)
        gen_predicates = set(extraction.get("domain_knowledge", []))

        clean_gen_predicates = set()
        for p in gen_predicates:
            if ' = ' in p:
                clean_gen_predicates.add(p)
            elif ' IN ' in p:
                p = p.replace(' IN (', ' = ').replace(')', '')
                clean_gen_predicates.add(p)
            else:
                pass
        cleaned_gold_predicates = []
        for p in gold_predicates:
            # update p to remove ' IN (' and ')' in the list
            p = p.replace(' IN (', ' = ').replace(')', '')
            cleaned_gold_predicates.append(p)


        f1_domain = compute_predicate_f1(clean_gen_predicates, cleaned_gold_predicates)
        
        scores = {
            "f1_tables": f1_tables,
            "f1_columns": f1_columns,
            "f1_join_keys": f1_joins,
            "f1_domain_knowledge": f1_domain
        }
        result["scores"] = scores
        result["status"] = "success"
        
        # Save extraction detail
        output_dir = os.path.join(output_base_dir, subdir_name)
        os.makedirs(output_dir, exist_ok=True)
        with open(os.path.join(output_dir, "extraction.json"), "w") as f:
            json.dump(result, f, indent=4)
            
        return result

    except Exception as e:
        print(f"{subdir_path}: {e}")
        return result

def run_task(subdir, gold_data, model, output_dir):
    client = GPTChat(model=model)
    return evaluate_single_entry(subdir, gold_data, client, output_dir)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input_dir", type=str, default="postprocessed_data/beaver_sp_opt2_beaver_sp_opt2_CTX-200/RESULTS_MODEL-anthropic/claude-sonnet-4.5-SQL")
    parser.add_argument("--gold_file", type=str, default="../../data/sp/dev_sampled.json")
    parser.add_argument("--model", type=str, default="gpt-5-mini")
    parser.add_argument("--baseline_method", type=str, choices=["reforce", "dinsql", "dailsql"], default="reforce", help="Baseline method (controls output path structure)")
    parser.add_argument("--output_dir", type=str, default="output/extractions")
    parser.add_argument("--num_workers", type=int, default=40)
    args = parser.parse_args()

    print(f"Loading gold from {args.gold_file}")
    with open(args.gold_file) as f: gold_data = json.load(f)

    subdirs = sorted(glob.glob(os.path.join(args.input_dir, "*")))
    subdirs = [d for d in subdirs if os.path.isdir(d)]
    print(f"Subdirs: {len(subdirs)}")

    

    if args.baseline_method == "reforce":
        output_dir = os.path.join(args.output_dir, args.input_dir.split('/')[-1])
        output_dir = os.path.join("Reforce", output_dir)
    elif args.baseline_method == "dinsql":
        if 'qwen' in args.input_dir or 'minimax' in args.input_dir or 'claude' in args.input_dir:
            output_dir = os.path.join(args.output_dir, '/'.join(args.input_dir.split('/')[-2:]))
        else:
            output_dir = os.path.join(args.output_dir, args.input_dir.split('/')[-1])
        output_dir = os.path.join("dinsql", output_dir)
    elif args.baseline_method == "dailsql":
        if 'qwen' in args.input_dir or 'minimax' in args.input_dir or 'claude' in args.input_dir:
            output_dir = os.path.join(args.output_dir, '/'.join(args.input_dir.split('/')[-3:]))
        else:
            output_dir = os.path.join(args.output_dir, '/'.join(args.input_dir.split('/')[-2:]))
        output_dir = os.path.join("dailsql", output_dir)
    os.makedirs(output_dir, exist_ok=True)
    
    results = []
    
    try:
        from tqdm import tqdm
        pbar = tqdm(total=len(subdirs))
    except ImportError:
        pbar = None

    with ThreadPoolExecutor(max_workers=args.num_workers) as executor:
        futures = [executor.submit(run_task, d, gold_data, args.model, output_dir) for d in subdirs]
        for f in futures:
            res = f.result()
            results.append(res)
            if pbar: pbar.update(1)

    if pbar: pbar.close()

    # Calculate Totals for Summary
    # Summary should just be a json that save each generated query's table... and the F1 scores
    
    avg_scores = {
        "avg_f1_tables": 0.0,
        "avg_f1_columns": 0.0,
        "avg_f1_join_keys": 0.0,
        "avg_f1_domain_knowledge": 0.0
    }
    
    valid_results = [r for r in results if r["status"] == "success"]
    n = len(valid_results)
    
    if n > 0:
        avg_scores["avg_f1_tables"] = sum(r["scores"]["f1_tables"] for r in valid_results) / n
        avg_scores["avg_f1_columns"] = sum(r["scores"]["f1_columns"] for r in valid_results) / n
        avg_scores["avg_f1_join_keys"] = sum(r["scores"]["f1_join_keys"] for r in valid_results) / n
        avg_scores["avg_f1_domain_knowledge"] = sum(r["scores"]["f1_domain_knowledge"] for r in valid_results) / n
        
    summary = {
        "averages": avg_scores,
        "details": results
    }
    
    with open(os.path.join(output_dir, "summary.json"), "w") as f:
        json.dump(summary, f, indent=4)
        
    print("\nDone.")
    print(json.dumps(avg_scores, indent=2))

if __name__ == "__main__":
    main()
