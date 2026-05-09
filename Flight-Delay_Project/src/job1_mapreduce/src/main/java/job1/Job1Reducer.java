package job1;

import java.io.IOException;
import java.util.HashSet;
import org.apache.hadoop.io.Text;
import org.apache.hadoop.mapreduce.Reducer;

// Each Reducer base class is defined by 4 parameters (Generics):
// Input Key - Text because has to match the Mapper's output key type
// Input Value - Text because has to match the Mapper's output value type
// Output Key - Text because the reducer will output strings of "Carrier, Airport" pairs
// Output Value - Text because the reducer will output a formatted string with all the metrics
public class Job1Reducer extends Reducer<Text, Text, Text, Text> {

    // output variable
    private Text resultValue = new Text();

    @Override
    protected void reduce(Text key, Iterable<Text> values, Context context) throws IOException, InterruptedException {
        
        // counters and variables for:
        // a. total flights operated by this carrier,airport ()
        int totalFlights = 0;
        // b. total cancelled flights by this carrier,airport
        int totalCancelled = 0;
        // c. minimum and maximum arrival delays
        float minDelay = Float.MAX_VALUE;
        float maxDelay = -Float.MAX_VALUE;
        // d. total sum of delays (for average calculation)
        float sumDelay = 0.0f;
        // e. total flight with valid delay (non-cancelled) (for average calculation)
        int validDelayCount = 0;
        // f. set to track unique months of operation
        // HashSet automatically handles duplicates
        HashSet<Integer> months = new HashSet<>();

        // Iterate over all flights of the specific pair carrier,airport
        for (Text val : values) {

            // (i) counts total flights for this carrier,airport
            totalFlights++;
            
            // decodes the string of values emitted by the Mapper
            String[] parts = val.toString().split(",");
            // converts the string values back to their original types
            float arrDelay = Float.parseFloat(parts[0]);
            int cancelled = Integer.parseInt(parts[1]);
            int month = Integer.parseInt(parts[2]);

            // (iv) adds the month to the set
            months.add(month);

            // counts cancelled flights
            if (cancelled == 1) {
                totalCancelled++;
            // processes only non-cancelled flights for delay metrics
            } else {
                // (ii) updates min and max values
                if (arrDelay < minDelay) minDelay = arrDelay;
                if (arrDelay > maxDelay) maxDelay = arrDelay;
                // updates sum and count for average calculation
                sumDelay += arrDelay;
                validDelayCount++;
            }
        }

        // Calculates final metrics
        // (ii) average delay
        float avgDelay = (validDelayCount > 0) ? (sumDelay / validDelayCount) : 0.0f;
        // (iii) cancellation rate
        float cancelRate = (float) totalCancelled / totalFlights;

        // handles the case where all flights are cancelled (no valid delay data)
        if (validDelayCount == 0) {
            minDelay = 0.0f;
            maxDelay = 0.0f;
        }

        // Format the output to make it readable
        String formattedOutput = String.format("Total flights operated: %d \t Min Arrival Delay: %.2f \t Max Arrival Delay: %.2f \t Avg Arrival Delay: %.2f \t Cancellation Rate: %.4f \t Months of operation: %s", 
                                                totalFlights, minDelay, maxDelay, avgDelay, cancelRate, months.toString());
        
        resultValue.set(formattedOutput);
        
        context.write(key, resultValue);
    }
}