import re

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

def eval_extraction_output(extraction_output, gold_entry):
    # Gold Standards
    gold_tables = gold_entry.get("tables", [])
    
    # Flatten mapping dict
    gold_columns = []
    if "mapping" in gold_entry:
        for k, v in gold_entry["column_mapping"].items():
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
    gold_predicates.extend(extract_predicates_from_evidence(gold_entry.get("domain_knowledge", [])))

    # Compute F1s
    # 1. Tables
    f1_tables = compute_f1(extraction_output.get("tables", []), gold_tables)
    
    # 2. Columns
    f1_columns = compute_f1(extraction_output.get("columns", []), gold_columns)
    
    # 3. Join Keys (Requires normalization)
    gen_joins_norm = set([normalize_join_key(j) for j in extraction_output.get("join_keys", [])])
    gold_joins_norm = set([normalize_join_key(j) for j in gold_joins])
    
    tp_j = len(gen_joins_norm.intersection(gold_joins_norm))
    fp_j = len(gen_joins_norm - gold_joins_norm)
    fn_j = len(gold_joins_norm - gen_joins_norm)
    
    prec_j = tp_j / (tp_j + fp_j) if (tp_j + fp_j) > 0 else 0
    rec_j = tp_j / (tp_j + fn_j) if (tp_j + fn_j) > 0 else 0
    f1_joins = 2 * (prec_j * rec_j) / (prec_j + rec_j) if (prec_j + rec_j) > 0 else 0

    # 4. Domain Knowledge (Predicates)
    gen_predicates = set(extraction_output.get("domain_knowledge", []))

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

    return {
        "table_retrieval_f1": f1_tables,
        "column_mapping_f1": f1_columns,
        "join_key_f1": f1_joins,
        "domain_knowledge_f1": f1_domain
    }