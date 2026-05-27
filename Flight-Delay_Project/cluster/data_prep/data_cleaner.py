import time
import os
import csv
import sys
from pyspark.sql import SparkSession
from pyspark.sql.functions import col

# ==========================================
# DEBUG AND PERFORMANCE SWITCH
# ==========================================
# Set to True for development (print counts, detailed logs, and diagrams)
# Set to False for production (maximum speed and efficiency)
DEBUG_MODE = False

# function to initialize the SparkSession with a specified application
# name given as an input parameter
def init_spark(appName="FlightData_Preparation"):

    print(" SparkSession Initialization...")
    spark = SparkSession.builder.appName(appName).getOrCreate()
    # to avoid excessive logging in console
    spark.sparkContext.setLogLevel("ERROR")

    print(" SparkSession Ready!")
    return spark

# function to load the data from the specified input path given as an input parameter
def load_data(spark, input_path):

    print(f" Loading data from: {input_path}")

    # the function reads the CSV file and create a Spark DataFrame from it
    # header=True is used to indicate that the first row of the CSV file contains the column names
    # inferSchema=True is used to automatically detect the data types of the columns
    df_raw = spark.read.csv(input_path, header=True, inferSchema=True)

    print(" Data loaded successfully!")
    return df_raw

# function to check for duplicates and remove them if found
def remove_duplicates(df, current_count=None):

    print(" Checking for duplicates...")
    df_deduplicated = df.dropDuplicates()

    print(" Duplicate check and removal completed successfully!")

    # Debugging informations
    if DEBUG_MODE and current_count is not None:
        new_count = df_deduplicated.count()
        print(f"  Initial Records: {current_count}")
        print(f"  Final Records (cleaned): {new_count}")
        print(f"  Duplicates found and removed: {current_count - new_count}")
        return df_deduplicated, new_count
    else:
        return df_deduplicated, current_count

# function to normalize the data by column pruning and type casting
def select_and_cast_features(df):

    print(" Applying Column Pruning & Data Type Casting...")

    # uses the data dictionary to categorize the relevant columns based on the data types
    # float types relevant columns
    float_cols = ["distance", "dep_delay", "arr_delay"]
    # int types relevant columns (Spark has just one integer type, so we can unify the the
    # int64 and the Int64 that appears in the data dictionary)
    int_cols = [
        "month", "crs_dep_time", "cancelled",
        "carrier_delay", "weather_delay", "nas_delay",
        "security_delay", "late_aircraft_delay"
    ]
    # string types relevant columns
    string_cols = ["op_unique_carrier", "origin", "dest", "cancellation_code"]

    # DataFrame with only the selected columns, the rest are dropped
    all_columns = float_cols + int_cols + string_cols
    df_pruned = df.select([col(c) for c in all_columns])

    # new copy of the pruned DataFrame where the type casting will be applied iteratively for each column
    df_casted = df_pruned

    for c in float_cols:
        df_casted = df_casted.withColumn(c, col(c).cast("float"))
    for c in int_cols:
        df_casted = df_casted.withColumn(c, col(c).cast("integer")) 
    for c in string_cols:
        df_casted = df_casted.withColumn(c, col(c).cast("string"))

    print(" Column Pruning & Data Type Casting completed successfully!")

    # Debugging informations
    if DEBUG_MODE:
        total_cols = len(df.columns)
        print(f"  Total columns in the raw dataset: {total_cols}")
        print(f"  Total columns after pruning: {len(df_casted.columns)}")

    return df_casted

# function to engineer new features
def engineer_features(df):

    print(" Applying Feature Engineering...")

    # extraction of the hour from the scheduled departure time
    df_engineered = df.withColumn("hour", (col("crs_dep_time").cast("int") / 100).cast("int"))

    # fill NA only for delay causes (if there is no delay the cause is worth 0)
    delay_causes = ["carrier_delay", "weather_delay", "nas_delay", "security_delay", "late_aircraft_delay"]
    df_engineered = df_engineered.fillna(0.0, subset=delay_causes)

    df_engineered = df_engineered.drop("crs_dep_time")

    print(" Feature Engineering completed successfully!")

    # Debugging informations
    if DEBUG_MODE:
        print("  DataFrame schema before feature engineering:")
        df.printSchema()
        print("  DataFrame schema after feature engineering:")
        df_engineered.printSchema()

    return df_engineered

# function to apply logical filters to remove erroneous records
def apply_data_quality_filters(df, current_count=None):

    print(" Applying Logical Filters...")

    # flight logic filters: the distance must be greater than 0 and the origin and destination
    # airports must exist (not null)
    condition_flight = (
        (col("distance") > 0) & 
        col("origin").isNotNull() & 
        col("dest").isNotNull()
    )

    # filters on the logic of the flights operated:
    # if a flight is operated, the departure delay and the arrival delay must be not null
    # if a flight is diverted, the arrival delay must be null
    condition_operated = (
        (col("cancelled") == 0) & 
        col("cancellation_code").isNull() &
        col("dep_delay").isNotNull() & 
        col("arr_delay").isNotNull()
    )

    # filters on the logic of cancellations: if a flight is cancelled the cancellation code
    # must be not null
    condition_cancelled = ((col("cancelled") == 1) & col("cancellation_code").isNotNull())

    # applyes the filters
    df_filtered = df.filter(
        condition_flight & (condition_operated | condition_cancelled)
    )

    print(" Logical Filtering completed successfully!")

    # Debugging informations
    if DEBUG_MODE and current_count is not None:
        new_count = df_filtered.count()
        print(f"  Records before logical filtering: {current_count}")
        print(f"  Records after logical filtering: {new_count}")
        print(f"  Erroneous records found and removed: {current_count - new_count}")
        return df_filtered, new_count
    else:
        return df_filtered, current_count

# function to save the cleaned data in Parquet format to the specified output path
def save_data(df, output_path):

    print(f" Saving cleaned dataset to: {output_path}")
    df.write.mode("overwrite").parquet(output_path)
    print(" Data saved successfully!")
    return df

# function to orchestrate the entire process
def main():

    # 0. Command-line arguments checking
    # if the number of arguments is not 4, stop the execution
    if len(sys.argv) != 4:
        print("[EXECUTION ERROR] Usage: data_cleaner.py <input_path> <output_path> <environment>")
        sys.exit(1)

    # dynamic input and output paths and environment name
    input_path = sys.argv[1] 
    output_path = sys.argv[2]
    environment = sys.argv[3]

    # --- Data Cleaning Pipeline ---
    print("Starting the Data Cleaning Pipeline...")

    start_time = time.time()

    # 1. Initialize SparkSession
    print("\nStarting the Phase 1...")
    spark = init_spark()
    print("Phase 1 completed.")

    # 2. Data loading
    print("\nStarting the Phase 2...")
    df_raw = load_data(spark, input_path)
    print("Phase 2 completed.")

    current_row_count = None
    if DEBUG_MODE: 
        current_row_count = df_raw.count()

    # 3. Duplicate check and removal
    print("\nStarting the Phase 3...")
    df_deduplicated, current_row_count = remove_duplicates(df_raw, current_row_count)
    print("Phase 3 completed.")

    # 4. Column Pruning and Type Casting
    print("\nStarting the Phase 4...")
    df_casted = select_and_cast_features(df_deduplicated)
    print("Phase 4 completed.")

    # 5. Feature Engineering
    print("\nStarting the Phase 5...")
    df_engineered = engineer_features(df_casted)
    print("Phase 5 completed.")

    # 6. Logical Filtering
    print("\nStarting the Phase 6...")
    df_filtered, current_row_count = apply_data_quality_filters(df_engineered, current_row_count)
    print("Phase 6 completed.")

    # 7. Saving the cleaned dataset in Parquet format
    print("\nStarting the Phase 7...")
    df_cleaned = save_data(df_filtered, output_path)
    print("Phase 7 completed.")

    end_time = time.time()
    duration = end_time - start_time

    print("\nData Cleaning completed successfully!\n")
    
    if DEBUG_MODE: df_cleaned.printSchema()

    print("Calculating final metrics for the report...")
    initial_records = df_raw.count()
    final_records = df_cleaned.count()
    dropped_records = initial_records - final_records

    # Saving performances
    perf_dir = "/home/hadoop/results/data_prep"
    perf_file = f"{perf_dir}/data_cleaner_performance.csv"
    
    if not os.path.exists(perf_dir):
        os.makedirs(perf_dir)
        
    is_new = not os.path.exists(perf_file)
    
    with open(perf_file, "a", newline="") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(["Environment", "Dataset", "Execution_Time_Sec", "Initial_Records", "Final_Records", "Dropped_Records"])
        
        dataset_name = os.path.basename(sys.argv[1])
        writer.writerow([environment, dataset_name, f"{duration:.3f}", initial_records, final_records, dropped_records])

if __name__ == "__main__":
    main()