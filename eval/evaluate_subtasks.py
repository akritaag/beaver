import json
import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from dotenv import load_dotenv

from utils.llm import GPTChat
from utils.evaluate_extraction_subtasks import (
    eval_extraction_output, EXTRACTION_PROMPT
)
from utils.utils import read_json, write_json

load_dotenv('../.env')

PROMPT_TEMPLATE = """
You are a strict SQL decomposition evaluator. Your job is to score how well the GENERATED_SQL reflects the intended QUERY DECOMPOSITION described by the REFERENCE_SUBQUESTION_SQLS.

You are NOT judging performance, style, formatting, or minor syntax differences. You ARE judging whether the generated SQL contains (explicitly or implicitly) the same sub-questions/steps as the reference decomposition and correctly composes them into the final result.

Inputs:
1) GENERATED_SQL:
<<<
{generated_sql}
>>>

2) REFERENCE_SUBQUESTION_SQLS (a list; each item corresponds to a decomposition step; some items may be intermediate-only or a combined final query):
<<<
{reference_subquestion_sqls}
>>>

Task:
- Infer the core "decomposition steps" expressed by the REFERENCE_SUBQUESTION_SQLS.
- Check whether the GENERATED_SQL implements those steps, either as:
  (a) separate CTEs/subqueries, OR
  (b) merged into fewer steps but still logically equivalent (same filters, groupings, joins, and computed measures), OR
  (c) computed implicitly in a single query (allowed if equivalent).
- Focus on SEMANTIC equivalence of decomposition components, not surface form.

How to compare (important):
For each reference step, extract these semantic elements:
A. Tables involved (and whether they are required by the reference step)
B. Join structure / join keys (e.g., i.uuid = iic.instance_uuid)
C. Filters / predicates (e.g., deleted=0, host='prime-35', availability_zone='flare3', key_name non-empty)
D. Group-by keys and aggregation intent (e.g., count caches per display_name, count distinct uuid, variance(memory_mb))
E. Derived computations (e.g., MAX(memory_mb)-MIN(memory_mb), VARIANCE(memory_mb), LIMIT 10 top-k, MAX(instance_count) by zone)
F. Composition logic across steps (e.g., top-k step feeding into stats step; joining step outputs on display_name)

Then decide whether GENERATED_SQL includes each step’s intent:
- Fully covered: all critical elements A-F present (allow renamed aliases/CTE names).
- Partially covered: some critical elements missing/changed.
- Not covered: the step’s intent is absent or contradicted.

Criticality rules:
- Missing a required FILTER in the reference is a major error (unless the reference includes extra tables/joins that are clearly unnecessary and do not change semantics).
- Missing a required JOIN that affects row cardinality or eligibility is a major error.
- Differences in alias names, whitespace, casing, or minor reordering are NOT errors.
- Extra constraints in GENERATED_SQL that would change the answer are errors.
- Extra tables/joins are acceptable ONLY if they do not change the result (i.e., provably redundant) - otherwise treat as divergence.

Scoring rubric (0–5):
5 = Perfect decomposition fidelity.
    - All reference steps’ intents are covered (even if merged), and the final composition matches.
4 = Mostly faithful.
    - Covers nearly all step intents; at most one minor omission or a non-critical mismatch that likely does not change the final answer.
3 = Partially faithful.
    - Captures the general approach but misses or alters at least one important step element (e.g., a key filter, top-k, distinctness, required join, or key aggregation).
2 = Weak fidelity.
    - Only a small subset of decomposition is reflected; major components are missing or substantially changed.
1 = Minimal fidelity.
    - Barely aligns; only superficial overlap (some same tables/columns) without matching decomposition intent.
0 = No fidelity.
    - Unrelated SQL or entirely fails to reflect the reference decomposition.

Output format (MUST follow exactly):
Return a JSON object with:
- "score": integer from 0 to 5
- "step_coverage": array where each entry has:
    "reference_step_index", "covered" (one of "full"|"partial"|"none"), "notes"
- "overall_rationale": 3-6 sentences explaining the score, citing the biggest mismatches.
Do NOT include any extra keys. Do NOT include markdown. Do NOT include code blocks.

Now evaluate.
"""

def evaluate_single_entry(sql_fn: Path, gold_data, chat_client: GPTChat, output_dir: Path):
    result = {
        "sql_fn": str(sql_fn),
        "status": "error",
        "has_decomp": False,
        "generated_sql": "",
        "scores": {},
        "extraction_output": None,
        "decomp_output": None,
    }

    try:
        q_id = sql_fn.name.replace('.sql', '')

        gold_entry = gold_data[q_id]
        reference_sqls = gold_entry.get("sub_sqls", [])
        
        # Reuse cached result
        output_file = output_dir / f"{q_id}.json"
        if output_file.exists():
            try:
                return read_json(output_file)
            except Exception as e:
                print(f"File {output_file} corrupted? Re-evaluating. Error: {e}")

        # Read Generated SQL
        with open(sql_fn, "r") as f:
            generated_sql = f.read().strip()

        decomp_output = None
        if not reference_sqls or len(reference_sqls) == 0:
            result["has_decomp"] = False
        else:
            result["has_decomp"] = True
            decomp_prompt = PROMPT_TEMPLATE.format(
                generated_sql=generated_sql,
                reference_subquestion_sqls=json.dumps(reference_sqls, indent=2)
            )
            decomp_output = chat_client.get_json_response(decomp_prompt)
            result["decomp_output"] = decomp_output
        
        extraction_prompt = EXTRACTION_PROMPT.format(sql=generated_sql)
        extraction_output = chat_client.get_json_response(extraction_prompt)

        result["generated_sql"] = generated_sql
        result["extraction_output"] = extraction_output
        result["scores"] = {
            **eval_extraction_output(extraction_output, gold_entry),
            "query_decomposition_score": decomp_output.get("score", 0) if decomp_output else None
        }
        result["status"] = "success"
        print(f"Processed {q_id}: Score {json.dumps(result['scores'], indent=2)}")

        write_json(result, output_file)
    except Exception as e:
        print(f"Error processing {sql_fn}: {e}")
        import traceback
        traceback.print_exc()
        result["status"] = f"exception: {str(e)}"

    return result

def run_evaluation_task(sql_fn, gold_data, model, output_dir):
    try:
        chat_client = GPTChat(model=model)
        return evaluate_single_entry(sql_fn, gold_data, chat_client, output_dir)
    except Exception as e:
        return {"status": f"init_exception: {e}"}

def main():
    parser = argparse.ArgumentParser(description="Subtask evaluation")
    parser.add_argument("--input_dir", type=str)
    parser.add_argument("--dataset", type=str)
    parser.add_argument("--model", type=str, default="gpt-5-mini", help="LLM model to use")
    parser.add_argument("--num_workers", type=int, default=40, help="Number of parallel workers")
    args = parser.parse_args()
    
    gold_file = f'../data/{args.dataset}/dev.json'
    gold_data = read_json(gold_file)
    gold_data = {q['id']: q for q in gold_data}
    
    generated_sql_dir = Path(args.input_dir) / 'generated'
    generated_sql_fns = sorted(generated_sql_dir.glob('*.sql'))
    print(f"Found {len(generated_sql_fns)} generated SQLs to evaluate")
    
    output_dir = Path(args.input_dir) / 'subtask_eval'
    output_dir.mkdir(exist_ok=True)

    results = []
    
    try:
        from tqdm import tqdm
        pbar = tqdm(total=len(generated_sql_fns))
    except ImportError:
        pbar = None
    
    with ThreadPoolExecutor(max_workers=args.num_workers) as executor:
        futures = [
            executor.submit(run_evaluation_task, generated_sql_fn, gold_data, args.model, output_dir)
            for generated_sql_fn in generated_sql_fns
        ]
        for f in futures:
            res = f.result()
            results.append(res)
            if pbar: pbar.update(1)

    if pbar: pbar.close()
    
    avg_scores = {
        "avg_table_retrieval_f1": None,
        "avg_column_mapping_f1": None,
        "avg_join_key_f1": None,
        "avg_domain_knowledge_f1": None,
        "avg_query_decomposition_score": None
    }

    valid_results = [r for r in results if r["status"] == "success"]
    valid_results_with_decomp = [r for r in results if r["status"] == "success" and r["has_decomp"]]

    if len(valid_results) > 0:
        avg_scores["avg_table_retrieval_f1"] = sum(r["scores"]["table_retrieval_f1"] for r in valid_results) / len(valid_results)
        avg_scores["avg_column_mapping_f1"] = sum(r["scores"]["column_mapping_f1"] for r in valid_results) / len(valid_results)
        avg_scores["avg_join_key_f1"] = sum(r["scores"]["join_key_f1"] for r in valid_results) / len(valid_results)
        avg_scores["avg_domain_knowledge_f1"] = sum(r["scores"]["domain_knowledge_f1"] for r in valid_results) / len(valid_results)
    
    if len(valid_results_with_decomp) > 0:
        avg_scores["avg_query_decomposition_score"] = sum(r["scores"]["query_decomposition_score"] for r in valid_results_with_decomp) / len(valid_results_with_decomp)
        # the original decomp score is 0-5, divide by 5 to normalize
        avg_scores["avg_query_decomposition_score"] /= 5

    summary = {
        "total_questions": len(results),
        "total_valid_questions": len(valid_results),
        "total_valid_questions_with_query_decomposition": len(valid_results_with_decomp),
        **avg_scores
    }
    
    summary_path = Path(args.input_dir) / "summary_subtasks.json"
    write_json(summary, summary_path)

    print(f"\nEvaluation Complete.")
    print(f"Summary saved to: {summary_path}")
    print(json.dumps(summary, indent=2))

if __name__ == "__main__":
    main()
