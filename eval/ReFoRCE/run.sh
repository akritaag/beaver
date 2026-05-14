#!/bin/bash
set -e

# ============================================================================
# ReFoRCE Evaluation Pipeline for Beaver
# Usage: ./run.sh --model gpt-5-mini --dataset dw --setting 0
# ============================================================================

# --- Argument Parsing ---
MODEL="gpt-5.2"
DATASET="dw"
SETTING=0

while [[ $# -gt 0 ]]; do
    case $1 in
        --model)   MODEL="$2";   shift 2 ;;
        --dataset) DATASET="$2"; shift 2 ;;
        --setting) SETTING="$2"; shift 2 ;;
        --help|-h)
            echo "Usage: ./run.sh --model <model> --dataset <dataset> --setting <0|1|2>"
            echo ""
            echo "Arguments:"
            echo "  --model    LLM model name (default: gpt-5.2)"
            echo "  --dataset  Dataset split name, e.g. dw, sp, nova, neutron (default: dw)"
            echo "  --setting  Hint setting: 0=ETE, 1=schema hints, 2=all hints (default: 0)"
            exit 0
            ;;
        *) echo "Unknown argument: $1"; exit 1 ;;
    esac
done

# Map setting to internal option number (Python scripts use 1/2/3)
OPTION=$((SETTING + 1))

# --- Load .env ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "${SCRIPT_DIR}/../.env" ]; then
    set -a
    source "${SCRIPT_DIR}/../.env"
    set +a
fi

# --- Derived Variables ---
TIMESTAMP=$(date +"%Y%m%d-%H%M%S")
OUTPUT_PATH="output/${MODEL}-beaver-${DATASET}-setting${SETTING}-log-${TIMESTAMP}"
GOLD_RESULT_PATH="output/${MODEL}-beaver-${DATASET}-setting${SETTING}-gold-${TIMESTAMP}"
NUM_VOTES=8
NUM_WORKERS=16

# Export DB name for sql.py env-var fallback
DB_NAME="${DATASET}"
if [ "${DATASET}" == "dw_real" ]; then
    DB_NAME="dw"
fi
export MYSQL_DATABASE="${DB_NAME}"

# --- Preprocessing ---
mkdir -p "preprocessed_data/${DATASET}"

python convert_beaver_to_reforce.py \
    --beaver_questions "../../data/${DATASET}/dev_sampled.json" \
    --beaver_tables "../../data/${DATASET}/dev_tables.json" \
    --output "preprocessed_data/${DATASET}/beaver_${DATASET}_opt${OPTION}_sampled.json" \
    --db_id "${DATASET}" \
    --option ${OPTION}

DATA_FILE="preprocessed_data/${DATASET}/beaver_${DATASET}_opt${OPTION}_sampled.json"

echo "================================"
echo "ReFoRCE on Beaver ${DATASET} Dataset"
echo "Setting ${SETTING} (internal option ${OPTION})"
echo "================================"
echo "Model: $MODEL"
echo "Data: $DATA_FILE"
echo "Output Path: $OUTPUT_PATH"
echo "Gold Result Path: $GOLD_RESULT_PATH"
echo "Num Votes: $NUM_VOTES"
echo "================================"

# Step 1: Self-refinement + Majority Voting
echo ""
echo "Step 1: Self-refinement + Majority Voting"
echo "=========================================="

python run.py \
    --task beaver \
    --output_path $OUTPUT_PATH \
    --omnisql_format_pth $DATA_FILE \
    --gold_result_path $GOLD_RESULT_PATH \
    --do_self_refinement \
    --generation_model ${MODEL} \
    --max_iter 5 \
    --temperature 1 \
    --early_stop \
    --do_vote \
    --num_votes $NUM_VOTES \
    --option ${OPTION} \
    --num_workers $NUM_WORKERS

echo ""
echo "Evaluation after Step 1:"
python evaluate_beaver.py \
    --beaver_questions $DATA_FILE \
    --output_dir $OUTPUT_PATH \
    --gold_result_dir $GOLD_RESULT_PATH \
    --db_id $DATASET

# Step 2: Column Exploration + Rerun
echo ""
echo "Step 2: Column Exploration + Rerun"
echo "===================================="
python run.py \
    --task beaver \
    --output_path $OUTPUT_PATH \
    --omnisql_format_pth $DATA_FILE \
    --gold_result_path $GOLD_RESULT_PATH \
    --do_self_refinement \
    --generation_model ${MODEL} \
    --do_column_exploration \
    --column_exploration_model ${MODEL} \
    --max_iter 5 \
    --temperature 1 \
    --early_stop \
    --do_vote \
    --num_votes $NUM_VOTES \
    --option ${OPTION} \
    --num_workers $NUM_WORKERS \
    --rerun \
    --overwrite_unfinished

echo ""
echo "Evaluation after Step 2:"
python evaluate_beaver.py \
    --beaver_questions $DATA_FILE \
    --output_dir $OUTPUT_PATH \
    --gold_result_dir $GOLD_RESULT_PATH \
    --db_id $DATASET

# Step 3: Random vote for tie
echo ""
echo "Step 3: Random Vote for Tie"
echo "=============================="
python run.py \
    --task beaver \
    --output_path $OUTPUT_PATH \
    --omnisql_format_pth $DATA_FILE \
    --do_vote \
    --random_vote_for_tie \
    --num_votes $NUM_VOTES \
    --option ${OPTION} \
    --num_workers $NUM_WORKERS

echo ""
echo "Evaluation after Step 3:"
python evaluate_beaver.py \
    --beaver_questions $DATA_FILE \
    --output_dir $OUTPUT_PATH \
    --gold_result_dir $GOLD_RESULT_PATH \
    --db_id $DATASET

# Step 4: Final Vote
echo ""
echo "Step 4: Final Vote"
echo "==================="
python run.py \
    --task beaver \
    --output_path $OUTPUT_PATH \
    --omnisql_format_pth $DATA_FILE \
    --do_vote \
    --random_vote_for_tie \
    --final_choose \
    --num_votes $NUM_VOTES \
    --option ${OPTION} \
    --num_workers $NUM_WORKERS

echo ""
echo "Final Evaluation:"
python evaluate_beaver.py \
    --beaver_questions $DATA_FILE \
    --output_dir $OUTPUT_PATH \
    --gold_result_dir $GOLD_RESULT_PATH \
    --db_id $DATASET

echo ""
echo "========================================"
echo "ReFoRCE Beaver ${DATASET} Setting ${SETTING} - Generation Complete!"
echo "========================================"
echo "Results saved to: $OUTPUT_PATH"
echo "Gold results saved to: $GOLD_RESULT_PATH"

echo ""
echo "Step 5: Unifying generated SQLs..."
echo "========================================"
python unify.py \
    --input_dir $OUTPUT_PATH \
    --gold_file $DATA_FILE \
    --dataset $DATASET
