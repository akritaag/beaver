# Beaver Evaluation Pipeline

This repository contains the evaluation baselines and datasets for the **Beaver** text-to-SQL benchmark. It includes four different baseline methodologies for SQL generation and evaluation: **ReFoRCE**, **DAIL-SQL**, **DIN-SQL**, and **Few-shot**.

## Repository Structure

```
├── data/                       # Contains the dataset files (e.g. metadata, questions, tables)
│   ├── templates/              # Internal and specific templates
│   ├── dw/                     # `dw` database
│   ├── neutron/                # `csail_stata_nova` database
│   │   ├── dev.json            # Main questions file
│   │   ├── dev_tables.json     # Tables metadata for the split
│   │   ├── reranked_preds.json # Optional reranked table predictions
│   │   ├── example.json        # Few-shot examples
│   │   └── sample.py           # Script to generate a sampled split
│   ├── nova/                   # `csail_stata_nova` database
│   └── sp/                     # `sp` database
│
├── eval/                       # Evaluation baselines and scripts
│   ├── ReFoRCE/                # ReFoRCE evaluation pipeline
│   ├── dailsql/                # DAIL-SQL evaluation pipeline
│   ├── dinsql/                 # DIN-SQL evaluation pipeline
│   ├── Few-shot/               # Few-shot evaluation pipeline
│   ├── evaluate_decomposition.py # Script for LLM-as-a-judge decomposition evaluation
│   ├── evaluate_extraction.py  # Script for LLM-as-a-judge extraction evaluation
│   ├── .env                    # Credentials file (API keys + MySQL password)
```

---

## Data Preparation

For each database (e.g., `dw`, `neutron`, `nova`, `sp`), you will need to download the necessary data files into their respective folders.

Ensure the following files are present in the dataset split folder (e.g., `data/neutron/`):
- `dev.json`: The main dataset questions file.
- `dev_tables.json`: The tables and schema metadata.
- `reranked_preds.json`: The table retrieval rankings.
- `example.json`: Few-shot examples.
- `sample.py`: A utility script.

*(Optional)*: If you want to run evaluations on a smaller, sampled subset, you can execute the `sample.py` script to generate a sampled split for that dataset:
```bash
cd data/neutron
python sample.py
```

---

## Environment Setup

Each baseline has its own dependencies and environment requirements. You can manage these environments using either `conda` (recommended) or `venv` (as an alternative).

<!-- ### Building with `venv` (Alternative to `conda`)
If you prefer not to use `conda`, you can create a standard Python virtual environment inside each baseline folder (Note: Python versions may vary per baseline; please ensure your base python matches the version mentioned in the `conda` sections):

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
``` -->

### Credentials (`.env` file)

All API keys and MySQL credentials are managed through a single `eval/.env` file.

The `.env` file should contain:
```
# LLM API Keys
OPENAI_API_KEY=sk-proj-...
OPENROUTER_API_KEY=sk-...
GOOGLE_API_KEY=...

# MySQL Credentials (shared across all databases)
MYSQL_HOST=localhost
MYSQL_USER=root
MYSQL_PASSWORD=your_password
```

The database name is automatically determined from the `--dataset` argument — no need to create separate credential files per database.

### How to build the database in MySQL

Download the database dump file from **TODO** and import it into MySQL.

---

## Baseline Methods

We provide automated evaluation pipelines for three different text-to-SQL generation methods. In the paper, we define five subtasks: multi-table retrieval, join key detection, column mapping, domain knowledge extraction, and query decomposition. We also provide the annotations for these subtasks. We provide three settings of providing these annotations as oracle hints.

### Settings of subtask annotations
All baselines use a unified `run.sh` script with named arguments:

```bash
./run.sh --model <model> --dataset <dataset> --setting <0|1|2>
```

- **Setting 0**: Standard end-to-end setting with no hints. Base information with only the top-k tables provided. *(Recommended Baseline)*
- **Setting 1**: With hints for schema-linking subtasks. Includes gold tables + column mapping + join keys.
- **Setting 2**: With hints for all subtasks. Includes setting 1 + domain knowledge + subqueries.

### 1. ReFoRCE
Adpoted from [this official ReFoRCE implementation](https://github.com/Snowflake-Labs/ReFoRCE/tree/o3/methods/ReFoRCE).

**Setup:**
```bash
conda create -n reforce python=3.10 -y
conda activate reforce
cd eval/ReFoRCE
pip install -r requirements.txt
```
or you can use venv:
```bash
cd eval/ReFoRCE
python3 -m venv reforce
source reforce/bin/activate
pip install -r requirements.txt
```

**Execution Example:**
```bash
cd eval/ReFoRCE
./run.sh --model gpt-5-mini --dataset dw --setting 0
```

### 2. DAIL-SQL
Adpoted from [this Spider2 implementation](https://github.com/xlang-ai/Spider2/tree/main/spider2-lite/baselines/dailsql).

**Setup:**
```bash
conda create -n dailsql python=3.9 -y
conda activate dailsql
cd eval/dailsql
pip install -r requirements.txt
python nltk_downloader.py
pip install https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.5.0/en_core_web_sm-3.5.0-py3-none-any.whl
```
or you can use venv:
```bash
cd eval/dailsql
python3 -m venv dailsql
source dailsql/bin/activate
pip install -r requirements.txt
python nltk_downloader.py
pip install https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.5.0/en_core_web_sm-3.5.0-py3-none-any.whl
```

**Execution Example:**
```bash
cd eval/dailsql
./run.sh --model gpt-5.2 --dataset neutron --setting 2
```

### 3. DIN-SQL
Adpoted from [this Spider2 implementation](https://github.com/xlang-ai/Spider2/tree/main/spider2-lite/baselines/dinsql).

**Setup:**
```bash
conda create -n dinsql python=3.13 -y
conda activate dinsql
cd eval/dinsql
pip install -r requirements.txt
```
or you can use venv:
```bash
cd eval/dinsql
python3 -m venv dinsql
source dinsql/bin/activate
pip install -r requirements.txt
```

**Execution Example:**
```bash
cd eval/dinsql
./run.sh --model gpt-4 --dataset nova --setting 1
```

### 4. Few-shot
The Few-shot baseline provides a standard prompt-based evaluation.

**Setup:**
```bash
conda create -n fewshot python=3.10 -y
conda activate fewshot
cd eval/Few-shot
pip install -r requirements.txt
```
or you can use venv:
```bash
cd eval/Few-shot
python3 -m venv fewshot
source fewshot/bin/activate
pip install -r requirements.txt
```

**Execution Example:**
```bash
cd eval/Few-shot
./run.sh --model gpt-5-mini --dataset neutron --setting 2
```

---

## Advanced Evaluation (LLM-as-a-Judge)

Beyond standard execution accuracy (Exact Match), the repository includes advanced evaluation scripts located in the `eval/` root directory to analyze the reasoning capabilities of the generated SQLs.

These scripts analyze the outputs generated by the baselines.

### Query Decomposition
Evaluates if the generated SQL semantically reflects the intended query decomposition steps.
```bash
cd eval/
python evaluate_decomposition.py \
  --input_dir ReFoRCE/output/gpt-5-mini-beaver-dw-opt1-log-*/ \
  --gold_file ../data/dw/dev_sampled.json \
  --model gpt-5-mini \
  --num_workers 40
```
*(Outputs `summary.json` containing the average decomposition scores)*

### Extraction
Evaluates how well the model extracts correct database concepts (tables, columns, join keys, and domain knowledge).
```bash
cd eval/
python evaluate_extraction.py \
  --input_dir ReFoRCE/output/gpt-5-mini-beaver-dw-opt1-log-*/ \
  --gold_file ../data/dw/dev_sampled.json \
  --model gpt-5-mini \
  --num_workers 40
```
*(Outputs `summary.json` with F1 scores for the extractions)*
