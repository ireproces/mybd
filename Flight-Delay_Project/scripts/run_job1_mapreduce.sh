#!/bin/bash

# force the script to the project root
cd /app || exit 1

# Script for automatic execution of Job 1 v1 and Job 1 v2

# path settings
JAR_FILE="src/job1_mapreduce/target/job1-1.0-SNAPSHOT.jar"
INPUT_DIR="dataset/processed"
OUTPUT_BASE="results/job1_mapreduce"

# if no env parameter is passed to the script, it uses "Local_1" by default
ENV_NAME=${1:-"Local_1"}

echo "======================================================="
echo " Starting the jobs"
echo "======================================================="

# reduces system log printouts and keeps only the ERROR lines
export HADOOP_ROOT_LOGGER="ERROR,console"

# defines the list of datasets to test
DATASETS=("flight_10.parquet" "flight_25.parquet" "flight_50.parquet" "flight_75.parquet" "flight_100.parquet" "flight_150.parquet" "flight_200.parquet" "flight_300.parquet")

for DATASET in "${DATASETS[@]}"; do
    echo ""
    echo "Processing the $DATASET dataset:"

    # name extraction without extension (.parquet) for directory creation
    BASENAME=${DATASET%.parquet}

    # JOB 1
    OUT_DIR_J1="$OUTPUT_BASE/job1_v1/$BASENAME"
    # removes the output folder if it already exists
    rm -rf "$OUT_DIR_J1"

    echo " Starting Job 1 v1 [Carrier and Origin Airport]..."
    hadoop jar "$JAR_FILE" job1_v1.Job1V1Driver "$INPUT_DIR/$DATASET" "$OUT_DIR_J1" "$ENV_NAME"

    # JOB 1 v2
    OUT_DIR_J2="$OUTPUT_BASE/job1_v2/$BASENAME"
    rm -rf "$OUT_DIR_J2"

    echo " Starting Job 1 v2 [Carrier and Route]..."
    hadoop jar "$JAR_FILE" job1_v2.Job1V2Driver "$INPUT_DIR/$DATASET" "$OUT_DIR_J2" "$ENV_NAME"
done

echo ""
echo "======================================================="
echo " Jobs completed!"
echo "======================================================="