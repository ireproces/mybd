# Super-Scout

**Graph-RAG conversazionale per lo scouting calcistico**, costruito sull'*European Soccer
Database* di Kaggle.\
Riceve una domanda analitica in italiano, la traduce in Cypher, la esegue su un knowledge graph Neo4j e restituisce una risposta in italiano **insieme al frammento di grafo su cui si fonda**.

La tesi del progetto è una frase: *il difetto peggiore di un sistema di scouting non è
l'errore visibile, è la risposta plausibile e falsa*. Da qui ogni scelta di questo
repository — i controlli stanno nel codice e non nella persuasione di un prompt, e ogni
risposta porta con sé l'evidenza.

## Indice

1. [Avvio rapido](#1-avvio-rapido)
2. [Architettura](#2-architettura) — struttura del repository, componenti, tecnologie
3. [I dati: dalla sorgente al grafo](#3-i-dati-dalla-sorgente-al-grafo)
4. [I guardrail sulla query generata](#4-i-guardrail-sulla-query-generata)
5. [Il sotto-grafo di evidenza](#5-il-sotto-grafo-di-evidenza)
6. [Valutazione sperimentale](#6-valutazione-sperimentale) — risultati verificati, benchmark, efficienza, difetti, limiti
7. [Dati e riproducibilità](#7-dati-e-riproducibilità)

Documenti separati: [docs/GUIDA_AVVIO.md](docs/GUIDA_AVVIO.md) (avvio e verifica passo
passo), [notebooks/super_scout_analisi.ipynb](notebooks/super_scout_analisi.ipynb) (dataset, grafo e valutazione in grafici).

---

## 1. Avvio rapido

L'intero stack (Neo4j + API + interfaccia web) gira su Docker:

```bash
cp .env.example .env          # inserire OPENAI_API_KEY
docker compose up -d
```

L'API attende il healthcheck di Neo4j prima di partire. Il dataset si carica una volta sola:

```bash
docker compose run --rm api python scripts/download_data.py
docker compose run --rm api python scripts/load_data.py
```

- **Interfaccia web**: `http://localhost:8000/` (una domanda si può precaricare con `?q=...`)
- Swagger: `http://localhost:8000/docs`
- Neo4j Browser: `http://localhost:7474`

Istruzioni complete per Windows e macOS, esecuzione fuori da Docker, conteggi attesi dopo il caricamento e diagnosi dei problemi: [docs/GUIDA_AVVIO.md](docs/GUIDA_AVVIO.md).

---

## 2. Architettura

### 2.1 Struttura del repository

```
super-scout/
├── app/                 il servizio: API, pipeline Graph-RAG, parser eventi, sotto-grafo, UI
│   ├── config.py        configurazione da .env (chiavi, Neo4j, interruttore guardrail)
│   ├── graph.py         accesso a Neo4j (driver, sessioni, vincoli)
│   ├── events.py        parser generico degli otto XML di `Match` e totali di stagione
│   ├── rag.py           il cuore: schema, prompt, 48 guardrail, ciclo di riparazione
│   ├── subgraph.py      derivazione deterministica del sotto-grafo di evidenza
│   ├── main.py          endpoint FastAPI (`/ask`, `/health`, `/`) e file statici
│   └── static/          interfaccia web (una pagina HTML + Cytoscape)
├── scripts/             pipeline dei dati: download e caricamento nel grafo
├── notebooks/           notebook di analisi: dataset, grafo e valutazione in grafici
├── tests/               221 test, eseguibili senza database
├── evaluation/          benchmark, runner e risultati della valutazione sperimentale
├── docs/                guida d'avvio e mappatura dei requisiti del corso
├── data/                dataset grezzo (escluso da git)
├── docker-compose.yml   Neo4j + API, rete e volumi
├── Dockerfile           immagine del servizio
├── requirements.txt     dipendenze Python fissate a versione
└── .env / .env.example  configurazione locale (la chiave API non è versionata)
```

Il progetto ha **quattro assi**, e ogni cartella appartiene a uno solo di essi:
1. **DATI**
  - `scripts/` - la **pipeline dei dati**: `download_data.py` scarica `hugomathien/soccer` con
  KaggleHub; `load_data.py` costruisce il grafo e la *conoscenza derivata*. Tutte le scritture sono idempotenti (`MERGE` su chiavi stabili): il comando si può rieseguire senza danni, e due passi si possono eseguire da soli su un grafo già caricato `--events` (eventi di gioco) e `--styles` (stile delle squadre).
  - `data/` - **dataset** SQL
2. **SERVIZIO** (`app/`) - tutto ciò che viene eseguito quando l'applicazione risponde. È un package Python importabile (`from app.rag import ScoutAssistant`)
3. **VERIFICA**
  - `tests/` — **la verifica automatica**: 221 test, tutti eseguibili senza database e senza chiave API: la logica che conta è funzione pura di stringhe (il Cypher generato) e di dizionari (le righe restituite). `test_safety.py` copre i guardrail (ogni test porta nel nome il difetto reale che lo ha motivato), `test_subgraph.py` la riscrittura dell'evidenza, `test_events.py` il parser degli XML, `test_styles.py` il caricamento dello stile.
  - `evaluation/` — **la valutazione**: `benchmark.json` (33 casi, 55 formulazioni, verità in Cypher scritto a mano), `run_benchmark.py` (l'esecutore e le metriche), `results/` (i risultati versionati di baseline e ablazione).
4. **DOCUMENTAZIONE**
  - `docs/`
  - `notebooks/` — **l'analisi in grafici**: `super_scout_analisi.ipynb`: 14 grafici su dataset, grafo e valutazione, con i numeri letti dalle fonti vive e non ricopiati.

### 2.2 File rilevanti

| File | Ruolo |
|---|---|
| `rag.py` | La **pipeline Graph-RAG**: descrizione dello schema per il modello, prompt di generazione del Cypher, prompt di risposta, 48 guardrail più il rifiuto delle scritture, ciclo di riparazione, composizione della risposta. |
| `subgraph.py` | Riscrive il Cypher eseguito per ottenere le **entità** del grafo invece degli scalari, rigioca la traversata collassata dalle aggregazioni e restituisce **nodi e archi dell'evidenza**. Non usa il modello. |
| `events.py` | **Parser generico** degli otto campi **XML** annidati di `Match` e calcolo dei totali di stagione. Modulo puro: nessuna dipendenza da Neo4j, quindi testabile su stringhe XML. |
| `main.py` | **Applicazione FastAPI**: `POST /ask`, `GET /health`, `GET /` (la UI), montaggio di `/static`, chiusura del driver allo spegnimento. |
| `graph.py` | Un solo **punto di accesso a Neo4j**: `query()` che appiattisce i record in dizionari, `raw_query()` che li conserva per il sotto-grafo, vincoli di unicità. |
| `config.py` | Impostazioni via `pydantic-settings` lette da `.env`, incluso `GUARDRAILS` (l'interruttore usato dall'ablazione sperimentale). |
| `static/index.html` | L'intera **interfaccia** in un file: HTML, CSS con tema chiaro/scuro, JavaScript. Traduce in italiano nomi di colonna e valori codificati del grafo. |

### 2.3 I componenti e come dialogano

```
      browser                    servizio (FastAPI, Python)                     esterni
  ┌───────────────┐      ┌──────────────────────────────────────────┐
  │  index.html   │      │                                          │
  │  + Cytoscape  │─────▶│  main.py        POST /ask                │
  │               │ JSON │     │                                    │
  │  domanda      │◀─────│     ▼                                    │
  │  risposta     │      │  rag.py  ScoutAssistant.answer()         │
  │  tabella      │      │     │                                    │
  │  Cypher       │      │     ├─ 1. filtro metriche assenti        │
  │  sotto-grafo  │      │     ├─ 2. generazione Cypher  ──────────────▶ OpenAI API
  │  tempi        │      │     ├─ 3. guardrail (48) ─── riparazione ───▶ (gpt-4o-mini)
  └───────────────┘      │     ├─ 4. esecuzione  ──────────────────────▶ Neo4j
                         │     ├─ 5. guardrail sulle righe              (bolt)
                         │     ├─ 6. evidenza (subgraph.py) ──────────▶ Neo4j
                         │     └─ 7. spiegazione ─────────────────────▶ OpenAI API
                         └──────────────────────────────────────────┘
```

Il sistema è una **pipeline deterministica con due chiamate a un modello linguistico**, non
un insieme di agenti: ogni passaggio è una funzione con ingressi e uscite verificabili.

**Il knowledge graph** non è un archivio passivo: ha un ruolo attivo nei tre momenti richiesti
dal Topic 4 — **retrieval** (il Cypher lo interroga), **reasoning** (cammini minimi,
aggregazioni su relazioni con proprietà, finestre temporali che in SQL richiederebbero join
ricorsive), **explanation** (l'evidenza è un frammento del grafo stesso). Contiene **37.346
nodi e 1.952.560 relazioni**.

**I sette passi di `answer(question)`:**

1. **Filtro delle metriche assenti.** Prima di generare qualsiasi query, la domanda viene confrontata con ciò che il dataset non contiene (minuti giocati, sostituzioni, passaggi chiave, infortuni, parate). Se la nomina, la risposta dice cosa manca e cosa c'è (`mode: "unsupported"`). Senza questo passo il modello sostituisce la metrica mancante con la più somigliante.
2. **Generazione del Cypher.** Una chiamata a OpenAI con il prompt di sistema (~13.000 token: regole, mappature italiano → Cypher, esempi corretti ed esempi sbagliati con il motivo), la descrizione dello schema (~4.000 token) e la domanda. `temperature=0` e `seed` fisso rendono la stessa domanda riproducibile.
3. **I guardrail sul testo della query**. Quando un controllo scatta la query **non viene eseguita**: torna al modello con il motivo. Tutte le violazioni vengono raccolte in un solo messaggio; il budget è di **quattro riparazioni**.
4. **Esecuzione su Neo4j.** Anche l'errore del database è un messaggio di riparazione.
5. **I guardrail sulle righe.** Alcuni difetti sono invisibili nel testo e visibili solo nel risultato: un `null` in testa a una classifica, un giocatore ripetuto. Gli omonimi vengono distinti interrogando il grafo (tre Rafinha diversi non sono un duplicato).
6. **L'evidenza**.
7. **La spiegazione.** Una seconda chiamata scrive la risposta in italiano dalle righe (al massimo 15) e dal Cypher eseguito, con l'istruzione di confrontare i requisiti della domanda con la query e dichiarare quelli che non trova. Poi **il codice riprende il controllo**: l'elenco numerato viene ricostruito dalle righe, il criterio di ordinamento dichiarato se manca, le note su copertura degli eventi e "prima fascia" anteposte.

La risposta è un JSON con `answer`, `cypher`, `rows`, `subgraph`, `repairs`, `trace` (il Cypher di ogni tentativo scartato), `timings` e `mode` (`graph_rag`, `unsupported`, `rejected`, `failed`, `configuration_required`).

**I contratti tra i componenti** sono pochi ed espliciti: UI ↔ API parlano JSON su HTTP (l'unico ingresso è `{question}`); `rag.py` raggiunge Neo4j solo attraverso `GraphStore`; `subgraph.py` riceve una stringa Cypher e restituisce nodi e archi, o `None`; `load_data.py` ↔ `events.py` si scambiano dizionari puri.

**Quando qualcosa va storto**:

| Situazione | Comportamento |
|---|---|
| Il modello produce una scrittura | Respinta (`rejected`). Il ciclo di riparazione **non** la intercetta: rimandarla al modello trasformerebbe un rifiuto in una lettura compiacente. |
| Un guardrail scatta | La query torna al modello con il motivo; fino a quattro giri. |
| Neo4j rifiuta la query | Stesso ciclo: l'errore del database è la descrizione più precisa del problema. |
| Le riparazioni si esauriscono | `failed` con una spiegazione, non un HTTP 500. |
| La riscrittura dell'evidenza fallisce | Nessun sotto-grafo; la risposta resta valida. |
| Manca la chiave API | `configuration_required`; l'interfaccia lo dice. |

### 2.4 Le tecnologie, e perché

| Tecnologia | Ruolo | Perché questa |
|---|---|---|
| **Neo4j 5.26 Community** | Memoria del dominio, interrogata in Cypher | Le tre domande più utili allo scouting — cammini tra entità, aggregazioni su eventi con proprietà, serie per stagione — in SQL richiederebbero join ricorsive o pivot che un modello genera con molta meno affidabilità. E l'evidenza per cammino non ha un equivalente naturale su tabelle. |
| **Cypher** | Linguaggio bersaglio della traduzione | Dichiarativo e leggibile: la query eseguita viene **mostrata** all'utente, ed è parte della spiegazione. |
| **OpenAI `gpt-4o-mini`** | Traduzione domanda → Cypher e sintesi della risposta | Usato con `temperature=0` e `seed` fisso. Il modello è trattato come un componente **non affidabile**: tutto ciò che produce viene verificato prima dell'uso. |
| **FastAPI + Uvicorn** | Servizio HTTP | Validazione dichiarativa con Pydantic, Swagger su `/docs`, `lifespan` per chiudere il driver. |
| **pydantic-settings** | Configurazione | Un solo punto di verità per chiavi, credenziali e interruttori. |
| **SQLite** (sorgente) | Dataset Kaggle | Non è un componente a runtime: letto una volta dal loader. |
| **KaggleHub** | Download riproducibile | Riferimento ufficiale `hugomathien/soccer`; i dati grezzi non sono versionati. |
| **Docker Compose** | Ambiente riproducibile | Healthcheck sul database e `depends_on: service_healthy` perché l'API non parta prima. Volumi nominati: il grafo sopravvive a `stop` e `down`. |
| **Cytoscape.js** | Disegno del sotto-grafo | Servita localmente: l'interfaccia funziona senza rete. |
| **pytest** | Verifica | 221 test senza database né chiave: la logica che garantisce la correttezza è pura. |

**Cosa è stato deliberatamente escluso**:
- *Framework per applicazioni LLM* (LangChain,LlamaIndex): la pipeline ha due chiamate e sette passi, tutti da controllare puntualmente; un livello di astrazione in più avrebbe reso i guardrail più difficili da scrivere e da testare.
- *Agenti cooperanti*: le responsabilità esistono come passi distinti, non come agenti — una pipeline deterministica si testa, un insieme di agenti si osserva. *RAG vettoriale*: le domande sono analitiche (conteggi, soglie, classifiche, finestre temporali) e un retrieval per similarità non può garantire completezza né esattezza numerica. *Cache delle risposte*: nasconderebbe la variabilità che la valutazione deve misurare.

---

## 3. I dati: dalla sorgente al grafo

Il caricamento non copia le tabelle nel grafo: le **interpreta**. La parte più utile allo scouting — chi gioca dove *oggi*, per chi giocava in una certa stagione, che ruolo copre, com'è andato il campionato — non esiste come colonna nella sorgente.

I gol dei giocatori sono estratti dall'XML annidato di `Match.goal` e caricati come `(:Player)-[:SCORED]->(:Match)`, una relazione per gol, con gli autogol tenuti a parte come `:OWN_GOAL`. I tipi di gol sono stati validati contro i punteggi dichiarati: `n + p + o` dà 38.612 gol contro i 38.620 di `home_team_goal + away_team_goal`, quindi `npm` e `dg` sono tentativi non finalizzati e vanno esclusi.

### 3.1 Ruolo, club attuale e stato di attività

Né la posizione né il club attuale sono memorizzati, ma entrambi si ricavano dalle distinte.\
Le colonne `home_player_X/Y` di `Match` sono le coordinate di schieramento: Y è la linea (1 portiere, 2-4 difesa, 5-8 centrocampo, 9-11 attacco), X la posizione orizzontale su una scala 1-9 centrata su 4-6. La cella più frequente della griglia di un giocatore dà il suo ruolo, validato su profili noti.

Ogni presenza in distinta è caricata anche come `(:Player)-[:APPEARED_IN]->(:Match)`, **542.267 relazioni**. Senza, le presenze esistono solo come `p.appearances`, un totale di carriera sommato su undici campionati e non scomponibile.

L'ultima presenza dà altre due cose che servono a uno scout: `(:Player)-[:CURRENT_TEAM]->(:Team)`, il club per cui gioca davvero — `PLAYS_FOR` lo lega invece a ogni club della carriera — e `p.active`, vero solo per i 4.566 giocatori scesi in campo nel 2015/2016.

Nessuna delle due dice per chi giocava in una stagione data, e *"capocannoniere della Serie A 2010/2011"* riceveva il club del 2015/2016 presentato come quello di allora: Ibrahimović, capocannoniere 2008/2009 con l'Inter, tornava come giocatore del Paris Saint-Germain. Ogni presenza registra ora anche il club per cui il giocatore è sceso in campo (`team_id` su `APPEARED_IN`), e da lì il grafo deriva `(:Player)-[:SEASON_TEAM {season, main, appearances, season_total, league, league_total, position}]->(:Team)`: **35.002 relazioni**, una per giocatore per stagione per club, con le partite giocate e il ruolo prevalente di quell'anno.
Un giocatore trasferito a gennaio ne ha due per la stessa stagione (1.285 casi), ciascuna con le proprie presenze; quella con più porta `main = true`, così `{season: X, main: true}` è esattamente una per giocatore per stagione.

### 3.2 Le classifiche, e cosa significa "prima fascia"

Il dataset non ha classifiche né alcuna nozione di livello di un club: alla richiesta di prospetti *"che non giochino già in un club di prima fascia"* il modello confrontava `t.name` con i nomi dei campionati — un filtro che non esclude nessuno — e presentava tutti i 479 candidati, Dele Alli al Tottenham compreso, come giocatori di club minori. I risultati però ci sono, quindi la classifica di ogni campionato in ogni stagione viene calcolata al caricamento e conservata come `(:Team)-[:RANKED {season, league, rank, teams, points, ...}]->(:Season)`, **1.481 relazioni** (Leicester City primo in Premier 2015/2016 con 81 punti, Juventus prima in Serie A con 91).\
"Prima fascia" diventa così una definizione verificabile e proporzionale al campionato — il 30% di testa, mai meno di 4 club, che approssima i posti europei: 4 su 10 in Svizzera, 6 su 20 in Serie A — conservata come `top` sulla relazione. La risposta dichiara la definizione nella prima riga, aggiunta dal codice e non lasciata al modello. Con questo la stessa domanda restituisce 320 giocatori.

### 3.3 La serie temporale degli attributi

`Player_Attributes` contiene 183.978 rilevazioni FIFA prese ogni poche settimane tra il 2007 e il 2016. Caricandone solo l'ultima, un giocatore si riduceva a una fotografia: nessun attributo fisico, nessun andamento. Il grafo porta ora l'intera serie condensata per stagione come `(:Player)-[:RATED {season, stamina, strength, ...35 attributi}]->(:Season)`, **54.652 relazioni** con l'ultima rilevazione di ogni stagione, più `(:Player)-[:ASSESSED {date, ...}]->(:Season)` con tutte e **167.322** le rilevazioni non vuote, per le domande sull'andamento *dentro* una stagione. Le rilevazioni senza overall non vengono caricate: conservandole, mettevano un `null` in testa a ogni classifica — Neo4j ordina i null per primi in `ORDER BY ... DESC` — e  "il miglior difensore del 2015/2016" era uno di quelli. È questo che rende rispondibili le domande su forma e tenuta: *"30 presenze per cinque stagioni consecutive senza cali drastici di stamina e strength"* dà 13 giocatori, guidati da Cristiano Ronaldo.

### 3.4 Gli eventi di gioco: la cronaca di ogni partita

`Match` porta otto colonne XML annidate — `goal`, `shoton`, `shotoff`, `foulcommit`, `card`, `cross`, `corner`, `possession` — per circa 918.000 eventi con una struttura uniforme (tipo, sottotipo, minuto, squadra, uno o due giocatori, coordinate). Caricare solo i gol significava estendere il loader a ogni nuova classe di domanda; per un progetto sui big data, selezionare i campi che servono a una domanda nota è la direzione sbagliata. Un parser generico (`app/events.py`) mappa ora ogni famiglia su una relazione con lo stesso schema: `SHOT {on_target, blocked}` (186.453), `FOUL` commessi (210.100) e `FOULED` subiti (188.396), `CARD {card_type: y | y2 | r}` (61.095), `CROSS` (270.059), `CORNER` (85.026), ognuna con minuto, sottotipo, coordinate e la stagione della partita.

L'XML dei gol contiene anche `player2`, chi ha servito l'assist, in 17.065 gol su 37.496: `(:Player)-[:ASSISTED]->(:Match)` più l'arco giocatore-giocatore `(:Player)-[:ASSISTED_GOALS_OF {goals, seasons}]->(:Player)`. Gli assist non sono più "una metrica che il database non contiene" (Özil 19 nel 2015/2016, coincidente con la stagione reale; Daniel Alves → Messi 21 gol è la coppia più prolifica).

Poiché il modello se la cava meglio con i filtri piatti che con i join a tre vie, i **totali di stagione** sono derivati al caricamento su `SEASON_TEAM`: gol, assist, tiri, tiri in porta, falli commessi e subiti, cartellini gialli e rossi, cross, corner e, per i portieri, i tiri e i tiri in porta subiti — quelli degli avversari nelle partite in cui era schierato, dato che il portiere non compare mai nell'evento del tiro (`player1` è chi calcia). *"Portieri con più tiri in porta subiti nel 2014/15 e 2015/16 mantenendo gk_reflexes e gk_positioning sopra 78"* diventa così due `SEASON_TEAM` e due `RATED` per giocatore e una somma: Zieler 275, Ruffier 244, Fährmann 237, Lloris 226.

**La copertura è l'avvertenza:** la cronaca completa esiste per **8.466 partite su 25.979** — Premier League completa; Liga, Serie A, Bundesliga e Ligue 1 in parte; Olanda un terzo; Belgio, Portogallo e Svizzera nessuna. Ogni partita porta `has_events`, ogni `SEASON_TEAM` porta `events_covered`, il prompt lo esige accanto a ogni totale, e il codice antepone la nota sulla copertura a ogni risposta che tocca un evento. Un tiro murato è in `shoton` ma non arriva mai al portiere: `blocked` è conservato, e i tiri in porta subiti lo escludono.

### 3.5 Lo stile di gioco dei club

Con gli eventi caricati, l'unica entità della sorgente ancora fuori dal grafo era `Team_Attributes`: 1.458 rilevazioni FIFA per 288 club, una all'anno (febbraio 2010-2012, settembre 2013-2015, quindi le stagioni 2009/10-2011/12 e 2013/14-2015/16 — il 2012/13 non c'è). Descrivono **come** un club gioca, non come ha fatto: velocità, dribbling e passaggio in costruzione, passaggio, cross e tiro nella creazione, pressione, aggressività, ampiezza e linea difensiva, ciascuno come valore 20-80 e come classe (`fast`, `short`, `high`, `offside_trap`...). Sono caricate come `(:Team)-[:STYLE {season, ...}]->(:Season)`, **1.457 relazioni**, e abilitano lo scouting contestuale — *"esterni con crossing sopra 80 in squadre che creano molto sui cross"* (Candreva alla Lazio, il cui valore di cross 80 è il massimo del dataset, coerente con i suoi 172 cross), *"difensori centrali veloci dietro una linea alta"* (Koulibaly) — e le domande sui club (*"le squadre di Serie A con il pressing più alto"*: Lazio 59, Milan e Fiorentina 58).

Con questo **ogni tabella dell'European Soccer Database è nel grafo**, tranne le colonne delle quote dei bookmaker in `Match`, che non sono dati di scouting.

### 3.6 Cosa il dataset non contiene

Il dataset non ha **valore di mercato, ingaggio o costo del cartellino** per nessun giocatore. Le domande su un'alternativa "economica" vengono risolte ordinando per rating FIFA crescente come approssimazione, e ogni risposta del genere lo dichiara esplicitamente invece di lasciar intendere un prezzo.

Altre metriche non hanno alcun ripiego onesto: minuti giocati, sostituzioni, passaggi chiave, infortuni e parate sono semplicemente assenti (i tiri in porta subiti si conoscono, le parate no: la differenza tra i due non è una parata). Prima che gli eventi fossero caricati, alla domanda *"giocatori con più di 10 assist in Serie A"* il modello contava `:SCORED` e chiamava il risultato `assist`, riportando i 36 gol di Higuaín come 36 assist — un numero plausibile con nulla nell'output a rivelare lo scambio. Le metriche ancora mancanti vengono riconosciute nella domanda e rifiutate **prima** che una query venga generata, con una risposta che dice cosa il database contiene davvero.

---

## 4. I guardrail sulla query generata

Due controlli sono applicati in codice, non lasciati alla buona volontà del modello:

- **Sola lettura.** Una query generata che contenga `CREATE`, `DELETE`, `DETACH`, `DROP`, `MERGE`, `REMOVE` o `SET` solleva `UnsafeCypher`, che il ciclo di riparazione deliberatamente **non** cattura: ripararla trasformerebbe una scrittura respinta in una lettura che la asseconda.
- **Conteggio distinto.** Un giocatore ha una `PLAYS_FOR` per club, quindi una query che lega sia `PLAYS_FOR` sia `SCORED` conta ogni gol una volta per squadra — i 38 gol di Ibrahimović diventano 152, senza errore visibile. Ogni conteggio di una relazione viene riscritto in `count(DISTINCT ...)`, che è sempre corretto: senza duplicazione i due coincidono.

Gli altri **48 controlli** rimandano la query al modello attraverso il ciclo di riparazione, con un messaggio che dice esattamente cosa manca. Ognuno è nato da un difetto osservato davvero; qui sono raggruppati per famiglia.

**Cosa la risposta deve contenere**:

- **Il club è obbligatorio.** Una query che proietta giocatori senza `CURRENT_TEAM` o `SEASON_TEAM` viene respinta: due risposte allo stesso tipo di domanda differivano sull'esserci o meno del club. E dev'essere una **colonna**, non un salto: con il club legato a un `(:Team)` anonimo le righe non lo contenevano e la spiegazione scriveva "Allan (Napoli)" dalla propria memoria.
- **I giocatori hanno un nome.** Righe che descrivono giocatori — club, ruolo, attributi — senza `p.name` vengono respinte: "Manchester United, centravanti, 26 gol" non dice a uno scout chi ha segnato.
- **Ciò su cui si filtra va in tabella.** Ogni valore confrontato in un `WHERE` — proprietà, alias, espressione — deve essere restituito, salvo i filtri di contesto (stagione, campionato, `active`). La regola non dipende dalla formulazione: la query stessa dice cosa è rilevante.
- **Il criterio di ordinamento è una colonna.** Una lista ordinata per `r.potential - r.overall` che restituiva overall e potential separati faceva ricalcolare i margini alla spiegazione, che li sbagliava e saltava due righe. Ogni chiave di `ORDER BY`, non solo le espressioni aritmetiche, dev'essere una colonna restituita: `ORDER BY r.overall` senza restituirlo ha fatto presentare la precisione di testa (86) come overall.

**Cosa la domanda impone**

- **Il ruolo è obbligatorio quando la domanda lo nomina** (o chiede un'alternativa a un giocatore nominato). Alla richiesta di un'alternativa economica a Hugo Lloris tra i 20 e i 22 anni con overall 78-80, il modello lasciava cadere il ruolo e proponeva Martial e Shaw; la risposta giusta è che un portiere così non esiste. E va filtrato **con un valore che esiste**: `p.position IN ['defender']` non dà righe e nessun errore, perché `defender` è un valore di `p.role`.
- **Ogni requisito riconoscibile lascia una traccia.** Una tabella mappa le parole della domanda (mancino, under 23, una stagione, un campionato, rigori, gol di testa, autogol, altezza) sul Cypher che deve esprimerle, valore compreso: alla richiesta di aggiungere il piede per "mancini", il modello scrisse `preferred_foot = 'right'`.
- **Le soglie si applicano con l'operatore della domanda.** "Sempre sopra 85" diventò `>= 85` (28 giocatori invece di 23) e "minimo 30 presenze" fu restituito ma mai filtrato (169 invece di 23).
- **Una soglia è un filtro, non una classifica.** Quando la domanda pone solo soglie e non nomina una metrica, l'ordine è l'overall restituito come colonna: ordinare per altezza, per uno degli attributi filtrati o per un conteggio di presenze mai richiesto produceva tre elenchi diversi degli stessi giocatori.
- **I nomi italiani degli attributi sono attributi.** "Precisione di testa" è `heading_accuracy`; la tabella dei requisiti leggeva "di testa" come gol di testa e pretendeva `s.subtype = 'header'`, trasformando una ricerca di difensori in una ricerca di difensori che avessero segnato di testa.

**Le convenzioni del dominio**

- **Senza stagione nella domanda, la stagione è quella corrente (2015/2016).** È uno strumento di scouting: *"difensori in Premier League con heading_accuracy > 82 e jumping > 80"* chiede chi ha quel profilo oggi. La stessa domanda in due forme tornava come un giocatore (`RATED {season: '2015/2016'}`) e come quattro (il massimo di carriera, con Distin che qualificava per un valore di anni prima). Le presenze restano cumulative.
- **Un campionato senza stagione è dove il giocatore gioca oggi.** "Difensori in Premier League" è `st.league` su `SEASON_TEAM {season: '2015/2016', main: true}`, mai un conteggio di `APPEARED_IN` o `SCORED` su `m.league`: quello conta l'intera carriera (Jelle van Damme, 4 partite in Premier anni fa, oggi allo Standard Liegi), aggiunge una colonna di presenze che nessuno ha chiesto e, passando da `SCORED`, esclude ogni difensore che non abbia segnato.
- **Una stagione nella domanda significa `RATED` di quella stagione**, mai lo snapshot di carriera su `PLAYS_FOR`: i due differiscono (354 contro 353 prospetti). E **una stagione passata non legge mai `CURRENT_TEAM`**: "rigoristi 2009/2010" perdeva Lampard e ogni ritirato.
- **Un elenco che mostra il club attuale è un elenco di giocatori attivi.** "Non giocano già in un top club" senza `p.active` dava 361 prospetti contro i 320 della stessa domanda formulata con "prima fascia" — 41 giocatori con una valutazione 2015/2016 e nessuna presenza.
- **Il piede preferito è una proprietà del giocatore.** Memorizzato solo su `PLAYS_FOR`, una volta per club, il modello lo cercava su `RATED` (dove non esiste e vale null) e, avvertito, o toglieva il filtro — Hazard e Clyne, destri, elencati tra i mancini — o insisteva fino a esaurire le riparazioni. Il loader deriva ora `p.preferred_foot` sul nodo.
- **Un intervallo di anni è un intervallo di stagioni, e una media resta una media.** "Tra il 2012 e il 2016" non attivava alcun controllo sulle stagioni, e "25 presenze medie annue" diventava `presenze >= 100`.

**Gli errori che Neo4j non segnala**

- **Ogni proprietà esiste.** Neo4j restituisce null, non un errore, per `m.home_team`: la query raggruppava tutte le partite in una riga senza squadra e la risposta era "nessuna squadra ha segnato più di 50 gol in casa". Ogni `variabile.proprietà` è verificata contro lo schema.
- **Un nome di campionato non si confronta mai con il nome di una squadra**, e una domanda sulla fascia o sulla posizione in classifica deve leggere `RANKED`.
- **Nessuna partita raggiunta due volte.** `(t:Team)-[:HOME_TEAM|AWAY_TEAM]->(m)` tocca ogni partita una volta per squadra: sommare `home_goals + away_goals` raddoppia ogni totale.
- **Nessuna soglia da stagione su una singola partita.** `m.home_goals > 50` confronta una partita con un totale che nessuna partita raggiunge.
- **Un `IN` tra tipi diversi è sempre falso.** `toInteger(...) IN anni` con `anni` di stringhe non è mai vero, e nessun errore lo rivela.
- **Una relazione legata e mai usata è un filtro silenzioso**: il pattern esige comunque che esista, ed esclude chi non ce l'ha.

**Le soglie per stagione**

- **Restano per stagione.** *"Almeno 30 presenze nelle stagioni 2014/15 e 2015/16, mantenendo la stamina sopra 85"* riceveva 296 giocatori: presenze sommate sulle due stagioni e stamina letta da una stagione qualsiasi. La risposta giusta è 23. Le stagioni nominate si trattano ora in forma piatta — una `SEASON_TEAM {season, main: true}` e una `RATED {season}` per stagione, nessuna aggregazione, il valore di ogni stagione una colonna — e i controlli rifiutano il conteggio sommato, la `RATED` non filtrata, un attributo "mantenuto" verificato con `max()` o filtrato prima di `min()`, una chiave di raggruppamento che è essa stessa una misura per stagione, una variabile aggregata e raggruppata insieme, e una variabile Team condivisa da due relazioni (che forza lo stesso club in entrambe le stagioni: 16 invece di 23).

**Gli eventi hanno trappole proprie.** I tiri subiti da un portiere non sono mai `(p)-[:SHOT]->(m)` (quelli sono i tiri che ha calciato lui); un limite superiore su un conteggio di eventi ("meno di 5 cartellini") deve leggere il totale su `SEASON_TEAM`, perché legare la relazione e contarla esclude chi ne ha zero; una relazione-evento contata senza stagione quando la domanda ne nomina una (Messi "89 assist nel 2015/2016": la sua carriera); un filtro sul campionato che la domanda non ha chiesto (copiato da un esempio: 6 portieri invece di 20); righe che descrivono una coppia di giocatori non sono doppioni.

**Infine, il codice riprende il controllo sulla risposta.** L'elenco numerato "N. Nome (Club), valore" è fatto di tre colonne note e viene **ricostruito dalle righe**: il modello, con l'overall come ultima colonna, aveva copiato al suo posto lo sprint speed (86) e invertito due giocatori.

Tutte le violazioni vengono valutate insieme e il modello riceve l'elenco completo in un solo messaggio: un difetto per giro di riparazione non bastava a una domanda con quattro difetti.\
Tutti i controlli sono coperti da `tests/test_safety.py`, e le chiamate a OpenAI portano un `seed` fisso, così la stessa domanda produce lo stesso Cypher.

---

## 5. Il sotto-grafo di evidenza

Ogni risposta porta con sé il frammento esatto di grafo su cui si fonda. Il sotto-grafo **non è prodotto dall'LLM** — un'evidenza generata dal modello potrebbe essere a sua volta allucinata. È derivato deterministicamente dal Cypher che ha prodotto la risposta: la proiezione finale viene riscritta perché la query restituisca entità del grafo invece di scalari, e le relazioni che le collegano vengono rilette da Neo4j. Quando la query aggrega, la traversata collassata dal `WITH` viene **rigiocata** con gli stessi filtri, così gli archi disegnati sono quelli che l'aggregato ha davvero consumato: per *"i 10 rigoristi del 2009/2010"*, le partite in cui quei rigori sono stati segnati. La riesecuzione è limitata per giocatore, e il sotto-grafo lo dichiara ("campione") quando un giocatore ne aveva più di quanti se ne potessero disegnare.

Gli archi disegnati tra i nodi raccolti sono solo quelli che la query ha usato: i tipi di relazione che nomina, ristretti alla stagione che nomina e alle uguaglianze che impone sulle relazioni (`s.penalty = true`, `main: true`). Prima di questo filtro, il sotto-grafo di una domanda sul 2009/2010 mostrava la `SEASON_TEAM` di ogni stagione più `PLAYS_FOR` e `CURRENT_TEAM` — 61 archi tra 21 nodi, di cui uno per nodo era pertinente. La riscrittura è coperta da `tests/test_subgraph.py`; una query che non si può riscrivere in sicurezza produce **nessun** sotto-grafo invece di uno sbagliato.

---

## 6. Valutazione sperimentale

La valutazione è di quattro tipi: **correttezza contro verità nota** (§6.1), **robustezza alla formulazione** (§6.2), **benchmark rieseguibile con ablazione dei guardrail** (§6.3) e **misure di efficienza e scala** (§6.4-6.5). Il §6.6 è il catalogo dei difetti trovati, con causa e correzione; il §6.7 i limiti dichiarati.

Macchina di riferimento: portatile macOS, Neo4j 5.26 Community e API in Docker (heap 2 GB, page cache 512 MB), modello `gpt-4o-mini` via API. Le latenze del modello dipendono dal servizio remoto: vanno lette come ordini di grandezza, mentre le proporzioni tra fasi sono stabili. Le misure di efficienza e scala (§6.4-6.5) sono state prese sul grafo di allora (919.232 relazioni, prima degli eventi e dello stile); il benchmark sul grafo attuale (1.952.560 relazioni).

### 6.1 Risultati verificati contro verità nota

Ogni domanda qui sotto è stata **eseguita davvero** contro il grafo, e il risultato confrontato con un Cypher scritto a mano oppure con fatti storici noti.

| Domanda | Esito | Verifica |
|---|---|---|
| I 10 marcatori più prolifici del 2015/2016 | Suárez 40, Ibrahimović 38, Higuaín 36, Ronaldo 35 | coincide con i capocannonieri reali |
| Capocannoniere Serie A 2010/2011 | Antonio Di Natale, 28 gol (Udinese) | fatto storico |
| I 10 giocatori con più gol di testa | de Jong 13, Bale 9, Giroud 7 | `subtype = 'header'` |
| I 10 rigoristi (intera serie) | Ronaldo 55, Ibrahimović 41, Messi 36, Totti 29 | `penalty = true` |
| I 10 giocatori con più autogol | Škrtel 7, Dunne 6, McAuley 5 | relazione `OWN_GOAL`, separata da `SCORED` |
| Confronto campionati per gol totali 2015/2016 | Liga 1.043, Premier 1.026, Serie A 979 | aggregazione su 26.000 partite |
| Oltre 150 presenze in Premier e overall medio > 75 | 60 giocatori: Howard 282, Hart 275, Barry 258 | Cypher manuale |
| Miglior marcatore di ogni squadra di Premier | Kane 25, Vardy 24, Agüero 24, Lukaku 18 | coincide con i capocannonieri di club reali |
| Alternativa economica a Lukaku con almeno 21 gol | Milik, Janssen, de Jong, Vardy | tutti `striker`, tutti sotto l'overall di Lukaku (82) |
| Alternativa economica a Pirlo | Michael Carrick 80 (Manchester United) | riferimento ritirato, candidati attivi |
| Prospetti under 23, overall ≥ 75, margine ≥ 10 | 20: Halilović 77, Correa 77, Dembélé 19 anni | filtro età obbligatorio |
| Almeno 30 partite per 5 stagioni consecutive nei top 5 senza cali fisici | 13 profili: Ronaldo 7 stagioni, Gabi, Evra | finestra scorrevole sugli anni + serie storica |
| Giocatori con almeno 3 posizioni diverse in distinta | 2.259 profili: Vidal 6 ruoli, Eriksen 6 | versatilità dalle coordinate partita per partita |
| Che collegamento c'è tra Vardy e Lukaku? | Vardy → partita 2014/2015 → West Bromwich → Lukaku, 3 salti | `shortestPath`: una domanda che un relazionale non risponde senza join di profondità ignota |
| Chi ha fornito più assist nel 2015/2016 | Özil 19 (Arsenal) | coincide con la stagione reale |
| Coppie gol-assist più prolifiche | Dani Alves → Messi 21, Pedro → Messi 16 | `ASSISTED_GOALS_OF` |
| Portieri con più tiri in porta subiti 14/15 e 15/16, gk_reflexes e gk_positioning > 78 | 20: Zieler 275, Ruffier 244, Fährmann 237 | due `SEASON_TEAM` + due `RATED`, somma |
| Squadre di Serie A con il pressing più alto | Lazio 59, Milan 58, Fiorentina 58 | `RANKED {league}` + `STYLE {season}` |

**Quattro categorie di domanda vengono rifiutate** invece di ricevere una risposta inventata: le metriche assenti (parate, minuti giocati, sostituzioni, passaggi chiave, infortuni), le richieste di scrittura sul database, i campionati o le divisioni che il dataset non ha, e i prezzi (risolti con il rating come approssimazione **dichiarata**).

### 6.2 Stessa domanda, più formulazioni

Dieci casi d'uso posti ciascuno in tre forme — sintetica, prolissa, con i vincoli in ordine inverso — per 30 domande, ognuna confrontata con il risultato calcolato a mano. Tre esecuzioni: **24/30, 28/30, 30/30**. Ogni divergenza è stata ricondotta a una causa e a un guardrail, e nessuna dipendeva dalla lunghezza della frase in sé: la forma sintetica perdeva un filtro tanto quanto quella prolissa ne applicava uno al posto sbagliato.

Sei casi successivi hanno ripetuto l'esperimento su domande più difficili, ciascuno scoprendo difetti diversi (§6.6): i difensori di Premier League in due forme, i mancini in tre, l'escursione dell'overall su un intervallo di anni, la crescita tra due stagioni, gli eventi di gioco, lo stile dei club.

### 6.3 Il benchmark e l'ablazione dei guardrail

`evaluation/benchmark.json` raccoglie 33 casi in tredici gruppi (base, storico, aggregati, attributi, scouting, presenze, convenzione della stagione, temporale, grafo, eventi, stile, sessione, rifiuti) per 55 formulazioni. Per ogni caso la verità è un **Cypher scritto a mano** che restituisce `name` e `value`, eseguito sullo stesso grafo al momento della prova; dove una domanda ammette due letture, il file lo annota.

`evaluation/run_benchmark.py` esegue ogni formulazione attraverso lo stesso `ScoutAssistant` che serve l'API e misura: **esattezza** dell'insieme dei nomi, precision e recall, **risposte sbagliate in silenzio** (righe restituite ma insieme diverso — il difetto che la tesi vuole azzerare), **correttezza sintattica** (accettazione al primo tentativo, tentativi, errori Neo4j, guardrail per classe), **copertura dei concetti** nel Cypher, **coerenza** tra formulazioni ed esecuzioni, **rifiuti** e **latenza per fase**.

L'ablazione ripete tutto con `GUARDRAILS=off`: il Cypher del modello viene eseguito com'è (resta solo il rifiuto delle scritture). È il confronto che isola il contributo dei controlli rispetto al modello.

| Metrica | Con guardrail | Senza guardrail |
|---|---:|---:|
| Insieme dei nomi esatto | **93/100 (93%)** | 32/50 (64%) |
| Nomi e valori esatti, dove confrontabili | 84/93 | 25/31 |
| Precision / recall media sui nomi | 97,9% / 96,2% | 77,9% / 80,5% |
| **Risposte sbagliate in silenzio** | **7/100 (7%)** | 16/50 (32%) |
| Risposte vuote a torto | 0/100 | 2/50 |
| Traduzioni fallite | 0/100 | 0/50 |
| Rifiuti corretti (parate, minuti, scrittura) | **8/8** | 1/4 |
| Copertura dei concetti nel Cypher | 98,5% | 97,7% |
| Coerenza tra formulazioni dello stesso caso | 28/32 | 12/16 |
| Coerenza tra esecuzioni ripetute | 44/50 | n/d |
| Cypher accettato al primo tentativo | 26/102 (25%) | 42/51 (82%) |
| Tentativi medi | 2,05 | 1,24 |
| Interventi dei guardrail | 131 (21 classi) | 0 |
| Latenza totale, mediana / p90 | 5,5 s / 13,9 s | 4,1 s / 8,2 s |

Baseline: 110 domande (55 formulazioni × 2 esecuzioni), 26 minuti. Ablazione: 55 domande, 12 minuti. Stesso modello, stesso prompt, stesso grafo, stesso giorno. I guardrail più attivi: `FilteredNotReturned` 28, `MissingClub` 20, `CurrentTeamWithoutActive` 13, `UnusedBinding` e `ThresholdsWithoutRankingMetric` 8, `GrowthNotRanked`, `RatingNotCurrent` e `LeagueOnEarlierSeason` 6 ciascuno.

**Come si legge.**

- **La correttezza è dei controlli, non del modello.** Lo stesso modello con lo stesso prompt passa dal 64% al 93% di insiemi esatti quando le sue query vengono verificate e rimandate; le risposte sbagliate in silenzio scendono dal 32% al 7%. Senza controlli il modello accetta la propria prima query nell'82% dei casi ed è 1,4 s più veloce: è quello il prezzo della garanzia.
- **La copertura dei concetti non discrimina** (98,5% contro 97,7%): il Cypher senza guardrail *contiene* quasi sempre i costrutti giusti e sbaglia altrove — una stagione in meno, un `LIMIT`, un totale al posto di una media, un join che moltiplica. È il motivo per cui la metrica di sovrapposizione di token prevista al pitch è stata abbandonata a favore dell'esattezza dell'insieme.
- **I rifiuti sono interamente dei controlli**: senza il filtro delle metriche assenti il modello risponde a "portieri con più parate" con 239 righe e a "minuti giocati" con 466, numeri presi da altre proprietà e presentati sotto il nome chiesto.
- **Le 7 divergenze residue** sono di due tipi. Quattro sono definizioni ammesse dalla domanda e diverse dalla verità scelta (la rosa di un club come club di stagione invece che attuale, gli assist-man in attività, il ruolo di stagione invece di quello di carriera, i tiri totali invece di quelli nello specchio). Tre sono difetti veri emersi nella seconda esecuzione, ciascuno ora con un controllo e un test; i quattro casi rieseguiti dopo la correzione sono 9/9 esatti (`evaluation/results/post-fix*.md`).
- **Il benchmark ha trovato difetti prima di essere eseguito per intero**: tredici guardrail sono nati da questa fase di valutazione (§6.6).

### 6.4 Efficienza per fase

Ogni risposta dell'API riporta i tempi per fase nel campo `timings`, visibile anche nel pannello Cypher dell'interfaccia: `generate_ms` (le chiamate che producono il Cypher, riparazioni comprese), `attempts`, `database_ms`, `evidence_ms`, `explain_ms`, `total_ms`.

Sulle 30 domande della prova a tre formulazioni:

| Fase | Mediana | p90 | Quota del totale |
|---|---:|---:|---:|
| Generazione Cypher | 1.902 ms | 3.317 ms | 41% |
| Esecuzione Neo4j | 216 ms | 341 ms | 5% |
| Evidenza | 294 ms | 692 ms | 8% |
| Spiegazione | 1.556 ms | 5.039 ms | 46% |
| **Totale** | **4.030 ms** | **8.419 ms** | 100% |

**Il database è marginale**: il 5% del tempo, con una mediana di 216 ms anche per le traversate sulle 542.000 presenze. Il collo di bottiglia è il modello, che pesa per l'87% tra generazione e spiegazione. Ogni riparazione costa una chiamata (circa 2 s): le 13 domande riparate impiegavano il 60% in più. Sulle 110 domande del benchmark, con il prompt cresciuto a ~17.000 token e il grafo raddoppiato, la mediana è 5,5 s e Neo4j scende sotto il 2%.

Il passo successivo indicato dai dati è correggere **in codice** i due guardrail più frequenti — `FilteredNotReturned` (28 interventi) e `MissingClub` (20), insieme 48 dei 131 misurati, entrambi risolvibili aggiungendo una colonna alla proiezione — invece di rimandare la query al modello.

### 6.5 Scala delle traversate

Quattro carichi presi dalle domande reali, eseguiti direttamente su Neo4j (senza modello, che non dipende dalla dimensione dei dati) restringendo la stessa query a 1, 2, 4 e 8 stagioni.\
Protocollo: cache calda = una esecuzione di riscaldamento e poi 9 esecuzioni; l'ordine dei sottoinsiemi è mescolato per non confondere la scala con il riscaldamento.

| Carico | 1 stagione | 2 | 4 | 8 | Archi a 8 stagioni |
|---|---:|---:|---:|---:|---:|
| `APPEARED_IN` (presenze) | 61 ms | 84 ms | 139 ms | 279 ms | 542.267 |
| `SCORED` + `SEASON_TEAM` | 64 ms | 62 ms | 108 ms | 243 ms | 37.303 |
| `RATED` (per stagione) | 103 ms | 113 ms | 140 ms | 168 ms | 54.652 |
| `ASSESSED` (serie completa) | 329 ms | 759 ms | 439 ms | 605 ms | 167.322 |
| `shortestPath` fino a 6 salti | — | — | — | 38 ms | — |

- **Le traversate scalano in modo sub-lineare**: `APPEARED_IN` passa da 61 a 279 ms per 7,4× archi (4,6× tempo). C'è un costo fisso per query di 50-100 ms che domina sui sottoinsiemi piccoli.
- **Tre volte gli archi, tre volte e mezzo il tempo**: la stessa domanda sulla serie completa (`ASSESSED`, 167.322 archi) costa 605 ms contro i 168 ms della vista per stagione (`RATED`, 54.652). È il motivo per cui `RATED` resta la vista predefinita del prompt.
- **La cache conta più della dimensione**: la prima esecuzione a freddo costa da 2 a 20 volte la mediana a caldo (`APPEARED_IN`: 5.606 ms contro 279). In un uso reale il grafo resta in page cache.
- **Anche a un milione di archi il database non è il collo di bottiglia**: la peggiore mediana a caldo (605 ms) è un settimo della sola chiamata di generazione del modello.

### 6.6 Il catalogo dei difetti

Ogni difetto qui elencato è stato **osservato davvero** in una risposta, quasi sempre nella forma peggiore: un numero plausibile e falso. Ognuno ha oggi un controllo in codice e un test.

**Numeri sbagliati restituiti in silenzio**

| Difetto osservato | Causa | Correzione |
|---|---|---|
| "Ibrahimović 152 gol" | `PLAYS_FOR` e `SCORED` nello stesso pattern: ogni gol contato una volta per club | riscrittura in `count(DISTINCT ...)` |
| "Higuaín 36 assist" (erano i suoi gol) | metrica assente sostituita con la più somigliante | filtro delle metriche assenti prima della generazione |
| Un requisito su tre applicato, risposta che li dichiarava tutti | requisiti scartati in silenzio | la spiegazione riceve domanda e Cypher e dichiara ciò che non trova |
| "Il miglior difensore ha overall null" | Neo4j ordina i null **prima** in `DESC` | `IS NOT NULL` su ogni attributo usato in aritmetica o ordinamento |
| Ibrahimović capocannoniere 2008/2009 "del PSG" | club di oggi presentato come club di allora | `SEASON_TEAM` per le domande con stagione |
| Liga 2015/2016 con 2.086 gol invece di 1.043 | partite raggiunte da entrambe le squadre | si parte da `(m:Match)`, mai da `Team` |
| Martial e Shaw come alternative a un portiere | ruolo lasciato cadere | il ruolo della domanda è obbligatorio nella query |
| Diamanti due volte nei top 10 rigoristi | trasferimento a gennaio: due `SEASON_TEAM` | `main: true`, una per giocatore per stagione |
| "Nessuna squadra ha segnato più di 50 gol in casa" | `m.home_team` non esiste: Neo4j restituisce null | ogni `variabile.proprietà` verificata contro lo schema |
| 479 candidati "non di prima fascia", Dele Alli compreso | `t.name` confrontato con il nome del campionato | `RANKED` con `top` calcolato dalle classifiche |
| 296 giocatori invece di 23 su due stagioni | presenze sommate, stamina da una stagione qualsiasi | forma piatta con una relazione per stagione |
| Gary Cahill tre volte nello stesso elenco | `RATED` legata e non aggregata | controllo sui doppioni esteso a ogni ripetizione |
| Hazard e Clyne, destri, tra i mancini | piede cercato su `RATED`, dove non esiste | `p.preferred_foot` sul nodo Player |
| Mahrez (+11) terzo dietro Smalling (+5) | crescita filtrata ma non restituita né usata per ordinare | la differenza è colonna e criterio di ordinamento |
| "Il giocatore con più assist" → 1.485 righe | superlativo singolare senza `LIMIT 1` | controllo sul superlativo |
| "Quali coppie" → 8.775 righe | classifica senza numero restituita per intero | convenzione: classifica senza numero = top 10 |
| Almen Abdi miglior marcatore del Watford | `collect(...)[0]` senza `ORDER BY` prima | controllo sulla testa non ordinata |
| Zero righe per "5 stagioni consecutive" | finestra con `+ 1` invece di `= 4` | controllo sull'aritmetica della finestra |
| 42 giocatori con il calo misurato su nulla | `IN` tra interi e stringhe: mai vero | controllo sui tipi nelle liste |
| Sei portieri invece di venti | filtro sul campionato copiato da un esempio, mai chiesto | controllo sul campionato non richiesto |

**Dati che il grafo non conteneva.** Quattro domande legittime erano irrisolvibili perché il caricamento scartava dati che la sorgente aveva — e in ogni caso la risposta era vuota o sbagliata, mai un errore: i gol per giocatore (aggiunti come `SCORED`), le presenze per campionato (`APPEARED_IN`), gli attributi fisici e il loro andamento (`RATED`), il ruolo nella singola partita (`position` su `APPEARED_IN`). Ruolo, età, squadra attuale e stato di attività mancavano del tutto: le risposte elencavano ritirati come Christian Vieri tra i "prospetti su cui investire".

**Errori di traduzione della domanda**

| Sintomo | Causa |
|---|---|
| "Lukaku" trovava Jordan invece di Romelu | il cognome non basta: serve `CONTAINS` sull'intera stringa quando la domanda dà nome e cognome |
| Harry Kane tra i giocatori "fuori dalla Premier League" | campionato confrontato con il **nome della squadra** |
| `Italy Serie B`, `England Championship` nelle query | campionati inventati: il dataset ha 11 massime serie |
| Suárez miglior marcatore del Liverpool nel 2015/16 | raggruppamento via `PLAYS_FOR` (tutta la carriera) invece di `CURRENT_TEAM` |
| Una query da 462 giocatori restituiva 767 righe | `PLAYS_FOR` legata senza essere mai usata |
| `max(s.stamina)` sempre nullo | attributi letti dal **nodo** invece che dalla relazione |
| "sempre sopra 80" tradotto con `max` invece di `min` | — |

**Difetti dell'applicazione.** Con 102 righe di risultato la richiesta superava i 128.000 token: ora ne vanno al modello 15, dichiarando quante sono in totale. Una query malformata arrivava cruda all'utente come `{code: Neo.ClientError...}`: ora il ciclo di riparazione la rimanda al modello, e se fallisce la risposta spiega perché invece di produrre un HTTP 500. Il ciclo di riparazione catturava anche il rifiuto anti-scrittura, trasformando una richiesta di cancellazione respinta in una lettura che la assecondava: `UnsafeCypher` è ora un tipo distinto. Sistemando un errore di scope, una riparazione aveva buttato `gol >= 15` — da 5 risultati a 90, senza alcun segnale — e la richiesta di riparazione impone ora di riverificare ogni vincolo.

**Una lezione ricorrente.** Tre volte su quattro il difetto non era una regola mancante, ma **un esempio nel prompt che insegnava la forma sbagliata**. Il cammino trovava Jordan Lukaku perché l'esempio scriveva `CONTAINS 'lukaku'`; i duplicati sparivano solo dopo aver corretto il primo esempio dello schema; le regole su `null` e deduplica hanno fatto presa solo dopo essere state spostate in cima al prompt. Un esempio sbagliato non è un dettaglio di documentazione: è codice che il modello esegue. Il caso più costoso: nell'esempio su Lukaku, un `WITH` non portava avanti una variabile che il `WHERE` successivo usava — il modello copiava fedelmente un esempio invalido, Neo4j lo rifiutava, e la riparazione lo sistemava consumando entrambi i tentativi allora disponibili, rendendo fragile qualunque variazione.

### 6.7 Limiti, dichiarati

**Della valutazione.** La verità è un Cypher scritto a mano: è la lettura più ragionevole della domanda secondo le convenzioni dichiarate, non l'unica; il benchmark la annota dove ce n'è un'altra. Un solo modello provato (`gpt-4o-mini`): il confronto con un secondo è un comando (`OPENAI_MODEL=... run_benchmark.py`) ma non è stato eseguito. Il limite di token al minuto del servizio impone una pausa tra le domande, quindi le latenze sono per domanda e non di un carico concorrente. 55 formulazioni sono un campione: i guardrail nati da questa fase dimostrano che ogni serie nuova di domande ne trova altri. Efficienza e scala sono misurate su un solo nodo Neo4j e su un portatile, e la scala è quella del lavoro per query (stagioni toccate), non dello storage.

**Del sistema.** Una domanda su un attributo può ancora restituire una riga per stagione qualificante: ogni riga è un fatto vero, ma i nomi si ripetono (aggirabile aggiungendo "uno per giocatore"). Le coordinate di schieramento non separano in modo affidabile un mediano da un trequartista, quindi i ruoli si fermano a sette. I qualificatori vaghi ("un buon valore di ball_control") non vengono tradotti in una soglia né segnalati come requisito non applicato.\
La verifica finale dei requisiti è affidata a un modello, quindi è una rete di sicurezza in più, non una garanzia: le garanzie sono i controlli in codice e i test. Il sistema non è deterministico — due chiamate sulla stessa domanda possono produrre Cypher diversi; il campo `repairs` dice quante correzioni sono servite, e se è costantemente pieno conviene riformulare. Il dataset si ferma al **2015/2016** e copre 11 campionati: un giocatore che in quegli anni militava altrove non esiste nel grafo, ed è quasi sempre questa la causa di una risposta vuota.

---

## 7. Dati e riproducibilità

Lo script di download usa il riferimento ufficiale KaggleHub `hugomathien/soccer`. Le credenziali Kaggle vanno configurate secondo KaggleHub, oppure si può copiare manualmente l'archivio SQLite in `data/raw/`. **Credenziali e dati grezzi non sono mai versionati.**

Il caricamento è idempotente e produce **37.346 nodi e 1.952.560 relazioni**; la tabella dei conteggi attesi per verificare un caricamento è in [docs/GUIDA_AVVIO.md](docs/GUIDA_AVVIO.md).

```bash
# test (nessun database, nessuna chiave API)
docker compose run --rm --no-deps api pytest tests -q

# benchmark e ablazione
docker compose exec api python evaluation/run_benchmark.py --tag baseline --runs 2
docker compose exec -e GUARDRAILS=off api python evaluation/run_benchmark.py --tag no-guardrails
docker compose cp api:/app/evaluation/results ./evaluation/

# notebook di analisi
docker compose exec api pip install -r notebooks/requirements.txt
docker compose exec api jupyter lab --ip 0.0.0.0 --port 8888 --no-browser --allow-root
```
