#!/bin/bash

# Script for automatic execution of Job 1 v1 and Job 1 v2

# enable script termination on error
set -e

# colors for readable output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

# force the script to the project root
if [ -d "/app" ]; then
    cd /app
fi

# Input parameter management
# Syntax: ./run_job1_mapreduce.sh <version> [environment]
# if no version parameter is passed to the script, it uses "all" by default
VERSION=$(echo "${1:-all}" | tr '[:upper:]' '[:lower:]')
# if no env parameter is passed to the script, it uses "Local_1" by default
ENV_NAME=${2:-"Local_1"}

# validation of the version parameter
if [[ "$VERSION" != "v1" && "$VERSION" != "v2" && "$VERSION" != "all" ]]; then
    echo -e "${RED}[ERROR] Incorrect syntax! Correct usage:${NC}"
    echo -e "  $0 <v1 | v2 | all> [nome_ambiente]"
    exit 1
fi

# Dynamic path settings
JAR_FILE="src/job1_mapreduce/target/job1-1.0-SNAPSHOT.jar"
# root paths on HDFS
HDFS_BASE_DIR="/user/hadoop/flight_data"
HDFS_OUTPUT_BASE="$HDFS_BASE_DIR/results/job1_mapreduce"
# root path on the local file system
LOCAL_OUTPUT_BASE="results/job1_mapreduce"

# checks for the presence of the JAR file
if [ ! -f "$JAR_FILE" ]; then
    echo -e "${RED}[ERROR] JAR file not found in: ${JAR_FILE}${NC}"
    exit 1
fi

# reduces system log printouts and keeps only the ERROR lines
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

# Defines the list of datasets to test
DATASETS=("flight_10.parquet" "flight_25.parquet" "flight_50.parquet" "flight_75.parquet" "flight_100.parquet" "flight_150.parquet" "flight_200.parquet" "flight_300.parquet")
# DATASETS=("flight_sample.parquet")

# Single job execution function
run_mapreduce_job() {
    local v_ver=$1 # version
    local v_dataset=$2 # dataset name
    local v_hdfs_sub=$3 # hdfs subfolder (complete/scalability)
    
    # name extraction without extension (.parquet) for directory creation
    local basename=${v_dataset%.parquet}
    local class_name=""
    
    if [ "$v_ver" == "v1" ]; then
        class_name="job1_v1.Job1V1Driver"
    else
        class_name="job1_v2.Job1V2Driver"
    fi

    local hdfs_input="$HDFS_BASE_DIR/$v_hdfs_sub/$v_dataset"
    local hdfs_output="$HDFS_OUTPUT_BASE/job1_${v_ver}/$basename"
    local local_output="$LOCAL_OUTPUT_BASE/job1_${v_ver}/$basename"

    echo -e "\\n${BLUE}[*] Running Job 1 ${v_ver} on the dataset: ${basename}...${NC}"
    
    # 3a. removes the hdfs output folder if it already exists
    if hdfs dfs -test -d "$hdfs_output" 2>/dev/null; then
        hdfs dfs -rm -r -f "$hdfs_output"
    fi

    # 3b. execution of the job
    hadoop jar "$JAR_FILE" "$class_name" "$hdfs_input" "$hdfs_output" "$ENV_NAME"
    
    echo -e "${GREEN}[✓] Job 1 ${v_ver} successfully completed on HDFS!${NC}"

    # 3c. synchronization of results to local disk (overwrites previous results)
    echo -e "\\n${BLUE}[*] Synchronizing results to local disk...${NC}"
    rm -rf "$local_output" # removes the local folder if it already exists
    mkdir -p "$(dirname "$local_output")"
    
    hdfs dfs -get "$hdfs_output" "$local_output"
    echo -e "${GREEN}[✓] Local results ready in: ${local_output}${NC}"
}

# Execution cycle on all datasets
for DATASET in "${DATASETS[@]}"; do

    # dynamically determining HDFS subfolder
    if [ "$DATASET" == "flight_sample.parquet" ]; then
        HDFS_SUBFOLDER="test" # flight_sample.parquet resides in /test
    elif [ "$DATASET" == "flight_100.parquet" ]; then
        HDFS_SUBFOLDER="complete" # flight_100.parquet resides in /complete
    else
        HDFS_SUBFOLDER="scalability" # other samples are in /scalability
    fi

    # conditional execution based on user choice
    if [[ "$VERSION" == "v1" || "$VERSION" == "all" ]]; then
        run_mapreduce_job "v1" "$DATASET" "$HDFS_SUBFOLDER"
    fi

    if [[ "$VERSION" == "v2" || "$VERSION" == "all" ]]; then
        run_mapreduce_job "v2" "$DATASET" "$HDFS_SUBFOLDER"
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