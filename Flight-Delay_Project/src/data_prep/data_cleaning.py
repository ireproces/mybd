import sys
from pyspark.sql import SparkSession
from pyspark.sql.functions import col

def main():
    print("SparkSession Initialization...")
    # 0. Creating the Spark engine instance with a specific app name
    spark = SparkSession.builder \
        .appName("FlightData_Preparation") \
        .getOrCreate()

    # to avoid excessive logging in console
    spark.sparkContext.setLogLevel("ERROR")
    
    # sys.argv is used to get the command-line arguments passed to the script
    # if the number of arguments is not 3 (script name + 2 parameters), stop the execution
    if len(sys.argv) != 3:
        print("EXECUTION ERROR: Missing parameters.")
        sys.exit(1)

    # dynamic input and output paths
    input_path = sys.argv[1] 
    output_path = sys.argv[2]

    # 1. Data loading
    print(f"Loading data from: {input_path}")

    df_raw = spark.read.csv(input_path, header=True, inferSchema=True)

    # 2. Column Pruning / Projection
    # only columns relevant to job execution and EDA discovers were retained
    columns_to_keep = [
        "op_unique_carrier", "origin", "dest", "month", 
        "crs_dep_time", "distance", "dep_delay", "arr_delay",
        "cancelled", "cancellation_code", "carrier_delay",
        "weather_delay", "nas_delay", "security_delay", "late_aircraft_delay"
    ]

    df_selected = df_raw.select([col(c) for c in columns_to_keep])

    # 3. Type casting & Feature Engineering Base
    print("Data Type Casting & Feature Engineering...")
    df_cleaned = df_selected \
        .withColumn("dep_delay", col("dep_delay").cast("float")) \
        .withColumn("arr_delay", col("arr_delay").cast("float")) \
        .withColumn("distance", col("distance").cast("float")) \
        .withColumn("cancelled", col("cancelled").cast("integer")) \
        .withColumn("hour", (col("crs_dep_time").cast("int") / 100).cast("int")) # hour extraction

    # fill NA only for delay causes (if there is no delay the cause is worth 0)
    delay_causes = ["carrier_delay", "weather_delay", "nas_delay", "security_delay", "late_aircraft_delay"]
    df_cleaned = df_cleaned.fillna(0.0, subset=delay_causes)

    # 4. Duplicate check and removal (if any)
    print("Checking for duplicates...")
    initial_records = df_cleaned.count() # comment for the complete dataset

    # removes all the identical rows in all the columns
    df_cleaned = df_cleaned.dropDuplicates()

    final_records = df_cleaned.count() # comment for the complete dataset
    duplicates_removed = initial_records - final_records

    print("\n")
    print(f"Initial Records: {initial_records}")
    print(f"Final Records (cleaned): {final_records}")
    print(f"Duplicates found and removed: {duplicates_removed}")

    # 5. Saving in Parquet
    # dropping the original departure time column as we have extracted the hour feature from it
    df_cleaned = df_cleaned.drop("crs_dep_time")
    print(f"Saving cleaned dataset to: {output_path}")

    # use 'overwrite' for avoiding errors if the script is re-run
    df_cleaned.write.mode("overwrite").parquet(output_path)

    print("Data Preparation completed successfully!")
    df_cleaned.printSchema()

if __name__ == "__main__":
    main()