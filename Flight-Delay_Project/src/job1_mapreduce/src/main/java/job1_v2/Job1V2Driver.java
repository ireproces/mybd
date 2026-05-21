package job1_v2;

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

public class Job1V2Driver {

    public static void main(String[] args) throws Exception {
        
        // 1. Parameters checking
        if (args.length != 3) {
            System.err.println("[Driver - EXECUTION ERROR][!]");
            System.err.println("Correct usage: Job1v2Driver <input_path> <output_path> <environment_name>");
            System.exit(-1);
        }

        // 2. Job configuration and initialization
        Configuration conf = new Configuration();
        Job job = Job.getInstance(conf, "Flight Delay - Job 1 (Carriers and Routes)");
        
        job.setJarByClass(Job1V2Driver.class);

        // 3. Set Mapper and Reducer classes
        job.setMapperClass(Job1V2Mapper.class);
        job.setReducerClass(Job1V2Reducer.class);

        // 4. Output data types definition
        job.setOutputKeyClass(Text.class);
        job.setOutputValueClass(Text.class);

        job.setInputFormatClass(ExampleInputFormat.class);

        // 6. Input and Output paths
        ExampleInputFormat.addInputPath(job, new Path(args[0]));
        FileOutputFormat.setOutputPath(job, new Path(args[1]));

        // 7. Execution and Time Measurement
        System.out.println("[Driver] Starting the job...");

        long startTime = System.currentTimeMillis();

        boolean success = job.waitForCompletion(true);

        if (success) {
            long endTime = System.currentTimeMillis();

            double totalDuration = (endTime - startTime) / 1000.0;

            Counters counters = job.getCounters();
            long mapTime = counters.findCounter(JobCounter.MILLIS_MAPS).getValue();
            long reduceTime = counters.findCounter(JobCounter.MILLIS_REDUCES).getValue();
            long shuffleBytes = counters.findCounter(TaskCounter.MAP_OUTPUT_BYTES).getValue();
            long mapInputRecords = counters.findCounter(TaskCounter.MAP_INPUT_RECORDS).getValue();
            long reduceOutputRecords = counters.findCounter(TaskCounter.REDUCE_OUTPUT_RECORDS).getValue();
            
            System.out.println("[Driver] job done!");

            String perfDir = "/app/results/job1_mapreduce/performance";
            String perfFile = perfDir + "/job1v2_performance.csv";

            try {
                File directory = new File(perfDir);
                if (!directory.exists()) directory.mkdirs();

                File file = new File(perfFile);
                boolean isNew = !file.exists();

                PrintWriter out = new PrintWriter(new FileWriter(perfFile, true));

                if (isNew) {
                    out.println("Environment,Dataset,Total_Wall_Clock_Sec,Total_Map_Task_MS,Total_Reduce_Task_MS,Shuffle_Bytes,Map_Input_Records,Reduce_Output_Records");
                }

                String datasetName = new File(args[0]).getName();
                String environment = args[2];

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