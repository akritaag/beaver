# 🦫 BEAVER: An Enterprise Benchmark for Text-to-SQL

This repository contains the datasets and evaluation for the **Beaver** text-to-SQL benchmark. It includes four text-to-SQL methods for evaluation: **ReFoRCE**, **DAIL-SQL**, **DIN-SQL**, and **Few-shot**.

## Repository Structure

```
├── data/                                # Dataset files (e.g. metadata, questions, tables)
│   ├── dw/                              # `dw` database
│   │   ├── dev.json                     # Question file
│   │   ├── dev_tables.json              # Tables metadata
│   │   ├── top_tables.json              # Top-k tables for generation
│   │   └── example.json                 # Few-shot examples
│   └── ...                              # Other databases
└── eval/                                # Evaluation baselines and scripts
    ├── .env                             # Credential file (API keys + MySQL password)
    ├── ReFoRCE/                         # ReFoRCE evaluation pipeline
    ├── fewshot/                         # Few-shot evaluation pipeline
    ├── dailsql/                         # DAIL-SQL evaluation pipeline
    ├── dinsql/                          # DIN-SQL evaluation pipeline
    ├── evaluate_decomposition.py        # Script for evaluating query decomposition subtask
    └── evaluate_extraction.py           # Script for evaluating other subtasks
```

## Data Preparation

For each database (e.g., `dw`), you will need to download the necessary data files from [here](https://drive.google.com/drive/folders/1xV4Wxk_AuE8gx-Q678mas7zChrAQliof?usp=sharing) into their respective folders (e.g., `data/dw/`).
Ensure the following files are present:
- `dev.json`: The main dataset questions file.
- `dev_tables.json`: The tables and schema metadata.
- `top_tables.json`: The table retrieval rankings.
- `example.json`: Few-shot examples.

You also need to setup the MySQL databases by importing the dump files which can be downloaded from [here](https://drive.google.com/drive/folders/19bRoRxgWQLcJN3LTxwgev0xTahunjPIR?usp=drive_link). A free MySQL installation can be found [here](https://dev.mysql.com/downloads/mysql/). After the installation, import the MySQL dump files using

```
mysql -u root -p < `xxx.sql`
```

<!-- - `sample.py`: A utility script. -->

<!-- *(Optional)*: If you want to run evaluations on a smaller, sampled subset, you can execute the `sample.py` script to generate a sampled split for that dataset: -->
<!-- ```bash
cd data/neutron
python sample.py
``` -->

## Environment Setup

**Credentials (`.env` file)**

All API keys and MySQL credentials are managed through a single `eval/.env` file.

The `.env` file should contain:
```
# LLM API Keys
OPENAI_API_KEY=xxx
OPENROUTER_API_KEY=xxx
GOOGLE_API_KEY=xxx

# MySQL Credentials (shared across all databases)
MYSQL_HOST=localhost
MYSQL_USER=root
MYSQL_PASSWORD=xxx
```

**Text-to-SQL methods**

Each method has its own dependencies and environment requirements. You can manage these environments using either `conda` or `venv`.

1. ReFoRCE (adapted from [this official ReFoRCE implementation](https://github.com/Snowflake-Labs/ReFoRCE/tree/o3/methods/ReFoRCE))

setup using `conda`:
```bash
conda create -n reforce python=3.10 -y
conda activate reforce
cd eval/ReFoRCE
pip install -r requirements.txt
```
setup using `venv`:
```bash
cd eval/ReFoRCE
python3 -m venv reforce
source reforce/bin/activate
pip install -r requirements.txt
```

2. DAIL-SQL (adapted from [this Spider2 implementation](https://github.com/xlang-ai/Spider2/tree/main/spider2-lite/baselines/dailsql))

setup using `conda`:
```bash
conda create -n dailsql python=3.9 -y
conda activate dailsql
cd eval/dailsql
pip install -r requirements.txt
python nltk_downloader.py
pip install https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.5.0/en_core_web_sm-3.5.0-py3-none-any.whl
```
setup using `venv`:
```bash
cd eval/dailsql
python3 -m venv dailsql
source dailsql/bin/activate
pip install -r requirements.txt
python nltk_downloader.py
pip install https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.5.0/en_core_web_sm-3.5.0-py3-none-any.whl
```

3. DIN-SQL (adapted from [this Spider2 implementation](https://github.com/xlang-ai/Spider2/tree/main/spider2-lite/baselines/dinsql))

setup using `conda`:
```bash
conda create -n dinsql python=3.13 -y
conda activate dinsql
cd eval/dinsql
pip install -r requirements.txt
```
setup using `venv`:
```bash
cd eval/dinsql
python3 -m venv dinsql
source dinsql/bin/activate
pip install -r requirements.txt
```

4. Few-shot
<!-- The Few-shot baseline provides a standard prompt-based evaluation. -->
setup using `conda`:
```bash
conda create -n fewshot python=3.10 -y
conda activate fewshot
cd eval/few-shot
pip install -r requirements.txt
```
setup using `venv`:
```bash
cd eval/few-shot
python3 -m venv fewshot
source fewshot/bin/activate
pip install -r requirements.txt
```

<!-- The database name is automatically determined from the `--dataset` argument — no need to create separate credential files per database. -->


## Execution accuracy evaluation

<!-- In the paper, we define five subtasks: multi-table retrieval, join key detection, column mapping, domain knowledge extraction, and query decomposition. We also provide annotations for these subtasks. We provide three settings of providing these annotations as oracle hints. -->

<!-- ### Settings of subtask annotations -->
All baselines can be executed using the `run.sh` script in their respective folders with named arguments:

For example, to execute ReFoRCE,
```bash
cd eval/ReFoRCE
./run.sh --model <model> --dataset <dataset> --setting <0|1|2>
```

- **Setting 0** *(Standard baseline)*: Standard end-to-end setting with no hints. Base information with only the top-k tables provided.
- **Setting 1**: With hints for schema-linking subtasks. Includes gold tables, column mapping, and join keys.
- **Setting 2**: With hints for all subtasks. Includes **Setting 1**, domain knowledge, and subqueries.

### Unified Evaluation Structure
After generating the SQL predictions, the `run.sh` scripts will automatically execute a `unify.py` script. This executes both the predicted SQL and the gold SQL against the MySQL database and saves the outputs as CSV files inside `eval/output/unified/<baseline>/<run_name>/`. 

To obtain the final evaluation metrics (exact set match over the CSVs), you must run the `unified_evaluation.py` script on the unified directory:
```bash
cd eval
python unified_evaluation.py --unified_dir output/unified/<baseline>/<run_name>
```
*(Outputs `evaluation_summary.json` containing the execution accuracy and detailed question-level results)*

For details on subtasks, please refer to the paper.

## Subtask evaluation

Beyond standard execution accuracy (exact match), we also provide fine-grained evaluation on the subtasks. These scripts analyze the outputs generated by the methods above, so you need to first execute `run.sh` before you can obtain the subtask performance.

**Query decomposition subtask**
Evaluates if the generated SQL semantically reflects the intended query decomposition steps.
```bash
cd eval/
python evaluate_decomposition.py \
  --input_dir ReFoRCE/output/gpt-5-mini-beaver-dw-setting0-log-*/ \
  --gold_file ../data/dw/dev.json \
  --model gpt-5-mini \
  --baseline_method reforce \
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
  --baseline_method reforce \
  --num_workers 40
```
*(Outputs `summary.json` with F1 scores for these tasks)*
