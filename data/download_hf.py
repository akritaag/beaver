import argparse
import os
import json
import random
from datasets import load_dataset

def save_json(data, path):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def parse_if_string(val, default_type=list):
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return default_type()
    return val if val is not None else default_type()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", default="100")
    args = parser.parse_args()
    sample_size = int(args.sample)

    print("Loading datasets from Hugging Face...")
    try:
        query_ds = load_dataset('beaverbench/beaver-query')
        table_ds = load_dataset('beaverbench/beaver-table')
    except Exception as e:
        print(f"Error loading datasets: {e}")
        return

    base_data_dir = "./data"
    os.makedirs(base_data_dir, exist_ok=True)

    splits = query_ds.keys()
    print(f"Available splits in queries: {list(splits)}")

    for split in splits:
        print(f"\nProcessing split: {split}")
        split_dir = os.path.join(base_data_dir, split)
        os.makedirs(split_dir, exist_ok=True)

        rerank_file = "reranked_preds.json"
        target_rerank_path = os.path.join(split_dir, rerank_file)

        # 2. Process Queries
        raw_queries = list(query_ds[split])
        processed_queries = []
        for entry in raw_queries:
            mapped_entry = {
                "id": entry.get("id"),
                "question": entry.get("question"),
                "db": entry.get("db"),
                "sql": entry.get("sql"),
                "tables": parse_if_string(entry.get("tables")),
                "column_mapping": parse_if_string(entry.get("column_mapping"), dict),
                "join_keys": parse_if_string(entry.get("join_keys")),
                "domain_knowledge": parse_if_string(entry.get("domain_knowledge")),
                "sub_questions": parse_if_string(entry.get("sub_questions")),
                "sub_sqls": parse_if_string(entry.get("sub_sqls")),
                "category": entry.get("category"),
                "detailed_category": entry.get("detailed_category"),
                "contains_domain_knowledge": entry.get("contains_domain_knowledge")
            }
            processed_queries.append(mapped_entry)

        query_path = os.path.join(split_dir, "dev.json")
        save_json(processed_queries, query_path)
        print(f"Saved {len(processed_queries)} queries to {query_path}")

        # 3. Process Tables with dictionary structure
        # If split is 'dw_real', use tables from 'dw' as per user request
        table_source_split = split
        if split == 'dw_real':
            table_source_split = 'dw'
            
        if table_source_split in table_ds:
            raw_tables = list(table_ds[table_source_split])
            processed_tables = {} # DICTIONARY keyed by table name
            for entry in raw_tables:
                table_name = entry.get("table_name")
                mapped_table = {
                    "db": entry.get("db"),
                    "table_name": table_name,
                    "column_names": parse_if_string(entry.get("column_names")),
                    "column_types": parse_if_string(entry.get("column_types")),
                    "example_rows": parse_if_string(entry.get("example_rows")),
                    "example_columns": parse_if_string(entry.get("example_columns"))
                }
                processed_tables[table_name] = mapped_table
                
            table_path = os.path.join(split_dir, "dev_tables.json")
            save_json(processed_tables, table_path)
            print(f"Saved {len(processed_tables)} tables to {table_path} (from {table_source_split} split)")
        else:
            print(f"No table data found for split {table_source_split} in beaver-table")

        # 4. Sampling Logic (Seed 77, sample 5)
        if len(processed_queries) > 0:
            random.seed(77)
            indices = list(range(len(processed_queries)))
            sample_size = min(sample_size, len(processed_queries))
            sampled_indices = random.sample(indices, sample_size)
            
            # Load reranked_preds if restored
            reranked_preds = {}
            if os.path.exists(target_rerank_path):
                with open(target_rerank_path, 'r') as f:
                    reranked_preds = json.load(f)
            
            sampled_data_with_preds = []
            for idx in sampled_indices:
                item = processed_queries[idx]
                # If reranked_preds exists, add top_k_tables
                if reranked_preds:
                    # Use the full id directly after migration
                    item_id = item.get("id", "")
                    preds = reranked_preds.get(item_id, [])
                    # retrieve top-15 tables
                    item["top_k_tables"] = preds[:15]
                sampled_data_with_preds.append(item)
            
            sample_path = os.path.join(split_dir, "dev_sampled.json")
            save_json(sampled_data_with_preds, sample_path)
            
            # indices_path = os.path.join(split_dir, "dev_sampled_indices.json")
            # save_json(sampled_indices, indices_path)
            print(f"Sampled {sample_size} queries to {sample_path}")

    print("\nDownload and sampling complete.")

if __name__ == "__main__":
    main()
