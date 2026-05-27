# Flight Delay Project

## Project structure
1. `Dockerfile` $\to$ immagine docker custom utilizzata dal (o dai) container
2. `requirements.txt`$\to$ requisiti di sistema
3. `src/`$\to$ codice sorgente della pipeline
    - `data_prep/` $\to$ Script Python/Spark per pulizia dati
    - `job1_mapreduce/` $\to$ Analisi 3.1: Statistiche compagnie
    - `job2_hive/` $\to$ Analisi 3.2: Report ritardi
    - `job3_spark/` $\to$ Analisi 3.3: Ranking anomalie
4. `scripts/` $\to$ Script di supporto
    - `run_local.sh` $\to$ scritp di avvio e configurazione dell'ambiente di lavoro
    - `generate_test_dataset.py` $\to$ script per la creazione dei dataset sintetici
5. `report/` $\to$ Rapporto finale ed eventuali file di supporto al lavoro
    - `EDA_fligh_data_2024.ipynb` $\to$ EDA del dataset completo
6. `dataset/` $\to$ Dataset originale
    - `raw/` $\to$ file vergini ottenuti da Kaggle
    - `processed/` $\to$ risultati delle operazioni di pipeline

## Docker Image
L'immagine è pronta per essere buildata ed eseguita in un container docker con la sola funzionalità di "Execution Runner": replica fedelmente le versioni dei software utilzzate, permettendo a chiunque di lanciare gli script Python e compilare il codice del progetto in un ambiente Linux isolato e standardizzato.

Note - Risparmio di Risorse: i demoni di Hadoop non sono avviati dentro Docker. Spark viene eseguito in modalità local[*] e MapReduce utilizza i runner locali. Questo mantiene il consumo di RAM del container bassissimo (circa 1-2 GB).

Note - Spark: l'intero motore Apache Spark pre-compilato è contenuto nella libreria PySpark, inclusa nell'immagine tramite il file requirements.txt. Questo ottimizza i tempi di build dell'immagine.

## Tecnologie utilizzate
- Java 11.0.30
- Hadoop 3.4.1
- Hive 2.3.9
- Spark 3.5.8
- Docker 4.73.0
- Python 3.10.18
- Pandas 2.2.1
- AWS Lab
- Amazon S3

## Installazione
0. scaricare il repository in locale

1. effettuare il download del dataset al seguente [https://www.kaggle.com/datasets/hrishitpatil/flight-data-2024?resource=download]link e posizionare il dataset completo (`flight_data_2024.csv`) nella cartella del repo `dataset/raw/`

### Esecuzione standalone
2. posizionarsi nella cartella `/scripts` ed eseguire lo script `run_local.sh` per avviare la build dell'immagine e, successivamente, l'avvio del container Docker (verrà creato un volume nel container, all'interno di `/app`, contenente il progetto)
2.1. per verificare che tutto sia stato configurato correttamente, eseguire i commandi `java -version`, `python --version` e `python -c "import pandas; print(pandas.__version__)"`, `hadoop version`, `hive --version`, `pyspark --version`

3. dalla shell del container eseguire il comando per la pulizia del dataset grezzo:
   `spark-submit src/data_prep/data_cleaner.py dataset/raw/flight_data_2024.csv dataset/processed/flight_100.parquet Local_1`
   - verificare che il dataset processato sia stato creato correttamente nel percorso `/dataset/processed/flight_100.parquet`
   - verificare che le statistiche sulle performance siano state create e salvate correttamente nel percorso `/results/data_prep/data_cleaner_performance.csv`
3.1. per visualizzare le prime 5 righe del dataset processato eseguire il comando `pyspark` (per entrare nella console interattiva) e successivamente
    - `df = spark.read.parquet("/app/dataset/processed/flight_100.parquet")`
    - `df.show(5)`
    per uscire dalla console digitare `exit()`

4. dalla shell container eseguire il comando per generare i dataset sample:
    `spark-submit src/data_prep/data_generator.py dataset/processed/flight_100.parquet dataset/processed/ Local_1`
    - verificare che i dataset siano stati correttamente creati e salvanti nel percorso `dataset/processed/flight_<perc>.parquet`
    - verificare che le statistiche sulle performance siano state create e salvate correttamente nel percorso `/results/data_prep/data_generator_performance.csv`

5. posizionarsi nella cartella `/scripts` (dal container) ed eseguire lo script `upload_to_hdfs.sh` che si occupa del caricamento dei dataset dal disco locale al file system distribuito di hadoop
    - cartella generale su hdfs `/user/hadoop/flight_data`
    - cartella contenente il dataset completo `/user/hadoop/flight_data/complete`
    - cartella contenente i dataset sample `/user/hadoop/flight_data/scalability`

6. sempre dalla cartella `/scripts` eseguire lo script `run_job1.sh` che si occupa dell'esecuzione automatizzata del job 1 $\to$ la sintassi del comando completo è `./run_job1 [version] [enviornment]` dove:
    - `[version]` specifica la versione del job ed accetta i valori `v1 | v2 | all` (`all` è il valore di default nel caso non venga specificato nessun parametro)
    - `[enviornment]` specifica l'ambiente dove viene eseguito il job ed accetta qualsiasi stringa (`Local_1` è il valore di default nel caso non venga specificato nessun parametro, indica un esecuzione standalone)

7. sempre dalla cartella `/scripts` eseguire lo script `run_job2.sh` che si occupa dell'esecuzione automatizzata del job 2. i risultati potranno essere visualizzati nella cartella /results/job2_hive in formato csv

8. sempre dalla cartella `/scripts` eseguire lo script `run_job3.sh` che si occupa dell'esecuzione automatizzata del job 3. i risultati potranno essere visualizzati, tramite interrogazioni in pyspark, nella cartella /results/job3_spark in formato parquet

### Esecuzione su cluster
0. creare un cluster AWS EMR e connettersi al nodo Master del cluster tramite ssh `ssh -i ~/.ssh/bigdata2026key.pem hadoop@ec2-XXX-XXX-XXX-XXX.compute-1.amazonaws.com`

1. scarica gli script da S3 `aws s3 cp s3://flight-delay-data2026/scripts/ /home/hadoop/scripts/ --recursive`
2. dai i permessi di esecuzione a tutti gli script bash `chmod +x /home/hadoop/scripts/*/*.sh`

3. lancia la data preparation `cd /home/hadoop/scripts/data_prep/` seguito da `./run_data_prep.sh AWS_4Nodes`
* i dataset processati verrano salvati su S3 in `flight-delay-data2026/data/processed`
* le performance degli script verrano salvati su S3 in `flight-delay-data2026/results/performance/data_prep`

4. lancia il job 1 `cd /home/hadoop/scripts/job1_mapreduce/` seguito da `./run_job1.sh all AWS_4Nodes`

5. lancia il job 2 `cd /home/hadoop/scripts/job2_hive/` seguito da `./run_job2.sh AWS_4Nodes`

6. lancia il job 3 `cd /home/hadoop/scripts/job3_spark/` seguito da `./run_job3.sh AWS_4Nodes`


## Dataset
il pacchetto scaricato da Kaggle contiene:
- il dataset completo (`flight_data_2024.csv`)
- un sample pre-creato (`flight_data_2024_sample.csv`)
- un dizionario dei dati (`flight_data_2024_data_dictionary.csv`)

nel repository sono già inclusi il sample ed il dizionario.

### Preparazione dei dati
I dati sono stati preparati prima di procedere con la realizzazione delle analisi, il dataset processato è stato salvato in formato .parquet per ridurre l'occupazione in memoria e rendere più efficienti le interrogazioni.
Inoltre, anziché elaborare un dataset specifico per ogni job, si è scelto di effettuare una sola preparazione che sia adatta ed efficiente all'esecuzioni di tutti i job.

### Generazione dei dataset di testing
Per testare la scalabilità (sia scale-up che scale-out) si è pensato di costruire dataset (già puliti e processati) di dimensioni differenti.

## Jobs