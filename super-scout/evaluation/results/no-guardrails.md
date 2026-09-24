# Benchmark `no-guardrails`

Modello: `gpt-4o-mini`; guardrail: DISATTIVATI; durata: 12.1 min.

Domande eseguite: 55 (55 formulazioni distinte, 33 casi, 1 esecuzioni)

## Efficacia

| Metrica | Valore |
|---|---:|
| Insieme dei nomi esatto | 32/50 (64%) |
| Nomi e valori esatti (dove confrontabili) | 25/31 |
| Precision media sui nomi | 77.9% |
| Recall media sui nomi | 80.5% |
| **Risposte sbagliate in silenzio** (righe restituite, insieme diverso) | 16/50 |
| Risposte vuote a torto | 2/50 |
| Traduzioni fallite (dopo le riparazioni) | 0/50 |
| Cammini nel grafo trovati | 1/1 |
| Rifiuti corretti (metriche assenti, scritture) | 1/4 |
| Copertura dei concetti nel Cypher (media) | 97.7% |

## Correttezza sintattica e riparazioni

| Metrica | Valore |
|---|---:|
| Cypher accettato al primo tentativo | 42/51 (82%) |
| Tentativi medi | 1.24 |
| Distribuzione tentativi | {1: 42, 2: 7, 3: 1, 4: 1} |
| Errori di sintassi Neo4j (totale) | 12 |
| Interventi dei guardrail (totale) | 0 |

## Coerenza

| Metrica | Valore |
|---|---:|
| Casi con formulazioni diverse che danno lo stesso insieme | 12/16 |
| Esecuzioni ripetute | n/d (una sola esecuzione) |

## Efficienza (per fase, ms)

| Fase | Mediana | p90 | Max |
|---|---:|---:|---:|
| Generazione Cypher (riparazioni comprese) | 2,098 | 5,449 | 12,117 |
| Esecuzione Neo4j | 122 | 416 | 3,577 |
| Sotto-grafo di evidenza | 106 | 755 | 3,025 |
| Spiegazione | 1,636 | 2,383 | 7,102 |
| Totale | 4,109 | 8,150 | 17,082 |

## Dettaglio per caso

| Caso | Gruppo | Forme x run | Esatti | Prec. | Rec. | Silenziosi | 1° tentativo | Tent. medi |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `overall_85` | base | 3 | 3/3 | 100% | 100% | 0 | 3/3 | 1.0 |
| `esterni_mancini` | base | 3 | 3/3 | 100% | 100% | 0 | 3/3 | 1.0 |
| `capocannoniere_2010` | storico | 3 | 3/3 | 100% | 100% | 0 | 3/3 | 1.0 |
| `gol_per_campionato` | aggregati | 2 | 2/2 | 100% | 100% | 0 | 0/2 | 2.0 |
| `alternativa_lloris` | scouting | 2 | 2/2 | 100% | 100% | 0 | 0/2 | 2.0 |
| `prospetti` | scouting | 3 | 3/3 | 100% | 100% | 0 | 3/3 | 1.0 |
| `presenze_premier` | presenze | 2 | 0/2 | 87% | 100% | 2 | 2/2 | 1.0 |
| `rigoristi_corrente` | convenzione_stagione | 2 | 1/2 | 55% | 55% | 1 | 2/2 | 1.0 |
| `marcatori_premier_2012` | storico | 2 | 1/2 | 50% | 50% | 1 | 2/2 | 1.0 |
| `squadre_50_gol_casa` | aggregati | 2 | 2/2 | 100% | 100% | 0 | 2/2 | 1.0 |
| `autogol_corrente` | convenzione_stagione | 1 | 0/1 | 0% | 0% | 1 | 1/1 | 1.0 |
| `difensori_premier_fisici` | sessione | 2 | 1/2 | 67% | 100% | 1 | 2/2 | 1.0 |
| `mancini_premier_veloci` | sessione | 3 | 3/3 | 100% | 100% | 0 | 3/3 | 1.0 |
| `stabilita_overall` | sessione | 2 | 0/2 | 64% | 23% | 2 | 1/2 | 2.0 |
| `crescita_overall` | sessione | 2 | 0/2 | 80% | 40% | 2 | 2/2 | 1.0 |
| `marcatore_per_squadra` | grafo | 1 | 0/1 | 8% | 100% | 1 | 0/1 | 4.0 |
| `versatilita` | grafo | 1 | 1/1 | 100% | 100% | 0 | 1/1 | 1.0 |
| `centrali_aerei` | attributi | 1 | 1/1 | 100% | 100% | 0 | 1/1 | 1.0 |
| `centrocampisti_stamina` | attributi | 1 | 1/1 | 100% | 100% | 0 | 1/1 | 1.0 |
| `collegamento` | grafo | 1 | 1/1 | — | — | — | 1/1 | 1.0 |
| `cinque_stagioni` | temporale | 1 | 0/1 | 42% | 100% | 1 | 1/1 | 1.0 |
| `due_stagioni_stamina` | temporale | 1 | 0/1 | 0% | 0% | 0 | 1/1 | 1.0 |
| `assist_2015` | eventi | 2 | 0/2 | 0% | 0% | 2 | 0/2 | 2.0 |
| `falli_difensori_premier` | eventi | 1 | 0/1 | 0% | 0% | 0 | 1/1 | 1.0 |
| `coppie_assist` | eventi | 1 | 0/1 | 90% | 90% | 1 | 1/1 | 1.0 |
| `cross_serie_a` | eventi | 1 | 1/1 | 100% | 100% | 0 | 1/1 | 1.0 |
| `portieri_tiri_subiti` | eventi | 2 | 2/2 | 100% | 100% | 0 | 1/2 | 1.5 |
| `esterni_squadre_cross` | stile | 1 | 1/1 | 100% | 100% | 0 | 1/1 | 1.0 |
| `pressing_serie_a` | stile | 1 | 0/1 | 50% | 100% | 1 | 1/1 | 1.0 |
| `centrali_linea_alta` | stile | 1 | 1/1 | 100% | 100% | 0 | 1/1 | 1.0 |
| `rifiuto_parate` | rifiuti | 2 | 0/2 | — | — | — | 1/2 | 1.5 |
| `rifiuto_minuti` | rifiuti | 1 | 0/1 | — | — | — | 1/1 | 1.0 |
| `rifiuto_scrittura` | rifiuti | 1 | 1/1 | — | — | — | 1/1 | 0.0 |

## Divergenze

- `presenze_premier` forma 1 run 1: mode=graph_rag, righe=60, precision=87%, recall=100%
- `presenze_premier` forma 2 run 1: mode=graph_rag, righe=59, precision=88%, recall=100%
- `rigoristi_corrente` forma 2 run 1: mode=graph_rag, righe=10, precision=10%, recall=10%
- `marcatori_premier_2012` forma 2 run 1: mode=graph_rag, righe=3, precision=0%, recall=0%
- `autogol_corrente` forma 1 run 1: mode=graph_rag, righe=10, precision=0%, recall=0%
- `difensori_premier_fisici` forma 1 run 1: mode=graph_rag, righe=3, precision=33%, recall=100%
- `stabilita_overall` forma 1 run 1: mode=graph_rag, righe=14, precision=64%, recall=23%
- `stabilita_overall` forma 2 run 1: mode=graph_rag, righe=14, precision=64%, recall=23%
- `crescita_overall` forma 1 run 1: mode=graph_rag, righe=5, precision=80%, recall=40%
- `crescita_overall` forma 2 run 1: mode=graph_rag, righe=5, precision=80%, recall=40%
- `marcatore_per_squadra` forma 1 run 1: mode=graph_rag, righe=264, precision=8%, recall=100%
- `cinque_stagioni` forma 1 run 1: mode=graph_rag, righe=26, precision=42%, recall=100%
- `due_stagioni_stamina` forma 1 run 1: mode=graph_rag, righe=0, precision=0%, recall=0%
- `assist_2015` forma 1 run 1: mode=graph_rag, righe=1, precision=0%, recall=0%
- `assist_2015` forma 2 run 1: mode=graph_rag, righe=1, precision=0%, recall=0%
- `falli_difensori_premier` forma 1 run 1: mode=graph_rag, righe=0, precision=0%, recall=0%
- `coppie_assist` forma 1 run 1: mode=graph_rag, righe=10, precision=90%, recall=90%
- `pressing_serie_a` forma 1 run 1: mode=graph_rag, righe=20, precision=50%, recall=100%