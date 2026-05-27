#!/bin/bash
set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

ENV_NAME=${1:-"AWS_Cluster"}

# S3 paths
S3_BUCKET="s3://flight-delay-data2026"
INPUT_RAW="$S3_BUCKET/data/raw/flight_data_2024.csv"
CLEANED_OUTPUT="$S3_BUCKET/data/processed/flight_100.parquet"
SAMPLES_OUTPUT_DIR="$S3_BUCKET/data/processed/"

# master node local paths
PY_CLEANER="/home/hadoop/scripts/data_prep/data_cleaner.py"
PY_GENERATOR="/home/hadoop/scripts/data_prep/data_generator.py"
LOCAL_PERF_DIR="/home/hadoop/results/data_prep"

echo -e "======================================================="
echo -e "[Data Prep] Starting..."
echo -e "======================================================="

echo -e "\n${BLUE}[*] Synchronizing performance history from S3...${NC}"
mkdir -p "$LOCAL_PERF_DIR"
aws s3 cp "$S3_BUCKET/results/performance/data_prep/" "$LOCAL_PERF_DIR/" --recursive > /dev/null 2>&1 || true
echo -e "${GREEN}[✓] Synchronization completed!${NC}"

# Step 1: cleaning
echo -e "\\n${BLUE}[*] Starting data_cleaner script...${NC}"
spark-submit \
    --master yarn \
    --deploy-mode client \
    --conf spark.hadoop.fs.s3.impl=com.amazon.ws.emr.hadoop.fs.EmrFileSystem \
    "$PY_CLEANER" "$INPUT_RAW" "$CLEANED_OUTPUT" "$ENV_NAME"
echo -e "${GREEN}[✓] Cleaning completed!${NC}"

# Step 2: sampling generation
echo -e "\\n${BLUE}[*] Starting data_generator script...${NC}"
spark-submit \
    --master yarn \
    --deploy-mode client \
    --conf spark.hadoop.fs.s3.impl=com.amazon.ws.emr.hadoop.fs.EmrFileSystem \
    "$PY_GENERATOR" "$CLEANED_OUTPUT" "$SAMPLES_OUTPUT_DIR" "$ENV_NAME"
echo -e "${GREEN}[✓] Generation completed!${NC}"

# Step 3: saving data to
echo -e "\\n${BLUE}[*] Saving to S3...${NC}"
aws s3 cp "$LOCAL_PERF_DIR/" "$S3_BUCKET/results/performance/data_prep/" --recursive > /dev/null 2>&1
echo -e "${GREEN}[✓] Save completed!${NC}"

echo -e "\\n======================================================="
echo -e "${GREEN}[✓] Data Preparation completed successfully!${NC}"
echo -e "======================================================="