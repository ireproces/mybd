#!/bin/bash

# Script for automatic execution of Job 3

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
# if no env parameter is passed to the script, it uses "Local_1" by default
ENV_NAME=${1:-"Local_1"}

# Dynamic path settings
PY_FILE="src/job3_spark/job3.py"
# root paths on HDFS
HDFS_BASE_DIR="/user/hadoop/flight_data"
HDFS_OUTPUT_BASE="$HDFS_BASE_DIR/results/job3_spark"
# root path on the local file system
LOCAL_OUTPUT_BASE="results/job3_spark"

# checks for the presence of the PySpark script file
if [ ! -f "$PY_FILE" ]; then
    echo -e "${RED}[ERROR] Python file not found: $PY_FILE${NC}"
    exit 1
fi

echo -e "======================================================="
echo -e "[Spark] Starting the job"
echo -e "======================================================="

# creating a local results folder
mkdir -p "$LOCAL_OUTPUT_BASE"

# List of datasets
DATASETS=("flight_10.parquet" "flight_25.parquet" "flight_50.parquet" "flight_75.parquet" "flight_100.parquet" "flight_150.parquet" "flight_200.parquet" "flight_300.parquet")
# DATASETS=("flight_sample.parquet")

for DATASET in "${DATASETS[@]}"; do

    # dynamic HDFS folder routing
    if [ "$DATASET" == "flight_sample.parquet" ]; then
        HDFS_SUBFOLDER="test"
    elif [ "$DATASET" == "flight_100.parquet" ]; then
        HDFS_SUBFOLDER="complete"
    else
        HDFS_SUBFOLDER="scalability"
    fi

    # name extraction without extension (.parquet) for directory creation
    BASENAME=${DATASET%.parquet}

    HDFS_INPUT_DIR="$HDFS_BASE_DIR/$HDFS_SUBFOLDER/$DATASET"
    HDFS_OUTPUT_DIR="$HDFS_OUTPUT_BASE/$BASENAME"
    LOCAL_OUTPUT_DIR="$LOCAL_OUTPUT_BASE/$BASENAME"

    echo -e "\\n${BLUE}[*] Processing the dataset: ${BASENAME}...${NC}"

    # cleaning up previous output on HDFS
    if hdfs dfs -test -d "$HDFS_OUTPUT_DIR" 2>/dev/null; then
        hdfs dfs -rm -r -f "$HDFS_OUTPUT_DIR"
    fi

    spark-submit --master local[*] --name "Job3_${BASENAME}" "$PY_FILE" "$HDFS_INPUT_DIR" "$HDFS_OUTPUT_DIR" "$ENV_NAME"

    echo -e "${GREEN}[✓] Job completed on HDFS!${NC}"
    
    echo ""
    echo -e "${BLUE}[*] Synchronizing results to local disk...${NC}"
    rm -rf "$LOCAL_OUTPUT_DIR" # removes the local folder if it already exists
    hdfs dfs -get "$HDFS_OUTPUT_DIR" "$LOCAL_OUTPUT_BASE/"
    
    echo -e "${GREEN}[✓] Local results ready in: ${LOCAL_OUTPUT_DIR}${NC}"
done

echo ""
echo -e "======================================================="
echo -e "${GREEN}[✓] Job completed!${NC}"
echo -e "======================================================="