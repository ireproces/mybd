import sys
from pyspark.sql import SparkSession

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

# function to load the base (100%) dataset from the specified input path
# the base dataset is the processed and cleaned dataset
def load_data(spark, input_path):

    print(f" Loading base dataset (100%) from: {input_path}")
    df_base = spark.read.parquet(input_path)
    print(" Data loaded successfully!")
    
    return df_base

# function to generate and save sub-sampled datasets (10%, 25%, 50%, 75%)
def generate_sub_samples(df_100, output_dir, base_count=None):

    print(" Generating sub-sampled datasets...")
    
    fractions = {"10": 0.10, "25": 0.25, "50": 0.50, "75": 0.75}
    
    for name, frac in fractions.items():

        # defines the output path for the current sub-sampled dataset
        out_path = f"{output_dir}flight_{name}.parquet"
        
        # uses a seed for reproducibility and withReplacement=False to avoid duplicates
        df_sampled = df_100.sample(withReplacement=False, fraction=frac, seed=42)

        df_sampled.write.mode("overwrite").parquet(out_path)
        print(f"  {name}% dataset generated and saved!")
        
        # Debugging informations
        if DEBUG_MODE and base_count is not None:
            print(f"   Expected rows: ~{int(base_count * frac)} | Actual rows: {df_sampled.count()}")

    print(" Sub-sampling generation completed successfully!")

# function to generate and save over-sampled datasets (150%, 200%, 300%)
def generate_over_samples(df_100, output_dir, base_count=None):

    print(" Generating over-sampled datasets...")

    # --- 150% ---
    # output path for the 150% dataset
    out_150 = f"{output_dir}flight_150.parquet"

    # random sample of 50% of the original dataset
    df_50 = df_100.sample(withReplacement=False, fraction=0.50, seed=42)
    # union the original dataset with the 50% sample to create the 150% dataset
    df_150 = df_100.union(df_50)

    df_150.write.mode("overwrite").parquet(out_150)
    print(f"  150% dataset generated and saved!")
    
    if DEBUG_MODE and base_count is not None:
        print(f"   Expected rows: ~{int(base_count * 1.5)} | Actual rows: {df_150.count()}")

    # --- 200% ---
    # output path for the 200% dataset
    out_200 = f"{output_dir}flight_200.parquet"
    
    # union the original dataset with itself to create the 200% dataset
    df_200 = df_100.union(df_100)

    df_200.write.mode("overwrite").parquet(out_200)
    print(f"  200% dataset generated and saved!")

    if DEBUG_MODE and base_count is not None:
        print(f"   Expected rows: {base_count * 2} | Actual rows: {df_200.count()}")

    # --- 300% ---
    # output path for the 300% dataset
    out_300 = f"{output_dir}flight_300.parquet"
    
    # union the 200% dataset with the original dataset to create the 300% dataset
    df_300 = df_200.union(df_100)

    df_300.write.mode("overwrite").parquet(out_300)
    print(f"  300% dataset generated and saved!")

    if DEBUG_MODE and base_count is not None:
        print(f"   Expected rows: {base_count * 3} | Actual rows: {df_300.count()}")

    print(" Over-sampling generation completed successfully!")

# function to orchestrate the entire process 
def main():

    # 0. Command-line arguments checking
    # if the number of arguments is not 3 (script name + 2 parameters), stop the execution
    if len(sys.argv) != 3:
        print("EXECUTION ERROR: Missing parameters.")
        sys.exit(1)

    # dynamic input and output paths
    input_path = sys.argv[1] 
    output_dir = sys.argv[2]

    # ensures the output directory ends with a slash to avoid path concatenation errors
    if not output_dir.endswith("/"):
        output_dir += "/"

    # --- Sampling Dataset Creation Pipeline ---
    print("Starting the Sample Generation Pipeline...")

    # 1. Initialize SparkSession
    print("\nStarting the Phase 1...")
    spark = init_spark()
    print("Phase 1 completed.")

    # 2. Data loading
    print("\nStarting the Phase 2...")
    df_100 = load_data(spark, input_path)
    print("Phase 2 completed.")

    base_count = None
    if DEBUG_MODE:
        base_count = df_100.count()
        print(f" \nBase dataset has {base_count} records.")

    # 3. Sub-sampling generation
    print("\nStarting the Phase 3...")
    generate_sub_samples(df_100, output_dir, base_count)
    print("Phase 3 completed.")

    # 4. Over-sampling generation
    print("\nStarting the Phase 4...")
    generate_over_samples(df_100, output_dir, base_count)
    print("Phase 4 completed.")

    print("\nDatasets Generation completed successfully!\n")

if __name__ == "__main__":
    main()