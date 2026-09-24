"""Parser generico degli eventi di gioco annidati nell'XML di `Match`.

La sorgente porta otto colonne XML per partita - goal, shoton, shotoff,
foulcommit, card, cross, corner, possession - con la stessa struttura: una
lista di <value> con tipo, sottotipo, minuto, squadra, uno o due giocatori e,
per alcuni, le coordinate. Il grafo caricava solo i gol, e ogni nuova classe
di domanda (tiri subiti, falli, cartellini, assist) richiedeva un'estensione ad
hoc del loader. Qui ogni famiglia di eventi diventa una relazione con lo stesso
schema, e gli assist - `player2` del gol, presenti in 17.065 gol su 37.496 -
smettono di essere "un dato che il database non contiene".

Copertura: gli eventi diversi dai gol e dai cartellini esistono per 8.465
partite su 25.979 (Premier League completa; Liga, Serie A, Bundesliga e Ligue 1
parziali; Belgio, Portogallo e Svizzera assenti). Ogni partita porta quindi
`has_events`, e ogni conteggio va letto insieme alle partite coperte.
"""

import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from typing import Any

# Colonna XML -> tipo di relazione nel grafo.
EVENT_RELATIONSHIPS = {
    "shoton": "SHOT",
    "shotoff": "SHOT",
    "foulcommit": "FOUL",
    "card": "CARD",
    "cross": "CROSS",
    "corner": "CORNER",
}
# Le colonne che definiscono una partita "con eventi": i gol e i cartellini esistono
# anche per partite senza il resto, e da soli non dicono che la cronaca sia completa.
DETAILED_COLUMNS = ("shoton", "shotoff", "foulcommit", "cross", "corner")


def _int(text: str | None) -> int | None:
    try:
        return int(text) if text not in (None, "") else None
    except ValueError:
        return None


def _coordinates(value: ET.Element) -> tuple[int | None, int | None]:
    node = value.find("coordinates")
    if node is None:
        return None, None
    parts = [_int(v.text) for v in node.findall("value")]
    return (parts + [None, None])[:2]


def parse_events(column: str, match_id: int, document: str | None) -> list[dict[str, Any]]:
    """Gli eventi di una colonna XML di una partita, uno per <value> con un giocatore.

    Ogni evento porta: relationship, event_id, match_id, player_id, team_id,
    minute, subtype, x, y; i tiri anche on_target e blocked, i cartellini
    card_type ('y', 'y2', 'r'), i falli victim_id (player2, chi lo subisce).
    """
    if not document or len(document) < 20 or column not in EVENT_RELATIONSHIPS:
        return []
    try:
        root = ET.fromstring(document)
    except ET.ParseError:
        return []
    events: list[dict[str, Any]] = []
    for value in root.findall("value"):
        event_id = _int(value.findtext("id"))
        player_id = _int(value.findtext("player1"))
        if event_id is None or player_id is None:
            continue
        x, y = _coordinates(value)
        subtype = value.findtext("subtype") or ""
        event: dict[str, Any] = {
            "relationship": EVENT_RELATIONSHIPS[column],
            "event_id": event_id,
            "match_id": match_id,
            "player_id": player_id,
            "team_id": _int(value.findtext("team")),
            "minute": _int(value.findtext("elapsed")) or 0,
            "subtype": subtype,
            "x": x,
            "y": y,
        }
        if column in ("shoton", "shotoff"):
            event["on_target"] = column == "shoton"
            # Un tiro murato e' in shoton ma non arriva mai al portiere.
            event["blocked"] = "blocked" in subtype
        elif column == "foulcommit":
            event["victim_id"] = _int(value.findtext("player2"))
        elif column == "card":
            card_type = value.findtext("card_type") or ""
            if card_type not in ("y", "y2", "r"):
                continue  # 605 cartellini senza tipo: inutilizzabili
            event["card_type"] = card_type
        events.append(event)
    return events


def parse_assists(match_id: int, document: str | None) -> list[dict[str, Any]]:
    """Gli assist: `player2` dei gol su azione, `player1` e' il marcatore.

    Controprova: Neymar (player1) su assist di Busquets (player2), Barcellona
    2015/2016. I rigori non hanno assist e gli autogol non contano.
    """
    if not document or len(document) < 40:
        return []
    try:
        root = ET.fromstring(document)
    except ET.ParseError:
        return []
    assists: list[dict[str, Any]] = []
    for value in root.findall("value"):
        if value.findtext("type") != "goal" or value.findtext("goal_type") != "n":
            continue
        event_id, scorer, assister = _int(value.findtext("id")), _int(value.findtext("player1")), _int(value.findtext("player2"))
        if event_id is None or scorer is None or assister is None or assister == scorer:
            continue
        assists.append({
            "event_id": event_id,
            "match_id": match_id,
            "player_id": assister,
            "scorer_id": scorer,
            "team_id": _int(value.findtext("team")),
            "minute": _int(value.findtext("elapsed")) or 0,
        })
    return assists


def parse_possession(document: str | None) -> tuple[int | None, int | None]:
    """Il possesso palla finale (casa, trasferta): l'ultima rilevazione della partita."""
    if not document or len(document) < 20:
        return None, None
    try:
        root = ET.fromstring(document)
    except ET.ParseError:
        return None, None
    last: tuple[int | None, int | None] = (None, None)
    for value in root.findall("value"):
        home, away = _int(value.findtext("homepos")), _int(value.findtext("awaypos"))
        if home is not None and away is not None:
            last = (home, away)
    return last


def season_event_counts(
    events: list[dict[str, Any]],
    assists: list[dict[str, Any]],
    goals: list[dict[str, Any]],
    appearances: list[dict[str, Any]],
    seasons_by_match: dict[int, str],
    covered_matches: set[int],
    sides_by_match: dict[int, tuple[int, int]],
) -> list[dict[str, Any]]:
    """I totali di stagione per (giocatore, stagione, club), da scrivere su SEASON_TEAM.

    Le domande di scouting devono restare piatte: "portieri con piu' tiri subiti
    e gk_reflexes > 78" e' un filtro su proprieta', non un join a tre vie che
    il modello sbaglierebbe. `events_covered` e' il numero di presenze del
    giocatore in partite con cronaca completa: un conteggio senza quel numero
    accanto premia chi gioca in Inghilterra.
    I tiri subiti di un portiere sono i tiri della squadra avversaria nelle
    partite in cui era schierato: il portiere non compare nell'evento.
    """
    counts: dict[tuple[int, str, int], Counter] = defaultdict(Counter)

    def key_of(player_id: int, match_id: int, team_id: int | None):
        season = seasons_by_match.get(match_id)
        if season is None or team_id is None:
            return None
        return (player_id, season, team_id)

    for event in events:
        key = key_of(event["player_id"], event["match_id"], event["team_id"])
        if key is None:
            continue
        rel = event["relationship"]
        if rel == "SHOT":
            counts[key]["shots"] += 1
            if event["on_target"]:
                counts[key]["shots_on_target"] += 1
        elif rel == "FOUL":
            counts[key]["fouls_committed"] += 1
            if event.get("victim_id") is not None:
                # Chi subisce il fallo gioca per l'altra squadra della partita.
                home, away = sides_by_match.get(event["match_id"], (None, None))
                other = away if event["team_id"] == home else home
                victim = key_of(event["victim_id"], event["match_id"], other)
                if victim is not None:
                    counts[victim]["fouls_suffered"] += 1
        elif rel == "CARD":
            if event["card_type"] in ("y", "y2"):
                counts[key]["yellow_cards"] += 1
            if event["card_type"] in ("r", "y2"):
                counts[key]["red_cards"] += 1
        elif rel == "CROSS":
            counts[key]["crosses"] += 1
        elif rel == "CORNER":
            counts[key]["corners"] += 1
    for assist in assists:
        key = key_of(assist["player_id"], assist["match_id"], assist["team_id"])
        if key is not None:
            counts[key]["assists"] += 1
    for goal in goals:
        key = key_of(goal["player_id"], goal["match_id"], goal.get("team_id"))
        if key is not None:
            counts[key]["goals"] += 1

    # Portieri: i tiri dell'avversario nelle partite coperte in cui erano in campo.
    shots_by_match_team: dict[tuple[int, int], Counter] = defaultdict(Counter)
    for event in events:
        if event["relationship"] == "SHOT" and event["team_id"] is not None:
            bucket = shots_by_match_team[(event["match_id"], event["team_id"])]
            bucket["shots"] += 1
            if event["on_target"] and not event["blocked"]:
                bucket["on_target"] += 1
    covered_appearances: Counter = Counter()
    keys_seen: set[tuple[int, str, int]] = set()
    for row in appearances:
        key = key_of(row["player_id"], row["match_id"], row["team_id"])
        if key is None:
            continue
        keys_seen.add(key)
        if row["match_id"] in covered_matches:
            covered_appearances[key] += 1
            if row.get("position") == "goalkeeper":
                home, away = sides_by_match.get(row["match_id"], (None, None))
                other = away if row["team_id"] == home else home
                faced = shots_by_match_team.get((row["match_id"], other), Counter())
                counts[key]["shots_faced"] += faced["shots"]
                counts[key]["shots_on_target_faced"] += faced["on_target"]

    fields = ["shots", "shots_on_target", "fouls_committed", "fouls_suffered", "yellow_cards", "red_cards",
              "crosses", "corners", "assists", "goals", "shots_faced", "shots_on_target_faced"]
    return [
        {
            "player_id": player_id, "season": season, "team_id": team_id,
            "events_covered": covered_appearances[key],
            **{field: counts[key][field] for field in fields},
        }
        for key in keys_seen | set(counts)
        for player_id, season, team_id in [key]
    ]
