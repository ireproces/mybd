"""Test del parser generico degli eventi di gioco e dei totali di stagione."""

from app.events import parse_assists, parse_events, parse_possession, season_event_counts


SHOTON = (
    "<shoton><value><stats><blocked>1</blocked></stats><coordinates><value>24</value><value>14</value></coordinates>"
    "<elapsed>9</elapsed><subtype>blocked_shot</subtype><player1>75489</player1><team>9825</team><type>shoton</type><id>3647350</id></value>"
    "<value><elapsed>40</elapsed><subtype>shot</subtype><player1>30981</player1><team>8634</team><type>shoton</type><id>3647351</id></value>"
    "<value><elapsed>50</elapsed><subtype>shot</subtype><team>8634</team><type>shoton</type><id>3647352</id></value></shoton>"
)
GOAL = (
    "<goal><value><comment>n</comment><elapsed>12</elapsed><subtype>shot</subtype><player1>30981</player1><player2>30955</player2>"
    "<team>8634</team><goal_type>n</goal_type><type>goal</type><id>1</id></value>"
    "<value><comment>p</comment><elapsed>60</elapsed><player1>40636</player1><team>8634</team><goal_type>p</goal_type><type>goal</type><id>2</id></value>"
    "<value><comment>o</comment><elapsed>70</elapsed><player1>1</player1><player2>2</player2><team>9825</team><goal_type>o</goal_type><type>goal</type><id>3</id></value></goal>"
)
FOUL = ("<foulcommit><value><elapsed>5</elapsed><subtype>trip</subtype><player1>100</player1><player2>200</player2><team>9825</team>"
        "<type>foulcommit</type><id>10</id></value></foulcommit>")
CARD = ("<card><value><elapsed>30</elapsed><card_type>y</card_type><player1>100</player1><team>9825</team><type>card</type><id>20</id></value>"
        "<value><elapsed>80</elapsed><card_type>y2</card_type><player1>100</player1><team>9825</team><type>card</type><id>21</id></value>"
        "<value><elapsed>85</elapsed><player1>101</player1><team>9825</team><type>card</type><id>22</id></value></card>")
POSSESSION = ("<possession><value><elapsed>45</elapsed><homepos>54</homepos><awaypos>46</awaypos></value>"
              "<value><elapsed>90</elapsed><homepos>55</homepos><awaypos>45</awaypos></value></possession>")


def test_i_tiri_portano_bersaglio_muro_e_coordinate():
    shots = parse_events("shoton", 7, SHOTON)
    assert len(shots) == 2  # il terzo non ha player1
    assert shots[0] == {"relationship": "SHOT", "event_id": 3647350, "match_id": 7, "player_id": 75489, "team_id": 9825,
                        "minute": 9, "subtype": "blocked_shot", "x": 24, "y": 14, "on_target": True, "blocked": True}
    assert shots[1]["blocked"] is False and shots[1]["x"] is None
    assert parse_events("shotoff", 7, SHOTON.replace("shoton", "shotoff"))[0]["on_target"] is False


def test_gli_assist_sono_il_player2_dei_gol_su_azione():
    assists = parse_assists(7, GOAL)
    assert assists == [{"event_id": 1, "match_id": 7, "player_id": 30955, "scorer_id": 30981, "team_id": 8634, "minute": 12}]


def test_falli_cartellini_e_possesso():
    foul = parse_events("foulcommit", 7, FOUL)[0]
    assert foul["relationship"] == "FOUL" and foul["victim_id"] == 200 and foul["player_id"] == 100
    cards = parse_events("card", 7, CARD)
    assert [c["card_type"] for c in cards] == ["y", "y2"]  # il terzo, senza tipo, e' scartato
    assert parse_possession(POSSESSION) == (55, 45)
    assert parse_possession(None) == (None, None)
    assert parse_events("goal", 7, GOAL) == []  # i gol hanno il loro parser


def test_i_totali_di_stagione_e_i_tiri_subiti_dal_portiere():
    events = parse_events("shoton", 7, SHOTON) + parse_events("foulcommit", 7, FOUL) + parse_events("card", 7, CARD)
    assists = parse_assists(7, GOAL)
    goals = [{"player_id": 30981, "match_id": 7, "team_id": 8634}, {"player_id": 40636, "match_id": 7, "team_id": 8634}]
    appearances = [
        {"player_id": 30981, "match_id": 7, "team_id": 8634, "position": "striker"},
        {"player_id": 999, "match_id": 7, "team_id": 9825, "position": "goalkeeper"},   # portiere avversario
        {"player_id": 998, "match_id": 7, "team_id": 8634, "position": "goalkeeper"},
        {"player_id": 200, "match_id": 7, "team_id": 8634, "position": "winger"},
        {"player_id": 555, "match_id": 8, "team_id": 8634, "position": "striker"},      # partita senza eventi
    ]
    totals = {(t["player_id"], t["team_id"]): t for t in season_event_counts(
        events, assists, goals, appearances, {7: "2015/2016", 8: "2015/2016"}, {7}, {7: (8634, 9825), 8: (8634, 1)})}
    scorer = totals[(30981, 8634)]
    assert scorer["shots"] == 1 and scorer["shots_on_target"] == 1 and scorer["goals"] == 1 and scorer["events_covered"] == 1
    assert totals[(30955, 8634)]["assists"] == 1
    assert totals[(100, 9825)] | {} == totals[(100, 9825)] and totals[(100, 9825)]["fouls_committed"] == 1
    assert totals[(100, 9825)]["yellow_cards"] == 2 and totals[(100, 9825)]["red_cards"] == 1  # y + y2; y2 e' anche rosso
    assert totals[(200, 8634)]["fouls_suffered"] == 1
    # Il portiere del 9825 subisce i tiri dell'8634: uno nello specchio (il murato del 9825 non conta, e' della sua squadra).
    assert totals[(999, 9825)]["shots_faced"] == 1 and totals[(999, 9825)]["shots_on_target_faced"] == 1
    assert totals[(998, 8634)]["shots_faced"] == 1 and totals[(998, 8634)]["shots_on_target_faced"] == 0  # solo il tiro murato
    assert totals[(555, 8634)]["events_covered"] == 0 and totals[(555, 8634)]["shots"] == 0
