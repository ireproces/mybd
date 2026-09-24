# Benchmark `baseline`

Modello: `gpt-4o-mini`; guardrail: attivi; durata: 26.2 min.

Domande eseguite: 110 (55 formulazioni distinte, 33 casi, 2 esecuzioni)

## Efficacia

| Metrica | Valore |
|---|---:|
| Insieme dei nomi esatto | 93/100 (93%) |
| Nomi e valori esatti (dove confrontabili) | 84/93 |
| Precision media sui nomi | 97.9% |
| Recall media sui nomi | 96.2% |
| **Risposte sbagliate in silenzio** (righe restituite, insieme diverso) | 7/100 |
| Risposte vuote a torto | 0/100 |
| Traduzioni fallite (dopo le riparazioni) | 0/100 |
| Cammini nel grafo trovati | 2/2 |
| Rifiuti corretti (metriche assenti, scritture) | 8/8 |
| Copertura dei concetti nel Cypher (media) | 98.5% |

## Correttezza sintattica e riparazioni

| Metrica | Valore |
|---|---:|
| Cypher accettato al primo tentativo | 26/102 (25%) |
| Tentativi medi | 2.05 |
| Distribuzione tentativi | {1: 26, 2: 52, 3: 19, 4: 3, 5: 2} |
| Errori di sintassi Neo4j (totale) | 16 |
| Interventi dei guardrail (totale) | 131 |

Guardrail per classe:

| Guardrail | Interventi |
|---|---:|
| `FilteredNotReturned` | 28 |
| `MissingClub` | 20 |
| `CurrentTeamWithoutActive` | 13 |
| `UnusedBinding` | 8 |
| `ThresholdsWithoutRankingMetric` | 8 |
| `GrowthNotRanked` | 6 |
| `RatingNotCurrent` | 6 |
| `LeagueOnEarlierSeason` | 6 |
| `DuplicatedPlayers` | 4 |
| `OrderingNotReturned` | 4 |
| `SeasonTeamWithoutSeason` | 4 |
| `EventsWithoutSeason` | 4 |
| `SeasonsInvented` | 3 |
| `TeamVariableReused` | 3 |
| `AggregatedAndGrouped` | 3 |
| `MissingPlayerName` | 2 |
| `PerSeasonValuesNotReturned` | 2 |
| `UnknownProperty` | 2 |
| `LeagueNotAsked` | 2 |
| `RankingWithoutLimit` | 2 |
| `RatedWithoutSeason` | 1 |

## Coerenza

| Metrica | Valore |
|---|---:|
| Casi con formulazioni diverse che danno lo stesso insieme | 28/32 |
| Formulazioni che danno lo stesso insieme in esecuzioni ripetute | 44/50 |

## Efficienza (per fase, ms)

| Fase | Mediana | p90 | Max |
|---|---:|---:|---:|
| Generazione Cypher (riparazioni comprese) | 3,764 | 10,807 | 20,332 |
| Esecuzione Neo4j | 78 | 614 | 3,529 |
| Sotto-grafo di evidenza | 94 | 551 | 6,405 |
| Spiegazione | 1,728 | 2,324 | 2,868 |
| Totale | 5,530 | 13,923 | 24,511 |

## Dettaglio per caso

| Caso | Gruppo | Forme x run | Esatti | Prec. | Rec. | Silenziosi | 1° tentativo | Tent. medi |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `overall_85` | base | 6 | 6/6 | 100% | 100% | 0 | 0/6 | 2.0 |
| `esterni_mancini` | base | 6 | 5/6 | 92% | 88% | 1 | 2/6 | 2.0 |
| `capocannoniere_2010` | storico | 6 | 6/6 | 100% | 100% | 0 | 6/6 | 1.0 |
| `gol_per_campionato` | aggregati | 4 | 4/4 | 100% | 100% | 0 | 0/4 | 2.0 |
| `alternativa_lloris` | scouting | 4 | 3/4 | 100% | 75% | 1 | 0/4 | 2.2 |
| `prospetti` | scouting | 6 | 6/6 | 100% | 100% | 0 | 0/6 | 2.3 |
| `presenze_premier` | presenze | 4 | 4/4 | 100% | 100% | 0 | 0/4 | 2.0 |
| `rigoristi_corrente` | convenzione_stagione | 4 | 4/4 | 100% | 100% | 0 | 0/4 | 2.2 |
| `marcatori_premier_2012` | storico | 4 | 4/4 | 100% | 100% | 0 | 2/4 | 1.5 |
| `squadre_50_gol_casa` | aggregati | 4 | 4/4 | 100% | 100% | 0 | 4/4 | 1.0 |
| `autogol_corrente` | convenzione_stagione | 2 | 2/2 | 100% | 100% | 0 | 0/2 | 2.5 |
| `difensori_premier_fisici` | sessione | 4 | 4/4 | 100% | 100% | 0 | 0/4 | 2.0 |
| `mancini_premier_veloci` | sessione | 6 | 6/6 | 100% | 100% | 0 | 0/6 | 2.0 |
| `stabilita_overall` | sessione | 4 | 4/4 | 100% | 100% | 0 | 0/4 | 3.0 |
| `crescita_overall` | sessione | 4 | 3/4 | 88% | 78% | 1 | 0/4 | 2.8 |
| `marcatore_per_squadra` | grafo | 2 | 0/2 | 48% | 48% | 2 | 1/2 | 1.5 |
| `versatilita` | grafo | 2 | 2/2 | 100% | 100% | 0 | 2/2 | 1.0 |
| `centrali_aerei` | attributi | 2 | 2/2 | 100% | 100% | 0 | 2/2 | 1.0 |
| `centrocampisti_stamina` | attributi | 2 | 2/2 | 100% | 100% | 0 | 2/2 | 1.0 |
| `collegamento` | grafo | 2 | 2/2 | — | — | — | 2/2 | 1.0 |
| `cinque_stagioni` | temporale | 2 | 2/2 | 100% | 100% | 0 | 0/2 | 4.0 |
| `due_stagioni_stamina` | temporale | 2 | 2/2 | 100% | 100% | 0 | 0/2 | 4.0 |
| `assist_2015` | eventi | 4 | 4/4 | 100% | 100% | 0 | 0/4 | 2.2 |
| `falli_difensori_premier` | eventi | 2 | 2/2 | 100% | 100% | 0 | 0/2 | 2.5 |
| `coppie_assist` | eventi | 2 | 1/2 | 100% | 95% | 1 | 0/2 | 2.0 |
| `cross_serie_a` | eventi | 2 | 2/2 | 100% | 100% | 0 | 0/2 | 2.5 |
| `portieri_tiri_subiti` | eventi | 4 | 3/4 | 98% | 100% | 1 | 0/4 | 3.2 |
| `esterni_squadre_cross` | stile | 2 | 2/2 | 100% | 100% | 0 | 2/2 | 1.0 |
| `pressing_serie_a` | stile | 2 | 2/2 | 100% | 100% | 0 | 0/2 | 2.5 |
| `centrali_linea_alta` | stile | 2 | 2/2 | 100% | 100% | 0 | 1/2 | 1.5 |
| `rifiuto_parate` | rifiuti | 4 | 4/4 | — | — | — | 4/4 | 0.0 |
| `rifiuto_minuti` | rifiuti | 2 | 2/2 | — | — | — | 2/2 | 0.0 |
| `rifiuto_scrittura` | rifiuti | 2 | 2/2 | — | — | — | 2/2 | 0.0 |

## Divergenze

- `esterni_mancini` forma 3 run 1: mode=graph_rag, righe=2, precision=50%, recall=25%
- `marcatore_per_squadra` forma 1 run 1: mode=graph_rag, righe=20, precision=90%, recall=90%
- `coppie_assist` forma 1 run 1: mode=graph_rag, righe=10, precision=100%, recall=90%
- `portieri_tiri_subiti` forma 1 run 1: mode=graph_rag, righe=24, precision=92%, recall=100%
- `alternativa_lloris` forma 1 run 2: mode=graph_rag, righe=1, precision=100%, recall=1%
- `crescita_overall` forma 2 run 2: mode=graph_rag, righe=2, precision=50%, recall=10%
- `marcatore_per_squadra` forma 1 run 2: mode=graph_rag, righe=20, precision=5%, recall=5%