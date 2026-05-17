import os
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor
from openai import OpenAI

from prompt import get_ete_prompts
from utils import EvalConfig

import argparse

MODEL_MAP = {
    "gpt-5-mini": "gpt-5-mini",
    "gpt-5.2": "gpt-5.2",
    "o4-mini": "o4-mini",
    "gemini": "gemini-2.0-flash",
    "qwen": "qwen/qwen3-next-80b-a3b-instruct",
    "claude": "anthropic/claude-sonnet-4.5",
    "minimax": "minimax/minimax-m2.1",
}


def process_output(output):
    # remove everything of left of <ans> if <ans> is in output
    if "<ans>" in output:
        output = output.split("<ans>")[-1]
    # remove everything of right of </ans> if </ans> is in output
    if "</ans>" in output:
        output = output.split("</ans>")[0]
    # remove everything of left of ```sql if ```sql is in output
    if "```sql" in output:
        output = output.split("```sql")[-1]
    # remove everything of right of ``` if ``` is in output
    if "```" in output:
        output = output.split("```")[0]
    return output.strip()




def run_prompts(prompts, indices, instance_ids, output_dir: str, model: str):
    prompts_to_process = []
    indices_to_process = []
    instance_ids_to_process = []
    
    for prompt, idx, instance_id in zip(prompts, indices, instance_ids):
        instance_dir = os.path.join(output_dir, instance_id)
        sql_file = os.path.join(instance_dir, "predicted_0.sql")
        
        # Check if already processed
        if not os.path.exists(sql_file) or os.path.getsize(sql_file) == 0:
            prompts_to_process.append(prompt)
            indices_to_process.append(idx)
            instance_ids_to_process.append(instance_id)
            
    if not prompts_to_process:
        print("All prompts already processed.")
        return

    print(f"Running {len(prompts_to_process)} prompts...")
    
    # Using environment variable for API key
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    model_name = MODEL_MAP.get(model, model)


    if "gemini" in model_name:
        client = OpenAI(
                    api_key=os.environ.get("GEMINI_API_KEY"),
                    base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
                )
    elif "qwen" in model_name:
        client = OpenAI(
                    base_url="https://openrouter.ai/api/v1",
                    api_key=os.environ.get("OPENROUTER_API_KEY"),
                )
    elif "claude" in model_name:
        client = OpenAI(
                    base_url="https://openrouter.ai/api/v1",
                    api_key=os.environ.get("OPENROUTER_API_KEY"),
                )
    elif "minimax" in model_name:
        client = OpenAI(
                    base_url="https://openrouter.ai/api/v1",
                    api_key=os.environ.get("OPENROUTER_API_KEY"),
                )
    else:
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    def process_prompt(item):
        prompt, idx, instance_id = item
        if prompt is None:
            return idx, instance_id, "", ""

        try:
            if "gemini" in model_name:
                resp = client.chat.completions.create(
                    model=model_name,
                    messages=prompt,
                    # temperature=0,
                )
            elif "qwen" in model_name:
                resp = client.chat.completions.create(
                    model=model_name,
                    messages=prompt,
                    # temperature=0,
                    extra_body={
                    "reasoning": {
                        "effort": "none",
                    }
                },
                )
            elif "claude" in model_name:
                resp = client.chat.completions.create(
                    model=model_name,
                    messages=prompt,
                    # temperature=0,
                    extra_body={
                    "reasoning": {
                        "effort": "none",
                    }
                },
                )
            elif "minimax" in model_name:
                resp = client.chat.completions.create(
                    model=model_name,
                    messages=prompt,
                    # temperature=0,
                )
            else:
                resp = client.chat.completions.create(
                    model=model_name,
                    messages=prompt,
                    # temperature=0,
                )
            
            content = resp.choices[0].message.content
            # To match the structure of other baselines, we could log the prompt/response
            # Here we provide a simple generic generation log.
            generation_log = f"Prompt:\n{prompt}\n\nResponse:\n{content}"
            return idx, instance_id, content, generation_log
        except Exception as e:
            print(f"Error processing index {idx}: {e}")
            return idx, instance_id, "", f"Error: {e}"

    # Using ThreadPoolExecutor for parallel processing
    with ThreadPoolExecutor(max_workers=20) as executor:
        # Zip prompts with indices for processing
        items = list(zip(prompts_to_process, indices_to_process, instance_ids_to_process))
        
        # Map returns results in the same order as input
        results = list(tqdm(executor.map(process_prompt, items), total=len(items)))
        
        # save results
        for idx, instance_id, content, gen_log in results:
            processed_sql = process_output(content)
            
            instance_dir = os.path.join(output_dir, instance_id)
            os.makedirs(instance_dir, exist_ok=True)
            
            sql_file = os.path.join(instance_dir, "predicted_0.sql")
            log_file = os.path.join(instance_dir, "generation.log")
            
            with open(sql_file, "w") as f:
                f.write(processed_sql)
            with open(log_file, "w") as f:
                f.write(gen_log)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=str, default="gpt-5-mini")
    parser.add_argument("--dataset", type=str, default="dw")
    parser.add_argument("--q_fn", type=str, default="dev")
    parser.add_argument("--output_dir", type=str, default="")
    parser.add_argument("--data_dir", type=str, default="../../data")
    parser.add_argument("--gold_tables", action="store_true")
    parser.add_argument("--join_keys", action="store_true")
    parser.add_argument("--mapping", action="store_true")
    parser.add_argument("--knowledge", action="store_true")
    parser.add_argument("--decomp", action="store_true")
    args = parser.parse_args()

    model = args.model
    dataset = args.dataset
    q_fn = args.q_fn

    eval_config = EvalConfig(
        gold_tables=args.gold_tables, join_keys=args.join_keys, mapping=args.mapping, knowledge=args.knowledge, decomp=args.decomp
    )
    print(eval_config)

    prompts, instance_ids = get_ete_prompts(dataset, q_fn, eval_config, args.data_dir)
    
    output_dir = args.output_dir

    print(f"Output directory: {output_dir}")
    os.makedirs(output_dir, exist_ok=True)

    selected_indices = list(range(len(prompts)))
    selected_prompts = [prompts[i] for i in selected_indices]

    run_prompts(selected_prompts, selected_indices, instance_ids, output_dir, model)
