import json
import pandas as pd

def read_json(fn):
    with open(fn) as f:
        return json.load(f)

def write_json(obj, fn):
    with open(fn, 'w') as f:
        json.dump(obj, f, indent=2)

def cosine_sim(A, B):
    import torch

    # Normalize A and B row-wise
    A_norm = A / A.norm(dim=1, keepdim=True)
    B_norm = B / B.norm(dim=1, keepdim=True)

    # Compute cosine similarity: A_norm @ B_norm.T
    cosine_sim = torch.mm(A_norm, B_norm.T)

    return cosine_sim

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