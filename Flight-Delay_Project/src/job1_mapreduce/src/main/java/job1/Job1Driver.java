package job1;

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

public class Job1Driver {

    public static void main(String[] args) throws Exception {
        
        // 1. Parameters checking
        // 2 parameters expected: [input_path] [output_path]
        if (args.length != 2) {
            System.err.println("Usage: Job1Driver <input path> <output path>");
            System.exit(-1);
        }

        // 2. Job configuration and initialization
        Configuration conf = new Configuration();
        Job job = Job.getInstance(conf, "Flight Delay - Job 1 (Carriers and Routes)");
        
        // tells Hadoop what is the main class in the Jar file
        job.setJarByClass(Job1Driver.class);

        // 3. Set Mapper and Reducer classes
        job.setMapperClass(Job1Mapper.class);
        job.setReducerClass(Job1Reducer.class);

        // 4. Output data types definition
        job.setOutputKeyClass(Text.class);
        job.setOutputValueClass(Text.class);

        // Hadoop uses the decoder for reading the Parquet file
        job.setInputFormatClass(ExampleInputFormat.class);

        // 6. Input and Output paths
        ExampleInputFormat.addInputPath(job, new Path(args[0]));
        FileOutputFormat.setOutputPath(job, new Path(args[1]));

        // 7. Execution and Time Measurement
        System.out.println("=================================================");
        System.out.println(" Starting Job 1 - MapReduce...");
        System.out.println(" Input: " + args[0]);
        System.out.println(" Output: " + args[1]);
        System.out.println("=================================================");

        // starts the timer
        long startTime = System.currentTimeMillis();

        // launce the job and wait for its completion
        boolean success = job.waitForCompletion(true);

        if (success) {
            // stops the timer
            long endTime = System.currentTimeMillis();

            // calculates the duration in seconds
            double totalDuration = (endTime - startTime) / 1000.0;

            // retrieves official hadoop job counters
            Counters counters = job.getCounters();
            long mapTime = counters.findCounter(JobCounter.MILLIS_MAPS).getValue();
            long reduceTime = counters.findCounter(JobCounter.MILLIS_REDUCES).getValue();
            
            System.out.println("=================================================");
            System.out.println(" Job completed successfully!");
            System.out.println(" Total execution time: " + totalDuration + " seconds");
            System.out.println(" Cumulative Map time: " + (mapTime / 1000.0) + " seconds");
            System.out.println(" Cumulative Reduce time: " + (reduceTime / 1000.0) + " seconds");
            System.out.println("=================================================");

            // automatic performance report csv generation and saving
            String perfDir = "/app/results/performance";
            String perfFile = perfDir + "/job1_performance.csv";

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
                    out.println("Dataset,Total_Wall_Clock_Sec,Total_Map_Task_MS,Total_Reduce_Task_MS");
                }

                // retrieves the dataset name from the input path
                String datasetName = new File(args[0]).getName();

                // writes the performance metrics in csv format
                out.printf("%s,%.3f,%d,%d\n", datasetName, totalDuration, mapTime, reduceTime);
                out.close();
                
                System.out.println(" Metrics saved in: " + perfFile);
            } catch (Exception e) {
                System.err.println(" Error in saving metrics: " + e.getMessage());
            }

            System.exit(0);
        } else {
            System.out.println(" Error: The Job has failed.");
            System.exit(1);
        }
    }
}