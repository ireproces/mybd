"""Test del caricamento dello stile di gioco delle squadre (Team_Attributes -> STYLE)."""

import sqlite3

from scripts.load_data import _style_class, team_styles
from app.rag import _coverage_note, _extract_cypher, _rating_not_current, _unknown_property


def _database() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.execute("""CREATE TABLE Team_Attributes (id INTEGER, team_fifa_api_id INTEGER, team_api_id INTEGER, date TEXT,
        buildUpPlaySpeed INTEGER, buildUpPlaySpeedClass TEXT, buildUpPlayDribbling INTEGER, buildUpPlayDribblingClass TEXT,
        buildUpPlayPassing INTEGER, buildUpPlayPassingClass TEXT, buildUpPlayPositioningClass TEXT,
        chanceCreationPassing INTEGER, chanceCreationPassingClass TEXT, chanceCreationCrossing INTEGER, chanceCreationCrossingClass TEXT,
        chanceCreationShooting INTEGER, chanceCreationShootingClass TEXT, chanceCreationPositioningClass TEXT,
        defencePressure INTEGER, defencePressureClass TEXT, defenceAggression INTEGER, defenceAggressionClass TEXT,
        defenceTeamWidth INTEGER, defenceTeamWidthClass TEXT, defenceDefenderLineClass TEXT)""")
    row = lambda i, team, date, speed: (i, 1, team, date, speed, "Fast" if speed > 65 else "Balanced", None, "Little", 40, "Short", "Free Form",
                                        50, "Risky", 80, "Lots", 60, "Normal", "Organised", 70, "High", 65, "Double", 60, "Wide", "Offside Trap")
    c.executemany("INSERT INTO Team_Attributes VALUES (" + ",".join("?" * 25) + ")", [
        row(1, 9825, "2010-02-22 00:00:00", 50),   # febbraio 2010 -> stagione 2009/2010
        row(2, 9825, "2015-09-10 00:00:00", 70),   # settembre 2015 -> 2015/2016
        row(3, 9825, "2015-09-20 00:00:00", 72),   # stessa stagione, piu' recente: vince
        row(4, 8634, "2013-09-20 00:00:00", 60),
    ])
    return c


def test_una_rilevazione_per_squadra_per_stagione_con_classi_normalizzate():
    styles = {(s["team_id"], s["season"]): s for s in team_styles(_database())}
    assert set(styles) == {(9825, "2009/2010"), (9825, "2015/2016"), (8634, "2013/2014")}
    ultima = styles[(9825, "2015/2016")]
    assert ultima["build_up_speed"] == 72 and ultima["build_up_speed_class"] == "fast" and ultima["date"] == "2015-09-20"
    assert ultima["build_up_dribbling"] is None and ultima["build_up_dribbling_class"] == "little"
    assert ultima["build_up_positioning_class"] == "free_form" and ultima["defence_defender_line_class"] == "offside_trap"
    assert ultima["defence_pressure_class"] == "high" and ultima["chance_creation_crossing_class"] == "lots"
    assert _style_class("Offside Trap") == "offside_trap" and _style_class(None) is None


ESTERNI_PER_CROSS = (
    "MATCH (p:Player)-[:CURRENT_TEAM]->(t:Team)-[sty:STYLE {season: '2015/2016'}]->(:Season), (p)-[r:RATED {season: '2015/2016'}]->(:Season) "
    "WHERE p.active AND p.position IN ['winger', 'wide_midfielder'] AND r.crossing > 80 AND sty.chance_creation_crossing_class = 'lots' "
    "RETURN p.name, r.overall AS overall, r.crossing, sty.chance_creation_crossing, sty.chance_creation_crossing_class, t.name ORDER BY overall DESC"
)


def test_lo_stile_segue_la_stagione_corrente_ed_e_annotato():
    question = "esterni con crossing > 80 che giocano in squadre che creano molto sui cross"
    assert _extract_cypher(ESTERNI_PER_CROSS, question).startswith("MATCH")
    senza_stagione = ESTERNI_PER_CROSS.replace("[sty:STYLE {season: '2015/2016'}]", "[sty:STYLE]")
    assert "STYLE" in _rating_not_current(question, senza_stagione)
    assert "stile di gioco" in _coverage_note(ESTERNI_PER_CROSS)
    assert _unknown_property("MATCH (t:Team)-[sty:STYLE]->(:Season) RETURN t.name, sty.pressing") == ("sty", "pressing", "STYLE")
    assert _unknown_property(ESTERNI_PER_CROSS) is None
