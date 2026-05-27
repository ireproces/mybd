#!/bin/bash
set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

ENV_NAME=${1:-"AWS_Cluster"}

S3_BUCKET="s3://flight-delay-data2026"
HDFS_BASE_DIR="$S3_BUCKET/data/processed"
HDFS_OUTPUT_BASE="$S3_BUCKET/results/job2_hive"

HQL_FILE="/home/hadoop/scripts/job2_hive/job2_query.hql"
LOCAL_OUTPUT_BASE="/home/hadoop/results/job2_hive"
LOCAL_PERF_DIR="/home/hadoop/results/performance/job2_hive"
PERF_FILE="${LOCAL_PERF_DIR}/job2_performance.csv"

if [ ! -f "$HQL_FILE" ]; then
    echo -e "${RED}[ERROR] HQL file not found in: $HQL_FILE${NC}"
    exit 1
fi

echo -e "======================================================="
echo -e "[Hive] Starting the job"
echo -e "======================================================="

mkdir -p "$LOCAL_OUTPUT_BASE"
mkdir -p "$LOCAL_PERF_DIR"

aws s3 cp "$S3_BUCKET/results/performance/job2_hive/job2_performance.csv" "$PERF_FILE" > /dev/null 2>&1 || true

if [ ! -f "$PERF_FILE" ]; then
    echo "Environment,Dataset,Execution_Time_Sec" > "$PERF_FILE"
fi

DATASETS=("flight_10.parquet" "flight_25.parquet" "flight_50.parquet" "flight_75.parquet" "flight_100.parquet" "flight_150.parquet" "flight_200.parquet" "flight_300.parquet")

for DATASET in "${DATASETS[@]}"; do
    BASENAME=${DATASET%.parquet}
    HDFS_INPUT_DIR="$HDFS_BASE_DIR/$DATASET"
    HDFS_OUTPUT_DIR="$HDFS_OUTPUT_BASE/$BASENAME"
    LOCAL_OUTPUT_DIR="$LOCAL_OUTPUT_BASE/$BASENAME"

    echo -e "\\n${BLUE}[*] Processing the dataset: ${BASENAME}...${NC}"

    aws s3 rm "$HDFS_OUTPUT_DIR" --recursive 2>/dev/null || true
    
    START_TIME=$(date +%s.%N)

    hive -hiveconf INPUT_PATH="$HDFS_INPUT_DIR" \
         -hiveconf OUTPUT_DIR="$HDFS_OUTPUT_DIR" \
         -f "$HQL_FILE" > /dev/null 2>&1

    END_TIME=$(date +%s.%N)
    DURATION=$(awk -v t1="$START_TIME" -v t2="$END_TIME" 'BEGIN{print t2 - t1}')
    
    echo -e "${GREEN}[✓] Job completed in $(printf "%.3f" "$DURATION") s!${NC}"
    
    printf "%s,%s,%.3f\n" "$ENV_NAME" "$DATASET" "$DURATION" >> "$PERF_FILE"

    echo -e "${BLUE}[*] Formatting results and uploading to S3...${NC}"
    rm -rf "$LOCAL_OUTPUT_DIR"
    aws s3 cp "$HDFS_OUTPUT_DIR/" "$LOCAL_OUTPUT_DIR/" --recursive > /dev/null 2>&1
    echo -e "${GREEN}[✓] Upload completed!${NC}"
    
    if [ -d "$LOCAL_OUTPUT_DIR" ]; then
        echo "origin,month,delay_band,total_flights,avg_dep_delay,avg_arr_delay,top_cause_1,top_cause_2,top_cause_3" > "${LOCAL_OUTPUT_BASE}/${BASENAME}.csv"
        cat "$LOCAL_OUTPUT_DIR"/* >> "${LOCAL_OUTPUT_BASE}/${BASENAME}.csv"
        
        aws s3 cp "${LOCAL_OUTPUT_BASE}/${BASENAME}.csv" "$HDFS_OUTPUT_BASE/${BASENAME}.csv" > /dev/null 2>&1
        aws s3 cp "$PERF_FILE" "$S3_BUCKET/results/performance/job2_hive/job2_performance.csv" > /dev/null 2>&1
        rm -rf "$LOCAL_OUTPUT_DIR"
    fi
done

echo -e "\\n======================================================="
echo -e "${GREEN}[✓] Job completed!${NC}"
echo -e "======================================================="