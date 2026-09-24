import math
import sqlite3
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from app.events import DETAILED_COLUMNS, parse_assists, parse_events, parse_possession, season_event_counts
from app.graph import GraphStore


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"


def rows(connection: sqlite3.Connection, query: str):
    connection.row_factory = sqlite3.Row
    return [dict(row) for row in connection.execute(query)]


def goal_events(connection: sqlite3.Connection) -> tuple[list[dict], list[dict]]:
    """Estrae i gol dall'XML annidato della colonna `Match.goal`.

    Controprova sui dati: i tipi n (azione), p (rigore) e o (autogol) sommano
    38.612 gol contro i 38.620 dichiarati da home_team_goal + away_team_goal,
    mentre includendo npm e dg si arriva a 39.928. Sono quindi quei tre i gol
    reali; npm e dg sono tiri non finalizzati e vanno esclusi.

    L'autogol viene registrato a parte: conta per il punteggio della squadra
    avversaria, ma attribuirlo al marcatore come produzione offensiva
    falserebbe qualunque valutazione di scouting.
    """
    scored: list[dict] = []
    own: list[dict] = []
    query = "SELECT match_api_id, goal FROM Match WHERE goal IS NOT NULL AND length(goal) > 40"
    for match_id, document in connection.execute(query):
        try:
            root = ET.fromstring(document)
        except ET.ParseError:
            continue
        for value in root.findall("value"):
            if value.findtext("type") != "goal":
                continue
            kind = value.findtext("goal_type")
            scorer = value.findtext("player1")
            event_id = value.findtext("id")
            if kind not in ("n", "p", "o") or not scorer or not event_id:
                continue
            team = value.findtext("team")
            event = {
                "event_id": int(event_id),
                "match_id": match_id,
                "player_id": int(scorer),
                "team_id": int(team) if team else None,
                "minute": int(value.findtext("elapsed") or 0),
                "subtype": value.findtext("subtype") or "",
                "penalty": kind == "p",
            }
            (own if kind == "o" else scored).append(event)
    return scored, own


# La stagione piu recente del dataset: definisce chi e ancora in attivita.
LAST_SEASON = "2015/2016"
# Le stagioni coperte dalle partite: le valutazioni fuori da questo arco sono inutilizzabili.
SEASONS = [f"{y}/{y + 1}" for y in range(2008, 2016)]
# Eta calcolata a fine dataset, non a oggi: il dato si ferma li.
REFERENCE_DATE = date(2016, 7, 1)


def _role_from_grid(x: int, y: int) -> tuple[str, str]:
    """Traduce la posizione in formazione in un ruolo leggibile.

    Le colonne home_player_X/Y del Match sono le coordinate dello schieramento:
    Y e la linea di campo (1 portiere, 2-4 difesa, 5-8 centrocampo, 9-11
    attacco), X la posizione orizzontale su una scala 1-9 con il centro a 4-6.
    Controprova su ruoli noti: Lloris e Neuer sempre (1,1), Chiellini (6,3),
    Pirlo (5,7), Lukaku e Giroud (5,11), Robben (3,8).
    """
    central = 4 <= x <= 6
    if y <= 1:
        return "goalkeeper", "goalkeeper"
    if y <= 4:
        return "defender", "centre_back" if central else "full_back"
    if y <= 8:
        return "midfielder", "central_midfielder" if central else "wide_midfielder"
    return "forward", "striker" if central else "winger"


def squad_profile(connection: sqlite3.Connection) -> tuple[list[dict], list[dict]]:
    """Ricava ruolo, squadra attuale e stato di attivita dalle formazioni.

    La relazione PLAYS_FOR collega un giocatore a ogni squadra per cui e sceso
    in campo, senza distinguere quella attuale da quelle passate: per lo
    scouting serve sapere dove gioca oggi e se gioca ancora. Entrambe le cose
    si ricavano dall'ultima partita in cui compare in formazione.
    """
    grid: dict[int, Counter] = defaultdict(Counter)
    appearances: Counter = Counter()
    latest: dict[int, tuple[str, int, str]] = {}

    for side in ("home", "away"):
        for slot in range(1, 12):
            query = (
                f"SELECT {side}_player_{slot}, {side}_player_X{slot}, {side}_player_Y{slot}, "
                f"date, season, {side}_team_api_id FROM Match "
                f"WHERE {side}_player_{slot} IS NOT NULL"
            )
            for player_id, x, y, played_on, season, team_id in connection.execute(query):
                appearances[player_id] += 1
                if x is not None and y is not None and y > 0:
                    grid[player_id][(x, y)] += 1
                if team_id is not None and (player_id not in latest or played_on > latest[player_id][0]):
                    latest[player_id] = (played_on, team_id, season)

    birthdays = dict(connection.execute("SELECT player_api_id, birthday FROM Player"))

    players: list[dict] = []
    current: list[dict] = []
    for player_id, total in appearances.items():
        role = position = None
        if grid[player_id]:
            (x, y), _ = grid[player_id].most_common(1)[0]
            role, position = _role_from_grid(x, y)

        played_on, team_id, season = latest.get(player_id, (None, None, None))
        age = None
        birthday = birthdays.get(player_id)
        if birthday:
            born = date.fromisoformat(birthday[:10])
            age = REFERENCE_DATE.year - born.year - (
                (REFERENCE_DATE.month, REFERENCE_DATE.day) < (born.month, born.day)
            )

        players.append({
            "id": player_id,
            "role": role,
            "position": position,
            "age": age,
            "appearances": total,
            "last_season": season,
            "active": season == LAST_SEASON,
        })
        if team_id is not None:
            current.append({"player_id": player_id, "team_id": team_id})

    return players, current


def lineup_appearances(connection: sqlite3.Connection) -> list[dict]:
    """Ogni presenza in formazione come coppia giocatore-partita.

    Il totale di carriera in `p.appearances` non e scomponibile: non permette di
    chiedere quante presenze un giocatore abbia in un singolo campionato o in una
    stagione, perche somma tutti gli 11 campionati del dataset. La relazione
    esplicita rende invece interrogabile ogni sottoinsieme, e collega il
    giocatore alle partite che ha giocato e non solo a quelle in cui ha segnato.
    """
    rows: list[dict] = []
    for side in ("home", "away"):
        for slot in range(1, 12):
            query = (
                f"SELECT {side}_player_{slot}, match_api_id, "
                f"{side}_player_X{slot}, {side}_player_Y{slot}, {side}_team_api_id FROM Match "
                f"WHERE {side}_player_{slot} IS NOT NULL AND match_api_id IS NOT NULL"
            )
            for player_id, match_id, x, y, team_id in connection.execute(query):
                # La posizione della singola partita, non quella prevalente del
                # giocatore: e' cio' che rende misurabile la sua versatilita.
                position = None
                if x is not None and y is not None and y > 0:
                    position = _role_from_grid(x, y)[1]
                rows.append({
                    "player_id": player_id,
                    "match_id": match_id,
                    "position": position,
                    # La squadra per cui e' sceso in campo: senza, da una partita
                    # si risale a due club e il suo resta indistinguibile.
                    "team_id": team_id,
                })
    return rows


def season_teams(appearances: list[dict], seasons_by_match: dict[int, str],
                 leagues_by_match: dict[int, str]) -> list[dict]:
    """La squadra (e il ruolo) di ogni giocatore in ogni stagione.

    CURRENT_TEAM risponde a "dove gioca oggi", PLAYS_FOR elenca tutti i club
    della carriera senza dire quando: nessuna delle due dice per chi giocava
    nella stagione 2010/2011. Una domanda che nomina una stagione riceveva
    quindi il club attuale spacciato per quello dell'epoca: il capocannoniere
    della Serie A 2008/2009, Ibrahimovic all'Inter, risultava "del Paris
    Saint-Germain". La relazione per stagione si ricava dalle presenze e porta
    con se' il numero di partite e il ruolo prevalente in quell'annata. Un
    giocatore trasferito a gennaio ne ha due per la stessa stagione, ciascuna
    con le proprie presenze; quella con piu' presenze porta `main = true`, cosi'
    "il club di quella stagione" e' un filtro e non un'aggregazione da
    ricostruire ogni volta: Diamanti 2009/2010 e' West Ham (18 presenze), non
    Livorno (1), e nessuna query deve ordinare e collassare per saperlo.
    """
    count: Counter = Counter()
    grid: dict[tuple[int, str, int], Counter] = defaultdict(Counter)
    league_of: dict[tuple[int, str, int], str] = {}
    league_total: Counter = Counter()
    for row in appearances:
        season = seasons_by_match.get(row["match_id"])
        if season is None or row["team_id"] is None:
            continue
        key = (row["player_id"], season, row["team_id"])
        count[key] += 1
        league = leagues_by_match.get(row["match_id"])
        if league:
            # Il campionato del club in quella stagione, e le presenze del
            # giocatore in QUEL campionato: "30 presenze in Premier nel 2014/15"
            # e' cosi' un filtro piatto, come season_total, invece di un
            # conteggio su APPEARED_IN filtrato per lega e stagione.
            league_of[key] = league
            league_total[(row["player_id"], season, league)] += 1
        if row["position"]:
            grid[key][row["position"]] += 1
    best: dict[tuple[int, str], tuple[int, int]] = {}
    season_total: Counter = Counter()
    for (player_id, season, team_id), total in count.items():
        season_total[(player_id, season)] += total
        if (player_id, season) not in best or total > best[(player_id, season)][0]:
            best[(player_id, season)] = (total, team_id)
    return [
        {
            "player_id": player_id,
            "season": season,
            "team_id": team_id,
            "appearances": total,
            "position": grid[key].most_common(1)[0][0] if grid[key] else None,
            "main": best[(player_id, season)][1] == team_id,
            # Presenze dell'intera stagione, tutti i club: "almeno 30 presenze nel
            # 2014/2015" e' un filtro su una proprieta', non un conteggio da
            # raggruppare per stagione e trasportare tra WITH.
            "season_total": season_total[(player_id, season)],
            "league": league_of.get(key),
            "league_total": league_total[(player_id, season, league_of.get(key))] if key in league_of else None,
        }
        for key, total in count.items()
        for player_id, season, team_id in [key]
    ]


# I 34 attributi tecnici e fisici di Player_Attributes, oltre a overall e potential.
SKILL_COLUMNS = [
    "crossing", "finishing", "heading_accuracy", "short_passing", "volleys", "dribbling",
    "curve", "free_kick_accuracy", "long_passing", "ball_control", "acceleration",
    "sprint_speed", "agility", "reactions", "balance", "shot_power", "jumping", "stamina",
    "strength", "long_shots", "aggression", "interceptions", "positioning", "vision",
    "penalties", "marking", "standing_tackle", "sliding_tackle", "gk_diving", "gk_handling",
    "gk_kicking", "gk_positioning", "gk_reflexes",
]


def _season_of(iso_date: str) -> str:
    """La stagione calcistica a cui appartiene una data (spartiacque a luglio)."""
    year, month = int(iso_date[:4]), int(iso_date[5:7])
    start = year if month >= 7 else year - 1
    return f"{start}/{start + 1}"


def all_assessments(connection: sqlite3.Connection) -> list[dict]:
    """Ogni rilevazione di Player_Attributes, non solo l'ultima della stagione.

    RATED condensa la serie in un valore per stagione ed e' la vista giusta per
    quasi ogni domanda. La serie intera - 183.978 rilevazioni, una ogni poche
    settimane - serve a due cose: alle domande sull'andamento DENTRO una
    stagione, e a misurare come le traversate temporali scalano quando gli
    archi da attraversare triplicano. Le rilevazioni vuote sono escluse come
    per RATED.
    """
    columns = ", ".join(f"a.{name}" for name in SKILL_COLUMNS)
    query = (
        f"SELECT a.player_api_id, a.date, a.overall_rating, a.potential, "
        f"a.attacking_work_rate, a.defensive_work_rate, {columns} "
        f"FROM Player_Attributes a WHERE a.date IS NOT NULL AND a.overall_rating IS NOT NULL"
    )
    rows: list[dict] = []
    for row in connection.execute(query):
        player_id, date, overall, potential, attack_rate, defence_rate = row[:6]
        season = _season_of(date)
        if not (SEASONS[0] <= season <= SEASONS[-1]):
            continue
        record = {
            "player_id": player_id,
            "season": season,
            "date": date,
            "overall": overall,
            "potential": potential,
            "attacking_work_rate": attack_rate,
            "defensive_work_rate": defence_rate,
        }
        record.update(dict(zip(SKILL_COLUMNS, row[6:])))
        rows.append(record)
    return rows


def standings(connection: sqlite3.Connection) -> list[dict]:
    """La classifica di ogni campionato in ogni stagione, calcolata dai risultati.

    Il dataset non ha classifiche, ma ha tutte le partite con il punteggio: 3
    punti a vittoria, 1 a pareggio, ordinamento per punti, differenza reti e
    gol fatti. Serve a dare un significato verificabile a "club di prima
    fascia": senza, il modello confrontava il nome della squadra con quello
    del campionato - un filtro che non esclude nessuno - e presentava 479
    giocatori, Dele Alli al Tottenham compreso, come "non di prima fascia".
    """
    table: dict[tuple[str, str, int], dict] = {}
    query = (
        "SELECT m.season, l.name, m.home_team_api_id, m.away_team_api_id, m.home_team_goal, m.away_team_goal "
        "FROM Match m JOIN League l ON l.country_id = m.country_id "
        "WHERE m.home_team_goal IS NOT NULL AND m.away_team_goal IS NOT NULL"
    )
    for season, league, home, away, hg, ag in connection.execute(query):
        for team, scored, conceded in ((home, hg, ag), (away, ag, hg)):
            row = table.setdefault((season, league, team), {
                "season": season, "league": league, "team_id": team,
                "played": 0, "points": 0, "won": 0, "drawn": 0, "lost": 0, "goals_for": 0, "goals_against": 0,
            })
            row["played"] += 1
            row["goals_for"] += scored
            row["goals_against"] += conceded
            if scored > conceded:
                row["points"] += 3; row["won"] += 1
            elif scored == conceded:
                row["points"] += 1; row["drawn"] += 1
            else:
                row["lost"] += 1
    by_group: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in table.values():
        row["goal_difference"] = row["goals_for"] - row["goals_against"]
        by_group[(row["season"], row["league"])].append(row)
    rows: list[dict] = []
    for group in by_group.values():
        group.sort(key=lambda r: (-r["points"], -r["goal_difference"], -r["goals_for"]))
        # "Prima fascia" proporzionale al campionato: le prime 30%, mai meno di 4.
        # Approssima i posti per le coppe europee: 4 su 10 (Svizzera), 4 su 12
        # (Scozia), 5 su 16, 6 su 18 (Bundesliga, Eredivisie), 6 su 20 (Serie A,
        # Premier, Liga, Ligue 1). Un numero fisso sbaglierebbe da una parte o
        # dall'altra.
        top_places = max(4, math.ceil(0.3 * len(group)))
        for rank, row in enumerate(group, start=1):
            row["rank"] = rank
            row["teams"] = len(group)
            row["top_places"] = top_places
            row["top"] = rank <= top_places
            rows.append(row)
    return rows


def season_ratings(connection: sqlite3.Connection) -> list[dict]:
    """Una valutazione per giocatore e per stagione, con tutti gli attributi.

    Player_Attributes contiene una rilevazione ogni poche settimane: 183.978 righe
    che formano una serie storica. Il grafo conservava solo l'ultima, perdendo
    ogni possibilita di rispondere a domande sull'andamento nel tempo - un calo
    di stamina, la crescita di un giovane, la tenuta fisica di un veterano.
    Di ogni stagione si tiene la rilevazione piu recente, cioe lo stato del
    giocatore a fine annata.
    """
    columns = ", ".join(f"a.{name}" for name in SKILL_COLUMNS)
    query = (
        f"SELECT a.player_api_id, a.date, a.overall_rating, a.potential, "
        f"a.attacking_work_rate, a.defensive_work_rate, {columns} "
        f"FROM Player_Attributes a WHERE a.date IS NOT NULL ORDER BY a.date"
    )
    latest: dict[tuple[int, str], dict] = {}
    for row in connection.execute(query):
        player_id, date, overall, potential, attack_rate, defence_rate = row[:6]
        season = _season_of(date)
        if not (SEASONS[0] <= season <= SEASONS[-1]):
            continue
        # Alcune righe di Player_Attributes sono vuote (Djilobodji: sette stagioni
        # di soli null). Tenerle come "ultima valutazione della stagione" mette un
        # overall nullo nel grafo, e Neo4j ordina i null PRIMA in DESC: il
        # miglior difensore del 2015/2016 risultava lui, "con un overall di null".
        if overall is None:
            continue
        record = {
            "player_id": player_id,
            "season": season,
            "date": date,
            "overall": overall,
            "potential": potential,
            "attacking_work_rate": attack_rate,
            "defensive_work_rate": defence_rate,
        }
        record.update(dict(zip(SKILL_COLUMNS, row[6:])))
        # Le righe arrivano ordinate per data: l'ultima sovrascrive le precedenti.
        latest[(player_id, season)] = record
    return list(latest.values())


# Colonna di Team_Attributes -> proprieta' della relazione STYLE (valore numerico e classe).
STYLE_COLUMNS = {
    "buildUpPlaySpeed": "build_up_speed", "buildUpPlayDribbling": "build_up_dribbling",
    "buildUpPlayPassing": "build_up_passing", "buildUpPlayPositioningClass": "build_up_positioning_class",
    "chanceCreationPassing": "chance_creation_passing", "chanceCreationCrossing": "chance_creation_crossing",
    "chanceCreationShooting": "chance_creation_shooting", "chanceCreationPositioningClass": "chance_creation_positioning_class",
    "defencePressure": "defence_pressure", "defenceAggression": "defence_aggression",
    "defenceTeamWidth": "defence_team_width", "defenceDefenderLineClass": "defence_defender_line_class",
}


def team_styles(connection: sqlite3.Connection) -> list[dict]:
    """Lo stile di gioco di ogni squadra per stagione, da Team_Attributes.

    L'ultima entita' della sorgente non ancora nel grafo: 1.458 rilevazioni
    FIFA per 288 squadre - velocita' e tipo di costruzione, creazione delle
    occasioni, pressione, aggressivita' e ampiezza difensiva, linea difensiva -
    ognuna con un valore 20-80 e una classe ('fast', 'short', 'high',
    'offside_trap'...). Una per anno (febbraio 2010-2012, settembre 2013-2015):
    la stagione e' quella della data, e il 2012/2013 non ha rilevazioni. Serve
    allo scouting contestuale: "un esterno con crossing > 80 per una squadra che
    gioca sui cross", "difensori veloci per una linea alta".
    """
    rows_out: list[dict] = []
    query = "SELECT team_api_id, date, " + ", ".join(
        f"{col}" + (f", {col}Class" if not col.endswith("Class") else "") for col in STYLE_COLUMNS
    ) + " FROM Team_Attributes ORDER BY date"
    columns = [d[0] for d in connection.execute(query).description]
    latest: dict[tuple[int, str], dict] = {}
    for values in connection.execute(query):
        record = dict(zip(columns, values))
        item: dict = {"team_id": record["team_api_id"], "date": record["date"][:10], "season": _season_of(record["date"])}
        for source, target in STYLE_COLUMNS.items():
            if source.endswith("Class"):
                item[target] = _style_class(record[source])
            else:
                item[target] = record[source]
                item[target + "_class"] = _style_class(record[source + "Class"])
        latest[(item["team_id"], item["season"])] = item  # l'ultima rilevazione della stagione
    rows_out.extend(latest.values())
    return rows_out


def _style_class(value: str | None) -> str | None:
    """'Free Form' -> 'free_form', 'Offside Trap' -> 'offside_trap': valori confrontabili in Cypher."""
    return value.strip().lower().replace(" ", "_") if value else None


def load_styles(session, styles: list[dict]) -> None:
    session.execute_write(lambda tx: tx.run(
        """
        UNWIND $items AS item
        MATCH (t:Team {id: toInteger(item.team_id)}), (s:Season {name: item.season})
        MERGE (t)-[st:STYLE {season: item.season}]->(s)
        SET st.date = item.date,
            st.build_up_speed = toInteger(item.build_up_speed), st.build_up_speed_class = item.build_up_speed_class,
            st.build_up_dribbling = toInteger(item.build_up_dribbling), st.build_up_dribbling_class = item.build_up_dribbling_class,
            st.build_up_passing = toInteger(item.build_up_passing), st.build_up_passing_class = item.build_up_passing_class,
            st.build_up_positioning_class = item.build_up_positioning_class,
            st.chance_creation_passing = toInteger(item.chance_creation_passing), st.chance_creation_passing_class = item.chance_creation_passing_class,
            st.chance_creation_crossing = toInteger(item.chance_creation_crossing), st.chance_creation_crossing_class = item.chance_creation_crossing_class,
            st.chance_creation_shooting = toInteger(item.chance_creation_shooting), st.chance_creation_shooting_class = item.chance_creation_shooting_class,
            st.chance_creation_positioning_class = item.chance_creation_positioning_class,
            st.defence_pressure = toInteger(item.defence_pressure), st.defence_pressure_class = item.defence_pressure_class,
            st.defence_aggression = toInteger(item.defence_aggression), st.defence_aggression_class = item.defence_aggression_class,
            st.defence_team_width = toInteger(item.defence_team_width), st.defence_team_width_class = item.defence_team_width_class,
            st.defence_defender_line_class = item.defence_defender_line_class
        """,
        items=styles,
    ).consume())


def match_events(connection: sqlite3.Connection) -> tuple[list[dict], list[dict], set[int], dict[int, tuple[int | None, int | None]]]:
    """Tutti gli eventi delle otto colonne XML: eventi, assist, partite coperte, possesso."""
    events: list[dict] = []
    assists: list[dict] = []
    covered: set[int] = set()
    possession: dict[int, tuple[int | None, int | None]] = {}
    columns = ["goal", "shoton", "shotoff", "foulcommit", "card", "cross", "corner", "possession"]
    for row in connection.execute(f"SELECT match_api_id, {', '.join(columns)} FROM Match"):
        match_id, documents = row[0], dict(zip(columns, row[1:]))
        for column in ("shoton", "shotoff", "foulcommit", "card", "cross", "corner"):
            parsed = parse_events(column, match_id, documents[column])
            events.extend(parsed)
            if parsed and column in DETAILED_COLUMNS:
                covered.add(match_id)
        assists.extend(parse_assists(match_id, documents["goal"]))
        home, away = parse_possession(documents["possession"])
        if home is not None:
            possession[match_id] = (home, away)
    return events, assists, covered, possession


def load_events(connection: sqlite3.Connection, session, appearances: list[dict], scored: list[dict]) -> dict[str, int]:
    """Carica gli eventi di gioco e i totali di stagione. Idempotente: MERGE su event_id."""
    events, assists, covered, possession = match_events(connection)
    seasons_by_match = dict(connection.execute("SELECT match_api_id, season FROM Match"))
    sides_by_match = {m: (h, a) for m, h, a in connection.execute("SELECT match_api_id, home_team_api_id, away_team_api_id FROM Match")}
    by_type: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        by_type[event["relationship"]].append(event)
    extra = {
        "SHOT": "e.on_target = item.on_target, e.blocked = item.blocked,",
        "FOUL": "e.victim_id = toInteger(item.victim_id),",
        "CARD": "e.card_type = item.card_type,",
        "CROSS": "",
        "CORNER": "",
    }
    for relationship, items in by_type.items():
        for offset in range(0, len(items), 20000):
            session.execute_write(lambda tx, c=items[offset:offset + 20000], r=relationship: tx.run(
                f"""
                UNWIND $items AS item
                MATCH (p:Player {{id: toInteger(item.player_id)}}), (m:Match {{id: toInteger(item.match_id)}})
                MERGE (p)-[e:{r} {{event_id: toInteger(item.event_id)}}]->(m)
                SET e.minute = toInteger(item.minute), e.subtype = item.subtype, e.team_id = toInteger(item.team_id),
                    e.x = toInteger(item.x), e.y = toInteger(item.y), {extra[r]}
                    e.season = m.season
                """,
                items=c,
            ).consume())
    # Il fallo visto da chi lo subisce: la stessa partita, l'altra squadra.
    fouled = [e for e in by_type["FOUL"] if e.get("victim_id") is not None]
    for offset in range(0, len(fouled), 20000):
        session.execute_write(lambda tx, c=fouled[offset:offset + 20000]: tx.run(
            """
            UNWIND $items AS item
            MATCH (v:Player {id: toInteger(item.victim_id)}), (m:Match {id: toInteger(item.match_id)})
            MERGE (v)-[e:FOULED {event_id: toInteger(item.event_id)}]->(m)
            SET e.minute = toInteger(item.minute), e.subtype = item.subtype, e.by_id = toInteger(item.player_id), e.season = m.season
            """,
            items=c,
        ).consume())
    session.execute_write(lambda tx: tx.run(
        """
        UNWIND $items AS item
        MATCH (a:Player {id: toInteger(item.player_id)}), (m:Match {id: toInteger(item.match_id)})
        MERGE (a)-[e:ASSISTED {event_id: toInteger(item.event_id)}]->(m)
        SET e.minute = toInteger(item.minute), e.scorer_id = toInteger(item.scorer_id), e.team_id = toInteger(item.team_id), e.season = m.season
        """,
        items=assists,
    ).consume())
    # L'arco giocatore -> giocatore: la coppia assist-gol, quante volte e in quali stagioni.
    session.execute_write(lambda tx: tx.run(
        """
        MATCH (a:Player)-[e:ASSISTED]->(m:Match)
        MATCH (s:Player {id: e.scorer_id})
        WITH a, s, count(e) AS gol, collect(DISTINCT m.season) AS stagioni
        MERGE (a)-[c:ASSISTED_GOALS_OF]->(s)
        SET c.goals = gol, c.seasons = stagioni
        """
    ).consume())
    session.execute_write(lambda tx: tx.run(
        "MATCH (m:Match) SET m.has_events = m.id IN $covered", covered=list(covered),
    ).consume())
    session.execute_write(lambda tx: tx.run(
        """
        UNWIND $items AS item
        MATCH (m:Match {id: toInteger(item.match_id)})
        SET m.home_possession = toInteger(item.home), m.away_possession = toInteger(item.away)
        """,
        items=[{"match_id": k, "home": h, "away": a} for k, (h, a) in possession.items()],
    ).consume())
    totals = season_event_counts(events, assists, scored, appearances, seasons_by_match, covered, sides_by_match)
    for offset in range(0, len(totals), 20000):
        session.execute_write(lambda tx, c=totals[offset:offset + 20000]: tx.run(
            """
            UNWIND $items AS item
            MATCH (p:Player {id: toInteger(item.player_id)})-[st:SEASON_TEAM {season: item.season}]->(t:Team {id: toInteger(item.team_id)})
            SET st.events_covered = toInteger(item.events_covered), st.goals = toInteger(item.goals),
                st.assists = toInteger(item.assists), st.shots = toInteger(item.shots),
                st.shots_on_target = toInteger(item.shots_on_target), st.fouls_committed = toInteger(item.fouls_committed),
                st.fouls_suffered = toInteger(item.fouls_suffered), st.yellow_cards = toInteger(item.yellow_cards),
                st.red_cards = toInteger(item.red_cards), st.crosses = toInteger(item.crosses), st.corners = toInteger(item.corners),
                st.shots_faced = toInteger(item.shots_faced), st.shots_on_target_faced = toInteger(item.shots_on_target_faced)
            """,
            items=c,
        ).consume())
    return {"events": len(events), "assists": len(assists), "fouled": len(fouled), "covered": len(covered), "totals": len(totals),
            **{k: len(v) for k, v in by_type.items()}}


def main() -> None:
    database = next(RAW.glob("*.sqlite"), None)
    if database is None:
        raise FileNotFoundError("Run scripts/download_data.py first")
    connection = sqlite3.connect(database)
    graph = GraphStore()
    graph.create_schema()
    player_slots = [f"home_player_{number}" for number in range(1, 12)] + [f"away_player_{number}" for number in range(1, 12)]
    lineup_queries = [
        f"SELECT m.{slot} AS player_id, m.home_team_api_id AS team_id FROM Match m WHERE m.{slot} IS NOT NULL"
        if slot.startswith("home_")
        else f"SELECT m.{slot} AS player_id, m.away_team_api_id AS team_id FROM Match m WHERE m.{slot} IS NOT NULL"
        for slot in player_slots
    ]
    lineup_query = " UNION ALL ".join(lineup_queries)
    relationship_query = f"""
        WITH latest_dates AS (
            SELECT player_api_id, MAX(date) AS date
            FROM Player_Attributes
            GROUP BY player_api_id
        ), latest_attributes AS (
            SELECT attributes.player_api_id, attributes.date,
                   attributes.overall_rating AS overall,
                   attributes.potential, attributes.preferred_foot
            FROM Player_Attributes attributes
            JOIN latest_dates latest
              ON latest.player_api_id = attributes.player_api_id
             AND latest.date = attributes.date
        )
        SELECT DISTINCT lineup.player_id, lineup.team_id, attributes.date,
               attributes.overall, attributes.potential, attributes.preferred_foot
        FROM ({lineup_query}) lineup
        JOIN latest_attributes attributes
          ON attributes.player_api_id = lineup.player_id
    """
    with graph.driver.session() as session:
        session.execute_write(lambda tx: tx.run(
            """
            UNWIND $items AS item
            MERGE (p:Player {id: toInteger(item.id)})
            SET p.name=item.name, p.birthday=item.birthday,
                p.height=toFloat(item.height), p.weight=toInteger(item.weight)
            """,
            items=rows(connection, "SELECT player_api_id AS id, player_name AS name, birthday, height, weight FROM Player"),
        ).consume())
        session.execute_write(lambda tx: tx.run(
            "UNWIND $items AS item MERGE (t:Team {id: toInteger(item.id)}) SET t.name=item.name",
            items=rows(connection, "SELECT team_api_id AS id, team_long_name AS name FROM Team"),
        ).consume())
        session.execute_write(lambda tx: tx.run(
            """
            UNWIND $items AS item
            MATCH (p:Player {id: toInteger(item.player_id)}), (t:Team {id: toInteger(item.team_id)})
            MERGE (p)-[r:PLAYS_FOR {date: item.date}]->(t)
            SET r.overall=toInteger(item.overall), r.potential=toInteger(item.potential),
                r.preferred_foot=toLower(item.preferred_foot)
            """,
            items=rows(connection, relationship_query),
        ).consume())
        # Il piede e' una proprieta' del giocatore, ma Player_Attributes lo porta e
        # PLAYS_FOR lo ripete una volta per club: filtrarlo li' obbliga a legare una
        # relazione che moltiplica le righe, e il modello lo cercava su RATED, dove non
        # c'e'. Sul nodo e' un filtro piatto: p.preferred_foot = 'left'. E' costante per
        # giocatore (11.060 su 11.060 con un solo valore).
        session.execute_write(lambda tx: tx.run(
            """
            MATCH (p:Player)-[r:PLAYS_FOR]->()
            WHERE r.preferred_foot IS NOT NULL
            WITH p, collect(DISTINCT r.preferred_foot)[0] AS foot
            SET p.preferred_foot = foot
            """
        ).consume())
        session.execute_write(lambda tx: tx.run(
            """
            UNWIND $items AS item
            MERGE (m:Match {id: toInteger(item.id)})
            SET m.date=item.date, m.league=item.league, m.season=item.season,
                m.home_goals=toInteger(item.home_goals), m.away_goals=toInteger(item.away_goals)
            """,
            items=rows(connection, """
                SELECT m.match_api_id AS id, m.date, l.name AS league, m.season,
                       m.home_team_goal AS home_goals, m.away_team_goal AS away_goals
                FROM Match m JOIN League l ON l.country_id=m.country_id
            """),
        ).consume())
        session.execute_write(lambda tx: tx.run(
            """
            UNWIND $items AS item
            MATCH (home:Team {id: toInteger(item.home_id)}), (away:Team {id: toInteger(item.away_id)}), (m:Match {id: toInteger(item.id)})
            MERGE (home)-[:HOME_TEAM]->(m)
            MERGE (away)-[:AWAY_TEAM]->(m)
            """,
            items=rows(connection, "SELECT match_api_id AS id, home_team_api_id AS home_id, away_team_api_id AS away_id FROM Match"),
        ).consume())
        appearances = lineup_appearances(connection)
        profiles, current_teams = squad_profile(connection)
        session.execute_write(lambda tx: tx.run(
            """
            UNWIND $items AS item
            MATCH (p:Player {id: toInteger(item.id)})
            SET p.role=item.role, p.position=item.position, p.age=toInteger(item.age),
                p.appearances=toInteger(item.appearances), p.last_season=item.last_season,
                p.active=item.active
            """,
            items=profiles,
        ).consume())
        session.execute_write(lambda tx: tx.run(
            """
            UNWIND $items AS item
            MATCH (p:Player {id: toInteger(item.player_id)}), (t:Team {id: toInteger(item.team_id)})
            MERGE (p)-[:CURRENT_TEAM]->(t)
            """,
            items=current_teams,
        ).consume())
        session.execute_write(lambda tx: tx.run(
            """
            UNWIND $items AS item
            MATCH (p:Player {id: toInteger(item.player_id)}), (m:Match {id: toInteger(item.match_id)})
            MERGE (p)-[a:APPEARED_IN]->(m)
            SET a.position = item.position, a.team_id = toInteger(item.team_id)
            """,
            items=appearances,
        ).consume())
        seasons_by_match = dict(connection.execute("SELECT match_api_id, season FROM Match"))
        leagues_by_match = dict(connection.execute(
            "SELECT m.match_api_id, l.name FROM Match m JOIN League l ON l.country_id = m.country_id"
        ))
        season_rows = season_teams(appearances, seasons_by_match, leagues_by_match)
        session.execute_write(lambda tx: tx.run(
            """
            UNWIND $items AS item
            MATCH (p:Player {id: toInteger(item.player_id)}), (t:Team {id: toInteger(item.team_id)})
            MERGE (p)-[st:SEASON_TEAM {season: item.season}]->(t)
            SET st.appearances = toInteger(item.appearances), st.position = item.position,
                st.main = item.main, st.season_total = toInteger(item.season_total),
                st.league = item.league, st.league_total = toInteger(item.league_total)
            """,
            items=season_rows,
        ).consume())
        ratings = season_ratings(connection)
        session.execute_write(lambda tx: tx.run(
            "UNWIND $items AS name MERGE (:Season {name: name})", items=SEASONS,
        ).consume())
        properties = ", ".join(
            f"r.{name}=toInteger(item.{name})"
            for name in ["overall", "potential"] + SKILL_COLUMNS
        )
        for offset in range(0, len(ratings), 20000):
            session.execute_write(lambda tx, c=ratings[offset:offset + 20000]: tx.run(
                f"""
                UNWIND $items AS item
                MATCH (p:Player {{id: toInteger(item.player_id)}}), (s:Season {{name: item.season}})
                MERGE (p)-[r:RATED {{season: item.season}}]->(s)
                SET r.date=item.date, {properties},
                    r.attacking_work_rate=item.attacking_work_rate,
                    r.defensive_work_rate=item.defensive_work_rate
                """,
                items=c,
            ).consume())
        # Un grafo caricato prima dell'esclusione delle valutazioni vuote le
        # conserva, perche' MERGE non cancella: si rimuovono qui, cosi' un
        # ricaricamento converge allo stesso stato di un caricamento pulito.
        session.execute_write(lambda tx: tx.run(
            "MATCH ()-[r:RATED]->() WHERE r.overall IS NULL DELETE r"
        ).consume())
        table = standings(connection)
        session.execute_write(lambda tx: tx.run(
            """
            UNWIND $items AS item
            MATCH (t:Team {id: toInteger(item.team_id)}), (s:Season {name: item.season})
            MERGE (t)-[r:RANKED {season: item.season}]->(s)
            SET r.league = item.league, r.rank = toInteger(item.rank), r.teams = toInteger(item.teams),
                r.top = item.top, r.top_places = toInteger(item.top_places),
                r.points = toInteger(item.points), r.played = toInteger(item.played),
                r.won = toInteger(item.won), r.drawn = toInteger(item.drawn), r.lost = toInteger(item.lost),
                r.goals_for = toInteger(item.goals_for), r.goals_against = toInteger(item.goals_against),
                r.goal_difference = toInteger(item.goal_difference)
            """,
            items=table,
        ).consume())
        assessments = all_assessments(connection)
        for offset in range(0, len(assessments), 20000):
            session.execute_write(lambda tx, c=assessments[offset:offset + 20000]: tx.run(
                f"""
                UNWIND $items AS item
                MATCH (p:Player {{id: toInteger(item.player_id)}}), (s:Season {{name: item.season}})
                MERGE (p)-[r:ASSESSED {{date: item.date}}]->(s)
                SET r.season=item.season, {properties},
                    r.attacking_work_rate=item.attacking_work_rate,
                    r.defensive_work_rate=item.defensive_work_rate
                """,
                items=c,
            ).consume())
        scored, own_goals = goal_events(connection)
        session.execute_write(lambda tx: tx.run(
            """
            UNWIND $items AS item
            MATCH (p:Player {id: toInteger(item.player_id)}), (m:Match {id: toInteger(item.match_id)})
            MERGE (p)-[g:SCORED {event_id: toInteger(item.event_id)}]->(m)
            SET g.minute=toInteger(item.minute), g.penalty=item.penalty, g.subtype=item.subtype
            """,
            items=scored,
        ).consume())
        session.execute_write(lambda tx: tx.run(
            """
            UNWIND $items AS item
            MATCH (p:Player {id: toInteger(item.player_id)}), (m:Match {id: toInteger(item.match_id)})
            MERGE (p)-[g:OWN_GOAL {event_id: toInteger(item.event_id)}]->(m)
            SET g.minute=toInteger(item.minute)
            """,
            items=own_goals,
        ).consume())
        events_summary = load_events(connection, session, appearances, scored)
        styles = team_styles(connection)
        load_styles(session, styles)
    graph.close()
    connection.close()
    print(
        "Neo4j graph loaded:\n"
        f"  Player / Team / Match / Season nodes and {len(profiles):,} player profiles\n"
        f"  {len(appearances):,} APPEARED_IN (line-ups, with the position and team of each match)\n"
        f"  {len(season_rows):,} SEASON_TEAM (club, appearances and role per player per season)\n"
        f"  {len(ratings):,} RATED (35 attributes per player per season)\n"
        f"  {len(assessments):,} ASSESSED (every single assessment, the full time series)\n"
        f"  {len(table):,} RANKED (league standings computed from the results, per team per season)\n"
        f"  {len(scored):,} SCORED and {len(own_goals):,} OWN_GOAL from the nested match XML\n"
        f"  {events_summary['events']:,} match events (SHOT {events_summary.get('SHOT', 0):,}, FOUL {events_summary.get('FOUL', 0):,} "
        f"+ FOULED {events_summary['fouled']:,}, CARD {events_summary.get('CARD', 0):,}, CROSS {events_summary.get('CROSS', 0):,}, "
        f"CORNER {events_summary.get('CORNER', 0):,}), {events_summary['assists']:,} ASSISTED, "
        f"{events_summary['covered']:,} matches with full event coverage, season totals on {events_summary['totals']:,} SEASON_TEAM\n"
        f"  {len(styles):,} STYLE (team playing style per season, from Team_Attributes)"
    )


def events_only() -> None:
    """Solo gli eventi di gioco, su un grafo gia' caricato (`--events`)."""
    connection = sqlite3.connect(next(RAW.glob("*.sqlite")))
    graph = GraphStore()
    appearances = lineup_appearances(connection)
    scored, _ = goal_events(connection)
    with graph.driver.session() as session:
        summary = load_events(connection, session, appearances, scored)
    graph.close()
    connection.close()
    print("Match events loaded:", summary)


def styles_only() -> None:
    """Solo lo stile di gioco delle squadre, su un grafo gia' caricato (`--styles`)."""
    connection = sqlite3.connect(next(RAW.glob("*.sqlite")))
    graph = GraphStore()
    styles = team_styles(connection)
    with graph.driver.session() as session:
        load_styles(session, styles)
    graph.close()
    connection.close()
    print(f"Team styles loaded: {len(styles):,} STYLE relationships")


if __name__ == "__main__":
    if "--events" in sys.argv:
        events_only()
    elif "--styles" in sys.argv:
        styles_only()
    else:
        main()
