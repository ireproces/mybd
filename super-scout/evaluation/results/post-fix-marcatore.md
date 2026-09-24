# Benchmark `post-fix-marcatore`

Modello: `gpt-4o-mini`; guardrail: attivi; durata: 0.4 min.

Domande eseguite: 2 (1 formulazioni distinte, 1 casi, 2 esecuzioni)

## Efficacia

| Metrica | Valore |
|---|---:|
| Insieme dei nomi esatto | 2/2 (100%) |
| Precision media sui nomi | 100.0% |
| Recall media sui nomi | 100.0% |
| **Risposte sbagliate in silenzio** (righe restituite, insieme diverso) | 0/2 |
| Risposte vuote a torto | 0/2 |
| Traduzioni fallite (dopo le riparazioni) | 0/2 |
| Copertura dei concetti nel Cypher (media) | 100.0% |

## Correttezza sintattica e riparazioni

| Metrica | Valore |
|---|---:|
| Cypher accettato al primo tentativo | 2/2 (100%) |
| Tentativi medi | 1.00 |
| Distribuzione tentativi | {1: 2} |
| Errori di sintassi Neo4j (totale) | 0 |
| Interventi dei guardrail (totale) | 0 |

## Coerenza

| Metrica | Valore |
|---|---:|
| Formulazioni multiple | n/d |
| Formulazioni che danno lo stesso insieme in esecuzioni ripetute | 1/1 |

## Efficienza (per fase, ms)

| Fase | Mediana | p90 | Max |
|---|---:|---:|---:|
| Generazione Cypher (riparazioni comprese) | 2,250 | 2,441 | 2,441 |
| Esecuzione Neo4j | 70 | 111 | 111 |
| Sotto-grafo di evidenza | 146 | 210 | 210 |
| Spiegazione | 2,576 | 2,943 | 2,943 |
| Totale | 5,056 | 5,116 | 5,116 |

## Dettaglio per caso

| Caso | Gruppo | Forme x run | Esatti | Prec. | Rec. | Silenziosi | 1° tentativo | Tent. medi |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `marcatore_per_squadra` | grafo | 2 | 2/2 | 100% | 100% | 0 | 2/2 | 1.0 |