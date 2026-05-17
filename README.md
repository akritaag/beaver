# 🦫 BEAVER: An Enterprise Benchmark for Text-to-SQL

[![Dataset](https://img.shields.io/badge/HuggingFace-Dataset-blue?logo=huggingface)](https://huggingface.co/collections/beaverbench/beaver-dataset)
[![Paper](https://img.shields.io/badge/arXiv-Paper-red?logo=arxiv)](https://arxiv.org/abs/2409.02038)

This repository contains the evaluation code for the **BEAVER** text-to-SQL benchmark. It includes (i) four text-to-SQL methods: **ReFoRCE**, **DAIL-SQL**, **DIN-SQL**, and **Few-shot** (ii) two evaluation modes: coarse-grained evaluation (execution accuracy) and fine-grained evaluation across five subtasks critical to text-to-SQL.

## Repository Structure

```
├── .env                                 # Credential file (API keys + MySQL password)
├── data/                                # Dataset files (e.g. metadata, questions, tables)
│   ├── dw/                              # `dw` dataset
│   │   ├── dev.json                     # Question file
│   │   ├── dev_tables.json              # Tables metadata
│   │   └── example.json                 # Few-shot examples
│   └── ...                              # Other datasets
├── eval/                                # Evaluation methods and scripts
│   ├── reforce/                         # ReFoRCE evaluation pipeline
│   ├── fewshot/                         # Few-shot evaluation pipeline
│   ├── dailsql/                         # DAIL-SQL evaluation pipeline
│   ├── dinsql/                          # DIN-SQL evaluation pipeline
│   ├── evaluate_decomposition.py        # Script for evaluating query decomposition subtask
│   └── evaluate_extraction.py           # Script for evaluating other subtasks
└── retrieve/                            # Table retrieval
```

## Getting Started

### Credentials (`.env` file)

All API keys and MySQL credentials are managed through a single `.env` file at the root directory.

The `.env` file should contain:
```
# LLM API Keys
OPENAI_API_KEY=xxx
OPENROUTER_API_KEY=xxx

# MySQL Credentials (shared across all databases)
MYSQL_HOST=localhost
MYSQL_USER=root
MYSQL_PASSWORD=xxx
```

### Data Pre-processing
Our [gated dataset](https://huggingface.co/collections/beaverbench/beaver-dataset) is hoted on Hugging Face. You must be **authenticated** to access it, which means logging in through your CLI before downloading any files.

```bash
python data/download_hf.py --sample [sample_size]
```

For each of the four splits (`dw`, `nova`, `neutron`, `dw_real`), this script automatically downloads the datasets from Hugging Face and generates:
* `dev.json`: The full set of questions.
* `dev_tables.json`: The full set of tables.
* `dev_sampled.json`: A sampled subset of questions of size `sample_size` (default `100`) since running on the full dataset can be computationally expensive

You should also follow the instruction [here](https://huggingface.co/datasets/beaverbench/beaver-table#getting-started) to setup the MySQL databases.

### Table retrieval
The following command applies a retrieve-then-rerank pipeline to retrieve tables in `dev_tables.json` that are semantically relevant to questions in `dev_sampled.json`, guiding downstream text-to-SQL generation. The pipeline uses a dense embedding model `embed_model` for retrieval and an optional reranker model `rerank_model` for improved ordering. If `rerank_model` is omitted, the pipeline performs retrieval only, without the reranking step.

```
python retrieve/retrieve.py --dataset [dataset] --embed_provider local --rerank_model Qwen/Qwen3-Reranker-8B
```

The command takes the following arguments:
* `dataset`: one of `dw`, `dw_real`, `neutron`, `nova`
* `embed_model` (default `Qwen/Qwen3-Embedding-8B`): the dense embedding model for the retrieval step
* `embed_k` (default `50`): the number of tables returned by the retrieval step
* `embed_provider`: `local` (GPU required) or `openrouter` (requires a valid `OPENROUTER_API_KEY`)
* `rerank_model` (default `None`): the optional reranker model for the reranking step; the paper used `Qwen/Qwen3-Reranker-8B`
* `rerank_k` (default `15`): the number of tables returned by the reranking step

Note: the retrieval step outputs `retrieved_tables.json`, and the reranking step (if enabled) outputs `reranked_tables.json`. While generating SQL, the text-to-SQL method uses `reranked_tables.json` if it exists and otherwise `retrieved_tables.json`.

## Text-to-SQL methods

We consider four text-to-SQL methods:
1. ReFoRCE (adapted from [this official ReFoRCE implementation](https://github.com/Snowflake-Labs/ReFoRCE/tree/o3/methods/ReFoRCE)): an agentic method with candidate generation, majority voting, and column exploration.
2. DIN-SQL (adapted from [this Spider2 implementation](https://github.com/xlang-ai/Spider2/tree/main/spider2-lite/baselines/dinsql)): a method with query decomposition and self-correction
3. DAIL-SQL (adapted from [this Spider2 implementation](https://github.com/xlang-ai/Spider2/tree/main/spider2-lite/baselines/dailsql)): a method with example selection
4. Few-shot: a method with static in-context examples

### Environment

You can setup the package environment using either `conda` or `venv`.

```bash
cd eval

# Using `conda`
conda create -n beaver-eval python=3.10 -y
conda activate beaver-eval

# Using `venv`
python3 -m venv beaver-eval
source beaver-eval/bin/activate

pip install -r requirements.txt

# To run the DAIL‑SQL method, execute the following additional commands:
python dailsql/nltk_downloader.py
python -m spacy download en_core_web_sm
```

### SQL generation

All methods can be executed using the `run.sh` script in their respective folders. For example, to execute ReFoRCE,
```bash
cd eval/reforce
./run.sh --model [model] --dataset [dataset] --setting {0|1|2}
```
- `model`: the LLM for generating the text-to-SQL (e.g., `gpt-5-mini`)
- `dataset`: one of `dw`, `dw_real`, `neutron`, `nova`
- `setting=0` *(default setting)*: Standard end-to-end setting with no hints. Base information with only the top-k tables provided.
- `setting=1`: With hints for three schema-linking subtasks. Includes gold tables, column mapping, and join keys.
- `setting=2`: With hints for all five subtasks. Includes three subtasks in `setting=1`, domain knowledge, and subquery decomposition.

## Evaluation

<!-- In the paper, we define five subtasks: multi-table retrieval, join key detection, column mapping, domain knowledge extraction, and query decomposition. We also provide annotations for these subtasks. We provide three settings of providing these annotations as oracle hints. -->

<!-- ### Settings of subtask annotations -->


### Execution accuracy
After generating the SQL predictions, the `run.sh` scripts will automatically execute a `unify.py` script. This executes both the predicted SQL and the gold SQL against the MySQL database and saves the outputs as CSV files inside `eval/output/unified/<method>/<run_name>/`. 

To obtain the final evaluation metrics (exact set match over the CSVs), you must run the `unified_evaluation.py` script on the unified directory:
```bash
cd eval
python unified_evaluation.py --unified_dir output/unified/<method>/<run_name>
```
*(Outputs `evaluation_summary.json` containing the execution accuracy and detailed question-level results)*

For details on subtasks, please refer to the paper.

### Subtask evaluation

Beyond standard execution accuracy (exact match), we also provide fine-grained evaluation on the subtasks. These scripts analyze the outputs generated by the methods above, so you need to first execute `run.sh` before you can obtain the subtask performance.

**Query decomposition subtask**
Evaluates if the generated SQL semantically reflects the intended query decomposition steps.
```bash
cd eval/
python evaluate_decomposition.py \
  --input_dir ReFoRCE/output/gpt-5-mini-beaver-dw-setting0-log-*/ \
  --gold_file ../data/dw/dev.json \
  --model gpt-5-mini \
  --method reforce \
  --num_workers 40
```
*(Outputs `summary.json` containing the average LLM-as-a-judge decomposition scores)*

**Other subtasks**
Evaluates the performance on table retrieval, join key detection, column mapping, and domain knowledge extraction.
```bash
cd eval/
python evaluate_extraction.py \
  --input_dir ReFoRCE/output/gpt-5-mini-beaver-dw-setting0-log-*/ \
  --gold_file ../data/dw/dev.json \
  --model gpt-5-mini \
  --method reforce \
  --num_workers 40
```
*(Outputs `summary.json` with F1 scores for these tasks)*
