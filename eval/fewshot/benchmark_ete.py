from tiger_utils import read_json
from tiger_utils.model import system, user, assistant
from tqdm import tqdm

from utils import format_tables, EvalConfig, format_join

def get_mapping(mapping):
    desc = []

    for sq in mapping:
        cols_desc = []
        cols = mapping[sq]
        for col in cols:
            table_name, col = col.split(".")
            table_name = f"{table_name.lower()}"
            cols_desc.append(f"column {col} in table {table_name}")
        cols_desc = ", ".join(cols_desc)
        desc.append(f'"{sq}" in the user question refers to {cols_desc}')

    return "\n".join(desc)


def get_join_keys(join_keys):
    desc = []
    for join_key in join_keys:
        # key1, key2 = join_key
        # t1, col1 = key1.split(".")
        # t2, col2 = key2.split(".")
        # t1, t2 = f"{t1.lower()}", f"{t2.lower()}"
        # desc.append(
        #     f"Column {col1} in table {t1} joins with column {col2} in table {t2}"
        # )
        desc.append(format_join(join_key))
    return "\n".join(desc)


def get_knowledge(internal_knowledge, external_knowledge):
    return "\n".join(internal_knowledge + external_knowledge)


def get_decomp(decomps):
    decomp_str = []
    for subq_idx, subq in enumerate(decomps):
        decomp_str.append(f"Subquery {subq_idx + 1}: {subq}")
    return "\n".join(decomp_str)


def get_user_prompt(
    q,
    tables,
    corpus_tables,
    eval_config: EvalConfig
):
    desc = [
        format_tables(tables, corpus_tables, eval_config.instances),
        f"User question: {q['question']}",
    ]

    if eval_config.mapping:
        desc.append(f"Mapping:\n{get_mapping(q['mapping'])}")

    if eval_config.join_keys:
        desc.append(f"Join keys:\n{get_join_keys(q['join_keys'])}")

    if eval_config.knowledge and get_knowledge(q['internal_evidence'], q['external_evidence']) != '':
        desc.append(
            f"Domain knowledge:\n{get_knowledge(q['internal_evidence'], q['external_evidence'])}"
        )

    if eval_config.decomp and get_decomp(q["subquery_gold_questions"]) != "":
        desc.append(f"Query decomposition:\n{get_decomp(q['subquery_gold_questions'])}")

    return user("\n\n".join(desc))

STRUCTURE_MAPPING = {
    'filtered_chain_with_domain.json': 'chain',
    'filtered_tree_with_domain.json': 'tree',
    'filtered_chain_tree_with_domain.json': 'chain_tree',
    'filtered_tree_chain_with_domain.json': 'tree_chain',
    'filtered_chain_without_domain.json': 'chain',
    'filtered_tree_without_domain.json': 'tree',
    'filtered_chain_tree_without_domain.json': 'chain_tree',
    'filtered_tree_chain_without_domain.json': 'tree_chain',
    'filtered_subquery_column_with_domain.json': None
}

def get_ete_prompts(dataset: str, q_fn: str, eval_config: EvalConfig, data_dir: str = "../../data"):
    structures = read_json(f"{data_dir}/template_structure.json")
    qs = read_json(f"{data_dir}/{dataset}/{q_fn}.json")
    example = read_json(f"{data_dir}/{dataset}/example.json")
    dev_tables = read_json(f"{data_dir}/{dataset}/dev_tables.json")

    if not eval_config.gold_tables:
        preds = read_json(f"{data_dir}/{dataset}/reranked_preds.json")

    example_prompt = [
        get_user_prompt(example, example["gold_tables"], dev_tables, eval_config),
        assistant(f"SQL: <ans>{example['sql']}</ans>"),
    ]

    prompts = []
    instance_ids = []

    for q_idx, q in enumerate(tqdm(qs)):
        # print(q_idx)

        q_knowledge = eval_config.knowledge and get_knowledge(q['internal_evidence'], q['external_evidence']) != ''
        q_decomp = eval_config.decomp and get_decomp(q["subquery_gold_questions"]) != ""

        if eval_config.gold_tables:
            tables = q["gold_tables"]
        else:
            if str(q_idx) not in preds:
                prompts.append(None)
                continue
            pred = preds[str(q_idx)]
            tables = pred[:eval_config.top_k]

        db_type = "MySQL"

        instruction = ["You are given a list of tables", "a user question"]
        if eval_config.join_keys:
            instruction.append("join keys among the provided tables")
        if eval_config.mapping:
            instruction.append("a mapping from information mentioned in the user question to columns in the provided tables")
        if q_knowledge:
            instruction.append("domain knowledge"),
        if q_decomp:
            instruction.append("decomposition of the user question")
        instruction[-1] = f"and {instruction[-1]}"
        instruction = ", ".join(instruction) + ", "

        instruction += f"your task is output a {db_type} SQL statement that can be used to answer the user question based on the provided information. You need to ensure that syntax and functions used in your SQL statement are appropriate for {db_type} database. If you are unable to determine the SQL statement, output None. "

        if eval_config.mapping:
            instruction += f"You should use the provided mapping to determine which columns and tables should be used in the SQL statement. "
        if eval_config.join_keys:
            instruction += f"You should use the provided join keys to determine how to connect the tables in the SQL statement. "
        if q_knowledge:
            instruction += f"You should use the provided domain knowledge to determine which tables, columns, and literals should be used in the SQL statement. "
        if q_decomp:
            instruction += f"You must answer each subquery individually and then combine them to form the complete SQL statement. Each subquery you generate must be explicitly used in the final SQL statement, without being simplified. "

            instruction += f"Below is the structure of the SQL statement with subqueries denoted. Each provided subquery is used in the final SQL statement in such a structure."

            structure_name = STRUCTURE_MAPPING[q["source_file"]]
            if structure_name:
                structure = structures[structure_name]
                instruction += f"\n\n{structure['structure']}"

            # The following explanation describes which numbered subqueries you are given correspond to which query in the query structure you were provided:
            instruction += f"\n\n{structure['subquery_decomposition']} "

        instruction += f"The SQL statement need to be wrapped in <ans></ans> tags."

        prompt = [system(instruction)] + example_prompt
        prompt += [get_user_prompt(q, tables, dev_tables, eval_config)]

        prompts.append(prompt)
        instance_ids.append(q.get("instance_id", f"beaver_{dataset}_{q_idx:03d}"))

    print(f"#prompts: {len(prompts)}")
    return prompts, instance_ids
