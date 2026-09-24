"""Test del controllo di sola lettura sulle query generate dall'LLM.

Il modello di norma rifiuta da solo di produrre Cypher distruttivo, ma quella e'
una garanzia probabilistica. Il controllo che conta e' questo, applicato dal
codice prima che la query raggiunga il database.
"""

import pytest

from app.rag import (
    GuardrailViolations, FilteredNotReturned, _filtered_not_returned, CurrentTeamForPastSeason, _current_team_for_past_season, TeamVariableReused, _team_variable_reused, WrongComparison, _wrong_comparison, AggregatedAndGrouped, PerSeasonValuesNotReturned, _aggregated_and_grouped, _per_season_values_not_returned, DoubleCountedMatches, DuplicatedPlayers, LeagueComparedToName, MissingClub, NullRankedFirst, UnknownProperty,
    AppearancesNotPerSeason, CurrentTeamWithoutActive, GroupedByMeasure, ThresholdBeforeMin, _grouped_by_measure, _threshold_before_min, KeptAttributeWithMax, OrderingNotReturned, RatedWithoutSeason,
    _appearances_not_per_season, _kept_attribute_with_max, _rated_without_season, SnapshotRatingWithSeason, _current_team_without_active, _answer_denies_rows, _ordering_not_returned, _stable_order, _tier_note, _duplicated_players, _league_compared_to_name,
    _null_ranked_first, _snapshot_rating_with_season, _unknown_property, MissingPlayerName, MissingRequirement, MissingRole,
    SingleMatchThreshold, UnsafeCypher, _double_counted_matches, _extract_cypher, _harden_counts,
    LeagueMembershipViaMatches, OrderedByUnaskedAppearances, _league_membership_via_matches, _ordered_by_unasked_appearances,
    ThresholdsWithoutRankingMetric, WrongRoleValue, _expected_role_filter, _thresholds_without_ranking_metric, _wrong_role_value,
    RatingNotCurrent, _rating_not_current,
    _missing_club, _missing_player_name, _missing_requirement, _missing_role,
    _single_match_threshold, _unavailable_metric,
)


def raises_violation(expected, cypher, question=""):
    """La query deve essere respinta con `expected` tra le violazioni.

    Quando i difetti sono piu' d'uno, _extract_cypher li raccoglie in un solo
    GuardrailViolations: qui conta che quello atteso ci sia.
    """
    with pytest.raises(ValueError) as raised:
        _extract_cypher(cypher, question)
    error = raised.value
    found = error.violations if isinstance(error, GuardrailViolations) else [error]
    assert any(isinstance(v, expected) for v in found), [type(v).__name__ for v in found]


MUTATIONS = [
    "MATCH (p:Player) DETACH DELETE p",
    "MATCH (p:Player) DELETE p",
    "MATCH (p:Player) SET p.overall = 99",
    "MATCH (p:Player) REMOVE p.name",
    "MATCH (t:Team) MERGE (t)-[:FAKE]->(:Player)",
    "MATCH (p:Player) WITH p CREATE (:Player {name: 'fake'})",
]


@pytest.mark.parametrize("cypher", MUTATIONS)
def test_rifiuta_ogni_operazione_di_scrittura(cypher):
    raises_violation(UnsafeCypher, cypher)


@pytest.mark.parametrize("cypher", MUTATIONS)
def test_il_rifiuto_non_e_un_errore_riparabile(cypher):
    """UnsafeCypher non deve essere confuso con un errore di sintassi.

    Il ciclo di riparazione cattura le eccezioni generiche e richiede al modello
    una query corretta: se catturasse anche questa, una richiesta di scrittura
    respinta verrebbe riproposta come lettura, assecondandola di fatto.
    """
    with pytest.raises(UnsafeCypher) as raised:
        _extract_cypher(cypher)
    # Il tipo e' distinto, ed e' quello su cui `_cypher_with_repair` fa `raise`.
    assert type(raised.value) is UnsafeCypher


def test_accetta_le_query_di_lettura():
    cypher = "MATCH (p:Player)-[r:PLAYS_FOR]->(:Team), (p)-[:CURRENT_TEAM]->(t:Team) WHERE r.overall > 85 AND p.active RETURN p.name, r.overall, t.name"
    assert _extract_cypher(cypher) == cypher


def test_rifiuta_una_risposta_che_non_e_cypher():
    raises_violation(ValueError, "Mi dispiace, non posso rispondere a questa domanda.")


def test_estrae_il_cypher_da_un_blocco_markdown():
    wrapped = "```cypher\nMATCH (p:Player)-[:CURRENT_TEAM]->(t:Team) WHERE p.active RETURN p.name, t.name\n```"
    assert _extract_cypher(wrapped) == "MATCH (p:Player)-[:CURRENT_TEAM]->(t:Team) WHERE p.active RETURN p.name, t.name"


class TestHardenCounts:
    """Il conteggio delle relazioni deve essere sempre DISTINCT.

    Senza questa riscrittura un join con PLAYS_FOR moltiplica i gol per il
    numero di club del giocatore, restituendo numeri sbagliati in silenzio.
    """

    def test_rende_distinct_il_conteggio_di_una_relazione(self):
        cypher = (
            "MATCH (p:Player)-[r:PLAYS_FOR]->(t:Team), (p)-[s:SCORED]->(m:Match) "
            "WHERE m.season = '2015/2016' WITH p, count(s) AS gol RETURN p.name, gol"
        )
        assert "count(DISTINCT s)" in _harden_counts(cypher)

    def test_non_tocca_il_conteggio_dei_nodi(self):
        # `p` e' un nodo: contarlo distinto o no e' una scelta semantica del
        # modello, non un errore di duplicazione da correggere.
        cypher = "MATCH (p:Player)-[:PLAYS_FOR]->(t:Team) RETURN count(p)"
        assert _harden_counts(cypher) == cypher

    def test_e_idempotente(self):
        cypher = "MATCH (p:Player)-[s:SCORED]->(m:Match) RETURN count(DISTINCT s)"
        assert _harden_counts(cypher) == cypher

    def test_applicato_dall_estrattore(self):
        cypher = "MATCH (p:Player)-[s:SCORED]->(m:Match) RETURN count(s) AS gol"
        assert "count(DISTINCT s)" in _extract_cypher(cypher)


class TestUnavailableMetrics:
    """Una metrica assente non va sostituita con la piu somigliante.

    Senza questo controllo il modello contava i gol e li presentava come assist:
    i 36 gol di Higuain diventavano '36 assist', un numero plausibile che nulla
    nell'output smentiva. Assist e cartellini sono poi entrati nel grafo dagli
    XML degli eventi e non sono piu' in questa lista.
    """

    @pytest.mark.parametrize("question", [
        "Quali giocatori hanno piu minuti giocati in Premier League?",
        "Quali giocatori hanno avuto piu infortuni?",
        "Il portiere con piu parate della stagione",
        "Chi e' subentrato piu volte dalla panchina?",
    ])
    def test_riconosce_le_metriche_assenti(self, question):
        assert _unavailable_metric(question) is not None

    @pytest.mark.parametrize("question", [
        "Dammi un'alternativa economica a Lukaku",
        "Quali giocatori mancini hanno un potenziale almeno pari a 85?",
        "I 10 marcatori piu prolifici della stagione 2015/2016",
        "Giocatori con almeno 30 presenze per 5 stagioni consecutive",
    ])
    def test_non_blocca_le_domande_rispondibili(self, question):
        # Il costo non e' qui: ha un surrogato dichiarato (il rating FIFA).
        assert _unavailable_metric(question) is None


# ---------- squadra obbligatoria in ogni elenco di giocatori ----------

SENZA_CLUB = [
    "MATCH (p:Player)-[r:PLAYS_FOR]->(:Team) WHERE r.potential >= 85 "
    "WITH p, max(r.potential) AS potenziale RETURN p.name, potenziale ORDER BY potenziale DESC",
    "MATCH (p:Player)-[s:SCORED]->(m:Match) WHERE m.season = '2010/2011' "
    "WITH p, count(DISTINCT s) AS gol RETURN p.name, gol ORDER BY gol DESC LIMIT 1",
]

CON_CLUB = [
    "MATCH (p:Player)-[r:PLAYS_FOR]->(:Team), (p)-[:CURRENT_TEAM]->(t:Team) WHERE r.potential >= 85 AND p.active "
    "WITH p, t, max(r.potential) AS potenziale RETURN p.name, potenziale, t.name",
    "MATCH (p:Player)-[s:SCORED]->(m:Match) WHERE m.season = '2010/2011' WITH p, count(DISTINCT s) AS gol "
    "MATCH (p)-[st:SEASON_TEAM {season: '2010/2011'}]->(t:Team) RETURN p.name, gol, t.name",
    # Non proietta giocatori: nessuna squadra da pretendere.
    "MATCH (t:Team)-[:HOME_TEAM]->(m:Match) WITH t, sum(m.home_goals) AS goals RETURN t.name, goals",
    "MATCH (p:Player)-[s:SCORED]->(m:Match) RETURN count(s) AS gol",
    # Il cammino porta gia' con se' le squadre attraversate.
    "MATCH (a:Player), (b:Player) WHERE toLower(a.name) CONTAINS 'vardy' AND toLower(b.name) CONTAINS 'lukaku' "
    "MATCH percorso = shortestPath((a)-[*..6]-(b)) RETURN [n IN nodes(percorso) | n.name] AS catena",
]


@pytest.mark.parametrize("cypher", SENZA_CLUB)
def test_rileva_i_giocatori_senza_squadra(cypher):
    assert _missing_club(cypher)
    raises_violation(MissingClub, cypher)


@pytest.mark.parametrize("cypher", CON_CLUB)
def test_accetta_le_query_con_squadra_o_senza_giocatori(cypher):
    assert not _missing_club(cypher)
    _extract_cypher(cypher)


def test_la_squadra_mancante_e_un_errore_riparabile():
    """A differenza di UnsafeCypher, MissingClub deve passare dal ciclo di riparazione."""
    assert issubclass(MissingClub, ValueError) and not issubclass(MissingClub, UnsafeCypher)


# ---------- ruolo obbligatorio quando la domanda lo nomina ----------

LLORIS = "trovami un'alternativa economica a hugo lloris con overall tra 78 e 80 ed eta tra 20 e 22"
SENZA_RUOLO = (
    "MATCH (p:Player)-[r:PLAYS_FOR]->(:Team), (p)-[:CURRENT_TEAM]->(t:Team) "
    "WHERE p.active AND p.age >= 20 AND p.age <= 22 AND r.overall >= 78 AND r.overall <= 80 "
    "WITH p, t, max(r.overall) AS overall RETURN p.name, overall, t.name"
)
CON_RUOLO = (
    "MATCH (ref:Player) WHERE toLower(ref.name) CONTAINS 'hugo lloris' WITH ref.position AS ruolo "
    "MATCH (p:Player)-[r:PLAYS_FOR]->(:Team), (p)-[:CURRENT_TEAM]->(t:Team) "
    "WHERE p.active AND p.position = ruolo AND p.age >= 20 AND p.age <= 22 "
    "WITH p, t, max(r.overall) AS overall RETURN p.name, p.age, overall, t.name"
)


@pytest.mark.parametrize("question", [LLORIS, "i terzini mancini con overall sopra 80", "le migliori ali under 23", "portieri con piu presenze"])
def test_rileva_il_ruolo_ignorato(question):
    assert _missing_role(question, SENZA_RUOLO)
    raises_violation(MissingRole, SENZA_RUOLO, question)


def test_accetta_il_ruolo_filtrato():
    assert not _missing_role(LLORIS, CON_RUOLO)
    _extract_cypher(CON_RUOLO, LLORIS)


@pytest.mark.parametrize("question", [
    "giocatori mancini con potenziale almeno 85",
    "chi ha segnato piu gol nel 2015/2016?",
    # 'registrato' non e' 'regista', 'mediana' non e' 'mediano'.
    "giocatori che hanno registrato +10 tra potential e overall",
    "overall mediana dei giocatori under 23",
])
def test_non_pretende_il_ruolo_se_la_domanda_non_lo_nomina(question):
    assert not _missing_role(question, SENZA_RUOLO)


# ---------- partite raggiunte due volte dalle squadre ----------

DOPPIO = (
    "MATCH (t:Team)-[:HOME_TEAM|AWAY_TEAM]->(m:Match) WHERE m.season = '2015/2016' "
    "RETURN m.league, sum(m.home_goals + m.away_goals) AS gol"
)
SINGOLO = [
    "MATCH (m:Match) WHERE m.season = '2015/2016' RETURN m.league, sum(m.home_goals + m.away_goals) AS gol",
    # Una sola squadra, un solo lato: nessuna duplicazione.
    "MATCH (t:Team)-[:HOME_TEAM]->(m:Match) WHERE m.season = '2015/2016' RETURN t.name, sum(m.home_goals) AS gol",
    # Entrambi i lati ma senza sommare i gol totali della partita: lecito per contare le partite.
    "MATCH (t:Team)-[:HOME_TEAM|AWAY_TEAM]->(m:Match) WHERE m.season = '2015/2016' RETURN t.name, count(DISTINCT m) AS partite",
]


def test_rileva_le_partite_contate_due_volte():
    assert _double_counted_matches(DOPPIO)
    raises_violation(DoubleCountedMatches, DOPPIO)


@pytest.mark.parametrize("cypher", SINGOLO)
def test_accetta_le_somme_sulle_partite(cypher):
    assert not _double_counted_matches(cypher)
    _extract_cypher(cypher)


# ---------- ogni requisito riconoscibile nella domanda deve avere una traccia nel Cypher ----------

ESTERNI = (
    "MATCH (p:Player)-[r:PLAYS_FOR]->(:Team) WHERE p.active AND p.position IN ['winger', 'wide_midfielder'] "
    "AND r.overall >= 85 WITH p, max(r.overall) AS overall MATCH (p)-[:CURRENT_TEAM]->(t:Team) "
    "RETURN p.name, overall, t.name"
)


@pytest.mark.parametrize("question, label", [
    ("esterni mancini con overall almeno 85", "left-footed"),
    ("esterni con piede sinistro e overall almeno 85", "left-footed"),
    ("esterni destri con overall almeno 85", "right-footed"),
    ("esterni under 23 con overall almeno 85", "age"),
    ("esterni con overall almeno 85 nella stagione 2014/2015", "season"),
    ("esterni della Serie A con overall almeno 85", "league"),
    ("esterni con piu gol su rigore", "penalty"),
    ("esterni con piu gol di testa", "headers"),
    ("esterni con piu autogol", "own goals"),
    ("esterni alti almeno 185 cm con overall almeno 85", "height"),
])
def test_rileva_il_requisito_mancante(question, label):
    assert label in _missing_requirement(question, ESTERNI)
    # Con una stagione nella domanda puo' scattare prima il controllo sul rating da
    # PLAYS_FOR: entrambi rimandano la query al modello.
    raises_violation((MissingRequirement, SnapshotRatingWithSeason), ESTERNI, question)


def test_accetta_la_query_che_esprime_il_requisito():
    con_piede = ESTERNI.replace("r.overall >= 85", "r.overall >= 85 AND r.preferred_foot = 'left'")
    assert _missing_requirement("esterni mancini con overall almeno 85", con_piede) is None
    _extract_cypher(con_piede, "esterni mancini con overall almeno 85")


def test_il_piede_sbagliato_non_soddisfa_il_requisito():
    """Alla richiesta di aggiungere il piede il modello ha messo 'right' ai mancini."""
    piede_sbagliato = ESTERNI.replace("r.overall >= 85", "r.overall >= 85 AND r.preferred_foot = 'right'")
    assert "left-footed" in _missing_requirement("esterni mancini con overall almeno 85", piede_sbagliato)


def test_nessun_requisito_se_la_domanda_non_ne_nomina():
    assert _missing_requirement("esterni con overall almeno 85", ESTERNI) is None


@pytest.mark.parametrize("question", [
    "i 10 esterni migliori tra tutti i campionati",
    "confronto tra campionati per gol totali",
    "esterni con overall almeno 85, in qualsiasi campionato",
])
def test_il_campionato_generico_non_e_un_filtro(question):
    """'Tra tutti i campionati' non chiede m.league: pretenderlo ha fatto raggruppare per lega."""
    assert _missing_requirement(question, ESTERNI) is None


def test_il_campionato_specifico_lo_e():
    assert "league" in _missing_requirement("esterni del campionato italiano con overall almeno 85", ESTERNI)


# ---------- il nome del giocatore e' obbligatorio quando le righe descrivono giocatori ----------

SENZA_NOME = (
    "MATCH (p:Player)-[s:SCORED]->(m:Match) WHERE m.season = '2012/2013' WITH p, count(DISTINCT s) AS gol "
    "ORDER BY gol DESC LIMIT 3 MATCH (p)-[st:SEASON_TEAM {season: '2012/2013'}]->(t:Team) "
    "RETURN t.name AS squadra, st.position AS ruolo, gol"
)
CON_NOME = SENZA_NOME.replace("RETURN t.name", "RETURN p.name, t.name")
AGGREGATO = "MATCH (p:Player) WHERE p.active RETURN count(p) AS giocatori"
SOLO_ETA = "MATCH (p:Player) WHERE p.active RETURN p.age, p.position"


def test_rileva_i_giocatori_senza_nome():
    assert _missing_player_name(SENZA_NOME) and _missing_player_name(SOLO_ETA)
    raises_violation(MissingPlayerName, SENZA_NOME)


@pytest.mark.parametrize("cypher", [CON_NOME, AGGREGATO])
def test_accetta_il_nome_o_un_aggregato_puro(cypher):
    assert not _missing_player_name(cypher)
    _extract_cypher(cypher)


# ---------- soglie da stagione sui gol di una singola partita ----------

PARTITA_SINGOLA = [
    "MATCH (m:Match) WHERE m.season = '2015/2016' WITH m, m.home_goals + m.away_goals AS total_goals "
    "WHERE total_goals > 50 MATCH (t:Team)-[:HOME_TEAM]->(m) RETURN t.name, sum(m.home_goals) AS gol",
    "MATCH (t:Team)-[:HOME_TEAM]->(m:Match) WHERE m.season = '2015/2016' AND m.home_goals > 80 RETURN t.name",
    # Aggregato raggruppato per la partita stessa: e' ancora il valore della singola partita.
    "MATCH (m:Match) WHERE m.season = '2015/2016' WITH m, sum(m.home_goals) AS total_home_goals "
    "WHERE total_home_goals > 50 RETURN m.league, total_home_goals",
]
AGGREGATE_OK = [
    "MATCH (t:Team)-[:HOME_TEAM]->(m:Match) WHERE m.season = '2015/2016' WITH t, sum(m.home_goals) AS gol "
    "WHERE gol > 50 RETURN t.name, gol",
    # Una soglia possibile in una singola partita (goleada) e' legittima.
    "MATCH (t:Team)-[:HOME_TEAM]->(m:Match) WHERE m.home_goals >= 6 RETURN t.name, count(m) AS goleade",
]


@pytest.mark.parametrize("cypher", PARTITA_SINGOLA)
def test_rileva_la_soglia_sulla_singola_partita(cypher):
    assert _single_match_threshold(cypher)
    raises_violation(SingleMatchThreshold, cypher)


@pytest.mark.parametrize("cypher", AGGREGATE_OK)
def test_accetta_le_soglie_sugli_aggregati(cypher):
    assert not _single_match_threshold(cypher)
    _extract_cypher(cypher)


# ---------- un giocatore per riga, anche quando ha cambiato club a gennaio ----------

RIGORISTI = (
    "MATCH (p:Player)-[s:SCORED]->(m:Match) WHERE m.season = '2009/2010' AND s.penalty = true "
    "WITH p, count(DISTINCT s) AS rigori MATCH (p)-[st:SEASON_TEAM {season: '2009/2010'}]->(t:Team) "
    "RETURN p.name, rigori, t.name"
)


def test_rileva_il_giocatore_ripetuto_per_club():
    rows = [
        {"p.name": "Frank Lampard", "rigori": 10, "t.name": "Chelsea"},
        {"p.name": "Alessandro Diamanti", "rigori": 4, "t.name": "Livorno"},
        {"p.name": "Alessandro Diamanti", "rigori": 4, "t.name": "West Ham United"},
    ]
    assert _duplicated_players(RIGORISTI, rows) == ["Alessandro Diamanti"]


def test_una_riga_per_stagione_e_legittima():
    per_stagione = "MATCH (p:Player)-[r:RATED]->(s:Season) WHERE toLower(p.name) CONTAINS 'messi' RETURN p.name, r.season, r.stamina"
    rows = [
        {"p.name": "Lionel Messi", "r.season": "2014/2015", "r.stamina": 80},
        {"p.name": "Lionel Messi", "r.season": "2015/2016", "r.stamina": 78},
    ]
    assert _duplicated_players(per_stagione, rows) == []


def test_il_duplicato_e_un_errore_riparabile():
    assert issubclass(DuplicatedPlayers, ValueError) and not issubclass(DuplicatedPlayers, UnsafeCypher)


# ---------- null in testa alla classifica ----------

CLASSIFICA = (
    "MATCH (p:Player)-[r:RATED]->(:Season) WHERE p.role = 'defender' AND r.season = '2015/2016' "
    "WITH p, max(r.overall) AS overall MATCH (p)-[:CURRENT_TEAM]->(t:Team) "
    "RETURN p.name, overall, t.name ORDER BY overall DESC LIMIT 1"
)


def test_rileva_il_null_in_testa():
    rows = [{"p.name": "Papy Djilobodji", "overall": None, "t.name": "SV Werder Bremen"}]
    assert _null_ranked_first(CLASSIFICA, rows) == "overall"


def test_accetta_una_classifica_piena():
    rows = [{"p.name": "Giorgio Chiellini", "overall": 87, "t.name": "Juventus"}]
    assert _null_ranked_first(CLASSIFICA, rows) is None


def test_ignora_i_null_fuori_dalla_colonna_di_ordinamento():
    rows = [{"p.name": "Giorgio Chiellini", "overall": 87, "t.name": None}]
    assert _null_ranked_first(CLASSIFICA, rows) is None


def test_il_null_in_testa_e_un_errore_riparabile():
    assert issubclass(NullRankedFirst, ValueError) and not issubclass(NullRankedFirst, UnsafeCypher)


# ---------- proprieta' inesistenti: Neo4j restituisce null, non un errore ----------

@pytest.mark.parametrize("cypher, atteso", [
    ("MATCH (m:Match) WHERE m.season = '2015/2016' WITH m.home_team AS team, sum(m.home_goals) AS g RETURN team, g",
     ("m", "home_team", "Match")),
    ("MATCH (p:Player) WHERE p.overall > 85 RETURN p.name", ("p", "overall", "Player")),
    ("MATCH (p:Player)-[:RATED]->(s:Season) RETURN p.name, max(s.stamina)", ("s", "stamina", "Season")),
    ("MATCH (p:Player)-[:PLAYS_FOR]->(t:Team) WHERE t.league = 'Italy Serie A' RETURN p.name", ("t", "league", "Team")),
])
def test_rileva_la_proprieta_inesistente(cypher, atteso):
    assert _unknown_property(cypher) == atteso
    raises_violation(UnknownProperty, cypher)


@pytest.mark.parametrize("cypher", [
    "MATCH (t:Team)-[:HOME_TEAM]->(m:Match) WHERE m.season = '2015/2016' WITH t, sum(m.home_goals) AS g WHERE g > 50 RETURN t.name, g",
    "MATCH (p:Player)-[r:RATED]->(:Season) WHERE r.stamina > 80 RETURN p.name, max(r.stamina)",
    "MATCH (p:Player)-[st:SEASON_TEAM {season: '2010/2011', main: true}]->(t:Team) RETURN p.name, t.name, st.position",
    # Alias di WITH e variabili senza etichetta non vengono giudicati.
    "MATCH (p:Player)-[r:PLAYS_FOR]->(:Team) WITH p, max(r.overall) AS overall MATCH (p)-[:CURRENT_TEAM]->(t) RETURN p.name, overall, t.name",
])
def test_accetta_le_proprieta_dello_schema(cypher):
    assert _unknown_property(cypher) is None


# ---------- fascia dei club: RANKED, mai il nome del campionato contro t.name ----------

FASCIA = "prospetti under 23 con +10 di margine che non giocano gia in club di prima fascia"
CONFRONTO_SBAGLIATO = (
    "MATCH (p:Player)-[r:RATED]->(:Season), (p)-[:CURRENT_TEAM]->(t:Team) "
    "WHERE p.active AND p.age <= 23 AND r.season = '2015/2016' AND r.potential - r.overall >= 10 "
    "WITH p, t, max(r.overall) AS overall WHERE t.name <> 'England Premier League' AND t.name <> 'Italy Serie A' "
    "RETURN p.name, overall, t.name"
)
CON_RANKED = (
    "MATCH (p:Player)-[r:RATED {season: '2015/2016'}]->(:Season), "
    "(p)-[:CURRENT_TEAM]->(t:Team)-[k:RANKED {season: '2015/2016'}]->(:Season) "
    "WHERE p.active AND p.age <= 23 AND r.potential - r.overall >= 10 AND k.top = false "
    "RETURN p.name, r.potential - r.overall AS margine, p.age, r.overall, r.potential, t.name, k.rank, k.league ORDER BY margine DESC"
)


def test_rileva_il_campionato_confrontato_con_il_nome():
    assert "t.name <> 'England Premier League'" in _league_compared_to_name(CONFRONTO_SBAGLIATO)
    raises_violation(LeagueComparedToName, CONFRONTO_SBAGLIATO, FASCIA)


def test_la_fascia_esige_ranked():
    senza = CON_RANKED.replace("-[k:RANKED {season: '2015/2016'}]->(:Season)", "").replace(" AND k.top = false", "").replace(", k.rank, k.league", "")
    assert "tier" in _missing_requirement(FASCIA, senza)
    assert _missing_requirement(FASCIA, CON_RANKED) is None
    _extract_cypher(CON_RANKED, FASCIA)


def test_il_campionato_sulla_partita_e_lecito():
    assert _league_compared_to_name("MATCH (m:Match) WHERE m.league = 'Italy Serie A' RETURN count(m)") is None


# ---------- stagione nella domanda: il rating viene da RATED, non da PLAYS_FOR ----------

SNAPSHOT = (
    "MATCH (p:Player)-[r:PLAYS_FOR]->(:Team) WHERE p.active AND p.age < 23 AND r.potential - r.overall >= 10 "
    "MATCH (p)-[st:SEASON_TEAM {season: '2015/2016', main: true}]->(t:Team) RETURN p.name, max(r.overall) AS overall, t.name"
)


def test_rileva_il_rating_snapshot_con_stagione():
    assert _snapshot_rating_with_season("under 23 con +10 tra potential e overall nel 2015/2016", SNAPSHOT)
    raises_violation(SnapshotRatingWithSeason, SNAPSHOT, "under 23 con +10 tra potential e overall nel 2015/2016")


def test_senza_stagione_plays_for_e_lecito():
    assert not _snapshot_rating_with_season("under 23 con +10 tra potential e overall", SNAPSHOT)


def test_plays_for_per_il_piede_e_lecito_anche_con_stagione():
    piede = "MATCH (p:Player)-[f:PLAYS_FOR]->(:Team), (p)-[r:RATED {season: '2015/2016'}]->(:Season) WHERE f.preferred_foot = 'left' RETURN p.name, r.overall"
    assert not _snapshot_rating_with_season("mancini con overall alto nel 2015/2016", piede)


# ---------- la risposta non puo' negare le righe ----------

@pytest.mark.parametrize("answer", [
    "Nessun portiere in attivita tra i 20 e i 22 anni con overall tra 78 e 80.",
    "Non ci sono giocatori che soddisfano le condizioni.",
    "Non risultano giocatori.",
])
def test_rileva_la_risposta_che_nega_le_righe(answer):
    assert _answer_denies_rows(answer, [{"p.name": "Tom Davies"}])


def test_la_negazione_e_lecita_senza_righe():
    assert not _answer_denies_rows("Nessun giocatore soddisfa le condizioni.", [])
    assert not _answer_denies_rows("Trovati 353 giocatori.", [{"p.name": "Tom Davies"}])


def test_la_definizione_di_prima_fascia_viene_dichiarata():
    note = _tier_note(FASCIA, CON_RANKED)
    assert "30%" in note and "mai meno di 4" in note and "2015/2016" in note
    assert "e'" not in note


def test_una_soglia_esplicita_viene_dichiarata_come_tale():
    note = _tier_note(FASCIA, CON_RANKED.replace("k.top = false", "k.rank > 6"))
    assert "prime 6 squadre" in note


def test_nessuna_nota_senza_fascia_nella_domanda():
    assert _tier_note("squadre nella meta inferiore della classifica", "MATCH (t:Team)-[k:RANKED]->() WHERE k.rank > 10 RETURN t.name") is None


@pytest.mark.parametrize("question, cypher", [
    ("gol su rigore: la classifica dei 10 migliori",
     "MATCH (p:Player)-[s:SCORED]->(m:Match) WHERE s.penalty = true WITH p, count(DISTINCT s) AS n MATCH (p)-[:CURRENT_TEAM]->(t:Team) RETURN p.name, n, t.name"),
    ("classifica autogol: i 10 giocatori con piu autoreti",
     "MATCH (p:Player)-[o:OWN_GOAL]->(m:Match) WITH p, count(DISTINCT o) AS n MATCH (p)-[:CURRENT_TEAM]->(t:Team) RETURN p.name, n, t.name"),
])
def test_la_classifica_dei_giocatori_non_esige_ranked(question, cypher):
    assert _missing_requirement(question, cypher) is None


@pytest.mark.parametrize("question", [
    "squadre nella meta inferiore della classifica di Serie A 2015/2016",
    "portieri in squadre nella meta bassa della classifica",
    "chi era terzo in classifica nel 2012/2013 in Premier?",
])
def test_la_classifica_di_campionato_esige_ranked(question):
    # Una query che esprime stagione e campionato ma non legge RANKED.
    senza_ranked = "MATCH (t:Team)-[:HOME_TEAM]->(m:Match) WHERE m.season = '2015/2016' AND m.league = 'England Premier League' RETURN t.name"
    assert "tier" in _missing_requirement(question, senza_ranked)


# ---------- ordine riproducibile: il nome come spareggio ----------

def test_aggiunge_il_nome_come_spareggio():
    cypher = "MATCH (p:Player)-[r:RATED]->(:Season) RETURN p.name, r.potential - r.overall AS margine, t.name ORDER BY margine DESC LIMIT 10"
    assert _stable_order(cypher).endswith("ORDER BY margine DESC, p.name ASC LIMIT 10")


def test_usa_l_alias_del_nome_se_presente():
    cypher = "MATCH (p:Player)-[s:SCORED]->(m:Match) RETURN p.name AS nome, count(s) AS gol ORDER BY gol DESC"
    assert _stable_order(cypher).endswith("ORDER BY gol DESC, nome ASC")


def test_non_tocca_un_ordine_gia_per_nome_o_senza_giocatori():
    per_nome = "MATCH (p:Player) RETURN p.name ORDER BY p.name"
    assert _stable_order(per_nome) == per_nome
    squadre = "MATCH (t:Team)-[k:RANKED]->() RETURN t.name, k.rank ORDER BY k.rank"
    assert _stable_order(squadre) == squadre


# ---------- la metrica dell'ORDER BY deve essere una colonna ----------

def test_rileva_l_ordinamento_calcolato_non_restituito():
    cypher = "MATCH (p:Player)-[r:RATED]->(:Season) RETURN p.name, r.overall, r.potential ORDER BY r.potential - r.overall DESC"
    assert _ordering_not_returned(cypher) == "r.potential - r.overall"
    raises_violation(OrderingNotReturned, cypher)


@pytest.mark.parametrize("cypher", [
    "MATCH (p:Player)-[r:RATED]->(:Season) RETURN p.name, r.potential - r.overall AS margine ORDER BY margine DESC",
    "MATCH (p:Player)-[s:SCORED]->(m:Match) WITH p, count(s) AS gol RETURN p.name, gol ORDER BY gol DESC",
    "MATCH (p:Player) RETURN p.name ORDER BY p.name",
])
def test_accetta_l_ordinamento_restituito(cypher):
    assert _ordering_not_returned(cypher) is None


# ---------- CURRENT_TEAM in un elenco esige p.active ----------

def test_rileva_current_team_senza_active():
    cypher = ("MATCH (p:Player)-[r:RATED {season: '2015/2016'}]->(:Season) WHERE p.age <= 23 AND r.potential - r.overall >= 10 "
              "MATCH (p)-[:CURRENT_TEAM]->(t:Team) RETURN p.name, t.name")
    assert _current_team_without_active(cypher)
    raises_violation(CurrentTeamWithoutActive, cypher)


def test_accetta_current_team_con_active():
    cypher = "MATCH (p:Player)-[:CURRENT_TEAM]->(t:Team) WHERE p.active AND p.age <= 23 RETURN p.name, t.name"
    assert not _current_team_without_active(cypher)


def test_il_giocatore_nominato_non_esige_active():
    cypher = "MATCH (p:Player)-[:CURRENT_TEAM]->(t:Team) WHERE toLower(p.name) CONTAINS 'pirlo' RETURN p.name, t.name"
    assert not _current_team_without_active(cypher)


def test_la_nota_usa_il_termine_della_domanda():
    assert "'top club'" in _tier_note("under 23 che non giocano in un top club", CON_RANKED)


# ---------- soglie per stagione, RATED con stagione, tenuta = min ----------

STAMINA_Q = "giocatori con almeno 30 presenze nelle stagioni 2014/15 e 2015/16, mantenendo la stamina sopra 85"
STAMINA_SBAGLIATA = (
    "MATCH (p:Player)-[a:APPEARED_IN]->(m:Match) WHERE m.season IN ['2014/2015', '2015/2016'] AND p.active "
    "WITH p, count(DISTINCT m) AS presenze WHERE presenze >= 30 "
    "MATCH (p)-[r:RATED]->(:Season) WHERE r.stamina > 85 WITH p, presenze, max(r.stamina) AS stamina "
    "MATCH (p)-[:CURRENT_TEAM]->(t:Team) RETURN p.name, presenze, stamina, t.name ORDER BY presenze DESC"
)
STAMINA_GIUSTA = (
    "MATCH (p:Player)-[:APPEARED_IN]->(m:Match) WHERE m.season IN ['2014/2015', '2015/2016'] AND p.active "
    "WITH p, m.season AS stagione, count(DISTINCT m) AS presenze WHERE presenze >= 30 "
    "WITH p, collect(stagione + ': ' + toString(presenze)) AS presenze_per_stagione WHERE size(presenze_per_stagione) = 2 "
    "MATCH (p)-[r:RATED]->(:Season) WHERE r.season IN ['2014/2015', '2015/2016'] "
    "WITH p, presenze_per_stagione, min(r.stamina) AS stamina_minima, count(r) AS n, "
    "collect(r.season + ': ' + toString(r.stamina)) AS stamina_per_stagione WHERE n = 2 AND stamina_minima > 85 "
    "MATCH (p)-[:SEASON_TEAM {season: '2015/2016', main: true}]->(t:Team) RETURN p.name, presenze_per_stagione, stamina_per_stagione, stamina_minima, t.name ORDER BY stamina_minima DESC"
)


def test_rileva_le_presenze_sommate_sulle_stagioni():
    assert _appearances_not_per_season(STAMINA_Q, STAMINA_SBAGLIATA)
    assert not _appearances_not_per_season(STAMINA_Q, STAMINA_GIUSTA)
    raises_violation(AppearancesNotPerSeason, STAMINA_SBAGLIATA, STAMINA_Q)


def test_una_sola_stagione_non_esige_il_raggruppamento():
    assert not _appearances_not_per_season("presenze nel 2015/2016", STAMINA_SBAGLIATA)


def test_rileva_rated_senza_stagione():
    senza_conteggio = STAMINA_SBAGLIATA.replace("WITH p, count(DISTINCT m) AS presenze WHERE presenze >= 30 ", "WITH p ")
    assert _rated_without_season(STAMINA_Q, senza_conteggio)
    assert not _rated_without_season(STAMINA_Q, STAMINA_GIUSTA)
    assert not _rated_without_season("giocatori con stamina sopra 85", senza_conteggio)


def test_rileva_la_tenuta_con_max():
    assert _kept_attribute_with_max(STAMINA_Q, "MATCH (p:Player)-[r:RATED]->() WHERE r.season IN ['2014/2015'] WITH p, max(r.stamina) AS s WHERE s > 85 RETURN p.name, s")
    assert not _kept_attribute_with_max(STAMINA_Q, STAMINA_GIUSTA)
    assert not _kept_attribute_with_max("stamina massima raggiunta", "WITH p, max(r.stamina) AS s RETURN p.name, s")


def test_la_query_giusta_passa_tutti_i_controlli():
    _extract_cypher(STAMINA_GIUSTA, STAMINA_Q)


# ---------- raggruppamento per un valore di stagione; soglia prima del min ----------

QUASI_GIUSTA = (
    "MATCH (p:Player)-[a:APPEARED_IN]->(m:Match) WHERE m.season IN ['2014/2015', '2015/2016'] AND p.active "
    "WITH p, m.season AS stagione, count(DISTINCT m) AS presenze "
    "WITH p, collect(stagione) AS stagioni, presenze WHERE size(stagioni) = 2 AND presenze >= 30 "
    "MATCH (p)-[r:RATED]->(s:Season) WHERE r.season IN ['2014/2015', '2015/2016'] AND r.stamina > 85 "
    "WITH p, min(r.stamina) AS stamina, stagioni MATCH (p)-[:CURRENT_TEAM]->(t:Team) RETURN p.name, stamina, stagioni, t.name"
)


def test_rileva_il_raggruppamento_per_presenze():
    assert _grouped_by_measure(QUASI_GIUSTA) == "presenze"
    assert _grouped_by_measure(STAMINA_GIUSTA) is None
    raises_violation(GroupedByMeasure, QUASI_GIUSTA, STAMINA_Q)


def test_rileva_la_soglia_prima_del_min():
    assert _threshold_before_min(STAMINA_Q, QUASI_GIUSTA) == "r.stamina"
    assert _threshold_before_min(STAMINA_Q, STAMINA_GIUSTA) is None
    # Senza 'mantenendo' nella domanda il filtro sulla singola stagione e' legittimo.
    assert _threshold_before_min("giocatori con stamina sopra 85 nel 2015/2016", QUASI_GIUSTA) is None


# ---------- aggregato e chiave insieme; valori per stagione restituiti ----------

def test_rileva_la_variabile_aggregata_e_raggruppata():
    cypher = ("MATCH (p:Player)-[r:RATED]->(:Season) WHERE r.season IN ['2014/2015', '2015/2016'] "
              "WITH p, r, min(r.stamina) AS minimo, count(DISTINCT r) AS n WHERE n = 2 RETURN p.name, minimo")
    assert _aggregated_and_grouped(cypher) == "r"
    assert _aggregated_and_grouped(STAMINA_GIUSTA) is None


def test_rileva_i_valori_per_stagione_non_restituiti():
    senza = STAMINA_GIUSTA.replace("collect(stagione + ': ' + toString(presenze)) AS presenze_per_stagione", "count(*) AS stagioni_ok").replace("size(presenze_per_stagione) = 2", "stagioni_ok = 2").replace("presenze_per_stagione, stamina_minima", "stamina_minima")
    assert _per_season_values_not_returned(STAMINA_Q, senza) == "presenze"
    # Con le presenze collezionate resta da restituire la stamina per stagione (domanda con 'mantenendo').
    assert _per_season_values_not_returned(STAMINA_Q, STAMINA_GIUSTA) is None


def test_una_sola_stagione_non_pretende_la_scomposizione():
    assert _per_season_values_not_returned("presenze nel 2015/2016", "MATCH (p:Player)-[:APPEARED_IN]->(m:Match) WHERE m.season = '2015/2016' WITH p, m.season AS s, count(m) AS presenze RETURN p.name") is None


def test_l_order_by_intermedio_non_e_giudicato():
    cypher = ("MATCH (p:Player)-[r:RATED]->(:Season) WITH p, r.season AS stagione, r.stamina AS stamina ORDER BY stagione "
              "WITH p, collect(stagione + ': ' + toString(stamina)) AS stamina_per_stagione, min(stamina) AS minimo "
              "RETURN p.name, stamina_per_stagione, minimo ORDER BY minimo DESC")
    assert _ordering_not_returned(cypher) is None
    assert _stable_order(cypher).endswith("ORDER BY minimo DESC, p.name ASC")


def test_le_presenze_sono_un_requisito_tracciato():
    solo_stamina = "MATCH (p:Player)-[r:RATED]->(:Season) WHERE r.season IN ['2014/2015', '2015/2016'] WITH p, min(r.stamina) AS minimo RETURN p.name, minimo"
    assert "appearances" in _missing_requirement("stamina mai sotto 85 e minimo 30 presenze a stagione nel 2014/15 e 2015/16", solo_stamina)


# ---------- operatori di confronto coerenti con le parole ----------

@pytest.mark.parametrize("question, cypher, atteso", [
    ("stamina sempre sopra 85", "WHERE minimo >= 85", "says > 85"),
    ("almeno 30 presenze", "WHERE presenze > 30", "says >= 30"),
    ("stamina mai sotto 85", "WHERE minimo > 85", "says >= 85"),
    ("overall inferiore a 70", "WHERE overall <= 70", "says < 70"),
    ("al massimo 23 anni", "WHERE p.age < 23", "says <= 23"),
])
def test_rileva_l_operatore_sbagliato(question, cypher, atteso):
    assert atteso in _wrong_comparison(question, cypher)


@pytest.mark.parametrize("question, cypher", [
    ("stamina sempre sopra 85 e almeno 30 presenze", "WHERE minimo > 85 AND presenze >= 30"),
    ("età minore o uguale a 23 anni", "WHERE p.age <= 23"),
    ("piu di 150 presenze", "WHERE presenze > 150"),
    ("overall tra 78 e 80", "WHERE overall >= 78 AND overall <= 80"),
])
def test_accetta_l_operatore_giusto(question, cypher):
    assert _wrong_comparison(question, cypher) is None


def test_la_stagione_sul_nodo_season_vale_come_vincolo():
    cypher = "MATCH (p:Player)-[r:RATED]->(s:Season) WHERE s.name IN ['2014/2015', '2015/2016'] WITH p, min(r.stamina) AS m RETURN p.name, m"
    assert not _rated_without_season(STAMINA_Q, cypher)


FLAT = (
    "MATCH (p:Player)-[s1:SEASON_TEAM {season: '2014/2015', main: true}]->(:Team), "
    "(p)-[s2:SEASON_TEAM {season: '2015/2016', main: true}]->(t:Team), "
    "(p)-[r1:RATED {season: '2014/2015'}]->(:Season), (p)-[r2:RATED {season: '2015/2016'}]->(:Season) "
    "WHERE p.active AND s1.season_total >= 30 AND s2.season_total >= 30 AND r1.stamina > 85 AND r2.stamina > 85 "
    "RETURN p.name, s1.season_total AS presenze_2014_2015, s2.season_total AS presenze_2015_2016, "
    "r1.stamina AS stamina_2014_2015, r2.stamina AS stamina_2015_2016, t.name ORDER BY stamina_2015_2016 DESC"
)


def test_la_forma_piatta_passa_tutti_i_controlli():
    _extract_cypher(FLAT, STAMINA_Q)


def test_il_club_deve_essere_una_colonna_non_solo_una_relazione():
    anonimo = ("MATCH (p:Player)-[s1:SEASON_TEAM {season: '2015/2016', main: true}]->(:Team), "
               "(p)-[r:RATED {season: '2015/2016'}]->(:Season) WHERE p.active RETURN p.name, r.stamina")
    assert _missing_club(anonimo)
    con_colonna = anonimo.replace("->(:Team)", "->(t:Team)").replace("RETURN p.name, r.stamina", "RETURN p.name, r.stamina, t.name")
    assert not _missing_club(con_colonna)


def test_rileva_la_variabile_team_riusata():
    stesso_club = FLAT.replace("]->(:Team), ", "]->(t:Team), ", 1)
    assert "t (" in _team_variable_reused(stesso_club)
    assert _team_variable_reused(FLAT) is None
    corrente = "MATCH (p:Player)-[r:PLAYS_FOR]->(t:Team), (p)-[:CURRENT_TEAM]->(t) WHERE p.active RETURN p.name, t.name"
    assert _team_variable_reused(corrente) is not None


def test_rileva_la_soglia_mai_applicata():
    assert "never compares anything with 30" in _wrong_comparison("minimo 30 presenze a stagione e stamina mai sotto 86", "WHERE minimo >= 86 RETURN p.name, presenze")


def test_current_team_su_stagione_passata():
    cypher = ("MATCH (p:Player)-[s:SCORED]->(m:Match) WHERE m.season = '2009/2010' AND s.penalty = true "
              "WITH p, count(DISTINCT s) AS rigori MATCH (p)-[:CURRENT_TEAM]->(t:Team) RETURN p.name, rigori, t.name")
    assert _current_team_for_past_season("top 10 rigoristi nella stagione 2009/2010", cypher) == "2009/2010"
    assert _current_team_for_past_season("top 10 rigoristi nella stagione 2015/2016", cypher) is None
    assert _current_team_for_past_season("top 10 rigoristi nel 2009/10", cypher) == "2009/2010"
    raises_violation(CurrentTeamForPastSeason, cypher, "top 10 rigoristi nella stagione 2009/2010")


# ---------- cio' su cui si filtra va in tabella ----------

def test_rileva_il_filtro_non_restituito():
    nascosto = FLAT.replace("s1.season_total AS presenze_2014_2015, s2.season_total AS presenze_2015_2016, ", "")
    assert _filtered_not_returned(nascosto) == "s1.season_total"
    assert _filtered_not_returned(FLAT) is None


def test_accetta_alias_e_aggregati_del_filtro():
    alias = "MATCH (p:Player)-[:APPEARED_IN]->(m:Match) WITH p, count(DISTINCT m) AS presenze WHERE presenze > 150 RETURN p.name, presenze"
    assert _filtered_not_returned(alias) is None
    espressione = "MATCH (p:Player)-[r:RATED]->(:Season) WHERE r.potential - r.overall >= 10 RETURN p.name, r.potential - r.overall AS margine"
    assert _filtered_not_returned(espressione) is None
    contesto = "MATCH (p:Player)-[st:SEASON_TEAM]->(t:Team) WHERE st.season = '2015/2016' AND p.age <= 23 RETURN p.name, p.age, t.name"
    assert _filtered_not_returned(contesto) is None
    eta_nascosta = "MATCH (p:Player)-[:CURRENT_TEAM]->(t:Team) WHERE p.active AND p.age <= 23 RETURN p.name, t.name"
    assert _filtered_not_returned(eta_nascosta) == "p.age"


def test_l_alias_dentro_un_espressione_non_e_una_colonna():
    solo_differenziale = ("MATCH (p:Player)-[r1:RATED {season: '2014/2015'}]->(:Season), (p)-[r2:RATED {season: '2015/2016'}]->(:Season) "
                          "WHERE r1.stamina > 80 AND r2.stamina > 80 WITH p, r1.stamina AS s14, r2.stamina AS s15 "
                          "RETURN p.name, s15 - s14 AS differenziale")
    assert _filtered_not_returned(solo_differenziale) == "r1.stamina (alias `s14`)"
    con_colonne = solo_differenziale.replace("RETURN p.name, s15 - s14 AS differenziale", "RETURN p.name, s14, s15, s15 - s14 AS differenziale")
    assert _filtered_not_returned(con_colonne) is None


# ---------- "difensori in Premier League": stessa domanda, due forme, stessa risposta ----------

DIFENSORI_ITA = ("Cercare difensori in premier league che abbiano altezza maggiore di 190 cm e peso maggiore "
                 "di 185 lbs, associati ad precisione di testa > 82 ed elevazione > 80")
DIFENSORI_ENG = ("Cercare difensori in premier league che abbiano la proprietà height > 190 cm e weight > 185 lbs, "
                 "associati a heading_accuracy > 82 e jumping > 80")

DIFENSORI_OK = (
    "MATCH (p:Player)-[st:SEASON_TEAM {season: '2015/2016', main: true}]->(t:Team) "
    "WHERE p.role = 'defender' AND p.active AND st.league = 'England Premier League' "
    "AND p.height > 190 AND p.weight > 185 "
    "MATCH (p)-[r:RATED {season: '2015/2016'}]->(:Season) WHERE r.heading_accuracy > 82 AND r.jumping > 80 "
    "RETURN p.name, r.overall AS overall, p.height, p.weight, r.heading_accuracy AS heading_accuracy, r.jumping AS jumping, t.name ORDER BY overall DESC"
)
# Forma 1 osservata: "precisione di testa" letta come gol di testa, e la Premier passata dai gol.
DIFENSORI_GOL_DI_TESTA = (
    "MATCH (p:Player)-[r:RATED]->(:Season) WHERE p.role = 'defender' AND p.active AND p.height > 190 "
    "AND p.weight > 185 AND r.heading_accuracy > 82 AND r.jumping > 80 "
    "MATCH (p)-[s:SCORED]->(m:Match) WHERE m.league = 'England Premier League' AND s.subtype = 'header' "
    "WITH p, max(r.heading_accuracy) AS heading_accuracy, max(r.jumping) AS jumping "
    "MATCH (p)-[:CURRENT_TEAM]->(t:Team) "
    "RETURN p.name, p.height, p.weight, heading_accuracy, jumping, t.name ORDER BY p.height DESC"
)
# Forma 2 osservata: la Premier passata dal conteggio delle presenze, che poi decide l'ordine.
DIFENSORI_PRESENZE = (
    "MATCH (p:Player)-[r:RATED]->(:Season) WHERE p.role = 'defender' AND p.active AND p.weight > 185 "
    "AND r.heading_accuracy > 82 AND r.jumping > 80 "
    "MATCH (p)-[:APPEARED_IN]->(m:Match) WHERE m.league = 'England Premier League' AND p.height > 190 "
    "WITH p, max(r.heading_accuracy) AS heading_accuracy, max(r.jumping) AS jumping, count(DISTINCT m) AS presenze "
    "MATCH (p)-[:CURRENT_TEAM]->(t:Team) "
    "RETURN p.name, p.height, p.weight, heading_accuracy, jumping, presenze, t.name ORDER BY presenze DESC, p.name ASC"
)


def test_precisione_di_testa_e_un_attributo_non_un_gol():
    # Prima "di testa" pretendeva s.subtype = 'header' e la query esigeva un gol di testa in Premier.
    assert _missing_requirement(DIFENSORI_ITA, DIFENSORI_OK) is None
    assert _missing_requirement("difensori con precisione nei colpi di testa sopra 82", DIFENSORI_OK) is None
    # I gol di testa restano un requisito quando la domanda li chiede davvero.
    assert "header" in _missing_requirement("i 10 giocatori con piu' gol di testa", "MATCH (p:Player)-[s:SCORED]->(m:Match) RETURN p.name, count(s)")


def test_la_lega_senza_stagione_non_si_legge_dalle_partite():
    assert "m.league" in _league_membership_via_matches(DIFENSORI_ITA, DIFENSORI_GOL_DI_TESTA)
    assert "m.league" in _league_membership_via_matches(DIFENSORI_ENG, DIFENSORI_PRESENZE)
    assert _league_membership_via_matches(DIFENSORI_ITA, DIFENSORI_OK) is None
    assert _league_membership_via_matches(DIFENSORI_ENG, DIFENSORI_OK) is None
    raises_violation(LeagueMembershipViaMatches, DIFENSORI_GOL_DI_TESTA, DIFENSORI_ITA)
    raises_violation(LeagueMembershipViaMatches, DIFENSORI_PRESENZE, DIFENSORI_ENG)


def test_le_partite_di_un_campionato_si_contano_quando_la_domanda_lo_chiede():
    presenze = "MATCH (p:Player)-[:APPEARED_IN]->(m:Match) WHERE m.league = 'England Premier League' WITH p, count(DISTINCT m) AS presenze WHERE presenze > 150 RETURN p.name, presenze"
    assert _league_membership_via_matches("piu' di 150 presenze in Premier League", presenze) is None
    gol = "MATCH (p:Player)-[s:SCORED]->(m:Match) WHERE m.league = 'Italy Serie A' RETURN p.name, count(DISTINCT s) AS gol"
    assert _league_membership_via_matches("chi ha segnato di piu' in Serie A", gol) is None
    stagione = "MATCH (p:Player)-[:APPEARED_IN]->(m:Match) WHERE m.league = 'Italy Serie A' AND m.season = '2014/2015' RETURN p.name, count(DISTINCT m)"
    assert _league_membership_via_matches("difensori della Serie A nel 2014/2015", stagione) is None


def test_l_ordine_per_presenze_va_giustificato_dalla_domanda():
    assert _ordered_by_unasked_appearances(DIFENSORI_ENG, DIFENSORI_PRESENZE) == "presenze"
    assert _ordered_by_unasked_appearances(DIFENSORI_ENG, DIFENSORI_OK) is None
    assert _ordered_by_unasked_appearances("piu' di 150 presenze in Premier League", DIFENSORI_PRESENZE) is None
    conteggio = DIFENSORI_PRESENZE.replace("AS presenze", "AS n").replace("presenze, t.name", "n, t.name").replace("ORDER BY presenze", "ORDER BY n")
    assert _ordered_by_unasked_appearances(DIFENSORI_ENG, conteggio) == "n"
    raises_violation(OrderedByUnaskedAppearances, DIFENSORI_PRESENZE, DIFENSORI_ENG)


def test_la_forma_corretta_passa_tutti_i_controlli_in_entrambe_le_formulazioni():
    for question in (DIFENSORI_ITA, DIFENSORI_ENG):
        assert _extract_cypher(DIFENSORI_OK, question).startswith("MATCH (p:Player)-[st:SEASON_TEAM")


def test_rileva_il_giocatore_ripetuto_con_valori_di_stagioni_diverse():
    # Gary Cahill tre volte, stesse presenze e stesso club, heading_accuracy di tre stagioni.
    rows = [
        {"p.name": "Gary Cahill", "heading_accuracy": 86, "jumping": 82, "presenze": 233, "t.name": "Chelsea"},
        {"p.name": "Gary Cahill", "heading_accuracy": 84, "jumping": 81, "presenze": 233, "t.name": "Chelsea"},
        {"p.name": "Gary Cahill", "heading_accuracy": 83, "jumping": 83, "presenze": 233, "t.name": "Chelsea"},
        {"p.name": "Brede Hangeland", "heading_accuracy": 83, "jumping": 89, "presenze": 221, "t.name": "Crystal Palace"},
    ]
    assert _duplicated_players(DIFENSORI_PRESENZE, rows) == ["Gary Cahill"]
    # Una riga per posizione coperta in distinta e' un dettaglio chiesto, non un doppione.
    per_ruolo = "MATCH (p:Player)-[a:APPEARED_IN]->(m:Match) WHERE toLower(p.name) CONTAINS 'vidal' RETURN p.name, a.position, count(m) AS partite"
    assert _duplicated_players(per_ruolo, [{"p.name": "Arturo Vidal", "a.position": "central_midfielder", "partite": 100},
                                           {"p.name": "Arturo Vidal", "a.position": "centre_back", "partite": 3}]) == []


def test_il_ruolo_va_filtrato_con_un_valore_che_esiste():
    # Riparazione osservata: 'defender' e' un valore di p.role, messo dentro p.position -> zero righe.
    sbagliato = DIFENSORI_OK.replace("p.role = 'defender'", "p.position IN ['defender']")
    assert "value of p.role" in _wrong_role_value(sbagliato)
    assert _wrong_role_value(DIFENSORI_OK) is None
    assert _wrong_role_value(DIFENSORI_OK.replace("p.role = 'defender'", "p.position IN ['centre_back', 'full_back']")) is None
    assert "not a value of p.role" in _wrong_role_value("MATCH (p:Player) WHERE p.role = 'centre_back' RETURN p.name")
    raises_violation(WrongRoleValue, sbagliato, DIFENSORI_ITA)


def test_la_riparazione_del_ruolo_dice_quale_filtro_usare():
    assert _expected_role_filter(DIFENSORI_ITA) == "p.role = 'defender'"
    assert _expected_role_filter("difensori centrali oltre 188 cm") == "p.position = 'centre_back'"
    assert _expected_role_filter("esterni mancini con overall almeno 85") == "p.position IN ['winger', 'wide_midfielder']"
    assert _expected_role_filter("alternativa a Hugo Lloris") is None
    senza_ruolo = DIFENSORI_OK.replace("p.role = 'defender' AND ", "")
    with pytest.raises(ValueError) as raised:
        _extract_cypher(senza_ruolo, DIFENSORI_ITA)
    assert "p.role = 'defender'" in str(raised.value)


def test_con_sole_soglie_l_ordine_e_l_overall():
    per_altezza = DIFENSORI_OK.replace("ORDER BY overall DESC", "ORDER BY p.height DESC")
    per_attributo = DIFENSORI_OK.replace("ORDER BY overall DESC", "ORDER BY heading_accuracy DESC, jumping DESC")
    for question in (DIFENSORI_ITA, DIFENSORI_ENG):
        assert _thresholds_without_ranking_metric(question, per_altezza) == "p.height"
        assert _thresholds_without_ranking_metric(question, per_attributo) == "heading_accuracy"
        assert _thresholds_without_ranking_metric(question, DIFENSORI_OK) is None
    raises_violation(ThresholdsWithoutRankingMetric, per_altezza, DIFENSORI_ITA)
    raises_violation(ThresholdsWithoutRankingMetric, per_attributo, DIFENSORI_ENG)
    assert _thresholds_without_ranking_metric(DIFENSORI_ITA, DIFENSORI_OK.replace(" ORDER BY overall DESC", "")) == "(no ORDER BY)"


def test_una_metrica_nominata_decide_l_ordine_da_sola():
    per_attributo = DIFENSORI_OK.replace("ORDER BY overall DESC", "ORDER BY heading_accuracy DESC")
    # Una classifica esplicita, una metrica di partite, una sola soglia o una soglia sull'overall: nessun vincolo.
    assert _thresholds_without_ranking_metric("i migliori difensori con heading_accuracy > 82 e jumping > 80", per_attributo) is None
    assert _thresholds_without_ranking_metric("difensori con piu' gol di testa, altezza > 190 e peso > 185", per_attributo) is None
    assert _thresholds_without_ranking_metric("difensori con heading_accuracy > 82", per_attributo) is None
    assert _thresholds_without_ranking_metric("difensori con overall > 80 e jumping > 80", per_attributo) is None
    assert _thresholds_without_ranking_metric("difensori centrali oltre 188 cm con i massimi valori combinati di heading_accuracy e jumping", per_attributo) is None


def test_il_criterio_di_ordinamento_si_legge_dalla_query():
    from app.rag import _ordering_label
    assert _ordering_label(DIFENSORI_OK) == "overall decrescente"
    assert _ordering_label(DIFENSORI_PRESENZE) == "presenze decrescente"
    assert _ordering_label(DIFENSORI_OK.replace("ORDER BY overall DESC", "ORDER BY p.height DESC, p.name ASC")) == "altezza decrescente"
    assert _ordering_label(DIFENSORI_OK.replace(" ORDER BY overall DESC", "")) is None


def test_senza_stagione_nella_domanda_si_legge_la_stagione_corrente():
    # Forma 2 osservata: RATED su tutta la carriera con max() (quattro difensori, Distin con un valore di anni prima).
    carriera = DIFENSORI_OK.replace("[r:RATED {season: '2015/2016'}]", "[r:RATED]")
    assert "without a season" in _rating_not_current(DIFENSORI_ENG, carriera)
    assert _rating_not_current(DIFENSORI_ITA, DIFENSORI_OK) is None
    altra = DIFENSORI_OK.replace("{season: '2015/2016'}]->(:Season)", "{season: '2012/2013'}]->(:Season)")
    assert "2012/2013" in _rating_not_current(DIFENSORI_ITA, altra)
    raises_violation(RatingNotCurrent, carriera, DIFENSORI_ENG)
    # I gol seguono la stessa regola: 'top 10 rigoristi' senza stagione e' il 2015/2016.
    rigori = "MATCH (p:Player)-[s:SCORED]->(m:Match) WHERE s.penalty = true WITH p, count(DISTINCT s) AS rigori MATCH (p)-[:CURRENT_TEAM]->(t:Team) RETURN p.name, rigori, t.name ORDER BY rigori DESC LIMIT 10"
    assert "SCORED" in _rating_not_current("top 10 rigoristi", rigori)
    assert _rating_not_current("top 10 rigoristi", rigori.replace("WHERE s.penalty = true", "WHERE m.season = '2015/2016' AND s.penalty = true")) is None


def test_la_carriera_e_l_andamento_restano_leggibili_su_richiesta():
    carriera = DIFENSORI_OK.replace("[r:RATED {season: '2015/2016'}]", "[r:RATED]")
    for question in ("difensori con heading_accuracy > 82 in carriera", "top 10 rigoristi di sempre",
                     "difensori la cui stamina e' sempre sopra 85", "centrocampisti con overall medio sopra 75",
                     "attaccanti in crescita di almeno 8 punti", "difensori senza cali drastici di strength",
                     "difensori nel 2012/2013 con heading_accuracy > 82", "alternativa a Pirlo, che era un regista"):
        assert _rating_not_current(question, carriera) is None, question
    fascia = "MATCH (p:Player)-[r:RATED {season: '2015/2016'}]->(:Season), (p)-[:CURRENT_TEAM]->(t:Team)-[k:RANKED {season: '2015/2016'}]->(:Season) WHERE k.top = false RETURN p.name, r.overall, t.name"
    assert _rating_not_current("prospetti che non giocano in un club di prima fascia", fascia) is None
    presenze = "MATCH (p:Player)-[:APPEARED_IN]->(m:Match) WHERE m.league = 'England Premier League' WITH p, count(DISTINCT m) AS presenze WHERE presenze > 150 RETURN p.name, presenze"
    assert _rating_not_current("piu' di 150 presenze in Premier League", presenze) is None  # le presenze sono cumulative


def test_gli_omonimi_non_sono_doppioni():
    posizioni = "MATCH (p:Player)-[a:APPEARED_IN]->(m:Match) WITH p, count(DISTINCT a.position) AS posizioni WHERE posizioni >= 3 MATCH (p)-[:CURRENT_TEAM]->(t:Team) RETURN p.name, posizioni, t.name"
    rows = [
        {"p.name": "Juanfran", "posizioni": 4, "t.name": "Atletico Madrid"},
        {"p.name": "Juanfran", "posizioni": 3, "t.name": "RC Deportivo de La Coruna"},
        {"p.name": "Gonzalo Castro", "posizioni": 5, "t.name": "Bayer 04 Leverkusen"},
        {"p.name": "Gonzalo Castro", "posizioni": 3, "t.name": "Malaga CF"},
    ]
    assert _duplicated_players(posizioni, rows) == []


def test_l_alias_del_nome_non_aggira_il_controllo_dei_doppioni():
    alias = DIFENSORI_PRESENZE.replace("RETURN p.name,", "RETURN p.name AS player_name,")
    rows = [
        {"player_name": "Zlatan Ibrahimovic", "heading_accuracy": 86, "jumping": 82, "presenze": 233, "t.name": "Paris Saint-Germain"},
        {"player_name": "Zlatan Ibrahimovic", "heading_accuracy": 84, "jumping": 81, "presenze": 233, "t.name": "Paris Saint-Germain"},
    ]
    assert _duplicated_players(alias, rows) == ["Zlatan Ibrahimovic"]


def test_la_chiave_di_ordinamento_semplice_deve_essere_una_colonna():
    # Osservato: ORDER BY r.overall senza restituirlo; la risposta ha mostrato la heading_accuracy (86) come overall.
    nascosto = DIFENSORI_OK.replace("r.overall AS overall, ", "").replace("ORDER BY overall DESC", "ORDER BY r.overall DESC")
    assert _ordering_not_returned(nascosto) == "r.overall"
    assert _ordering_not_returned(DIFENSORI_OK) is None
    assert _ordering_not_returned(DIFENSORI_OK.replace("ORDER BY overall DESC", "ORDER BY r.overall DESC, p.name ASC")) is None
    raises_violation(OrderingNotReturned, nascosto, DIFENSORI_ITA)


def test_gli_omonimi_con_valori_uguali_si_distinguono_sul_grafo():
    # Tre Rafinha diversi con lo stesso numero di posizioni: righe identiche tranne il club,
    # la stessa firma di un giocatore ripetuto. Solo il grafo sa che sono tre persone.
    from app.rag import ScoutAssistant

    class Graph:
        def query(self, cypher, parameters=None):
            return [{"n": {"Rafinha": 3, "Alessandro Diamanti": 1}[parameters["name"]]}]

    assistant = ScoutAssistant.__new__(ScoutAssistant)
    assistant.graph = Graph()
    rafinha = [{"p.name": "Rafinha", "posizioni": 3, "t.name": club} for club in ("FC Barcelona", "FC Bayern Munich", "KAA Gent")]
    assert not assistant._is_one_player("Rafinha", rafinha)
    diamanti = [{"p.name": "Alessandro Diamanti", "rigori": 4, "t.name": club} for club in ("Livorno", "West Ham United")]
    assert assistant._is_one_player("Alessandro Diamanti", diamanti)


def test_il_piede_scritto_alla_lettera_e_un_requisito():
    # "preferred_foot = 'left'" nella domanda non era riconosciuto: il piede e' sparito dalla query
    # e Hazard e Clyne, destri, sono usciti tra i mancini.
    senza_piede = ("MATCH (p:Player)-[st:SEASON_TEAM {season: '2015/2016', main: true}]->(t:Team) "
                   "WHERE st.league = 'England Premier League' AND p.active AND p.age < 26 "
                   "MATCH (p)-[r:RATED {season: '2015/2016'}]->(:Season) WHERE r.sprint_speed > 80 AND r.crossing > 78 "
                   "RETURN p.name, r.overall AS overall, p.age, r.sprint_speed, r.crossing, t.name ORDER BY overall DESC")
    for question in ("Filtrare i giocatori di premier league con preferred_foot = 'left', età < 26, sprint_speed > 80 e crossing > 78",
                     "Filtrare i giocatori di premier league con piede mancino, età < 26, sprint_speed > 80 e crossing > 78",
                     "Filtrare i giocatori di premier league con piede sinistro, età < 26, sprint_speed > 80 e crossing > 78",
                     "left-footed Premier League players under 26"):
        assert "left-footed" in _missing_requirement(question, senza_piede), question
    con_piede = senza_piede.replace("p.active AND", "p.active AND p.preferred_foot = 'left' AND")
    assert _missing_requirement("giocatori con piede mancino, età < 26", con_piede) is None
    inline = senza_piede.replace("MATCH (p:Player)-", "MATCH (p:Player {preferred_foot: 'left'})-")
    assert _missing_requirement("giocatori con piede mancino, età < 26", inline) is None


def test_il_piede_sta_sul_giocatore_non_su_rated():
    assert _unknown_property("MATCH (p:Player)-[r:RATED {season: '2015/2016'}]->(:Season) WHERE r.preferred_foot = 'left' RETURN p.name") == ("r", "preferred_foot", "RATED")
    assert _unknown_property("MATCH (p:Player) WHERE p.preferred_foot = 'left' RETURN p.name") is None
    with pytest.raises(ValueError) as raised:
        _extract_cypher("MATCH (p:Player)-[r:RATED {season: '2015/2016'}]->(:Season) WHERE r.preferred_foot = 'left' AND p.active RETURN p.name, r.overall AS overall", "mancini")
    assert "p.preferred_foot" in str(raised.value)


def test_l_elenco_numerato_lo_scrive_il_codice_dalle_righe():
    from app.rag import _rebuild_list
    cypher = ("MATCH (p:Player)-[st:SEASON_TEAM {season: '2015/2016', main: true}]->(t:Team) "
              "MATCH (p)-[r:RATED {season: '2015/2016'}]->(:Season) WITH p, t, r "
              "RETURN p.name, p.age, r.sprint_speed AS sprint_speed, t.name, r.overall AS overall ORDER BY overall DESC, p.name ASC")
    rows = [{"p.name": "Abdul Rahman Baba", "p.age": 21, "sprint_speed": 86, "t.name": "Chelsea", "overall": 77},
            {"p.name": "Alberto Moreno", "p.age": 23, "sprint_speed": 87, "t.name": "Liverpool", "overall": 77}]
    # Osservato: il modello ha copiato lo sprint_speed come overall e invertito i due.
    sbagliato = "2 giocatori, ordinati per overall.\n\n1. Alberto Moreno (Liverpool), 87\n2. Abdul Rahman Baba (Chelsea), 86"
    assert _rebuild_list(sbagliato, cypher, rows) == "2 giocatori, ordinati per overall.\n\n1. Abdul Rahman Baba (Chelsea), 77\n2. Alberto Moreno (Liverpool), 77"
    # Una riga sola e' una frase, non un elenco; senza club o senza ORDER BY non si tocca.
    assert _rebuild_list("Il migliore e' Baba (Chelsea) con 77", cypher, rows[:1]) == "Il migliore e' Baba (Chelsea) con 77"
    assert _rebuild_list(sbagliato, cypher.split(" ORDER BY")[0], rows) == sbagliato
    # La chiave e' un'espressione proiettata con alias: overall_medio AS ... ; float formattati.
    media = "MATCH (p:Player)-[:CURRENT_TEAM]->(t:Team) RETURN p.name, avg(1.0) AS overall_medio, t.name ORDER BY overall_medio DESC"
    assert _rebuild_list("x\n1. A (B), 1", media, [{"p.name": "A", "overall_medio": 79.0, "t.name": "B"}, {"p.name": "C", "overall_medio": 78.456, "t.name": "D"}]).endswith("1. A (B), 79\n2. C (D), 78.46")


VARIANZA = ("Trovare i giocatori della premier league la cui varianza dell'overall_rating tra il 2012 e il 2016 è inferiore "
            "a 3 punti (es. sempre tra 75 e 78), con almeno 25 presenze medie annue (da nodo Match).")
VARIANZA_OK = (
    "MATCH (p:Player)-[st:SEASON_TEAM {season: '2015/2016', main: true}]->(t:Team) WHERE st.league = 'England Premier League' AND p.active "
    "MATCH (p)-[r:RATED]->(:Season) WHERE r.season IN ['2012/2013', '2013/2014', '2014/2015', '2015/2016'] WITH p, t, r ORDER BY r.season "
    "WITH p, t, count(r) AS stagioni, max(r.overall) - min(r.overall) AS escursione, min(r.overall) AS minimo, max(r.overall) AS massimo, "
    "collect(r.season + ': ' + toString(r.overall)) AS overall_per_stagione WHERE stagioni = 4 AND escursione < 3 "
    "MATCH (p)-[:APPEARED_IN]->(m:Match) WHERE m.season IN ['2012/2013', '2013/2014', '2014/2015', '2015/2016'] "
    "WITH p, t, escursione, minimo, massimo, overall_per_stagione, count(DISTINCT m) / 4.0 AS presenze_medie WHERE presenze_medie >= 25 "
    "RETURN p.name, escursione, minimo, massimo, overall_per_stagione, presenze_medie, t.name ORDER BY escursione ASC, presenze_medie DESC"
)


def test_intervallo_di_anni_e_presenze_medie():
    # Prima: `presenze >= 100` (25 x 4) su una sola stagione, bloccato per sempre dal controllo sul 25.
    assert _extract_cypher(VARIANZA_OK, VARIANZA).startswith("MATCH")
    totale = VARIANZA_OK.replace("count(DISTINCT m) / 4.0 AS presenze_medie WHERE presenze_medie >= 25", "count(DISTINCT m) AS presenze WHERE presenze >= 100").replace("presenze_medie, t.name", "presenze, t.name").replace(", presenze_medie DESC", "")
    problem = _wrong_comparison(VARIANZA, totale)
    assert "25" in problem and "AVERAGE" in problem
    # "tra il 2012 e il 2016" e' un'indicazione di stagioni: RATED senza vincolo viene respinta.
    assert _rated_without_season(VARIANZA, "MATCH (p:Player)-[r:RATED]->(:Season) WITH p, max(r.overall) - min(r.overall) AS escursione RETURN p.name, escursione")
    # Osservato: SEASON_TEAM {main: true} senza stagione, Nzonzi e Cahill quattro volte.
    from app.rag import SeasonTeamWithoutSeason, _season_team_without_season
    senza_stagione = VARIANZA_OK.replace("SEASON_TEAM {season: '2015/2016', main: true}", "SEASON_TEAM {main: true}")
    assert _season_team_without_season(senza_stagione) == "st"
    assert _season_team_without_season(VARIANZA_OK) is None
    assert _season_team_without_season("MATCH (p:Player)-[st:SEASON_TEAM]->(t:Team) WHERE st.season = '2010/2011' RETURN p.name, t.name") is None
    raises_violation(SeasonTeamWithoutSeason, senza_stagione, VARIANZA)
    assert "season" in _missing_requirement(VARIANZA, "MATCH (p:Player)-[:CURRENT_TEAM]->(t:Team) RETURN p.name, t.name")
    assert _rating_not_current("giocatori con overall tra il 2012 e il 2016 sotto 80", VARIANZA_OK) is None


CRESCITA = "Trovare giocatori in premier league (età tra 24 e 27 anni nel 2016) che hanno visto crescere il loro overall_rating di almeno 5 punti tra la stagione 2014/15 e la 2015/16"
CRESCITA_OK = (
    "MATCH (p:Player)-[st:SEASON_TEAM {season: '2015/2016', main: true}]->(t:Team), (p)-[r1:RATED {season: '2014/2015'}]->(:Season), "
    "(p)-[r2:RATED {season: '2015/2016'}]->(:Season) WHERE st.league = 'England Premier League' AND p.active AND p.age >= 24 AND p.age <= 27 "
    "AND r2.overall - r1.overall >= 5 RETURN p.name, r2.overall - r1.overall AS crescita, p.age, r1.overall AS overall_2014_2015, "
    "r2.overall AS overall_2015_2016, t.name ORDER BY crescita DESC, p.name ASC"
)


def test_la_crescita_e_una_colonna_ed_e_il_criterio_di_ordinamento():
    from app.rag import GrowthNotRanked, _growth_not_ranked
    # Osservato: dieci giocatori giusti, ordinati per overall 2015/16 e senza la crescita: Mahrez (+11) terzo.
    osservato = ("MATCH (p:Player)-[st:SEASON_TEAM {season: '2015/2016', main: true}]->(t:Team) WHERE st.league = 'England Premier League' "
                 "AND p.active AND p.age >= 24 AND p.age <= 27 MATCH (p)-[r1:RATED {season: '2014/2015'}]->(:Season), (p)-[r2:RATED {season: '2015/2016'}]->(:Season) "
                 "WITH p, t, r1, r2, p.age AS age WHERE r2.overall - r1.overall >= 5 RETURN p.name, age, r1.overall AS overall_2014_2015, "
                 "r2.overall AS overall_2015_2016, t.name ORDER BY overall_2015_2016 DESC, p.name ASC")
    assert _growth_not_ranked(CRESCITA, osservato) == "r2.overall - r1.overall"
    raises_violation(GrowthNotRanked, osservato, CRESCITA)
    assert _growth_not_ranked(CRESCITA, CRESCITA_OK) is None
    assert _extract_cypher(CRESCITA_OK, CRESCITA).startswith("MATCH")
    # Colonna presente ma ordine sbagliato: ancora respinta. Domanda senza crescita: nessun vincolo.
    non_ordinata = CRESCITA_OK.replace("ORDER BY crescita DESC, p.name ASC", "ORDER BY overall_2015_2016 DESC")
    assert _growth_not_ranked(CRESCITA, non_ordinata) == "r2.overall - r1.overall"
    assert _growth_not_ranked("giocatori con overall > 80", non_ordinata) is None


def test_la_crescita_con_parentesi_o_via_alias_e_riconosciuta():
    from app.rag import _growth_not_ranked, LeagueOnEarlierSeason, _league_on_earlier_season
    # Respinta a torto: `(r2.overall - r1.overall) AS crescita`, e il filtro attraverso l'alias.
    parentesi = CRESCITA_OK.replace("r2.overall - r1.overall AS crescita", "(r2.overall - r1.overall) AS crescita")
    assert _growth_not_ranked(CRESCITA, parentesi) is None
    via_alias = ("MATCH (p:Player)-[st:SEASON_TEAM {season: '2015/2016', main: true}]->(t:Team), (p)-[r1:RATED {season: '2014/2015'}]->(:Season), "
                 "(p)-[r2:RATED {season: '2015/2016'}]->(:Season) WHERE st.league = 'England Premier League' AND p.active AND p.age >= 24 AND p.age <= 27 "
                 "WITH p, t, r1, r2, (r2.overall - r1.overall) AS crescita WHERE crescita >= 5 "
                 "RETURN p.name, p.age, r1.overall AS overall_2014_2015, r2.overall AS overall_2015_2016, crescita, t.name ORDER BY crescita DESC, p.name ASC")
    assert _growth_not_ranked(CRESCITA, via_alias) is None
    assert _growth_not_ranked(CRESCITA, via_alias.replace("ORDER BY crescita DESC, p.name ASC", "ORDER BY overall_2015_2016 DESC")) == "r2.overall - r1.overall"
    # La Premier letta sul 2014/15 con il 2015/16 in gioco: fuori chi e' arrivato nel 2015.
    precedente = ("MATCH (p:Player)-[st1:SEASON_TEAM {season: '2014/2015', main: true}]->(t:Team), (p)-[r1:RATED {season: '2014/2015'}]->(:Season), "
                  "(p)-[r2:RATED {season: '2015/2016'}]->(:Season) WHERE p.active AND st1.league = 'England Premier League' AND r2.overall - r1.overall >= 5 "
                  "RETURN p.name, r2.overall - r1.overall AS crescita, t.name ORDER BY crescita DESC")
    assert _league_on_earlier_season(precedente) == "st1 (SEASON_TEAM 2014/2015)"
    assert _league_on_earlier_season(CRESCITA_OK) is None
    assert _league_on_earlier_season(FLAT) is None
    raises_violation(LeagueOnEarlierSeason, precedente, CRESCITA)


def test_il_campionato_restituito_ma_non_filtrato_non_basta():
    proiettato = ("MATCH (p:Player)-[st2:SEASON_TEAM {season: '2015/2016', main: true}]->(t2:Team), (p)-[r1:RATED {season: '2014/2015'}]->(:Season), "
                  "(p)-[r2:RATED {season: '2015/2016'}]->(:Season) WHERE p.active AND p.age >= 24 AND p.age <= 27 AND r2.overall - r1.overall >= 5 "
                  "RETURN p.name, r2.overall - r1.overall AS crescita, p.age, st2.league AS league, t2.name ORDER BY crescita DESC")
    assert "league" in _missing_requirement(CRESCITA, proiettato)
    assert _missing_requirement(CRESCITA, proiettato.replace("WHERE p.active", "WHERE st2.league = 'England Premier League' AND p.active")) is None
    assert _missing_requirement(CRESCITA, proiettato.replace("{season: '2015/2016', main: true}", "{season: '2015/2016', main: true, league: 'England Premier League'}")) is None


def test_una_relazione_legata_e_mai_usata_e_un_filtro_silenzioso():
    from app.rag import UnusedBinding, _unused_binding
    # Osservato: st1 del 2014/15 mai letto, e i quattro arrivati dalla Championship spariti (6 invece di 10).
    residuo = ("MATCH (p:Player)-[st1:SEASON_TEAM {season: '2014/2015', main: true}]->(t:Team), (p)-[st2:SEASON_TEAM {season: '2015/2016', main: true}]->(t2:Team), "
               "(p)-[r1:RATED {season: '2014/2015'}]->(:Season), (p)-[r2:RATED {season: '2015/2016'}]->(:Season) "
               "WHERE p.active AND st2.league = 'England Premier League' AND p.age >= 24 AND p.age <= 27 AND r2.overall - r1.overall >= 5 "
               "RETURN p.name, r2.overall - r1.overall AS crescita, p.age, r1.overall AS overall_2014_2015, r2.overall AS overall_2015_2016, t2.name ORDER BY crescita DESC")
    assert _unused_binding(residuo) == "st1:SEASON_TEAM"
    assert _unused_binding(CRESCITA_OK) is None
    assert _unused_binding(FLAT) is None
    assert _unused_binding(DIFENSORI_OK) is None
    raises_violation(UnusedBinding, residuo, CRESCITA)


def test_i_club_di_un_campionato_non_si_scrivono_a_mano():
    from app.rag import HardcodedClubs, _hardcoded_clubs
    domanda = "Miglior marcatore di ogni squadra di Premier League"
    lista = ("MATCH (t:Team)-[:RANKED {season: '2015/2016'}]->(:Season) WHERE t.name IN ['Arsenal', 'Chelsea', 'Everton'] WITH DISTINCT t "
             "MATCH (p:Player)-[:CURRENT_TEAM]->(t) WHERE p.active MATCH (p)-[s:SCORED]->(m:Match) WHERE m.season = '2015/2016' AND m.league = 'England Premier League' "
             "WITH t, p, count(DISTINCT s) AS gol ORDER BY gol DESC WITH t, collect({nome: p.name, gol: gol})[0] AS migliore RETURN t.name, migliore.nome AS marcatore, migliore.gol AS gol")
    assert "3 club names" in _hardcoded_clubs(domanda, lista)
    fascia = lista.replace("[:RANKED {season: '2015/2016'}]->(:Season) WHERE t.name IN ['Arsenal', 'Chelsea', 'Everton']", "[k:RANKED {season: '2015/2016'}]->(:Season) WHERE k.top = true")
    assert "k.top" in _hardcoded_clubs(domanda, fascia)
    assert _hardcoded_clubs("prospetti che non giocano gia' in un club di prima fascia", fascia) is None
    corretta = lista.replace("[:RANKED {season: '2015/2016'}]->(:Season) WHERE t.name IN ['Arsenal', 'Chelsea', 'Everton']", "[k:RANKED {season: '2015/2016', league: 'England Premier League'}]->(:Season)")
    assert _hardcoded_clubs(domanda, corretta) is None
    assert _extract_cypher(corretta, domanda).startswith("MATCH")
    raises_violation(HardcodedClubs, lista, domanda)


# ---------- eventi di gioco ----------

PORTIERI = "portieri con più tiri in porta subiti nel 2015/2016 e gk_reflexes > 80"
PORTIERI_OK = (
    "MATCH (p:Player)-[st:SEASON_TEAM {season: '2015/2016', main: true}]->(t:Team), (p)-[r:RATED {season: '2015/2016'}]->(:Season) "
    "WHERE st.position = 'goalkeeper' AND st.events_covered > 0 AND r.gk_reflexes > 80 "
    "RETURN p.name, st.shots_on_target_faced AS tiri_in_porta_subiti, st.events_covered, r.gk_reflexes, t.name ORDER BY tiri_in_porta_subiti DESC"
)


def test_gli_eventi_di_gioco_sono_disponibili_e_annotati():
    from app.rag import _coverage_note
    for question in ("giocatori con più assist nel 2015/2016", "chi ha preso più cartellini gialli", "falli subiti da Hazard"):
        assert _unavailable_metric(question) is None, question
    assert _unavailable_metric("portieri con più parate") == "le parate"
    assert _extract_cypher(PORTIERI_OK, PORTIERI).startswith("MATCH")
    assert "8.466" in _coverage_note(PORTIERI_OK)
    assert _coverage_note(DIFENSORI_OK) is None
    assert _unknown_property("MATCH (p:Player)-[s:SHOT]->(m:Match) WHERE s.on_target RETURN p.name, count(s)") is None
    assert _unknown_property("MATCH (p:Player)-[st:SEASON_TEAM]->(t:Team) RETURN p.name, st.saves") == ("st", "saves", "SEASON_TEAM")


def test_gli_eventi_seguono_la_stagione_corrente_e_i_conteggi_di_lega():
    assist = "MATCH (p:Player)-[a:ASSISTED]->(m:Match) WITH p, count(a) AS assist MATCH (p)-[:CURRENT_TEAM]->(t:Team) RETURN p.name, assist, t.name ORDER BY assist DESC"
    assert "ASSISTED" in _rating_not_current("giocatori con più assist", assist)
    assert _rating_not_current("giocatori con più assist", assist.replace("WITH p,", "WHERE a.season = '2015/2016' WITH p,")) is None
    assert _rating_not_current("giocatori con più assist in carriera", assist) is None
    # "falli in Premier League" giustifica il conteggio sulle partite del campionato.
    falli = "MATCH (p:Player)-[f:FOUL]->(m:Match) WHERE m.league = 'England Premier League' AND m.season = '2015/2016' WITH p, count(f) AS falli RETURN p.name, falli"
    assert _league_membership_via_matches("chi ha commesso più falli in Premier League", falli) is None
    assert _thresholds_without_ranking_metric("portieri con più tiri subiti, gk_reflexes > 78 e gk_positioning > 78", PORTIERI_OK) is None


def test_tiri_subiti_coppie_e_limiti_superiori():
    from app.rag import GoalkeeperOwnShots, UpperBoundOnJoinedCount, _goalkeeper_own_shots, _upper_bound_on_joined_count
    domanda = "portieri che hanno subito il maggior numero di tiri verso la porta nelle stagioni 14/15 e 15/16"
    propri = "MATCH (p:Player)-[st:SEASON_TEAM {season: '2015/2016', main: true}]->(t:Team) WHERE st.position = 'goalkeeper' MATCH (p)-[s:SHOT {on_target: true}]->(m:Match) RETURN p.name, count(s) AS tiri, t.name"
    assert _goalkeeper_own_shots(domanda, propri)
    assert not _goalkeeper_own_shots(domanda, PORTIERI_OK)
    raises_violation(GoalkeeperOwnShots, propri, domanda)
    # Coppie: due nomi per riga, Suarez in piu' coppie non e' un doppione.
    coppie = "MATCH (a:Player)-[c:ASSISTED_GOALS_OF]->(s:Player) MATCH (a)-[:CURRENT_TEAM]->(t:Team) RETURN a.name AS assistman, s.name AS marcatore, c.goals AS gol, t.name ORDER BY gol DESC"
    rows = [{"assistman": "Daniel Alves", "marcatore": "Lionel Messi", "gol": 21, "t.name": "Juventus"},
            {"assistman": "Pedro Rodriguez", "marcatore": "Lionel Messi", "gol": 16, "t.name": "Chelsea"}]
    assert _duplicated_players(coppie, rows) == []
    # "meno di 5 cartellini" su un conteggio di relazioni: chi ha zero sparisce.
    gialli = ("MATCH (p:Player)-[st:SEASON_TEAM {season: '2015/2016', main: true}]->(t:Team) WHERE p.role = 'defender' "
              "MATCH (p)-[c:CARD]->(m:Match) WHERE m.season = '2015/2016' AND c.card_type = 'y' WITH p, t, count(DISTINCT c) AS gialli WHERE gialli < 5 "
              "RETURN p.name, gialli, t.name ORDER BY gialli DESC")
    question = "difensori con meno di 5 cartellini gialli"
    assert _upper_bound_on_joined_count(question, gialli) == "gialli"
    assert _upper_bound_on_joined_count(question, gialli.replace("MATCH (p)-[c:CARD]", "OPTIONAL MATCH (p)-[c:CARD]")) is None
    piatto = "MATCH (p:Player)-[st:SEASON_TEAM {season: '2015/2016', main: true}]->(t:Team) WHERE p.role = 'defender' AND st.yellow_cards < 5 RETURN p.name, st.yellow_cards, t.name"
    assert _upper_bound_on_joined_count(question, piatto) is None
    raises_violation(UpperBoundOnJoinedCount, gialli, question)


def test_eventi_con_stagione_club_dell_ultima_stagione_e_coppie_in_elenco():
    from app.rag import EventsWithoutSeason, _events_without_season, _league_on_earlier_season, _rebuild_list
    # Osservato: "piu' assist nel 2015/2016" -> Messi 89, gli assist di tutta la carriera.
    carriera = "MATCH (p:Player)-[st:SEASON_TEAM {season: '2015/2016', main: true}]->(t:Team) MATCH (p)-[a:ASSISTED]->(m:Match) WITH p, count(DISTINCT a) AS assists, st.events_covered AS events_covered, t WHERE events_covered > 0 RETURN p.name, assists, events_covered, t.name ORDER BY assists DESC LIMIT 1"
    question = "chi ha fornito più assist nel 2015/2016?"
    assert _events_without_season(question, carriera) == "a:ASSISTED"
    raises_violation(EventsWithoutSeason, carriera, question)
    for fixed in (carriera.replace("(p)-[a:ASSISTED]->(m:Match)", "(p)-[a:ASSISTED]->(m:Match) WHERE m.season = '2015/2016'"),
                  carriera.replace("[a:ASSISTED]", "[a:ASSISTED {season: '2015/2016'}]"),
                  carriera.replace("(p)-[a:ASSISTED]->(m:Match)", "(p)-[a:ASSISTED]->(m:Match) WHERE a.season = '2015/2016'")):
        assert _events_without_season(question, fixed) is None
    assert _events_without_season("chi ha fornito più assist in carriera", carriera) is None
    # Il club mostrato deve essere quello dell'ultima stagione in gioco.
    due = ("MATCH (p:Player)-[st:SEASON_TEAM {season: '2014/2015', main: true}]->(t:Team), (p)-[st2:SEASON_TEAM {season: '2015/2016', main: true}]->(t2:Team) "
           "WHERE st.position = 'goalkeeper' RETURN p.name, st.shots_faced + st2.shots_faced AS tiri_subiti, t.name ORDER BY tiri_subiti DESC")
    assert "t.name" in _league_on_earlier_season(due)
    assert _league_on_earlier_season(due.replace("t.name ORDER", "t2.name ORDER")) is None
    # Coppie nell'elenco: entrambi i nomi.
    coppie = "MATCH (a:Player)-[c:ASSISTED_GOALS_OF]->(s:Player) MATCH (a)-[:CURRENT_TEAM]->(t:Team) WITH a, s, c.goals AS gol, t RETURN a.name AS assistman, s.name AS marcatore, gol, t.name ORDER BY gol DESC"
    rows = [{"assistman": "Daniel Alves", "marcatore": "Lionel Messi", "gol": 21, "t.name": "Juventus"},
            {"assistman": "Pedro Rodriguez", "marcatore": "Lionel Messi", "gol": 16, "t.name": "Chelsea"}]
    assert _rebuild_list("2 coppie.\n1. x, 1\n2. y, 2", coppie, rows) == "2 coppie.\n\n1. Daniel Alves → Lionel Messi (Juventus), 21\n2. Pedro Rodriguez → Lionel Messi (Chelsea), 16"


def test_un_campionato_non_chiesto_e_un_filtro_inventato():
    from app.rag import LeagueNotAsked, _league_not_asked, _ordering_column
    domanda = "portieri che hanno subito più tiri nelle stagioni 14/15 e 15/16 con gk_reflexes > 78"
    inventato = PORTIERI_OK.replace("st.position = 'goalkeeper'", "st.position = 'goalkeeper' AND st.league = 'England Premier League'")
    assert _league_not_asked(domanda, inventato) == "England Premier League"
    assert _league_not_asked(domanda, PORTIERI_OK) is None
    assert _league_not_asked("portieri di Premier League con più tiri subiti", inventato) is None
    assert _league_not_asked("gol totali per campionato 2015/2016", "MATCH (m:Match) WHERE m.league = 'Italy Serie A' RETURN count(m)") is None
    assert _league_not_asked(DIFENSORI_ITA, DIFENSORI_OK) is None
    raises_violation(LeagueNotAsked, inventato, domanda)
    # L'ORDER BY nel WITH prima del RETURN ordina l'elenco.
    coppie = "MATCH (a:Player)-[c:ASSISTED_GOALS_OF]->(s:Player) WITH a, s, c.goals AS gol MATCH (a)-[:CURRENT_TEAM]->(t:Team) WITH a, s, gol, t ORDER BY gol DESC RETURN a.name AS assistman, s.name AS marcatore, gol, t.name LIMIT 10"
    assert _ordering_column(coppie, [{"assistman": "A", "marcatore": "B", "gol": 21, "t.name": "C"}]) == "gol"


def test_la_crescita_aggregata_e_la_stessa_differenza():
    from app.rag import _growth_not_ranked, _rebuild_list
    prospetti = ("MATCH (p:Player)-[r:PLAYS_FOR]->(:Team) WHERE p.active AND p.age < 23 AND r.overall >= 75 AND r.potential - r.overall >= 10 "
                 "WITH p, max(r.overall) AS overall, max(r.potential) AS potential, (max(r.potential) - max(r.overall)) AS crescita "
                 "MATCH (p)-[:CURRENT_TEAM]->(t:Team) RETURN p.name, crescita, p.age, overall, potential, t.name ORDER BY crescita DESC")
    assert _growth_not_ranked("prospetti under 23 con overall almeno 75 e almeno 10 punti di margine di crescita", prospetti) is None
    assert _growth_not_ranked("prospetti con 10 punti di margine", prospetti.replace("ORDER BY crescita DESC", "ORDER BY overall DESC")) == "r.potential - r.overall"
    # Righe senza colonna-nome (il marcatore per squadra e' dentro un collect): nessuna ricostruzione, nessun errore.
    squadre = "MATCH (t:Team)-[:RANKED {season: '2015/2016', league: 'England Premier League'}]->(:Season) MATCH (p:Player)-[:CURRENT_TEAM]->(t) WITH t, collect({nome: p.name})[0] AS migliore RETURN t.name, migliore.nome AS marcatore, 1 AS gol ORDER BY gol DESC"
    rows = [{"t.name": "Tottenham", "marcatore": "Harry Kane", "gol": 25}, {"t.name": "Leicester", "marcatore": "Jamie Vardy", "gol": 24}]
    assert _rebuild_list("20 squadre.\n1. Kane (Tottenham), 25", squadre, rows) == "20 squadre.\n1. Kane (Tottenham), 25"


def test_il_superlativo_singolare_vuole_limit_1():
    from app.rag import SuperlativeWithoutLimit, _superlative_without_limit
    tutti = "MATCH (p:Player)-[a:ASSISTED]->(m:Match) WHERE m.season = '2015/2016' WITH p, count(a) AS assist MATCH (p)-[:CURRENT_TEAM]->(t:Team) RETURN p.name, assist, t.name ORDER BY assist DESC"
    for q in ("il giocatore con più assist nel 2015/2016", "chi ha fornito più assist nella stagione 2015/2016?", "il capocannoniere della Serie A 2010/2011"):
        assert _superlative_without_limit(q, tutti), q
        assert not _superlative_without_limit(q, tutti + " LIMIT 1")
    for q in ("i giocatori con più assist nel 2015/2016", "top 10 rigoristi", "quali giocatori hanno più assist", "il miglior marcatore di ogni squadra di Premier League"):
        assert not _superlative_without_limit(q, tutti), q
    raises_violation(SuperlativeWithoutLimit, tutti, "il giocatore con più assist nel 2015/2016")


def test_una_classifica_senza_numero_e_top_10():
    from app.rag import RankingWithoutLimit, _ranking_without_limit
    coppie = "MATCH (a:Player)-[c:ASSISTED_GOALS_OF]->(s:Player) MATCH (a)-[:CURRENT_TEAM]->(t:Team) WHERE a.active RETURN a.name AS assistman, s.name AS marcatore, c.goals AS gol, t.name ORDER BY gol DESC"
    assert _ranking_without_limit("quali coppie giocatore-assistman hanno prodotto più gol insieme?", coppie)
    assert not _ranking_without_limit("quali coppie giocatore-assistman hanno prodotto più gol insieme?", coppie + " LIMIT 10")
    assert not _ranking_without_limit("top 10 coppie con più gol", coppie)
    assert not _ranking_without_limit("difensori di Premier League con più falli commessi e meno di 5 cartellini gialli", coppie)  # soglia: e' un filtro
    assert not _ranking_without_limit("Miglior marcatore di ogni squadra di Premier League", coppie)
    raises_violation(RankingWithoutLimit, coppie, "quali coppie giocatore-assistman hanno prodotto più gol insieme?")


def test_difetti_emersi_dal_benchmark():
    from app.rag import AverageNotComputed, _average_not_computed, _growth_not_ranked
    # "partite giocate in casa" non sono presenze.
    casa = "MATCH (t:Team)-[:HOME_TEAM]->(m:Match) WHERE m.season = '2015/2016' WITH t, sum(m.home_goals) AS gol WHERE gol > 50 RETURN t.name, gol"
    assert _missing_requirement("Nella stagione 2015/2016, quali squadre hanno segnato piu' di 50 gol nelle partite giocate in casa?", casa) is None
    # "top 10 autogol" senza stagione e' il 2015/2016 anche per OWN_GOAL.
    autogol = "MATCH (p:Player)-[o:OWN_GOAL]->(m:Match) WITH p, count(o) AS autogol MATCH (p)-[:CURRENT_TEAM]->(t:Team) RETURN p.name, autogol, t.name ORDER BY autogol DESC LIMIT 10"
    assert "OWN_GOAL" in _rating_not_current("top 10 autogol", autogol)
    # "presenze medie annue" senza media.
    sola = VARIANZA_OK.replace("count(DISTINCT m) / 4.0 AS presenze_medie", "count(DISTINCT m) AS presenze_medie")
    assert _average_not_computed(VARIANZA, sola)
    assert not _average_not_computed(VARIANZA, VARIANZA_OK)
    raises_violation(AverageNotComputed, sola, VARIANZA)
    # "senza cali drastici" e' un vincolo (<= 10), non la metrica della classifica.
    cali = ("MATCH (p:Player)-[r:RATED]->(:Season) WITH p, max(r.stamina) - min(r.stamina) AS calo_stamina, count(r) AS stagioni "
            "WHERE calo_stamina <= 10 MATCH (p)-[:CURRENT_TEAM]->(t:Team) RETURN p.name, stagioni, calo_stamina, t.name ORDER BY stagioni DESC, calo_stamina ASC")
    assert _growth_not_ranked("almeno 30 partite per 5 stagioni consecutive senza cali drastici in stamina", cali) is None


def test_la_finestra_di_stagioni_consecutive_deve_tornare():
    from app.rag import ConsecutiveWindowMismatch, _consecutive_window_mismatch
    giusta = "WITH p, collect(anno) AS anni WHERE size(anni) >= 5 AND any(i IN range(0, size(anni) - 5) WHERE anni[i + 4] - anni[i] = 4) RETURN p.name"
    sbagliata = "WITH p, collect(stagione) AS anni WHERE size(anni) >= 5 AND any(i IN range(0, size(anni) - 5) WHERE toInteger(left(anni[i], 4)) + 1 = toInteger(left(anni[i + 4], 4))) RETURN p.name"
    assert _consecutive_window_mismatch(giusta) is None
    assert "span of 1" in _consecutive_window_mismatch(sbagliata)
    raises_violation(ConsecutiveWindowMismatch, "MATCH (p:Player) " + sbagliata, "5 stagioni consecutive")


def test_le_stagioni_non_si_inventano():
    from app.rag import SeasonsInvented, _seasons_invented
    inventate = "MATCH (p:Player)-[r:RATED]->(:Season) WHERE r.season IN ['2011/2012', '2012/2013', '2013/2014', '2014/2015', '2015/2016'] RETURN p.name"
    assert "2011/2012" in _seasons_invented("5 stagioni consecutive senza cali drastici", inventate)
    assert _seasons_invented("tra il 2012 e il 2016", inventate) is None
    assert _seasons_invented("5 stagioni consecutive", "MATCH (p:Player)-[r:RATED]->(:Season) WHERE toInteger(left(r.season, 4)) IN anni RETURN p.name") is None
    raises_violation(SeasonsInvented, inventate, "5 stagioni consecutive senza cali drastici")


def test_l_appartenenza_a_una_lista_confronta_tipi_uguali():
    from app.rag import ListTypeMismatch, _list_type_mismatch
    stringhe = ("MATCH (p:Player)-[:APPEARED_IN]->(m:Match) WITH p, m.season AS stagione, count(DISTINCT m) AS presenze WHERE presenze >= 30 "
                "WITH p, collect(stagione) AS anni MATCH (p)-[r:RATED]->(:Season) WHERE toInteger(left(r.season, 4)) IN anni RETURN p.name")
    assert "season strings" in _list_type_mismatch(stringhe)
    assert _list_type_mismatch(stringhe.replace("toInteger(left(r.season, 4)) IN anni", "r.season IN anni")) is None
    interi = stringhe.replace("m.season AS stagione", "toInteger(left(m.season, 4)) AS stagione").replace("toInteger(left(r.season, 4)) IN anni", "r.season IN anni")
    assert "integer years" in _list_type_mismatch(interi)
    raises_violation(ListTypeMismatch, stringhe, "5 stagioni consecutive")


def test_il_ruolo_della_query_e_quello_della_domanda():
    from app.rag import RoleMismatch, _role_mismatch
    attaccanti = "MATCH (p:Player)-[r:PLAYS_FOR]->(:Team) WHERE r.overall >= 85 AND p.preferred_foot = 'left' AND p.role = 'forward' AND p.active WITH p, max(r.overall) AS overall MATCH (p)-[:CURRENT_TEAM]->(t:Team) RETURN p.name, overall, t.name ORDER BY overall DESC"
    domanda = "overall almeno 85, piede sinistro, ruolo esterno: chi sono?"
    assert "forward" in _role_mismatch(domanda, attaccanti)
    assert _role_mismatch(domanda, attaccanti.replace("p.role = 'forward'", "p.position IN ['winger', 'wide_midfielder']")) is None
    assert _role_mismatch(domanda, attaccanti.replace("p.role = 'forward'", "p.position = 'winger'")) is None
    # Un reparto ammette il reparto o le sue posizioni; non un'altra posizione.
    assert _role_mismatch("difensori con overall > 80", attaccanti.replace("p.role = 'forward'", "p.position IN ['centre_back', 'full_back']")) is None
    assert "striker" in _role_mismatch("difensori con overall > 80", attaccanti.replace("p.role = 'forward'", "p.position = 'striker'"))
    assert _role_mismatch("alternativa a Lloris", attaccanti) is None
    raises_violation(RoleMismatch, attaccanti, domanda)


def test_difetti_della_seconda_esecuzione_del_benchmark():
    from app.rag import LimitOneUnasked, UnorderedHead, _limit_one_unasked, _unordered_head
    # LIMIT 1 su "alternativa a Lloris": un elenco ridotto a Buffon.
    lloris = ("MATCH (ref:Player) WHERE toLower(ref.name) CONTAINS 'hugo lloris' WITH ref ORDER BY ref.appearances DESC LIMIT 1 "
              "MATCH (ref)-[rr:PLAYS_FOR]->(:Team) WITH ref.position AS ruolo, max(rr.overall) AS riferimento "
              "MATCH (p:Player)-[r:PLAYS_FOR]->(:Team), (p)-[:CURRENT_TEAM]->(t:Team) WHERE p.active AND p.position = ruolo "
              "WITH p, t, riferimento, max(r.overall) AS overall WHERE overall < riferimento AND overall >= riferimento - 10 "
              "RETURN p.name, overall, t.name ORDER BY overall DESC")
    assert _limit_one_unasked("alternativa economica a Hugo Lloris", lloris + " LIMIT 1")
    assert not _limit_one_unasked("alternativa economica a Hugo Lloris", lloris)  # il LIMIT 1 del riferimento non conta
    assert not _limit_one_unasked("chi ha segnato di piu' nel 2015/2016?", lloris + " LIMIT 1")
    raises_violation(LimitOneUnasked, lloris + " LIMIT 1", "alternativa economica a Hugo Lloris")
    # "overall aumentato" senza .overall nel Cypher: erano i gol.
    gol = ("MATCH (p:Player)-[st1:SEASON_TEAM {season: '2014/2015', main: true}]->(t1:Team), (p)-[st2:SEASON_TEAM {season: '2015/2016', main: true}]->(t2:Team) "
           "WHERE p.active AND p.age >= 24 AND p.age <= 27 AND st2.league = 'England Premier League' WITH p, t2, (st2.goals - st1.goals) AS crescita "
           "WHERE crescita >= 5 RETURN p.name, crescita, p.age, t2.name ORDER BY crescita DESC")
    assert "overall" in _missing_requirement("giocatori di Premier League tra i 24 e i 27 anni il cui overall e' aumentato di almeno 5 punti dal 2014/2015 al 2015/2016", gol)
    assert _missing_requirement(CRESCITA, CRESCITA_OK) is None
    assert "stamina" in _missing_requirement("centrocampisti con stamina > 88", "MATCH (p:Player)-[r:RATED {season: '2015/2016'}]->(:Season) WHERE r.strength > 88 RETURN p.name, r.strength")
    # collect(...)[0] senza ORDER BY prima: Almen Abdi miglior marcatore del Watford.
    disordinato = ("MATCH (t:Team)-[k:RANKED {season: '2015/2016', league: 'England Premier League'}]->(:Season) WITH t "
                   "MATCH (p:Player)-[s:SCORED]->(m:Match) WHERE m.season = '2015/2016' AND p.active MATCH (p)-[st:SEASON_TEAM {season: '2015/2016', main: true}]->(t) "
                   "WITH t, p, count(DISTINCT s) AS gol WITH t, collect({nome: p.name, gol: gol}) AS marcatori "
                   "RETURN t.name AS squadra, marcatori[0].nome AS marcatore, marcatori[0].gol AS gol ORDER BY t.name")
    assert _unordered_head(disordinato)
    ordinato = disordinato.replace("WITH t, p, count(DISTINCT s) AS gol WITH t, collect", "WITH t, p, count(DISTINCT s) AS gol ORDER BY gol DESC WITH t, collect")
    assert not _unordered_head(ordinato)
    raises_violation(UnorderedHead, disordinato, "Miglior marcatore di ogni squadra di Premier League")
