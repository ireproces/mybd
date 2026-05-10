package job1;

import java.io.IOException;
import org.apache.hadoop.io.Text;
import org.apache.hadoop.mapreduce.Mapper;
import org.apache.parquet.example.data.Group;

// Each Mapper base class is defined by 4 parameters (Generics):
// Input Key -> Void, there is no input key
// Input Value -> Group, input data is in Parquet format
// Output Key -> Text, mapper will output strings keys
// Output Value -> Text, mapper will output strings data
public class Job1Mapper extends Mapper<Void, Group, Text, Text> {

    // output variables
    private Text outKey = new Text();
    private Text outValue = new Text();

    // the map method is called for each row of input data
    @Override
    protected void map(Void key, Group value, Context context) throws IOException, InterruptedException {
        try {
            // 1. Keys Extraction
            // index is 0 because parquet rows are lists and not arrays
            String carrier = value.getString("op_unique_carrier", 0);
            String origin = value.getString("origin", 0);

            // 2. Metric Values ​​Extraction
            int cancelled = value.getInteger("cancelled", 0);
            int month = value.getInteger("month", 0);
            float arrDelay = 0.0f;

            // arr_delay can be null for cancelled flights
            // checks if it exists before trying to read it
            if (value.getFieldRepetitionCount("arr_delay") > 0) {
                arrDelay = value.getFloat("arr_delay", 0);
            }

            // 3. Output key construction
            // joins categorical keys to create a composite key to give to the Reducer
            String compositeKey = carrier + "," + origin;
            outKey.set(compositeKey);

            // 4. Output value construction
            // joins metric values to create a composite value to give to the Reducer
            String compositeValue = arrDelay + "," + cancelled + "," + month;
            outValue.set(compositeValue);

            // 5. Emission
            // sends the <key,value> pair to the Shuffle & Sort phase
            context.write(outKey, outValue);

        } catch (Exception e) {
            context.getCounter("DEBUG_DATA", "Skipped_Corrupted_Lines").increment(1);
        }
    }

}