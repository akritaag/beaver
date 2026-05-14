import os
import json
import argparse
import glob
import re
from concurrent.futures import ThreadPoolExecutor

from utils.llm import GPTChat

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

def evaluate_single_entry(subdir_path, gold_data, chat_client: GPTChat, output_base_dir):
    result_info = {
        "subdir": os.path.basename(subdir_path),
        "score": 0,
        "has_decomposition": False,
        "status": "error"
    }

    try:
        subdir_name = os.path.basename(subdir_path)
        
        # Extract index
        match = re.search(r'_(\d+)$', subdir_name)
        if not match:
            result_info["status"] = "error_no_index"
            return result_info
            
        index = int(match.group(1))
        
        if index >= len(gold_data):
            result_info["status"] = "error_index_out_of_range"
            return result_info

        gold_entry = gold_data[index]
        reference_sqls = gold_entry.get("sub_sqls", [])
        
        # Skip if no prompt decomposition
        if not reference_sqls or len(reference_sqls) == 0:
            result_info["status"] = "skipped_no_gold"
            result_info["has_decomposition"] = False
            return result_info
            
        result_info["has_decomposition"] = True
        
        # Resume Check
        output_dir = os.path.join(output_base_dir, subdir_name)
        output_file = os.path.join(output_dir, "judge_result.json")
        
        if os.path.exists(output_file):
            try:
                with open(output_file, "r") as f:
                    data = json.load(f)
                    score = data.get("judgment", {}).get("score", 0)
                    result_info["score"] = score
                    result_info["status"] = "skipped_exists"
                    return result_info
            except Exception as e:
                print(f"File {output_file} corrupted? Re-evaluating. Error: {e}")

        # Read Generated SQL
        result_sql_path = os.path.join(subdir_path, "result.sql")
        
        # Fallback: look for any .sql file
        if not os.path.exists(result_sql_path):
            sql_files = glob.glob(os.path.join(subdir_path, "*.sql"))
            if sql_files:
                result_sql_path = sql_files[0]
            else:
                result_info["status"] = "error_no_result_sql"
                return result_info
            
        with open(result_sql_path, "r") as f:
            generated_sql = f.read().strip()
            
        # Prompt
        prompt = PROMPT_TEMPLATE.format(
            generated_sql=generated_sql,
            reference_subquestion_sqls=json.dumps(reference_sqls, indent=2)
        )
        
        # LLM Call
        response_json = chat_client.get_json_response(prompt)
        
        if response_json:
            os.makedirs(output_dir, exist_ok=True)
            
            output_data = {
                "generated_sql": generated_sql,
                "reference_subquestion_sqls": reference_sqls,
                "judgment": response_json
            }
            
            with open(output_file, "w") as f:
                json.dump(output_data, f, indent=4)
                
            result_info["score"] = response_json.get("score", 0)
            result_info["status"] = "success"
            # print(f"Processed {subdir_name}: Score {result_info['score']}")
        else:
            result_info["status"] = "error_llm_failure"

    except Exception as e:
        print(f"Error processing {subdir_path}: {e}")
        import traceback
        traceback.print_exc()
        result_info["status"] = f"exception: {str(e)}"

    return result_info

def run_evaluation_task(subdir, gold_data, model, output_dir):
    try:
        chat_client = GPTChat(model=model)
        return evaluate_single_entry(subdir, gold_data, chat_client, output_dir)
    except Exception as e:
        return {
            "subdir": os.path.basename(subdir),
            "score": 0,
            "has_decomposition": False,
            "status": f"init_exception: {e}"
        }

def main():
    parser = argparse.ArgumentParser(description="LLM-as-a-judge for SQL decomposition")
    parser.add_argument("--input_dir", type=str, default="postprocessed_data/beaver_sp_opt2_beaver_sp_opt2_CTX-200/RESULTS_MODEL-anthropic/claude-sonnet-4.5-SQL")
    parser.add_argument("--gold_file", type=str, default="../../data/sp/dev_sampled.json")
    parser.add_argument("--model", type=str, default="gpt-5-mini", help="LLM model to use")
    parser.add_argument("--baseline_method", type=str, choices=["reforce", "dinsql", "dailsql", "fewshot"], default="reforce", help="Baseline method (controls output path structure)")
    parser.add_argument("--num_workers", type=int, default=40, help="Number of parallel workers")
    parser.add_argument("--output_dir", type=str, default=None, help="Output directory") 
    
    args = parser.parse_args()
    
    print(f"Loading gold data from {args.gold_file}...")
    with open(args.gold_file, "r") as f:
        gold_data = json.load(f)
        
    subdirs = sorted(glob.glob(os.path.join(args.input_dir, "*")))
    subdirs = [d for d in subdirs if os.path.isdir(d)]
    
    print(f"Found {len(subdirs)} subdirectories to evaluate.")

    args.input_dir = args.input_dir.rstrip('/')
    if args.output_dir is None:
        if args.baseline_method == "reforce":
            args.output_dir = os.path.join('ReFoRCE/output/judge', args.input_dir.split('/')[-1])
        elif args.baseline_method == "dinsql":
            if 'qwen' in args.input_dir or 'minimax' in args.input_dir or 'claude' in args.input_dir:
                args.output_dir = os.path.join('dinsql/output/judge', '/'.join(args.input_dir.split('/')[-2:]))
            else:
                args.output_dir = os.path.join('dinsql/output/judge', args.input_dir.split('/')[-1])
        elif args.baseline_method == "dailsql":
            if 'qwen' in args.input_dir or 'minimax' in args.input_dir or 'claude' in args.input_dir:
                args.output_dir = os.path.join('dailsql/output/judge', '/'.join(args.input_dir.split('/')[-3:]))
            else:
                args.output_dir = os.path.join('dailsql/output/judge', '/'.join(args.input_dir.split('/')[-2:]))
        elif args.baseline_method == "fewshot":
            args.output_dir = os.path.join('fewshot/output/judge', args.input_dir.split('/')[-1])
    
    os.makedirs(args.output_dir, exist_ok=True)
    
    results = []
    
    try:
        from tqdm import tqdm
        pbar = tqdm(total=len(subdirs))
    except ImportError:
        pbar = None
    
    with ThreadPoolExecutor(max_workers=args.num_workers) as executor:
        futures = []
        for subdir in subdirs:
            futures.append(
                executor.submit(run_evaluation_task, subdir, gold_data, args.model, args.output_dir)
            )
            
        for future in futures:
            res = future.result()
            results.append(res)
            
            if pbar:
                pbar.update(1)
            elif len(results) % 20 == 0:
                print(f"Processed {len(results)}/{len(subdirs)}")

    if pbar:
        pbar.close()
        
    # Calculate Summary Metrics
    # 1. Logic for "sum(score)/ (#all questions ) * 5, use 0 if this question does not have a query decomposition"
    # User might mean: Normalized Score = Sum(Scores) / (NumQuestions * 5).
    # Since questions without decomposition are 0, they penalize the score.
    
    total_questions = len(results)
    sum_all_scores = sum(r["score"] for r in results)
    
    # Normalized score (0.0 to 1.0)
    normalized_score_all = 0.0
    if total_questions > 0:
        normalized_score_all = sum_all_scores / (total_questions * 5)
        
    # Average score (0 to 5)
    average_score_all = 0.0
    if total_questions > 0:
        average_score_all = sum_all_scores / total_questions

    # 2. Logic for "average of all available score"
    results_with_decomposition = [r for r in results if r["has_decomposition"]]
    num_with_decomposition = len(results_with_decomposition)
    sum_available_scores = sum(r["score"] for r in results_with_decomposition)
    
    average_score_available = 0.0
    if num_with_decomposition > 0:
        average_score_available = sum_available_scores / num_with_decomposition
        
    summary = {
        "metrics": {
            "total_questions": total_questions,
            "questions_with_decomposition": num_with_decomposition,
            "sum_score_all": sum_all_scores,
            "normalized_score_all_questions": normalized_score_all,
            "average_score_all_questions": average_score_all,
            "average_score_available_only": average_score_available
        },
        "results": results
    }
    
    summary_path = os.path.join(args.output_dir, "summary.json")
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=4)
        
    print(f"\nEvaluation Complete.")
    print(f"Summary saved to: {summary_path}")
    print(f"Total Questions: {total_questions}")
    print(f"Questions w/ Decomposition: {num_with_decomposition}")
    print(f"Average Score (All, 0-5): {average_score_all:.4f}")
    print(f"Normalized Score (All, 0-1): {normalized_score_all:.4f}")
    print(f"Average Score (Available Only, 0-5): {average_score_available:.4f}")

if __name__ == "__main__":
    main()
