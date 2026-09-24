# Benchmark `post-fix`

Modello: `gpt-4o-mini`; guardrail: attivi; durata: 0.0 min.

Domande eseguite: 16 (8 formulazioni distinte, 1 casi, 2 esecuzioni)

## Efficacia

| Metrica | Valore |
|---|---:|
| Insieme dei nomi esatto | 14/16 (88%) |
| Nomi e valori esatti (dove confrontabili) | 14/14 |
| Precision media sui nomi | 98.8% |
| Recall media sui nomi | 98.8% |
| **Risposte sbagliate in silenzio** (righe restituite, insieme diverso) | 2/16 |
| Risposte vuote a torto | 0/16 |
| Traduzioni fallite (dopo le riparazioni) | 0/16 |
| Copertura dei concetti nel Cypher (media) | 100.0% |

## Correttezza sintattica e riparazioni

| Metrica | Valore |
|---|---:|
| Cypher accettato al primo tentativo | 5/16 (31%) |
| Tentativi medi | 2.12 |
| Distribuzione tentativi | {1: 5, 2: 4, 3: 7} |
| Errori di sintassi Neo4j (totale) | 2 |
| Interventi dei guardrail (totale) | 28 |

Guardrail per classe:

| Guardrail | Interventi |
|---|---:|
| `UnusedBinding` | 8 |
| `MissingClub` | 4 |
| `FilteredNotReturned` | 4 |
| `LeagueOnEarlierSeason` | 4 |
| `GrowthNotRanked` | 4 |
| `AggregatedAndGrouped` | 2 |
| `DuplicatedPlayers` | 1 |
| `UnorderedHead` | 1 |

## Coerenza

| Metrica | Valore |
|---|---:|
| Casi con formulazioni diverse che danno lo stesso insieme | 6/6 |
| Formulazioni che danno lo stesso insieme in esecuzioni ripetute | 8/8 |

## Efficienza (per fase, ms)

| Fase | Mediana | p90 | Max |
|---|---:|---:|---:|
| Generazione Cypher (riparazioni comprese) | 4,717 | 8,841 | 9,090 |
| Esecuzione Neo4j | 66 | 124 | 216 |
| Sotto-grafo di evidenza | 84 | 132 | 171 |
| Spiegazione | 1,846 | 2,777 | 5,630 |
| Totale | 6,443 | 11,000 | 14,862 |

## Dettaglio per caso

| Caso | Gruppo | Forme x run | Esatti | Prec. | Rec. | Silenziosi | 1° tentativo | Tent. medi |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `marcatore_per_squadra` | grafo | 2 | 0/2 | 90% | 90% | 2 | 1/2 | 2.0 |

## Divergenze

- `marcatore_per_squadra` forma 1 run 1: mode=graph_rag, righe=20, precision=90%, recall=90%
- `marcatore_per_squadra` forma 1 run 2: mode=graph_rag, righe=20, precision=90%, recall=90%