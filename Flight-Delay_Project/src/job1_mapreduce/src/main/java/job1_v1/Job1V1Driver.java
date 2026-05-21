package job1_v1;

import org.apache.hadoop.conf.Configuration;
import org.apache.hadoop.fs.Path;
import org.apache.hadoop.io.Text;
import org.apache.hadoop.mapreduce.Job;
import org.apache.hadoop.mapreduce.lib.output.FileOutputFormat;
import org.apache.parquet.hadoop.example.ExampleInputFormat;
import java.io.FileWriter;
import java.io.PrintWriter;
import java.io.File;
import org.apache.hadoop.mapreduce.Counters;
import org.apache.hadoop.mapreduce.JobCounter;
import org.apache.hadoop.mapreduce.TaskCounter;

public class Job1V1Driver {

    public static void main(String[] args) throws Exception {
        
        // 1. Parameters checking: [input_path] [output_path] [environment_name]
        // [input_path] is the path to the input parquet format dataset
        // [output_path] is the path to the output directory where the results will be saved
        // [environment_name] is the environment in which the job runs
        if (args.length != 3) {
            System.err.println("[Driver - EXECUTION ERROR][!]");
            System.err.println("Correct usage: Job1Driver <input_path> <output_path> <environment_name>");
            System.exit(-1);
        }

        // 2. Job configuration and initialization
        Configuration conf = new Configuration();
        Job job = Job.getInstance(conf, "Flight Delay - Job 1 (Carriers and Origin Airports)");
        
        // tells Hadoop what is the main class in the Jar file
        job.setJarByClass(Job1V1Driver.class);

        // 3. Set Mapper and Reducer classes
        job.setMapperClass(Job1V1Mapper.class);
        job.setReducerClass(Job1V1Reducer.class);

        // 4. Output data types definition
        job.setOutputKeyClass(Text.class);
        job.setOutputValueClass(Text.class);

        // Hadoop uses the decoder for reading the Parquet file
        job.setInputFormatClass(ExampleInputFormat.class);

        // 6. Input and Output paths
        ExampleInputFormat.addInputPath(job, new Path(args[0]));
        FileOutputFormat.setOutputPath(job, new Path(args[1]));

        // 7. Execution and Time Measurement
        System.out.println("[Driver] Starting the job...");

        // starts the timer
        long startTime = System.currentTimeMillis();

        // launce the job and wait for its completion
        boolean success = job.waitForCompletion(true);

        if (success) {
            // stops the timer
            long endTime = System.currentTimeMillis();

            // calculates the duration in seconds
            double totalDuration = (endTime - startTime) / 1000.0;

            // retrieves official hadoop job counters for map and reduce operation times
            Counters counters = job.getCounters();
            long mapTime = counters.findCounter(JobCounter.MILLIS_MAPS).getValue();
            long reduceTime = counters.findCounter(JobCounter.MILLIS_REDUCES).getValue();
            // retrieves official hadoop task counters for information on records processed by map and reduce
            long mapInputRecords = counters.findCounter(TaskCounter.MAP_INPUT_RECORDS).getValue();
            long reduceOutputRecords = counters.findCounter(TaskCounter.REDUCE_OUTPUT_RECORDS).getValue();
            // retrieves official hadoop byte counter for shuffle operation time
            long shuffleBytes = counters.findCounter(TaskCounter.MAP_OUTPUT_BYTES).getValue();
            
            System.out.println("[Driver] job done!");

            // automatic performance report csv generation and saving
            String perfDir = "/app/results/job1_mapreduce/performance";
            String perfFile = perfDir + "/job1v1_performance.csv";

            try {
                
                // creates the directory if it doesn't exist and checks if the file already exists
                File directory = new File(perfDir);
                if (!directory.exists()) directory.mkdirs();

                File file = new File(perfFile);
                boolean isNew = !file.exists();

                // file writer in append mode
                PrintWriter out = new PrintWriter(new FileWriter(perfFile, true));

                // if the file is new, write the header
                if (isNew) {
                    out.println("Environment,Dataset,Total_Wall_Clock_Sec,Total_Map_Task_MS,Total_Reduce_Task_MS,Shuffle_Bytes,Map_Input_Records,Reduce_Output_Records");
                }

                // retrieves the dataset name from the input path
                String datasetName = new File(args[0]).getName();
                // retrives the enviornment name
                String environment = args[2];

                // writes the performance metrics in csv format
                out.printf("%s,%s,%.3f,%d,%d,%d,%d,%d\n", 
                    environment, datasetName, totalDuration, mapTime, reduceTime, shuffleBytes, mapInputRecords, reduceOutputRecords);
                out.close();
            } catch (Exception e) {
                System.err.println("[Driver - ERROR][!] in saving metrics: " + e.getMessage());
            }

            System.exit(0);
        } else {
            System.out.println("[Driver - EXECUTION ERROR][!] Job has failed.");
            System.exit(1);
        }
    }
}