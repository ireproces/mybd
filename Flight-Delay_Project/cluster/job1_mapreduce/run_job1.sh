#!/bin/bash
set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

VERSION=$(echo "${1:-all}" | tr '[:upper:]' '[:lower:]')
ENV_NAME=${2:-"AWS_Cluster"}

if [[ "$VERSION" != "v1" && "$VERSION" != "v2" && "$VERSION" != "all" ]]; then
    echo -e "${RED}[ERROR] Incorrect syntax! Correct usage:${NC}"
    echo -e "  $0 <v1 | v2 | all> [environment_name]"
    exit 1
fi

S3_BUCKET="s3://flight-delay-data2026"
HDFS_BASE_DIR="$S3_BUCKET/data/processed"
HDFS_OUTPUT_BASE="$S3_BUCKET/results/job1_mapreduce"

LOCAL_OUTPUT_BASE="/home/hadoop/results/job1_mapreduce"
LOCAL_PERF_DIR="/home/hadoop/results/performance/job1_mapreduce"
JAR_FILE="/home/hadoop/scripts/job1_mapreduce/job1-1.0-SNAPSHOT.jar"

if [ ! -f "$JAR_FILE" ]; then
    echo -e "${RED}[ERROR] JAR file non trovato in: ${JAR_FILE}${NC}"
    exit 1
fi

export HADOOP_ROOT_LOGGER="ERROR,console"

if [[ "$VERSION" == "v1" || "$VERSION" == "v2" ]]; then
    echo -e "======================================================="
    echo -e "[MapReduce] Starting the $VERSION job"
    echo -e "======================================================="
else
    echo -e "======================================================="
    echo -e "[MapReduce] Starting all the jobs"
    echo -e "======================================================="
fi

echo -e "\\n${BLUE}[*] Synchronizing performance history from S3...${NC}"
mkdir -p "$LOCAL_PERF_DIR"
aws s3 cp "$S3_BUCKET/results/performance/job1_mapreduce/" "$LOCAL_PERF_DIR/" --recursive > /dev/null 2>&1 || true
echo -e "${GREEN}[✓] Synchronization completed successfully!${NC}"

DATASETS=("flight_10.parquet" "flight_25.parquet" "flight_50.parquet" "flight_75.parquet" "flight_100.parquet" "flight_150.parquet" "flight_200.parquet" "flight_300.parquet")

run_mapreduce_job() {
    local v_ver=$1
    local v_dataset=$2
    
    local basename=${v_dataset%.parquet}
    local class_name=""
    
    if [ "$v_ver" == "v1" ]; then
        class_name="job1_v1.Job1V1Driver"
    else
        class_name="job1_v2.Job1V2Driver"
    fi

    local hdfs_input="$HDFS_BASE_DIR/$v_dataset"
    local hdfs_output="$HDFS_OUTPUT_BASE/job1_${v_ver}/$basename"

    echo -e "\\n${BLUE}[*] Running Job 1 ${v_ver} on the dataset: ${basename}...${NC}"
    
    aws s3 rm "$hdfs_output" --recursive 2>/dev/null || true
    hadoop jar "$JAR_FILE" "$class_name" "$hdfs_input" "$hdfs_output" "$ENV_NAME"
    
    echo -e "${GREEN}[✓] Job 1 ${v_ver} completed successfully!${NC}"

    aws s3 cp "$LOCAL_PERF_DIR/" "$S3_BUCKET/results/performance/job1_mapreduce/" --recursive > /dev/null 2>&1
}

for DATASET in "${DATASETS[@]}"; do
    if [[ "$VERSION" == "v1" || "$VERSION" == "all" ]]; then
        run_mapreduce_job "v1" "$DATASET"
    fi

    if [[ "$VERSION" == "v2" || "$VERSION" == "all" ]]; then
        run_mapreduce_job "v2" "$DATASET"
    fi
done

if [[ "$VERSION" == "v1" || "$VERSION" == "v2" ]]; then
    echo ""
    echo -e "======================================================="
    echo -e "${GREEN}[✓] Job completed!${NC}"
    echo -e "======================================================="
else
    echo ""
    echo -e "======================================================="
    echo -e "${GREEN}[✓] Jobs completed!${NC}"
    echo -e "======================================================="
fi