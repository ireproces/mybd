#!/bin/bash

# Script for automatic execution of Job 2

# enable script termination on error
set -e

# colors for readable output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[0;33m'
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
HQL_FILE="src/job2_hive/job2_query.hql"
# root paths on HDFS
HDFS_BASE_DIR="/user/hadoop/flight_data"
HDFS_OUTPUT_BASE="$HDFS_BASE_DIR/results/job2_hive"
# root path on the local file system
LOCAL_OUTPUT_BASE="results/job2_hive"
PERF_FILE="${LOCAL_OUTPUT_BASE}/job2_performance.csv"

# checks for the presence of the HQL file
if [ ! -f "$HQL_FILE" ]; then
    echo -e "${RED}[ERROR] HQL file not found in: $HQL_FILE${NC}"
    exit 1
fi

echo "======================================================="
echo "[Hive] Starting the job"
echo "======================================================="

# creating a local performance folder
mkdir -p "$LOCAL_OUTPUT_BASE"

# initialize the performance file header if it doesn't exist
if [ ! -f "$PERF_FILE" ]; then
    echo "Environment,Dataset,Execution_Time_Sec" > "$PERF_FILE"
fi

# Metastore management (for local Docker environment only)
if [ "$ENV_NAME" == "Local_1" ]; then
    if [ ! -d "metastore_db" ]; then
        echo -e "\\n${BLUE}[*] Inizializzazione del Metastore Derby locale...${NC}"
        schematool -dbType derby -initSchema > /dev/null 2>&1 || true
        echo -e "${GREEN}[✓] Metastore ready!${NC}"
    fi
fi

# List of datasets
# DATASETS=("flight_10.parquet" "flight_25.parquet" "flight_50.parquet" "flight_75.parquet" "flight_100.parquet" "flight_150.parquet" "flight_200.parquet" "flight_300.parquet")
DATASETS=("flight_sample.parquet")

# Hive execution cycle
for DATASET in "${DATASETS[@]}"; do

    # dynamic HDFS Folder Routing
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
    
    START_TIME=$(date +%s.%N)

    # running Hive queries passing paths via configuration variables (hiveconf)
    hive -hiveconf INPUT_PATH="$HDFS_INPUT_DIR" \
         -hiveconf OUTPUT_DIR="$HDFS_OUTPUT_DIR" \
         -f "$HQL_FILE" > /dev/null 2>&1

    END_TIME=$(date +%s.%N)
    DURATION=$(awk -v t1="$START_TIME" -v t2="$END_TIME" 'BEGIN{print t2 - t1}')
    
    echo -e "${GREEN}[✓] Job completed on HDFS in $(printf "%.3f" "$DURATION") s!${NC}"
    
    # saving performance metrics
    printf "%s,%s,%.3f\\n" "$ENV_NAME" "$DATASET" "$DURATION" >> "$PERF_FILE"

    echo ""
    echo -e "${BLUE}[*] Sincronizzazione dei risultati sul disco locale...${NC}"
    
    rm -rf "$LOCAL_OUTPUT_DIR"
    rm -f "${LOCAL_OUTPUT_BASE}/${BASENAME}.csv"
    
    # download fragments from hdfs
    hdfs dfs -get "$HDFS_OUTPUT_DIR" "$LOCAL_OUTPUT_BASE/"
    
    if [ -d "$LOCAL_OUTPUT_DIR" ]; then
        # create the file and insert the header
        echo "origin,month,delay_band,total_flights,avg_dep_delay,avg_arr_delay,top_cause_1,top_cause_2,top_cause_3" > "${LOCAL_OUTPUT_BASE}/${BASENAME}.csv"
        # appends all pure data calculated by Hive
        cat "$LOCAL_OUTPUT_DIR"/* >> "${LOCAL_OUTPUT_BASE}/${BASENAME}.csv"
        rm -rf "$LOCAL_OUTPUT_DIR"
        echo -e "${GREEN}[✓] Aggregate result available in: ${LOCAL_OUTPUT_BASE}/${BASENAME}.csv${NC}"
    fi

done

echo ""
echo -e "======================================================="
echo -e "${GREEN}[✓] Job completed!${NC}"
echo -e "======================================================="