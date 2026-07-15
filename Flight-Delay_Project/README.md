 # Flight Delay Analysis Project
> In questo progetto viene effettuata un'analisi approfondita dei ritardi dei voli negli Stati Uniti nel 2024 utilizzando tecnologie per la gestione di Big Data.

## 1. Obiettivo del Progetto
L'obiettivo è analizzare un vasto dataset di voli commerciali negli Stati Uniti per estrarre insight significativi sui ritardi.
Vengono utilizzate diverse tecnologie dell'ecosistema Big Data per processare ed analizzare i dati su larga scala, rispondendo a specifiche domande di business:
1.  **statistiche per compagnia aerea**: calcolare le performance di ogni vettore (ritardi, cancellazioni) per aeroporto e per tratta.
2.  **report aggregato sui ritardi**: analizzare la distribuzione dei ritardi e le cause più frequenti per ogni aeroporto e mese.
3.  **ranking di performance**: confrontare le performance di una compagnia con la media dell'aeroporto in cui opera, stilando una classifica.

## 2. Tecnologie Utilizzate
- Java 11.0.30
- Hadoop 3.4.1
- Hive 2.3.9
- Spark 3.5.8
- Docker 4.73.0
- Python 3.10.18
- Pandas 2.2.1
- AWS EMR (per l'esecuzione su cluster)
- Amazon S3

---

## 3. Dataset
### Fonte
Il dataset originale (`flight_data_2024.csv`) è stato scaricato da Kaggle.

## 4. Exploratory Data Analysis (EDA)
Prima dell'implementazione della pipeline, è stata condotta un'analisi esplorativa approfondita sul dataset completo in un ambiente Jupyter Notebook (`report/EDA_fligh_data_2024.ipynb`).

L'uso di **PySpark** ha permesso di analizzare l'intero dataset (oltre 7 milioni di righe) in tempi ragionevoli grazie al processamento distribuito e all'esecuzione lazy.

### Fasi
Le principali fasi dell'EDA includono:
- analisi dimensionale e strutturale;
- analisi di colonne a varianza zero e ridondanza;
- ricerca di duplicati esatti e logici;
- analisi della sparsità (valori nulli);
- studio delle cause di cancellazione e di ritardo;
- analisi della distribuzione dei ritardi ed identificazione di outliers;
- correlazione tra ritardi e compagnie aeree, mesi e fasce orarie.

### Risultati
Le scoperte di questa analisi preliminare hanno guidato le scelte implementative nella fase di `data_prep`.

## 5. Data Preparation
### Preparazione dei Dati (Data Cleaning)
I dati grezzi vengono processati da uno **script Spark** (`src/data_prep/data_cleaner.py`) che esegue le seguenti operazioni:
- eliminazione di record duplicati;
- selezione delle 15 colonne rilevanti per le analisi;
- casting dei tipi di dato;
- estrazione dell'ora di partenza (`hour`) da `crs_dep_time`;
- gestione dei valori nulli, in particolare per le cause di ritardo (sostituiti con `0.0`);
- filtraggio di record incompleti o non significativi (es. voli dirottati).

Il dataset pulito viene salvato in formato **Parquet** per ottimizzare lo storage e le performance di lettura.

### Generazione Dataset di Test
Per validare la scalabilità della pipeline, lo script `src/data_prep/data_generator.py` crea dei campioni del dataset pulito a diverse percentuali (10%, 25%, 50%, 75%) e tramite oversampling (150%, 200%, 300%).

## 6. Architettura e Scelte di Progetto
### Ambiente di Esecuzione: Docker
Per garantire la **riproducibilità**, l'intero ambiente di sviluppo è incapsulato in un'immagine Docker.
- **Execution Runner**: l'immagine è concepita come un "esecutore". Non avvia i demoni di Hadoop, ma fornisce tutti gli eseguibili e le librerie necessarie per lanciare i job.
- **Risparmio di Risorse**: Spark viene eseguito in modalità `local[*]` e MapReduce usa i runner locali. Questo mantiene il consumo di RAM del container molto basso (1-2 GB).
- **Inclusione di Spark**: l'intero motore Spark è incluso tramite la libreria PySpark, ottimizzando i tempi di build dell'immagine.

### Formato dei Dati: Parquet
La scelta del formato Parquet è strategica per massimizzare le performance:
- **ecosistema**: è il formato nativo per Spark e si integra perfettamente con Hive;
- **architettura colonnare**: le query leggono solo le colonne necessarie, riducendo drasticamente l'I/O;
- **compressione**: occupa una frazione dello spazio rispetto al CSV, accelerando i trasferimenti di rete;
- **conservazione dello schema**: i metadati sono inclusi nel file, eliminando la necessità di inferire lo schema e prevenendo errori di casting.

## 7. Installazione e Setup
### Prerequisiti
- Docker installato ed in esecuzione.

### Esecuzione Standalone (Locale)
1.  **Clonare il repository**
    ```bash
    git clone <repository_url>
    cd Flight-Delay_Project
    ```
2.  **Scaricare il dataset**\
    Effettuare il download del dataset e posizionare il file `flight_data_2024.csv` nella cartella `dataset/raw/` del progetto.

3.  **Avviare l'ambiente Docker**\
    Lo script `run_local.sh` si occupa di buildare l'immagine ed avviare il container, montando il progetto nella directory `/app`.
    ```bash
    cd scripts
    ./run_local.sh
    ```
    Al termine, si avrà accesso alla shell del container.

4.  **Verificare l'ambiente**\
    Dalla shell del container, eseguire i seguenti comandi per assicurarsi che le tecnologie siano installate correttamente:
    ```bash
    java -version
    hadoop version
    hive --version
    pyspark --version
    ```

### Esecuzione su Cluster (AWS EMR)
Le istruzioni assumono che sia stato creato un cluster EMR e che si sia connessi al nodo Master via SSH.

1.  **Scaricare gli script da S3**
    ```bash
    aws s3 cp s3://flight-delay-data2026/scripts/ /home/hadoop/scripts/ --recursive
    ```
2.  **Dare i permessi di esecuzione**
    ```bash
    chmod +x /home/hadoop/scripts/*.sh
    chmod +x /home/hadoop/scripts/*/*.sh
    ```
3.  **Eseguire la pipeline**\
    Lanciare i job desiderati utilizzando gli script forniti, specificando l'ambiente del cluster (es. `AWS_4Nodes`).
    ```bash
    # Esempio per il Job 1
    cd /home/hadoop/scripts/job1_mapreduce/
    ./run_job1.sh all AWS_4Nodes
    ```

## 8. Esecuzione della Pipeline
Tutti i comandi seguenti devono essere eseguiti dalla shell del container Docker, nella root del progetto (`/app`).

### a. Data Preparation
Eseguire lo script di pulizia e quello per la generazione dei campioni.

```bash
# Pulizia del dataset grezzo
spark-submit src/data_prep/data_cleaner.py dataset/raw/flight_data_2024.csv dataset/processed/flight_100.parquet Local_1

# Generazione dei dataset per i test di scalabilità
spark-submit src/data_prep/data_generator.py dataset/processed/flight_100.parquet dataset/processed/ Local_1
```

### b. Data Ingestion su HDFS (solo per ambiente locale)
Caricare i dataset processati dal disco locale al file system HDFS.

```bash
scripts/upload_to_hdfs.sh
```

### c. Esecuzione Job 1 (MapReduce)
Eseguire lo script `run_job1.sh` - è possibile specificare la versione (`v1`, `v2`, `all`).

```bash
# Esegue entrambe le versioni del job su tutti i dataset di test
scripts/run_job1.sh all Local_1
```
I risultati vengono salvati in `results/job1_mapreduce/`.

### d. Esecuzione Job 2 (Hive)
Eseguire lo script `run_job2.sh`.

```bash
scripts/run_job2.sh Local_1
```
I risultati vengono salvati in formato CSV in `results/job2_hive/`.

### e. Esecuzione Job 3 (Spark)
Eseguire lo script `run_job3.sh`.

```bash
scripts/run_job3.sh Local_1
```
I risultati vengono salvati in formato Parquet in `results/job3_spark/`.

## 9. Dettaglio delle Analisi (Jobs)
### Job 1: Statistiche per Compagnia Aerea (MapReduce)
- **Richiesta**: generare statistiche per ogni compagnia, raggruppando per aeroporto di partenza (v1) o per tratta (v2).
- **Metriche**: numero di voli, ritardo minimo/massimo/medio in arrivo, tasso di cancellazione e mesi di operatività.
- **Tecnologia**: **MapReduce** - scelta dettata dalla natura dell'analisi, che è un'evoluzione del classico pattern "WordCount" e si adatta bene al modello di programmazione a batch di MapReduce.
- **Logica**: un `Driver` Java configura e lancia il job. Il `Mapper` estrae la coppia (compagnia, aeroporto/tratta) come chiave e le metriche rilevanti come valore. La fase di `Shuffle & Sort` raggruppa i valori per chiave. Il `Reducer` aggrega i valori per calcolare le statistiche finali.

### Job 2: Report Aggregato sui Ritardi (Hive)
- **Richiesta**: per ogni aeroporto e mese, calcolare il numero di voli in 3 fasce di ritardo (basso, medio, alto), il ritardo medio per fascia e le 3 cause di ritardo/cancellazione più frequenti.
- **Tecnologia**: **Hive (HQL)** - scelta ideale perché le metriche richiedono aggregazioni complesse, filtri e classifiche (ranking), operazioni che sono l'essenza dell'analisi relazionale su cui si basa SQL.
- **Logica**: una singola query HQL strutturata con CTE (Common Table Expressions) esegue l'analisi:
    1.  `FlightBase`: classifica ogni volo in una fascia di ritardo e identifica la causa principale.
    2.  `AggStats`: calcola le metriche aggregate (conteggio voli, ritardi medi) per aeroporto, mese e fascia.
    3.  `RankedCauses`: utilizza una Window Function (`ROW_NUMBER()`) per classificare le cause di ritardo in base alla frequenza.
    4.  `JOIN` finale e **Pivot Condizionale**: unisce le statistiche aggregate con le cause classificate, trasformando le prime 3 cause da righe a colonne.

### Job 3: Ranking di Performance delle Compagnie (Spark)
- **Richiesta**: confrontare le performance di ogni compagnia con la media dell'aeroporto di partenza. Il report deve includere: voli operati, ritardo medio, tasso di cancellazione, differenza rispetto alla media dell'aeroporto e posizione in classifica.
- **Tecnologia**: **Spark** - scelta dettata dalla necessità di eseguire aggregazioni multiple in sequenza. L'architettura In-Memory di Spark è perfetta per questo, poiché mantiene i DataFrame intermedi in RAM, evitando costose operazioni di I/O su disco tra uno step e l'altro.
- **Logica**: La pipeline PySpark è orchestrata in più passaggi:
    1.  Calcolo delle statistiche per singola compagnia (`groupBy` su `origin` e `op_unique_carrier`).
    2.  Calcolo delle statistiche medie per aeroporto (usando una Window Function su `origin`).
    3.  `Join` dei due DataFrame per avere sulla stessa riga sia la metrica della compagnia sia quella dell'aeroporto.
    4.  Calcolo della differenza e classificazione finale tramite la funzione `rank()` su una Window.