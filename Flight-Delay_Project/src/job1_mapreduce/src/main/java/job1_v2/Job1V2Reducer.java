package job1_v2;

import java.io.IOException;
import java.util.HashSet;
import org.apache.hadoop.io.Text;
import org.apache.hadoop.mapreduce.Reducer;

public class Job1V2Reducer extends Reducer<Text, Text, Text, Text> {

    private Text resultValue = new Text();

    private static class RouteStats {
        int totalFlights = 0;
        int totalCancelled = 0;
        float sumDelay = 0.0f;
        int validDelayCount = 0;
        float minDelay = Float.MAX_VALUE;
        float maxDelay = -Float.MAX_VALUE;
        HashSet<Integer> months = new HashSet<>();
    }

    @Override
    protected void reduce(Text key, Iterable<Text> values, Context context) throws IOException, InterruptedException {

        RouteStats stats = new RouteStats();
        
        // 1. Aggregation
        for (Text val : values) {

            String[] parts = val.toString().split(",");

            float arrDelay = Float.parseFloat(parts[0]);
            int cancelled = Integer.parseInt(parts[1]);
            int month = Integer.parseInt(parts[2]);

            // (i) counts total flights operated
            stats.totalFlights++;
            // (iv) adds the month to the set
            stats.months.add(month);

            if (cancelled == 1) {
                stats.totalCancelled++;
            } else {
                stats.sumDelay += arrDelay;
                stats.validDelayCount++;
                // (ii) updates min and max values
                if (arrDelay < stats.minDelay) stats.minDelay = arrDelay;
                if (arrDelay > stats.maxDelay) stats.maxDelay = arrDelay;
            }
        }

        // 2. Calculation of final metrics
        // (iii) cancellation rate
        float cancelRatePct = ((float) stats.totalCancelled / stats.totalFlights) * 100;
        float avgDelay = 0.0f;
        
        if (stats.validDelayCount == 0) {
            stats.minDelay = 0.0f;
            stats.maxDelay = 0.0f;
        } else {
            // (ii) average delay
            avgDelay = stats.sumDelay / stats.validDelayCount;
        }

        // 3. Output construction
        String formattedStats = String.format("Flights Operated: %d | Min Arr Delay: %.2f min | Max Arr Delay: %.2f min | Avg Arr Delay: %.2f min | Cancel Rate: %.2f%% | Months of operation: %s",
                stats.totalFlights, stats.minDelay, stats.maxDelay, avgDelay, cancelRatePct, stats.months.toString());
        
        resultValue.set(formattedStats);
        
        context.write(key, resultValue);
    }
}