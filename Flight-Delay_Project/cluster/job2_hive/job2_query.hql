-- Settings to disable compression and enable header
SET hive.exec.compress.output=false;
SET hive.cli.print.header=true; 

-- Creating and using the dedicated database
CREATE DATABASE IF NOT EXISTS flight_db;
USE flight_db;

-- Creating the External Table mapped to the Parquet files present on HDFS
DROP TABLE IF EXISTS flights_data;
CREATE EXTERNAL TABLE flights_data (
    distance FLOAT,
    dep_delay FLOAT,
    arr_delay FLOAT,
    month INT,
    cancelled INT,
    carrier_delay INT,
    weather_delay INT,
    nas_delay INT,
    security_delay INT,
    late_aircraft_delay INT,
    op_unique_carrier STRING,
    origin STRING,
    dest STRING,
    cancellation_code STRING,
    hour INT
)
STORED AS PARQUET
LOCATION '${hiveconf:INPUT_PATH}';

-- Start of Complex Query
-- processing pipeline using Common Table Expressions (CTE)
WITH 
-- STEP 1: classification of delay bands and calculation of the primary cause for each flight
FlightBase AS (
    SELECT origin, month, dep_delay, arr_delay,
        -- assignment of the delay band at departure
        CASE
            WHEN dep_delay < 15 THEN 'LOW'
            WHEN dep_delay >= 15 AND dep_delay <= 60 THEN 'MEDIUM'
            WHEN dep_delay > 60 THEN 'HIGH'
            ELSE 'UNKNOWN' 
        END as delay_band,
        -- identification of the main cause of the delay or cancellation
        CASE
            WHEN cancelled = 1 THEN CONCAT('CANCELLED_', COALESCE(cancellation_code, 'UNKNOWN'))
            WHEN carrier_delay >= GREATEST(weather_delay, nas_delay, security_delay, late_aircraft_delay) AND carrier_delay > 0 THEN 'CARRIER'
            WHEN weather_delay >= GREATEST(carrier_delay, nas_delay, security_delay, late_aircraft_delay) AND weather_delay > 0 THEN 'WEATHER'
            WHEN nas_delay >= GREATEST(carrier_delay, weather_delay, security_delay, late_aircraft_delay) AND nas_delay > 0 THEN 'NAS'
            WHEN security_delay >= GREATEST(carrier_delay, weather_delay, nas_delay, late_aircraft_delay) AND security_delay > 0 THEN 'SECURITY'
            WHEN late_aircraft_delay >= GREATEST(carrier_delay, weather_delay, nas_delay, security_delay) AND late_aircraft_delay > 0 THEN 'LATE_AIRCRAFT'
            ELSE 'NONE' 
        END as primary_cause
    FROM flights_data
),

-- STEP 2: Calculating aggregate metrics by Airport, Month and Band. Requests (a),(b)
AggStats AS (
    SELECT origin, month, delay_band, COUNT(*) as total_flights, AVG(dep_delay) as avg_dep_delay, AVG(arr_delay) as avg_arr_delay
    FROM FlightBase
    GROUP BY origin, month, delay_band
),

-- STEP 3: counting the frequencies of individual causes within each group
CauseCounts AS (
    SELECT origin, month, delay_band, primary_cause, COUNT(*) as cause_freq
    FROM FlightBase
    WHERE primary_cause != 'NONE'
    GROUP BY origin, month, delay_band, primary_cause
),

-- STEP 4: creation of internal ranking for each group via Window Function
RankedCauses AS (
    SELECT origin, month, delay_band, primary_cause, cause_freq,
        ROW_NUMBER() OVER(PARTITION BY origin, month, delay_band ORDER BY cause_freq DESC) as rnk
    FROM CauseCounts
)

-- Specify the destination HDFS folder and output format
INSERT OVERWRITE DIRECTORY '${hiveconf:OUTPUT_DIR}'
ROW FORMAT DELIMITED
FIELDS TERMINATED BY ','

-- STEP 5: final aggregation and Pivot of the Top 3 Causes. Request (c)
SELECT 
    s.origin,
    s.month,
    s.delay_band,
    s.total_flights,
    ROUND(s.avg_dep_delay, 2) as avg_dep_delay,
    ROUND(s.avg_arr_delay, 2) as avg_arr_delay,
    -- transforming ranking rows into separate columns (Pivot)
    MAX(CASE WHEN c.rnk = 1 THEN c.primary_cause ELSE NULL END) as top_cause_1,
    MAX(CASE WHEN c.rnk = 2 THEN c.primary_cause ELSE NULL END) as top_cause_2,
    MAX(CASE WHEN c.rnk = 3 THEN c.primary_cause ELSE NULL END) as top_cause_3
FROM AggStats s
LEFT JOIN RankedCauses c ON s.origin = c.origin AND s.month = c.month AND s.delay_band = c.delay_band
GROUP BY s.origin, s.month, s.delay_band, s.total_flights, s.avg_dep_delay, s.avg_arr_delay
ORDER BY s.origin, s.month, s.delay_band;