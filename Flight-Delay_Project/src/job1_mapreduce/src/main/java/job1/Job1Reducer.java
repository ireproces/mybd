package job1;

import java.io.IOException;
import java.util.HashSet;
import org.apache.hadoop.io.Text;
import org.apache.hadoop.mapreduce.Reducer;

// Each Reducer base class is defined by 4 parameters (Generics):
// Input Key -> Text, must match the mapper's output key type
// Input Value -> Text, must match the mapper's output value type
// Output Key -> Text, reducer will output strings keys
// Output Value -> Text, reducer will output string data
public class Job1Reducer extends Reducer<Text, Text, Text, Text> {

    // output variable
    private Text resultValue = new Text();

    // helper class for saving statistics
    private static class AirportStats {
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

        AirportStats stats = new AirportStats();
        
        // 1. Aggregation
        // iterates over all values of the specific <key,value> pair
        for (Text val : values) {

            // decodes the string of values emitted by the Mapper
            String[] parts = val.toString().split(",");

            float arrDelay = Float.parseFloat(parts[0]);
            int cancelled = Integer.parseInt(parts[1]);
            int month = Integer.parseInt(parts[2]);

            // (i) counts total flights operated
            stats.totalFlights++;
            // (iv) adds the month to the set
            stats.months.add(month);

            // counts cancelled flights
            if (cancelled == 1) {
                stats.totalCancelled++;
            // processes only non-cancelled flights for delay metrics
            } else {
                // updates sum and count for average calculation
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
        
        // handles the case where all flights are cancelled (no valid delay data)
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