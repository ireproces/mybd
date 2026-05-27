#!/bin/bash
set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

ENV_NAME=${1:-"AWS_Cluster"}

S3_BUCKET="s3://flight-delay-data2026"
HDFS_BASE_DIR="$S3_BUCKET/data/processed"
HDFS_OUTPUT_BASE="$S3_BUCKET/results/job3_spark"

PY_FILE="/home/hadoop/scripts/job3_spark/job3.py"
LOCAL_PERF_DIR="/home/hadoop/results/performance/job3_spark"

if [ ! -f "$PY_FILE" ]; then
    echo -e "${RED}[ERROR] Python file not found: $PY_FILE${NC}"
    exit 1
fi

echo -e "======================================================="
echo -e "[Spark] Starting the job"
echo -e "======================================================="

# Scarico storico performance per Append
echo -e "\\n${BLUE}[*] Synchronizing performance history from S3...${NC}"
mkdir -p "$LOCAL_PERF_DIR"
aws s3 cp "$S3_BUCKET/results/performance/job3_spark/job3_performance.csv" "$LOCAL_PERF_DIR/job3_performance.csv" > /dev/null 2>&1 || true

DATASETS=("flight_10.parquet" "flight_25.parquet" "flight_50.parquet" "flight_75.parquet" "flight_100.parquet" "flight_150.parquet" "flight_200.parquet" "flight_300.parquet")

for DATASET in "${DATASETS[@]}"; do
    BASENAME=${DATASET%.parquet}
    HDFS_INPUT_DIR="$HDFS_BASE_DIR/$DATASET"
    HDFS_OUTPUT_DIR="$HDFS_OUTPUT_BASE/$BASENAME"

    echo -e "\\n${BLUE}[*] Processing the dataset: ${BASENAME}...${NC}"

    aws s3 rm "$HDFS_OUTPUT_DIR" --recursive 2>/dev/null || true

    spark-submit \
        --master yarn \
        --deploy-mode client \
        --conf spark.hadoop.fs.s3.impl=com.amazon.ws.emr.hadoop.fs.EmrFileSystem \
        --name "Job3_${BASENAME}" \
        "$PY_FILE" "$HDFS_INPUT_DIR" "$HDFS_OUTPUT_DIR" "$ENV_NAME"

    echo -e "${GREEN}[✓] Job completed!${NC}"
    
    aws s3 cp "$LOCAL_PERF_DIR/" "$S3_BUCKET/results/performance/job3_spark/" --recursive > /dev/null 2>&1
done

echo -e "\\n======================================================="
echo -e "${GREEN}[✓] Job completed!${NC}"
echo -e "======================================================="