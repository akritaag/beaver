import numpy as np
import json
from sql_metadata import Parser
from tiger_utils import read_json, write_json
import os
import pandas as pd
from dataclasses import dataclass
import re

def create_directory(path):
    if not os.path.exists(path):
        os.makedirs(path)

def sig_fig(x):
    return float(f'{float(f"{x:.3g}"):g}')

def format_join(join_pair):
    """Format join pair for display."""
    left, right = join_pair
    return f"{left.split('.')[0]} joins {right.split('.')[0]} on {left} = {right}"

def format_table(table_name, corpus_tables, use_instance: bool, corpus_markdowns=None, trim=False):
    rows = corpus_tables[table_name]["rows"]
    if trim:
        for row in rows:
            for i in range(len(row)):
                if isinstance(row[i], str):
                    # 500
                    row[i] = row[i][:500]

    cols = corpus_tables[table_name]["column_names_original"]

    if corpus_markdowns is None:
        df = pd.DataFrame(rows, columns=cols)
        df_md = df.to_markdown(index=False)
    else:
        df_md = corpus_markdowns[table_name]

    table_string = []

    table_string.append(f'Table name: {table_name}')
    table_string.append(f"Example table content:\n{df_md}")

    if use_instance:
        instances = corpus_tables[table_name]["instances"]
        table_string.append(f"Top-10 most occurring values for each column:")
        for col_idx, col_name in enumerate(cols):
            _instance = " | ".join([str(x) for x in instances[col_idx]])
            table_string.append(f"{col_name}: {_instance}")

    table_string = "\n".join(table_string)

    return table_string

def format_tables(tables: list[str], corpus_tables, use_instance, corpus_markdowns=None):
    p = []
    for t in tables:
        p.append(format_table(t, corpus_tables, use_instance, corpus_markdowns))
    p = "\n\n".join(p)
    return p

def sql_to_tables(sql: str, db_id: str) -> list[str]:
    gold_ts = Parser(sql).tables
    gold_ts = [gold_t.upper() for gold_t in gold_ts]
    for i in range(len(gold_ts)):
        if "." in gold_ts[i]:
            gold_ts[i] = gold_ts[i].split(".")[1]
    gold_ts = [f"{db_id}#sep#{t}" if "#sep#" not in t.lower() else t for t in gold_ts]
    gold_ts = list(set(gold_ts))
    return gold_ts

@dataclass
class EvalConfig:
    gold_tables: bool
    join_keys: bool
    mapping: bool
    knowledge: bool
    decomp: bool
    instances: bool = False
    top_k: int = 15

def get_r_fn(dataset, model, eval_config: EvalConfig, batch=False):
    if batch:
        create_directory(f"./data/beaver/{dataset}/batch/")
    else:
        create_directory(f"./data/beaver/{dataset}/predictions/{model}/")

    if eval_config.gold_tables:
        suffix = ['GT']
        if eval_config.mapping: suffix.append('M')
        if eval_config.join_keys: suffix.append('J')
        if eval_config.knowledge: suffix.append('KNOW')
        if eval_config.decomp: suffix.append('DECOMP')
        suffix = '_'.join(suffix)
    else:
        suffix = f"top{eval_config.top_k}"

    if batch:
        result_fn = f"./data/{dataset}/batch/req_{suffix}.jsonl"
    else:
        result_fn = f"./data/{dataset}/predictions/{model}/{suffix}.json"
    return result_fn

def system(content: str):
    return {"role": "system", "content": content}


def user(content: str):
    return {"role": "user", "content": content}


def assistant(content: str):
    return {"role": "assistant", "content": content}


# format should be either json or npy
def merge(num_partitions: int, _fn: str, format: str):
    fn = f"{_fn}.{format}"

    results = []

    individuals = [
        (
            read_json(f"{_fn}_{partition}.json")
            if format == "json"
            else np.load(f"{_fn}_{partition}.npy")
        )
        for partition in range(num_partitions)
    ]

    if format == "json":
        for result in individuals:
            results += result
        write_json(results, fn)
    elif format == "npy":
        results = np.vstack(results)
        np.save(fn, results)

    print(len(results))


def write_jsonl(fn: str, prompts: list[str]):
    # clear the file
    with open(fn, "w"):
        pass

    for p_idx, prompt in enumerate(prompts):
        if prompt is None:
            continue
        with open(fn, "a") as f:
            # 128 for ottqa, 1024 for bird
            outputs = {
                "custom_id": str(p_idx),
                "method": "POST",
                "url": "/v1/chat/completions",
                "body": {
                    "model": "gpt-4o",
                    "messages": prompt,
                    "temperature": 0,
                    "max_tokens": 1024,
                },
            }
            f.write(json.dumps(outputs) + "\n")


def parse_jsonl(fn: str):
    with open(f"{fn}.jsonl") as f:
        preds = [json.loads(line) for line in f.readlines()]

    preds_format = {}

    # to handle r2 case where I skip indices
    last_pred_id = -1

    for pred in preds:
        pred_id = pred["custom_id"]
        pred_content = pred["response"]["body"]["choices"][0]["message"]["content"]

        for i in range(last_pred_id, int(pred_id) - 1):
            preds_format[i] = ""

        last_pred_id = int(pred_id)
        preds_format[pred_id] = pred_content

    print(len(preds_format))
    write_json(list(preds_format.values()), f"{fn}.json")


def print_prompt(p):
    for msg in p:
        print(msg["role"], msg["content"], end="\n\n")


def filter_by_indices(original_list, indices):
    return [original_list[i] for i in indices if i < len(original_list)]

def extract_tag_content(text, tag):
    pattern = rf"<{tag}>(.*?)</{tag}>"
    match = re.search(pattern, text, re.DOTALL)
    return match.group(1).strip() if match else ""