package job1_v2;

import java.io.IOException;
import org.apache.hadoop.io.Text;
import org.apache.hadoop.mapreduce.Mapper;
import org.apache.parquet.example.data.Group;

public class Job1V2Mapper extends Mapper<Void, Group, Text, Text> {

    private Text outKey = new Text();
    private Text outValue = new Text();

    @Override
    protected void map(Void key, Group value, Context context) throws IOException, InterruptedException {
        try {
            // 1. Keys Extraction
            String carrier = value.getString("op_unique_carrier", 0);
            String origin = value.getString("origin", 0);
            String dest = value.getString("dest", 0);

            // 2. Metric Values ​​Extraction
            int cancelled = value.getInteger("cancelled", 0);
            int month = value.getInteger("month", 0);
            float arrDelay = 0.0f;

            if (value.getFieldRepetitionCount("arr_delay") > 0) {
                arrDelay = value.getFloat("arr_delay", 0);
            }

            // 3. Output key construction
            // joins categorical keys to create a composite key to give to the Reducer
            String compositeKey = carrier + "," + origin + "-" + dest;
            outKey.set(compositeKey);

            // 4. Output value construction
            String compositeValue = arrDelay + "," + cancelled + "," + month;
            outValue.set(compositeValue);

            // 5. Emission
            context.write(outKey, outValue);

        } catch (Exception e) {
            context.getCounter("DEBUG_DATA", "Skipped_Corrupted_Lines").increment(1);
        }
    }

}