#!/bin/bash

# Enable script termination on error
set -e

# Force the script to the project root
cd /app || exit 1

# colors for readable output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[0;33m'
NC='\033[0m'

echo -e "======================================================================"
echo -e "[Data Ingestion] Start loading data into HDFS..."
echo -e "======================================================================"

# Dynamic path settings
# $1: local directory containing generated Parquet files
LOCAL_DATA_DIR=${1:-"/app/dataset/processed"}
# $2: destination directory in HDFS file system
HDFS_TARGET_DIR=${2:-"/user/hadoop/flight_data"}

# Checking the status of HDFS directories and cleaning them up if necessary
echo -e "\\n${BLUE}[*] Checking existing directories on HDFS...${NC}"
if hdfs dfs -test -d "${HDFS_TARGET_DIR}" 2>/dev/null; then
    echo -e "${YELLOW}[!] The directory ${HDFS_TARGET_DIR} already exists on HDFS.${NC}"
    echo -e "${YELLOW}[!] Recursively remove old data to avoid duplicates or conflicts...${NC}"
    hdfs dfs -rm -r -f "${HDFS_TARGET_DIR}"
    echo -e "${GREEN}[✓] Old directory successfully removed.${NC}"
fi

# Creating the directory structure on HDFS
echo -e "\\n${BLUE}[*] Creating the directory structure on HDFS...${NC}"
hdfs dfs -mkdir -p "${HDFS_TARGET_DIR}/complete"
hdfs dfs -mkdir -p "${HDFS_TARGET_DIR}/scalability"
echo -e "${GREEN}[✓] HDFS structure created.${NC}"

# Loading the base dataset (flight_100.parquet)
if [ -e "${LOCAL_DATA_DIR}/flight_100.parquet" ]; then
    echo -e "\\n${BLUE}[*] Loading the main dataset (flight_100.parquet)...${NC}"
    START_TIME=$(date +%s)
    hdfs dfs -put "${LOCAL_DATA_DIR}/flight_100.parquet" "${HDFS_TARGET_DIR}/complete/"
    END_TIME=$(date +%s)
    DURATION=$((END_TIME - START_TIME))
    echo -e "${GREEN}[✓] Main dataset successfully loaded in ${DURATION}s.${NC}"
else
    echo -e "\\n${YELLOW}[!] [ERROR] flight_100.parquet not found in ${LOCAL_DATA_DIR}.${NC}"
fi

# Cyclic loading of generated samples
echo -e "\\n${BLUE}[*] Scalability samples loading start...${NC}"

SAMPLES=("flight_10" "flight_25" "flight_50" "flight_75" "flight_150" "flight_200" "flight_300")

for SAMPLE in "${SAMPLES[@]}"; do
    LOCAL_PATH="${LOCAL_DATA_DIR}/${SAMPLE}.parquet"
    
    if [ -e "${LOCAL_PATH}" ]; then
        echo -e "    - Loading of ${SAMPLE}.parquet..."
        START_TIME=$(date +%s)
        hdfs dfs -put "${LOCAL_PATH}" "${HDFS_TARGET_DIR}/scalability/"
        END_TIME=$(date +%s)
        DURATION=$((END_TIME - START_TIME))
        echo -e "       ${GREEN}[✓] Completed in ${DURATION}s.${NC}"
    else
        echo -e "       ${YELLOW}[!] [ERROR] ${SAMPLE}.parquet not found locally.${NC}"
    fi
done

# Final verification of the content uploaded to HDFS
echo -e "\\n${BLUE}[Data Ingestion] Verifica finale del file system HDFS:${NC}"

echo -e "\\n${BLUE}[+] Contents of the 'cleaned' folder:${NC}"
hdfs dfs -ls "${HDFS_TARGET_DIR}/complete"

echo -e "\\n${BLUE}[+] Contents of the 'scalability' folder:${NC}"
hdfs dfs -ls "${HDFS_TARGET_DIR}/scalability"

echo -e "\\n======================================================================"
echo -e "${GREEN}[✓] Data Ingestion completed successfully!${NC}"
echo -e "======================================================================"