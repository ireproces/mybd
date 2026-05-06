# Flight Delay Project

## Project structure

1. `Dockerfile` $\to$
2. `requirements.txt`$\to$
3. `src/`$\to$ Tutto il codice sorgente
    - `data_prep/` $\to$ Script Python/Spark per pulizia dati
    - `job1_mapreduce/` $\to$ Analisi 3.1: Statistiche compagnie
    - `job2_hive/` $\to$ Analisi 3.2: Report ritardi
    - `job3_spark/` $\to$ Analisi 3.3: Ranking anomalie
4. `scripts/` $\to$ Script di esecuzione
5. `report/` $\to$ Rapporto finale
6. `dataset/` $\to$ Dataset originale suddiviso in `raw/` e `processed/`
7. `EDA_fligh_data_2024.ipynb` $\to$ EDA del dataset

## Docker Image
L'immagine è pronta per essere buildata ed eseguita in un container docker con la sola funzionalità di "Execution Runner": replica fedelmente le versioni dei software utilzzate, permettendo a chiunque di lanciare gli script Python e compilare il codice del progetto in un ambiente Linux isolato e standardizzato.

Note - Risparmio di Risorse: i demoni di Hadoop non sono avviati dentro Docker. Spark viene eseguito in modalità local[*] e MapReduce utilizza i runner locali. Questo mantiene il consumo di RAM del container bassissimo (circa 1-2 GB).

Note - Spark: l'intero motore Apache Spark pre-compilato è contenuto nella libreria PySpark, inclusa nell'immagine tramite il file requirements.txt. Questo ottimizza i tempi di build dell'immagine.

## Tecnologie utilizzate
- Java 11.0.30
- Hadoop 3.4.1
- Hive 2.3.9
- Spark 3.5.8
- Docker 4.71.0
- Python 3.10.18
- Pandas 2.2.1

## Esecuzione
0. scaricare il repository in locale
1. effettuare il download del dataset al seguente [https://www.kaggle.com/datasets/hrishitpatil/flight-data-2024?resource=download]link e posiziona tutto il contenuto nella cartella `dataset/raw/`
2. posizionarsi nella cartella `/scripts` ed eseguire lo script `run_local.sh` per avviare la build dell'immagine e, successivamente, l'avvio del container Docker --> verrà creato un volume contenente il progetto direttamente all'interno del container
2.1. per verificare che tutto sia stato configurato correttamente, eseguire i commandi `java -version`, `python --version` e
`python -c "import pandas; print(pandas.__version__)"`, `hadoop version`, `hive --version`, `pyspark --version` e verificare che i comandi siano riconosciuti e che le versioni coincidano
3. dalla shell del container, torna alla radice del progetto (se ti sei spostato) con `cd /app` ed esegui lo script `python src/data_prep/data_cleaning.py`


## Dataset
il pacchetto scaricato da Kaggle contiene:
- il dataset completo (`flight_data_2024.csv`)
- un sample pre-creato (`flight_data_2024_sample.csv`)
- un dizionario (`flight_data_2024_data_dictionary.csv`)

Il dataset completo contiene circa 7 milioni di record.

**EDA (Exploratory Data Analysis) - `flight_data_2024_sample.csv`**
1. Dimensioni e Morfologia: il campione contiene esattamente 10.000 righe e 35 colonne.
2. Anatomia dei Valori Mancanti (Nulli): strutturali:
    * `cancellation_code` (9.878 Nulli): perfettamente logico $\to$ 9.878 voli sono partiti e arrivati, mentre 122 voli sono stati cancellati (tasso di cancellazione dell'1.22%)
    * `dep_time` e `dep_delay` (116 Nulli): Sono i voli che non sono mai decollati (probabilmente cancellati prima dell'imbarco)
    * `arr_time` e `arr_delay` (164 Nulli): informazione preziosa $\to$ 164 voli senza orario di arrivo, che includono i 122 cancellati più 42 voli deviati (diverted = 1), atterrati altrove
3. Analisi dei Ritardi (Gli Outlier):
    * Ritardo medio in partenza (dep_delay): 13.00 minuti.
    * Ritardo medio in arrivo (arr_delay): 7.55 minuti (spesso gli aerei recuperano tempo in volo).
    * Outlier estremi: Il ritardo massimo registrato in partenza è di ben 2011 minuti (quasi 33 ore)
4. Cause di Cancellazione: Dei 122 voli cancellati nel sample, abbiamo 3 codici:
    * B: 74 voli (Generalmente indica cause Meteorologiche).
    * A: 31 voli (Generalmente indica cause della Compagnia aerea).
    * C: 17 voli (Generalmente indica cause del Traffico Aereo Nazionale - NAS).