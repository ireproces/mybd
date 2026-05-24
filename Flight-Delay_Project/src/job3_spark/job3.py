import sys
import time
import os
from pyspark.sql import SparkSession
from pyspark.sql.functions import col, count, avg, round, rank
from pyspark.sql.window import Window

DEBUG_MODE = False

# function to initialize the SparkSession with a specified application
# name given as an input parameter
def init_spark(appName="FlightData_Job3"):
    if DEBUG_MODE: print(" SparkSession Initialization...")

    spark = SparkSession.builder.appName(appName).getOrCreate()
    # to avoid excessive logging in console
    spark.sparkContext.setLogLevel("ERROR")

    if DEBUG_MODE: print(" SparkSession Ready!")
    return spark

# function to load the data from the specified input path (on hdfs) given as an input parameter,
def load_data(spark, input_path):
    print(f" Loading data from: {input_path}")

    # the function reads the parquet file and create a Spark DataFrame from it
    df = spark.read.parquet(input_path)

    if DEBUG_MODE: print(" Data loaded successfully!")
    return df

# function to calculate metrics for a single airline at an origin airport
def compute_carrier_stats(df):
    print(" Computing individual carrier statistics...")

    df_computed = df.groupBy("origin", "op_unique_carrier").agg(
        count("*").alias("total_flights"),
        round(avg("dep_delay"), 2).alias("avg_dep_delay"),
        round(avg("arr_delay"), 2).alias("avg_arr_delay"),
        round(avg("cancelled") * 100, 2).alias("cancellation_rate") 
    )
    if DEBUG_MODE: print(" Statistics calculated successfully!")

    return df_computed

# function to calculate the global departure average for the entire origin airport
def compute_airport_stats(df):
    print(" Computing global airport average...")

    df_computed = df.groupBy("origin").agg(
        round(avg("dep_delay"), 2).alias("airport_avg_dep_delay")
    )
    if DEBUG_MODE: print(" Statistics calculated successfully!")

    return df_computed

# function to join data and calculate the deviation from the airport average
def join_and_compute_difference(carrier_stats, airport_stats):
    print(" Joining datasets and computing differences...")

    joined_df = carrier_stats.join(airport_stats, on="origin", how="inner")
    if DEBUG_MODE: print(" Join performed successfully!")
    
    final_df = joined_df.withColumn(
        "diff_from_airport_avg", 
        round(col("avg_dep_delay") - col("airport_avg_dep_delay"), 2)
    )
    if DEBUG_MODE: print(" Difference calculated successfully!")

    return final_df

# function to assign the position in the ranking
def rank_carriers(df):
    print(" Ranking carriers...")

    # definition of the 'window': a partitioning per airport, sorted by increasing delay
    window_spec = Window.partitionBy("origin").orderBy(col("avg_dep_delay").asc())
    
    ranked_df = df.withColumn("carrier_rank", rank().over(window_spec))
    
    # final aesthetic sorting (by airport and by ranking)
    return ranked_df.orderBy("origin", "carrier_rank")

# function to save the final DataFrame to hdfs in Parquet format
def save_results(df, output_path):
    print(f" Saving results to hdfs: {output_path}")
    df.write.mode("overwrite").parquet(output_path)

    if DEBUG_MODE: print(" Results saved successfully!")

# function to save the metrics in a local CSV file
def save_performance_metrics(env_name, input_path, duration):
    print(" Saving performance metrics...")

    perf_file = "/app/results/job3_spark/job3_performance.csv"
    os.makedirs(os.path.dirname(perf_file), exist_ok=True)
    
    file_exists = os.path.isfile(perf_file)
    dataset_name = os.path.basename(input_path)
    
    with open(perf_file, "a") as f:
        if not file_exists:
            f.write("Environment,Dataset,Execution_Time_Sec\n")
        f.write(f"{env_name},{dataset_name},{duration:.3f}\n")

    if DEBUG_MODE: print(f" Metrics saved successfully!")

# function to orchestrate the entire process
def main():

    # 0. Command-line arguments checking
    # if the number of arguments is not 4, stop the execution
    if len(sys.argv) != 4:
        print("[EXECUTION ERROR] Usage: spark-submit job3_spark.py <input_path> <output_path> <env_name>")
        sys.exit(-1)

    input_path = sys.argv[1]
    output_path = sys.argv[2]
    env_name = sys.argv[3]

    # --- Job 3 Pipeline ---
    print("Starting the pipeline...")

    start_time = time.time()

    # 1. Initialize SparkSession
    if DEBUG_MODE: print("\nStarting the Phase 1...")
    spark = init_spark()
    if DEBUG_MODE: print("Phase 1 completed.")

    # 2. Data loading
    if DEBUG_MODE: print("\nStarting the Phase 2...")
    df = load_data(spark, input_path)
    if DEBUG_MODE: print("Phase 2 completed.")

    # 3. Calculation logic
    if DEBUG_MODE: print("\nStarting the Phase 3...")

    # 3A. individual company statistics for each airport
    if DEBUG_MODE: print("\nStarting the Step A...")
    carrier_stats = compute_carrier_stats(df)
    if DEBUG_MODE: print("Step A completed.")

    # 3B. global airport statistics
    if DEBUG_MODE: print("\nStarting the Step B...")
    airport_stats = compute_airport_stats(df)
    if DEBUG_MODE: print("Step B completed.")

    # 3C/3D. union of the two levels of aggregation and calculation of the difference
    if DEBUG_MODE: print("\nStarting the Step C and D...")
    compared_df = join_and_compute_difference(carrier_stats, airport_stats)
    if DEBUG_MODE: print("Step C and D completed.")

    # 3E. ranking via Window Function
    if DEBUG_MODE: print("\nStarting the Step E...")
    final_output_df = rank_carriers(compared_df)
    if DEBUG_MODE: print("Step E completed.")

    if DEBUG_MODE: print("\nPhase 3 completed.")

    # 4. Saving the dataset in hdfs in Parquet format
    if DEBUG_MODE: print("\nStarting the Phase 4...")
    save_results(final_output_df, output_path)
    if DEBUG_MODE: print("Phase 4 completed.")

    # 5. session closure and time calculation
    if DEBUG_MODE: print("\nStarting the Phase 5...")
    end_time = time.time()
    duration = end_time - start_time
    spark.stop()
    if DEBUG_MODE: print("Phase 5 completed.")

    # 6. Saving locally
    if DEBUG_MODE: print("\nStarting the Phase 6...")
    save_performance_metrics(env_name, input_path, duration)
    if DEBUG_MODE: print("Phase 6 completed.\n")

    print("Pipeline completed successfully!")

if __name__ == "__main__":
    main()