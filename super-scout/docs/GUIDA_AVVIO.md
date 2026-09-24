# Guida all'avvio di Super-Scout

Questa guida serve a **mettere in piedi il sistema e verificare che funzioni**. Tutto il resto — architettura, modellazione dei dati, guardrail, valutazione sperimentale — è nel [README](../README.md).

Il nome del progetto Docker è fissato a `super-scout` in `docker-compose.yml`, quindi container, rete e volumi non dipendono dalla cartella in cui si trova il repository.

---

## 1. Prerequisiti

- **Docker Desktop** installato e avviato.
- **Python 3.11 o superiore** (solo se si vuole eseguire l'API fuori da Docker).
- Un account **Kaggle** configurato per KaggleHub, oppure un file `database.sqlite` già presente in `data/raw`.
- Una **chiave API OpenAI**. La demo si avvia anche senza, ma `/ask` risponderà `configuration_required`.

---

## 2. Avvio con Docker (consigliato)

Dalla cartella del progetto:

```bash
cp .env.example .env     # su Windows: Copy-Item .env.example .env
# aprire .env e impostare OPENAI_API_KEY
docker compose up -d
docker compose ps
```

Vengono avviati due servizi, `neo4j` e `api`. L'API attende che il healthcheck di Neo4j sia verde prima di partire: non serve alcuna attesa manuale.

### Caricamento del dataset (una volta sola)

Il grafo resta nel volume `super-scout_neo4j_data` e sopravvive a `docker compose stop` e `down` (non a `down -v`).

```bash
docker compose run --rm api python scripts/download_data.py
docker compose run --rm api python scripts/load_data.py
```

Il secondo comando costruisce l'intero grafo: circa venti minuti su un database vuoto, meno se rieseguito, perché tutte le scritture sono idempotenti. Due passi si possono eseguire da soli su un grafo già caricato:

```bash
docker compose run --rm api python scripts/load_data.py --events   # solo gli eventi di gioco
docker compose run --rm api python scripts/load_data.py --styles   # solo lo stile delle squadre
```

---

## 3. Verificare che il caricamento sia andato a buon fine

Il loader stampa un riepilogo finale. I conteggi attesi sono questi:

| Elemento | Quantità | Origine |
|---|---:|---|
| Nodi `Player` / `Team` / `Match` / `Season` | 11.060 / 299 / 25.979 / 8 | tabelle relazionali |
| `APPEARED_IN` (con ruolo e squadra di quella partita) | 542.267 | distinte di formazione |
| `RATED` (35 attributi per stagione) | 54.652 | `Player_Attributes` |
| `ASSESSED` (ogni singola rilevazione) | 167.322 | `Player_Attributes` |
| `SEASON_TEAM` (club, presenze, ruolo e totali di stagione) | 35.002 | distinte di formazione |
| `SCORED` (minuto, rigore, tipo) | 37.303 | XML `Match.goal` |
| `OWN_GOAL` | 1.111 | XML `Match.goal` |
| `ASSISTED` / `ASSISTED_GOALS_OF` | 16.995 / 12.266 | `player2` dell'XML `Match.goal` |
| `SHOT` (in porta o fuori, murato, coordinate) | 186.453 | XML `shoton` / `shotoff` |
| `FOUL` (commessi) / `FOULED` (subiti) | 210.100 / 188.396 | XML `foulcommit` |
| `CARD` (`y`, `y2`, `r`) | 61.095 | XML `card` |
| `CROSS` / `CORNER` | 270.059 / 85.026 | XML `cross` / `corner` |
| `HOME_TEAM` / `AWAY_TEAM` | 51.958 | tabella `Match` |
| `PLAYS_FOR` | 18.557 | formazioni + attributi |
| `CURRENT_TEAM` | 11.060 | ultima presenza in distinta |
| `RANKED` (classifica per campionato e stagione) | 1.481 | risultati delle partite |
| `STYLE` (stile di gioco per stagione) | 1.457 | `Team_Attributes` |
| **Totale** | **37.346 nodi, 1.952.560 relazioni** | |

Per controllarlo direttamente:

```bash
docker compose exec api python -c "
from app.graph import GraphStore
g = GraphStore()
print(g.query('MATCH (n) RETURN count(n) AS nodi')[0])
print(g.query('MATCH ()-[r]->() RETURN count(r) AS relazioni')[0])
g.close()"
```

Oppure da Neo4j Browser (`http://localhost:7474`, utente `neo4j`, password
`super-scout-dev`):

```cypher
MATCH ()-[r]->() RETURN type(r) AS tipo, count(*) AS n ORDER BY n DESC
```

---

## 4. Verificare che il sistema risponda

**Indirizzi:**

- Interfaccia web: `http://localhost:8000/` (una domanda si può precaricare con `?q=...`)
- API e Swagger: `http://localhost:8000/docs`
- Health check: `http://localhost:8000/health`
- Neo4j Browser: `http://localhost:7474`

**Health check.** Deve rispondere `{"status":"ok"}`; un 503 significa che Neo4j non risponde.

```bash
curl http://localhost:8000/health
```

**Una domanda di prova.** Da Swagger (`POST /ask` → *Try it out*) oppure da terminale:

```bash
curl -s -X POST http://localhost:8000/ask \
  -H 'Content-Type: application/json' \
  -d '{"question": "Quali giocatori mancini hanno un potenziale almeno pari a 85?"}'
```

La risposta deve contenere `mode: "graph_rag"`, il Cypher generato, le righe, la spiegazione in italiano e il sotto-grafo di evidenza. Nell'interfaccia web lo stesso sotto-grafo viene disegnato sotto la risposta: nodi e archi sono cliccabili e ne mostrano le proprietà.

**La suite di test** (221 test, non richiede né database né chiave API):

```bash
docker compose run --rm --no-deps api pytest tests -q
```

### Come leggere il campo `mode`

L'interfaccia colora di giallo i quattro casi che non sono una risposta ordinaria:

| `mode` | Significato |
|---|---|
| `graph_rag` | Domanda tradotta ed eseguita: la risposta ha Cypher, righe e sotto-grafo. |
| `unsupported` | La domanda cita una metrica assente dal dataset (minuti giocati, sostituzioni, passaggi chiave, infortuni, parate). Viene rifiutata **prima** di generare la query, per non sostituirla con un'altra grandezza. Assist, tiri, falli, cartellini, cross, corner e possesso sono invece disponibili. |
| `rejected` | La domanda richiede una scrittura sul database. L'assistente interroga il grafo solo in lettura. |
| `failed` | La traduzione non è riuscita nemmeno dopo le correzioni automatiche. Succede quando la domanda richiede un legame che il grafo non ha. |
| `configuration_required` | Manca `OPENAI_API_KEY` nel `.env`. |

Il campo `repairs` elenca le correzioni applicate prima di arrivare alla query finale; se non è vuoto, il primo tentativo era sbagliato ed è stato riscritto. `trace` contiene il Cypher di ogni tentativo scartato.

---

## 5. Esecuzione fuori da Docker

Neo4j resta comunque in Docker; solo l'API gira in locale.

**Windows (PowerShell)**

```powershell
cd D:\super-scout            # la cartella del progetto
Copy-Item .env.example .env
notepad .env                 # impostare OPENAI_API_KEY

docker compose up -d neo4j

py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt

$env:PYTHONPATH = (Get-Location).Path
python scripts\download_data.py
python scripts\load_data.py
python -m uvicorn app.main:app --reload
```

**macOS / Linux**

```bash
cd /percorso/alla/cartella/super-scout
cp .env.example .env
nano .env                    # impostare OPENAI_API_KEY

docker compose up -d neo4j

python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

export PYTHONPATH="$PWD"
python scripts/download_data.py
python scripts/load_data.py
python -m uvicorn app.main:app --reload
```

---

## 6. Arresto e ripartenza

```bash
docker compose stop     # ferma tutto, il grafo resta
docker compose start    # riparte
docker compose down -v  # ATTENZIONE: cancella anche il grafo importato
```

Se l'API gira fuori da Docker, si ferma con `Ctrl+C`.