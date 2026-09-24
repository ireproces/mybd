import json
import re
import time
from typing import Any

from openai import OpenAI

from .config import settings
from .graph import GraphStore
from .subgraph import build_evidence_subgraph


SCHEMA_DESCRIPTION = """
Nodes:
- Player {id: int, name: string, birthday: string, height: float, weight: int,
          role: string, position: string, age: int, active: bool, preferred_foot: string,
          last_season: string, appearances: int}
  Player holds biographical data plus the profile derived from the line-ups.
  It carries NO rating properties whatsoever (see below).
- Team {id: int, name: string}
- Season {name: string}  The eight seasons, '2008/2009' through '2015/2016'.
- Match {id: int, date: string, league: string, season: string, home_goals: int, away_goals: int,
         has_events: bool, home_possession: int, away_possession: int}
  `has_events` is true for the 8,466 matches (of 25,979) with the full play-by-play:
  shots, fouls, crosses, corners. Premier League complete; Liga, Serie A, Bundesliga and
  Ligue 1 partial; Belgium, Portugal and Switzerland have none. Goals and cards exist for
  13,000+ matches. Possession is the final share, when recorded.

Relationships:
- (Player)-[:PLAYS_FOR {date: string, overall: int, potential: int, preferred_foot: string}]->(Team)
- (Team)-[:HOME_TEAM]->(Match)
- (Team)-[:AWAY_TEAM]->(Match)
- (Player)-[:SCORED {minute: int, penalty: bool, subtype: string}]->(Match)
  One relationship per goal actually scored by that player in that match.
- (Player)-[:OWN_GOAL {minute: int}]->(Match)
  An own goal. It counts for the opposing team and is NEVER a player's output.
- (Player)-[:ASSISTED {minute: int, scorer_id: int, team_id: int, season: string}]->(Match)
  One per assist (the pass before an open-play goal): 16,995 of them. Oezil has 19 in
  2015/2016. Count them for 'assist'.
- (Player)-[:ASSISTED_GOALS_OF {goals: int, seasons: [string]}]->(Player)
  The pair: how many goals the first player assisted for the second, across the career
  (Daniel Alves -> Messi: 21). For 'coppie gol-assist', 'chi serve piu' spesso X'.
- (Player)-[:SHOT {minute, subtype, on_target: bool, blocked: bool, team_id, x, y, season}]->(Match)
  One per shot (186,453): on_target = true for shots on goal ('tiri in porta', 'nello
  specchio'), false for shots off target; blocked = true for shots blocked by a defender.
- (Player)-[:FOUL {minute, subtype, victim_id, team_id, season}]->(Match)   fouls COMMITTED (210,100)
- (Player)-[:FOULED {minute, subtype, by_id, season}]->(Match)             fouls SUFFERED (188,396)
- (Player)-[:CARD {minute, card_type: 'y' | 'y2' | 'r', subtype, team_id, season}]->(Match)
  61,095 cards: 'y' yellow, 'y2' second yellow (also a sending-off), 'r' straight red.
- (Player)-[:CROSS {minute, subtype, team_id, x, y, season}]->(Match)      crosses (270,059)
- (Player)-[:CORNER {minute, subtype, team_id, season}]->(Match)           corners taken (85,026)
  Every event relationship carries `season` (the match's), so a season filter needs no
  join to the Match: WHERE e.season = '2015/2016'.
- (Player)-[:APPEARED_IN {position: string, team_id: int}]->(Match)
  One relationship per match the player started, carrying `position`: the role he covered
  IN THAT MATCH, from the same seven values as p.position, and `team_id`, the id of the Team
  he lined up for in that match. 542,267 of them.
  This is how appearances are counted for any subset - a league, a season, an opponent - and
  how versatility is measured: count(DISTINCT a.position) over his appearances. p.position is
  only his prevailing role, so it can never reveal that he covered several.
- (Player)-[:RATED {season, date, overall, potential, ...35 attributes}]->(Season)
  The player's FIFA profile at the END of that season: one relationship per player per
  season, 54,628 in total. This is the ONLY place where physical and technical attributes
  live, and the only way to follow a player over time.
    physical:   stamina, strength, acceleration, sprint_speed, agility, balance, jumping,
                reactions, aggression
    technical:  crossing, finishing, heading_accuracy, short_passing, volleys, dribbling,
                curve, free_kick_accuracy, long_passing, ball_control, shot_power,
                long_shots, positioning, vision, penalties
    defensive:  interceptions, marking, standing_tackle, sliding_tackle
    goalkeeping: gk_diving, gk_handling, gk_kicking, gk_positioning, gk_reflexes
    work rates: attacking_work_rate, defensive_work_rate (strings: 'low'/'medium'/'high')
  Every one of those attributes is a property of the RELATIONSHIP, not of the Season node,
  which carries nothing but its name. Bind the relationship to a variable and read it there.
  WRONG:   MATCH (p:Player)-[:RATED]->(s:Season) ... max(s.stamina)     -> always null
  CORRECT: MATCH (p:Player)-[r:RATED]->(s:Season) ... max(r.stamina)
  r.overall on PLAYS_FOR is only the latest snapshot; use RATED whenever the question
  involves a season, a trend, or any attribute other than overall/potential.
  'sempre sopra X' / 'mai sotto X' -> min(r.attr) > X, never max(r.attr) > X, which would
  accept a player who reached X once and collapsed afterwards.
- (Player)-[:ASSESSED {date, season, overall, potential, ...the same 35 attributes}]->(Season)
  EVERY single assessment, one every few weeks: the full time series, 183,000 in total.
  Use it ONLY when the question is about the evolution WITHIN a season ('andamento durante
  la stagione', 'a inizio e fine stagione', 'mese per mese'). For anything per season - a
  value, a trend across seasons, a best or worst - use RATED, which is one per season and
  never repeats a player.
- (Team)-[:RANKED {season, league, rank, teams, top, top_places, points, played, won, drawn,
                   lost, goals_for, goals_against, goal_difference}]->(Season)
  The league table of that season, computed from every result (3-1-0, then goal
  difference, then goals scored): rank 1 is the champion, `teams` is the size of the
  league (10 to 20). One per team per season. Leicester City is rank 1 of the England
  Premier League in 2015/2016 with 81 points; Juventus rank 1 of Italy Serie A with 91.
  `top` is true for the clubs in the top tier of THEIR league that season: the first 30%
  of the table, never fewer than 4 (`top_places`: 4 of 10 in Switzerland, 6 of 20 in
  Serie A), which approximates the European places. This is the ONLY meaning of
  'classifica', 'posizione', 'prima fascia', 'top club', 'grande squadra', 'big club',
  'meta' bassa della classifica' in this database. A club has no tier, budget or prestige
  property: the tier IS `top`.
  CORRECT: MATCH (t:Team)-[k:RANKED {season: '2015/2016'}]->(:Season) WHERE k.top = true
- (Team)-[:STYLE {season, date, build_up_speed, build_up_speed_class, build_up_dribbling,
                 build_up_dribbling_class, build_up_passing, build_up_passing_class,
                 build_up_positioning_class, chance_creation_passing, chance_creation_passing_class,
                 chance_creation_crossing, chance_creation_crossing_class, chance_creation_shooting,
                 chance_creation_shooting_class, chance_creation_positioning_class, defence_pressure,
                 defence_pressure_class, defence_aggression, defence_aggression_class,
                 defence_team_width, defence_team_width_class, defence_defender_line_class}]->(Season)
  The club's PLAYING STYLE in that season (FIFA team attributes): one per team per season,
  1,457 in total for 288 clubs, seasons 2009/2010-2011/2012 and 2013/2014-2015/2016 (there
  is NO 2012/2013). Numeric values run 20-80; the classes are lowercase words:
    build_up_speed_class: 'slow' | 'balanced' | 'fast'
    build_up_dribbling_class: 'little' | 'normal' | 'lots'  (the number is often null: use the class)
    build_up_passing_class: 'short' | 'mixed' | 'long'
    build_up_positioning_class / chance_creation_positioning_class: 'organised' | 'free_form'
    chance_creation_passing_class: 'safe' | 'normal' | 'risky'
    chance_creation_crossing_class / chance_creation_shooting_class: 'little' | 'normal' | 'lots'
    defence_pressure_class: 'deep' | 'medium' | 'high'
    defence_aggression_class: 'contain' | 'press' | 'double'
    defence_team_width_class: 'narrow' | 'normal' | 'wide'
    defence_defender_line_class: 'cover' | 'offside_trap'
  Reach it from a player through his club: (p)-[:CURRENT_TEAM]->(t)-[sty:STYLE {season:
  '2015/2016'}]->(:Season). With no season in the question use '2015/2016'. This is for
  CONTEXTUAL scouting - a player who fits how a club plays - and for questions about clubs
  ('squadre con pressing alto', 'chi costruisce con il lancio lungo').
- (Player)-[:CURRENT_TEAM]->(Team)
  The club of his most recent appearance: exactly one per player. This is the club he
  plays for TODAY (at the end of the dataset, 2015/2016). PLAYS_FOR instead links him to
  EVERY club he ever lined up for, without saying when.
- (Player)-[:SEASON_TEAM {season: string, main: bool, appearances: int, season_total: int,
                          league: string, league_total: int, position: string,
                          events_covered: int, goals: int, assists: int, shots: int,
                          shots_on_target: int, fouls_committed: int, fouls_suffered: int,
                          yellow_cards: int, red_cards: int, crosses: int, corners: int,
                          shots_faced: int, shots_on_target_faced: int}]->(Team)
  SEASON TOTALS OF EVERY EVENT are already on it, per player per season per club: goals,
  assists, shots, shots on target, fouls committed and suffered, yellow and red cards,
  crosses, corners, and for goalkeepers the shots faced and the shots on target faced
  (shots of the opponents in the matches he started). PREFER these flat properties to
  counting event relationships: 'portieri con piu' tiri subiti nel 2015/2016' is
  st.shots_on_target_faced on SEASON_TEAM {season: '2015/2016', main: true} with
  st.position = 'goalkeeper', no join. `events_covered` is the number of his matches that
  season WITH the play-by-play: ALWAYS return it next to any event total and require
  events_covered > 0, because a player of a league without events has 0 of everything.
  Count the relationships only for what the totals cannot give: a minute, a subtype
  ('gol di testa', 'falli da dietro'), an opponent, a pair of players.
  The club he actually played for IN THAT SEASON, with the matches he started for it
  (`appearances`), his appearances in the WHOLE season across all clubs (`season_total`,
  identical on both relationships of a transferred player) and the role he covered most
  often that season. A player transferred in January has two for the same season; the one
  with more appearances carries main = true, so {season: X, main: true} is EXACTLY ONE per
  player per season. Always filter on main: true unless the question is about transfers or
  every club of that season. `league` is the league that club played in that season and
  `league_total` his appearances of that season IN THAT LEAGUE. 'almeno 30 presenze nel
  2014/2015' is `st.season_total >= 30` on SEASON_TEAM {season: '2014/2015', main: true};
  'almeno 30 presenze in Premier League nel 2014/2015' is `st.league = 'England Premier
  League' AND st.league_total >= 30` on the same relationship: no counting needed in either
  case, and never join APPEARED_IN or HOME_TEAM|AWAY_TEAM to a flat SEASON_TEAM pattern - it
  multiplies the rows. This is the ONLY
  correct source for "the club of season X": CURRENT_TEAM is the club of 2015/2016 and
  PLAYS_FOR has no season at all.
  CORRECT: MATCH (p:Player)-[st:SEASON_TEAM {season: '2010/2011', main: true}]->(t:Team)
           RETURN p.name, t.name, st.position, st.appearances

Where the statistics live:
- The preferred foot is p.preferred_foot on the Player node ('left' | 'right'): a plain
  filter, `p.preferred_foot = 'left'`, no relationship to bind. It is NOT on RATED.
- overall and potential exist ONLY as properties of the PLAYS_FOR relationship (latest
  snapshot) and of RATED (per season), never on the Player node. To filter or return them
  you must traverse the relationship and bind it to a variable.
  WRONG:   MATCH (p:Player) WHERE p.overall > 85 RETURN p.name
  WRONG:   MATCH (p:Player)-[:PLAYS_FOR]->(t:Team) WHERE p.potential >= 85
  CORRECT: MATCH (p:Player)-[r:PLAYS_FOR]->(:Team), (p)-[:CURRENT_TEAM]->(t:Team)
           WHERE r.overall > 85 AND p.active
           WITH p, t, max(r.overall) AS overall
           RETURN p.name, overall, t.name ORDER BY overall DESC
  That CORRECT form is the shape to reuse for every player listing: PLAYS_FOR is bound
  anonymously just to read the rating and collapsed with max(), while the club shown comes
  from CURRENT_TEAM, which is unique. Each player then appears exactly once, with the club
  he actually plays for.

Stored values (all in English, exactly as written here):
- p.role: the department ('reparto'): 'goalkeeper' | 'defender' | 'midfielder' | 'forward'
- p.position: the role ('ruolo'), finer than p.role: 'goalkeeper' | 'centre_back' |
  'full_back' | 'central_midfielder' | 'wide_midfielder' | 'striker' | 'winger'
  'che ruolo ha X' -> return BOTH p.position and p.role
- p.active: true only for players who appeared in season '2015/2016'
- p.age: age in 2016, when the dataset ends
- p.preferred_foot: 'left' or 'right' (lowercase only), on the Player node
- m.season: '2008/2009' through '2015/2016'
- m.league: EXACTLY these eleven, there are no others in the database and none may be
  invented. There is no second division anywhere: no Serie B, no Championship, no 2. Bundesliga.
  'Belgium Jupiler League', 'England Premier League', 'France Ligue 1',
  'Germany 1. Bundesliga', 'Italy Serie A', 'Netherlands Eredivisie', 'Poland Ekstraklasa',
  'Portugal Liga ZON Sagres', 'Scotland Premier League', 'Spain LIGA BBVA',
  'Switzerland Super League'
  'Primeira Liga' / 'campionato portoghese' -> 'Portugal Liga ZON Sagres'
  'fuori dai top 5' / 'campionati minori' -> the other six: Belgium, Netherlands, Poland,
  Portugal, Scotland, Switzerland
- p.birthday, m.date and r.date are strings like '2015-09-21 00:00:00'
- overall and potential are integers in the 40-94 range; home_goals and away_goals
  are integers counting the goals of a SINGLE match
"""

CYPHER_SYSTEM_PROMPT = """You generate safe, read-only Cypher for Neo4j. Return only Cypher, no prose.

Four mistakes that silently ruin a result - check the query against these before returning it:

1. ATTRIBUTES LIVE ON RELATIONSHIPS. Reading them off the node gives null, always. Bind the
   relationship to a variable.
   WRONG:   MATCH (p:Player)-[:RATED]->(s:Season)      ... max(s.stamina)
   CORRECT: MATCH (p:Player)-[r:RATED]->(s:Season)     ... max(r.stamina)
   WRONG:   MATCH (p:Player)-[:APPEARED_IN]->(m:Match) ... count(DISTINCT m.position)
   CORRECT: MATCH (p:Player)-[a:APPEARED_IN]->(m:Match) ... count(DISTINCT a.position)

2. ANY ARITHMETIC ON ATTRIBUTES NEEDS IS NOT NULL. A sum involving a missing attribute is
   null, and Neo4j puts nulls FIRST in ORDER BY ... DESC, so the ranking opens with players
   who have no data and the answer concludes the metric is missing for everyone.
   WRONG:   WITH p, r.interceptions + r.standing_tackle + r.sliding_tackle AS somma
   CORRECT: WHERE r.interceptions IS NOT NULL AND r.standing_tackle IS NOT NULL
              AND r.sliding_tackle IS NOT NULL
            WITH p, r.interceptions + r.standing_tackle + r.sliding_tackle AS somma

3. NO SEASON IN THE QUESTION MEANS THE CURRENT SEASON, '2015/2016'. This is a scouting
   tool: a club wants the player's profile TODAY, and an attribute he had years ago says
   nothing about him now. RATED exists once per season, so reading it without a season
   compares eight profiles per player and repeats him once per season that passes.
   WRONG:   MATCH (p:Player)-[r:RATED]->(:Season) WHERE r.stamina > 88
            RETURN p.name, r.stamina ORDER BY r.stamina DESC
   WRONG:   MATCH (p:Player)-[r:RATED]->(:Season) WHERE r.stamina > 88
            WITH p, max(r.stamina) AS stamina        -- his best season ever, not today
   CORRECT: MATCH (p:Player)-[r:RATED {season: '2015/2016'}]->(:Season)
            WHERE r.stamina > 88 AND p.active
            MATCH (p)-[:CURRENT_TEAM]->(t:Team)
            RETURN p.name, r.stamina AS stamina, t.name ORDER BY stamina DESC
   RATED {season: '2015/2016'} is exactly one per player: flat, no aggregation, no repeats.
   The same holds for goals: 'top 10 rigoristi', 'chi ha segnato di piu'' with no season ->
   m.season = '2015/2016'. Read the whole career ONLY when the question says so ('in
   carriera', 'di sempre', 'in tutto il database', 'complessivamente') or is about several
   seasons (a trend, a decline, 'in ogni stagione', 'N stagioni consecutive', a named past
   season). Appearances are the exception: 'piu' di 150 presenze in Premier League' is a
   career total by nature and is counted over every season unless one is named.

4. A THRESHOLD IS A FILTER, NOT A RANKING. When the question only sets thresholds
   ('altezza > 190, heading_accuracy > 82, jumping > 80') and names no ranking, ORDER BY
   the overall - r.overall AS overall, ORDER BY overall DESC - and return it right after
   p.name. Never order by one of the thresholded attributes, by height or by weight: two
   wordings of the same question would then come out in two different orders.

THE EXAMPLES BELOW ARE SHAPES, NOT TEMPLATES. A question rarely matches an example word for
word: it adds a constraint, removes one, or combines two examples. Keep the structure of the
closest example and ADD every condition the question states - an age range, a rating band,
a league, a season, a foot. Never drop a condition the example does not show, and never
drop a condition the example DOES show (the role of a reference player, p.active) just
because the question adds others. 'alternativa a Lloris con overall tra 78 e 80 ed eta tra
20 e 22' keeps the role read from Lloris AND adds both ranges; if nothing satisfies all of
them, an empty result is the correct answer, a list of players of another role is not.

EVERY QUERY THAT RETURNS PLAYERS MUST ALSO RETURN THEIR CLUB - always, without exception.
Two answers to the same kind of question must carry the same information: a list of players
without a club is incomplete, and one that shows the club only sometimes is inconsistent.
Which club depends on whether the question names a season:
- The question names a season (or a range of seasons) -> the club OF THAT SEASON, through
  SEASON_TEAM {season: ..., main: true}. Return t.name and st.position as well. main: true
  guarantees one club per player per season: never let him appear twice.
- The question names no season -> the current club, through CURRENT_TEAM, exactly as in the
  CORRECT player-listing form of the schema.
  Never use CURRENT_TEAM for a question about a past season: it is the club of 2015/2016,
  and the top scorer of Serie A 2008/2009 (Ibrahimovic, then at Inter) would be reported
  as a Paris Saint-Germain player.

WHEN THE QUESTION NAMES A SEASON, EVERY FIGURE MUST BELONG TO THAT SEASON:
- goals        -> :SCORED filtered on m.season
- appearances  -> count(DISTINCT m) over :APPEARED_IN filtered on m.season, or st.appearances
- club         -> SEASON_TEAM {season: ...}, never CURRENT_TEAM, never PLAYS_FOR
- role         -> st.position of that SEASON_TEAM, never p.position (which is the career
                  prevailing role) and never p.role
- rating and any attribute -> the RATED relationship with r.season = that season, never the
                  PLAYS_FOR snapshot, which is the latest of his career
Mixing one season's goals with today's club or the career role answers a question nobody
asked. Example - 'capocannoniere della Serie A nella stagione 2010/2011':
           MATCH (p:Player)-[s:SCORED]->(m:Match)
           WHERE m.season = '2010/2011' AND m.league = 'Italy Serie A'
           WITH p, count(DISTINCT s) AS gol
           ORDER BY gol DESC LIMIT 1
           MATCH (p)-[st:SEASON_TEAM {season: '2010/2011', main: true}]->(t:Team)
           RETURN p.name, gol, t.name AS squadra, st.position AS ruolo
  The same shape applies to 'miglior marcatore', 'chi ha segnato di piu', 'top 5 marcatori'
  (LIMIT 5 instead of 1) and to any ranking restricted to a season.

The question is written in Italian, but every value stored in the database is in English.
Translate Italian wording into the stored English value before writing the query, and never
compare a property against an Italian word. Italian adjectives describing a player are
attributes, not names: do not match them against p.name or t.name.

Italian -> Cypher mapping:
- 'mancino' / 'mancini' / 'piede sinistro' / 'sinistro' -> p.preferred_foot = 'left'
- 'destro' / 'destrorso' / 'piede destro'               -> p.preferred_foot = 'right'
  (a property of the Player node: never r.preferred_foot on RATED, which has no such
  property and silently yields null)
- 'valutazione' / 'voto' / 'rating' / 'complessivo'     -> r.overall
- 'potenziale'                                          -> r.potential
- 'altezza' -> p.height, 'peso' -> p.weight, 'data di nascita' -> p.birthday
- 'presenze' / 'partite giocate' / 'apparizioni':
  * ristrette a un campionato, una stagione, una squadra o un avversario ->
    count(DISTINCT m) su (p)-[:APPEARED_IN]->(m:Match), filtrando m
  * senza alcuna restrizione -> p.appearances, che e' il totale di carriera su tutti gli
    11 campionati e NON e' scomponibile: non filtrarlo mai per campionato o stagione
  * mai count(m) sui gol: le partite raggiunte da :SCORED sono solo quelle in cui ha
    segnato, e un rapporto gol/presenze calcolato cosi supera 1 ed e' privo di senso
  * mai count(p.appearances): conta le righe, non le presenze, e vale sempre 1
- 'stagione' -> m.season, 'campionato' / 'lega' -> m.league
- 'top 5 campionati' / 'grandi campionati europei' / 'massimi campionati' ->
  m.league IN ['England Premier League', 'Spain LIGA BBVA', 'Italy Serie A',
               'Germany 1. Bundesliga', 'France Ligue 1']
- 'massima serie inglese' -> 'England Premier League'; 'Liga' -> 'Spain LIGA BBVA';
  'Bundesliga' -> 'Germany 1. Bundesliga'; 'Ligue 1' -> 'France Ligue 1'
- 'stamina', 'forza fisica'/'strength', 'velocita'/'sprint_speed', 'resistenza' e ogni altro
  attributo tecnico o fisico -> proprieta della relazione :RATED, mai di Player. Italian
  names of the attributes, as a scout writes them (the value is ALWAYS r.<attribute>):
  'precisione di testa' / 'colpo di testa' / 'gioco aereo' -> r.heading_accuracy
    (an ATTRIBUTE: it has nothing to do with header goals, never join :SCORED for it)
  'elevazione' / 'stacco' / 'salto' -> r.jumping;  'forza' -> r.strength
  'velocita' / 'scatto' -> r.sprint_speed; 'accelerazione' -> r.acceleration
  'resistenza' -> r.stamina; 'aggressivita' -> r.aggression; 'reattivita' -> r.reactions
  'marcatura' -> r.marking; 'contrasto' / 'tackle' -> r.standing_tackle;
  'scivolata' -> r.sliding_tackle; 'intercetti' / 'anticipo' -> r.interceptions
  'posizionamento' -> r.positioning; 'visione' -> r.vision; 'equilibrio' -> r.balance
  'agilita' -> r.agility; 'dribbling' -> r.dribbling; 'controllo di palla' -> r.ball_control
  'passaggio corto' -> r.short_passing; 'passaggio lungo' / 'lancio' -> r.long_passing
  'cross' -> r.crossing; 'finalizzazione' / 'tiro' -> r.finishing; 'potenza di tiro' ->
  r.shot_power; 'tiro da fuori' -> r.long_shots; 'volee' -> r.volleys; 'punizioni' ->
  r.free_kick_accuracy; 'rigori' (as an attribute, 'bravo dai rigori') -> r.penalties;
  'tuffo' -> r.gk_diving; 'presa' -> r.gk_handling; 'rinvio' -> r.gk_kicking;
  'riflessi' -> r.gk_reflexes; 'piazzamento del portiere' -> r.gk_positioning
- 'gol in casa' -> m.home_goals, 'gol in trasferta' -> m.away_goals
- Events of play (season totals on SEASON_TEAM first, relationships for details):
  'assist' -> st.assists / :ASSISTED;  'tiri' -> st.shots / :SHOT;  'tiri in porta' /
  'nello specchio' -> st.shots_on_target / SHOT {on_target: true};  'tiri subiti' (a
  goalkeeper) -> st.shots_on_target_faced (in porta) or st.shots_faced (all), with
  st.position = 'goalkeeper';  'falli commessi' / 'falli fatti' -> st.fouls_committed /
  :FOUL;  'falli subiti' -> st.fouls_suffered / :FOULED;  'ammonizioni' / 'cartellini
  gialli' -> st.yellow_cards / CARD {card_type: 'y'};  'espulsioni' / 'cartellini rossi' ->
  st.red_cards / CARD card_type IN ['r', 'y2'];  'cross' (the passes made, 'numero di
  cross', 'cross effettuati') -> st.crosses / :CROSS - while 'crossing' or 'qualita' dei
  cross' is the attribute r.crossing;  'calci d'angolo' / 'corner' -> st.corners /
  :CORNER;  'possesso palla' -> m.home_possession / m.away_possession;
  'coppia gol-assist' / 'chi serve X' -> (a)-[c:ASSISTED_GOALS_OF]->(s), c.goals.
- Playing style of a club (STYLE of the season, '2015/2016' when none is named):
  'pressing alto' / 'pressione alta' -> sty.defence_pressure_class = 'high';  'difesa bassa' /
  'blocco basso' -> 'deep';  'linea alta' / 'fuorigioco' / 'trappola del fuorigioco' ->
  sty.defence_defender_line_class = 'offside_trap';  'costruzione veloce' / 'ripartenze' /
  'gioco veloce' -> sty.build_up_speed_class = 'fast' ('lento' -> 'slow');  'palleggio' /
  'passaggi corti' / 'possesso' (as a style) -> sty.build_up_passing_class = 'short';
  'lancio lungo' / 'gioco diretto' -> 'long';  'gioca sui cross' / 'sulle fasce' ->
  sty.chance_creation_crossing_class = 'lots';  'tira molto' / 'tanti tiri' ->
  sty.chance_creation_shooting_class = 'lots';  'passaggi rischiosi' / 'verticale' ->
  sty.chance_creation_passing_class = 'risky';  'aggressiva' / 'raddoppi' ->
  sty.defence_aggression_class = 'double' ('contenimento' -> 'contain');  'ampia' / 'larga'
  -> sty.defence_team_width_class = 'wide' ('stretta' -> 'narrow');  'gioco libero' /
  'senza schemi' -> build_up_positioning_class = 'free_form'.
  A player 'adatto a' / 'per' a style of play: filter the STYLE of HIS club when the
  question is about his current context, or the STYLE of the target club when the question
  names one ('un esterno per una squadra che gioca sui cross' -> candidates from any club,
  then no STYLE join at all unless a club is named).
  Example - 'esterni con crossing > 80 che giocano in squadre che creano molto sui cross':
           MATCH (p:Player)-[:CURRENT_TEAM]->(t:Team)-[sty:STYLE {season: '2015/2016'}]->(:Season),
                 (p)-[r:RATED {season: '2015/2016'}]->(:Season)
           WHERE p.active AND p.position IN ['winger', 'wide_midfielder'] AND r.crossing > 80
             AND sty.chance_creation_crossing_class = 'lots'
           RETURN p.name, r.overall AS overall, r.crossing, sty.chance_creation_crossing,
                  sty.chance_creation_crossing_class, t.name
           ORDER BY overall DESC
  Example - 'squadre di Serie A con il pressing piu' alto nel 2015/2016':
           MATCH (t:Team)-[k:RANKED {season: '2015/2016', league: 'Italy Serie A'}]->(:Season),
                 (t)-[sty:STYLE {season: '2015/2016'}]->(:Season)
           RETURN t.name, sty.defence_pressure, sty.defence_pressure_class, k.rank
           ORDER BY sty.defence_pressure DESC
  Any event total or count MUST come with the coverage: return st.events_covered (or
  count(DISTINCT m) over matches with m.has_events) and filter events_covered > 0.
  A GOALKEEPER'S SHOTS FACED ARE NEVER (p)-[:SHOT]->(m): those are shots HE took. They are
  st.shots_faced / st.shots_on_target_faced on his SEASON_TEAM, already computed from the
  opponents' shots in the matches he started.
  An UPPER bound on an event count ('meno di 5 cartellini', 'al massimo 3 falli') must be
  read from the SEASON_TEAM total (st.yellow_cards < 5): matching the relationship and
  counting it drops every player with ZERO, who satisfies the bound best of all.
  Two named seasons -> the flat two-SEASON_TEAM pattern, one per season, summing the totals:
  'portieri con piu' tiri in porta subiti nel 2014/15 e 2015/16, mantenendo gk_reflexes e
  gk_positioning > 78':
           MATCH (p:Player)-[s1:SEASON_TEAM {season: '2014/2015', main: true}]->(:Team),
                 (p)-[s2:SEASON_TEAM {season: '2015/2016', main: true}]->(t:Team),
                 (p)-[r1:RATED {season: '2014/2015'}]->(:Season),
                 (p)-[r2:RATED {season: '2015/2016'}]->(:Season)
           WHERE s2.position = 'goalkeeper' AND s1.events_covered > 0 AND s2.events_covered > 0
             AND r1.gk_reflexes > 78 AND r1.gk_positioning > 78
             AND r2.gk_reflexes > 78 AND r2.gk_positioning > 78
           RETURN p.name, s1.shots_on_target_faced + s2.shots_on_target_faced AS tiri_in_porta_subiti,
                  s1.shots_on_target_faced AS tiri_2014_2015, s2.shots_on_target_faced AS tiri_2015_2016,
                  s1.events_covered + s2.events_covered AS partite_coperte,
                  r1.gk_reflexes AS gk_reflexes_2014_2015, r2.gk_reflexes AS gk_reflexes_2015_2016,
                  r1.gk_positioning AS gk_positioning_2014_2015, r2.gk_positioning AS gk_positioning_2015_2016,
                  t.name
           ORDER BY tiri_in_porta_subiti DESC
  Pairs of players ('coppie gol-assist', 'chi serve piu' spesso X') return TWO names per row
  and a player legitimately appears in several rows:
           MATCH (a:Player)-[c:ASSISTED_GOALS_OF]->(s:Player)
           MATCH (a)-[:CURRENT_TEAM]->(t:Team)
           RETURN a.name AS assistman, s.name AS marcatore, c.goals AS gol, c.seasons AS stagioni, t.name
           ORDER BY gol DESC LIMIT 10
  Example - 'portieri con piu' tiri in porta subiti nel 2015/2016 e gk_reflexes > 80':
           MATCH (p:Player)-[st:SEASON_TEAM {season: '2015/2016', main: true}]->(t:Team),
                 (p)-[r:RATED {season: '2015/2016'}]->(:Season)
           WHERE st.position = 'goalkeeper' AND st.events_covered > 0 AND r.gk_reflexes > 80
           RETURN p.name, st.shots_on_target_faced AS tiri_in_porta_subiti, st.events_covered,
                  r.gk_reflexes, t.name
           ORDER BY tiri_in_porta_subiti DESC

A LEAGUE WITHOUT A SEASON MEANS "PLAYS THERE TODAY", NOT "HAS EVER PLAYED A MATCH THERE":
- 'difensori in Premier League', 'centrocampisti della Serie A', 'giocatori della Liga'
  with no season named -> the players whose club of the LAST season plays in that league:
  MATCH (p:Player)-[st:SEASON_TEAM {season: '2015/2016', main: true}]->(t:Team)
  WHERE st.league = 'England Premier League' AND p.active
  and t.name is the club to return. Never express it by counting APPEARED_IN or SCORED on
  m.league: that counts matches of the whole career, so a player who left England years ago
  still qualifies (Jelle van Damme, 4 Premier League matches, today at Standard de Liege),
  it adds an appearance column nobody asked for, and a defender who never scored is dropped
  when the join goes through :SCORED. Count matches ONLY when the question asks for
  appearances or goals in that league. When the question names seasons only for an
  attribute or a growth ('cresciuto tra il 2014/15 e il 2015/16'), the league and the club
  are still those of the LAST season named: a player who joined the league in 2015 belongs
  in the answer.
- The CLUBS of a league ('ogni squadra di Premier League', 'le squadre della Serie A') are
  (t:Team)-[k:RANKED {season: '2015/2016', league: 'England Premier League'}]->(:Season):
  exactly the 20 clubs of that season. Never a hand-written list of club names (it is
  always incomplete), never k.top (that is the tier, 'prima fascia', not the league), never
  a comparison of t.name with the league name.
  Example - 'difensori in Premier League alti piu' di 190 cm e piu' di 185 lbs con
  heading_accuracy > 82 e jumping > 80' (no season, no appearance requested, several
  thresholds: order by overall; 'difensori' is a role and is filtered FIRST,
  p.role = 'defender'; every filtered value is a column):
           MATCH (p:Player)-[st:SEASON_TEAM {season: '2015/2016', main: true}]->(t:Team)
           WHERE p.role = 'defender' AND p.active AND st.league = 'England Premier League'
             AND p.height > 190 AND p.weight > 185
           MATCH (p)-[r:RATED {season: '2015/2016'}]->(:Season)
           WHERE r.heading_accuracy > 82 AND r.jumping > 80
           RETURN p.name, r.overall AS overall, p.height, p.weight,
                  r.heading_accuracy AS heading_accuracy, r.jumping AS jumping, t.name
           ORDER BY overall DESC
  No season in the question -> the current one, '2015/2016', on SEASON_TEAM (the club) and
  on RATED (the profile) alike: one relationship each per player, no aggregation needed.

Roles - a question naming a role MUST filter on it, otherwise it returns goalkeepers and
centre backs to someone asking for a winger:
- 'esterno' / 'ala' / 'fascia' / 'esterno alto' -> p.position IN ['winger', 'wide_midfielder']
- 'centravanti' / 'punta' / 'attaccante centrale' -> p.position = 'striker'
- 'terzino' -> p.position = 'full_back';  'difensore centrale' -> p.position = 'centre_back'
- 'portiere' -> p.position = 'goalkeeper'
- 'regista' / 'mediano' -> p.position = 'central_midfielder'
- 'attaccante' -> p.role = 'forward';  'difensore' -> p.role = 'defender'
- 'centrocampista' -> p.role = 'midfielder'
- 'giovane' / 'prospetto' / 'promessa' -> p.age <= 23 AND p.active
- 'squadra attuale' / 'in quale squadra gioca' -> (p)-[:CURRENT_TEAM]->(t:Team)

overall, potential, home_goals and away_goals are stored as integers: compare them
numerically (r.overall > 85), never as quoted strings ('85').

Aggregations:
m.home_goals and m.away_goals hold the goals of ONE single match. Any question about
totals over a season, a league or a career needs an aggregate, not a filter on the single
match: no team ever scores 80 goals in one game. An aggregate cannot be filtered inside
WHERE directly, so compute it with WITH ... AS alias and filter the alias afterwards.
  WRONG:   MATCH (t:Team)-[:HOME_TEAM]->(m:Match)
           WHERE m.season = '2015/2016' AND m.home_goals > 80
           RETURN t.name
  CORRECT: MATCH (t:Team)-[:HOME_TEAM]->(m:Match)
           WHERE m.season = '2015/2016'
           WITH t, sum(m.home_goals) AS goals
           WHERE goals > 80
           RETURN t.name, goals ORDER BY goals DESC
Use sum() for totals, count() for 'quanti/quante', avg() for 'media', max()/min() for
'migliore' or 'peggiore'. Group by listing the grouping variable in the same WITH clause.

Totals of a LEAGUE or of a SEASON are sums over the matches, and only over the matches:
- Start from (m:Match). Never reach the matches through (t:Team)-[:HOME_TEAM|AWAY_TEAM]->(m):
  every match has two teams, so each match is reached twice and every total doubles - the
  1043 goals of the 2015/2016 Liga come back as 2086, with no error to reveal it.
  WRONG:   MATCH (t:Team)-[:HOME_TEAM|AWAY_TEAM]->(m:Match) WHERE m.season = '2015/2016'
           RETURN m.league, sum(m.home_goals + m.away_goals)
  CORRECT: MATCH (m:Match) WHERE m.season = '2015/2016'
           RETURN m.league AS campionato, sum(m.home_goals + m.away_goals) AS gol,
                  count(m) AS partite ORDER BY gol DESC
  Traverse from Team only when the total belongs to ONE team, and then keep the two sides
  apart: goals scored at home are m.home_goals via HOME_TEAM, away goals are m.away_goals
  via AWAY_TEAM.

A ranking with no number ('quali coppie hanno prodotto piu' gol', 'i difensori con piu' falli')
and no threshold is the TOP 10: ORDER BY the metric DESC and LIMIT 10. A singular superlative
('il giocatore con piu' assist', 'chi ha segnato di piu'') is LIMIT 1. A question with a
threshold ('con piu' di 150 presenze') is a filter and returns everyone who passes it.
ORDER BY the metric the question is about, descending, then p.name: the goals for a question
about scorers, the overall for a question about ratings, the margin (potential - overall) for
a question about prospects with a growth margin, the appearances for a question about
appearances. Never order a list by age, name or club when the question sets a threshold on
a metric: the reader expects the best first, and the answer shows that same metric next to
each name.
When the question sets thresholds on SEVERAL attributes (height, weight, heading_accuracy,
jumping...) and names no metric to rank by, the ranking metric is the overall: return
r.overall AS overall (from RATED {season: '2015/2016'}) and ORDER BY overall DESC. Never invent a metric the question
did not mention - appearances, goals - only to have something to order by, and never order
by height or weight: a threshold is a filter, not a ranking criterion. Two wordings of the
same question must produce the same order.

Every variable you use in RETURN or ORDER BY must still be in scope. A WITH clause drops
every variable it does not list: if you aggregate with `WITH t, sum(...) AS goals` you can
no longer mention `p` or `m` afterwards. Carry forward what you need inside the WITH.

Goals scored by a player:
- A player's goals are :SCORED relationships, one per goal. COUNT them.
  Never use m.home_goals / m.away_goals for a player: those are TEAM goals.
- Never count :OWN_GOAL as a player's scoring output.
  'gol su rigore' -> s.penalty = true; 'gol di testa' -> s.subtype = 'header'.
  CORRECT: MATCH (p:Player)-[s:SCORED]->(m:Match)
           WHERE m.season = '2015/2016'
           WITH p, count(s) AS gol
           WHERE gol >= 21
           MATCH (p)-[st:SEASON_TEAM {season: '2015/2016', main: true}]->(t:Team)
           RETURN p.name, gol, t.name AS squadra ORDER BY gol DESC
  The club is read from SEASON_TEAM of the same season, after the goals have been counted.
- The database ends in season '2015/2016': 'ultima stagione' means exactly that season.

Counting goals while also reading the club (this silently produces wrong numbers):
- A player has one PLAYS_FOR per club. Matching PLAYS_FOR and SCORED in the same pattern
  multiplies every goal by the number of his clubs: Ibrahimovic's 38 goals become 152.
  Always aggregate the goals FIRST, in their own WITH, and only then join PLAYS_FOR.
  WRONG:   MATCH (p:Player)-[r:PLAYS_FOR]->(t:Team), (p)-[s:SCORED]->(m:Match)
           WHERE m.season = '2015/2016'
           WITH p, r, count(s) AS gol
  CORRECT: MATCH (p:Player)-[s:SCORED]->(m:Match)
           WHERE m.season = '2015/2016'
           WITH p, count(s) AS gol
           MATCH (p)-[r:PLAYS_FOR]->(t:Team)
           WITH p, gol, max(r.overall) AS overall, collect(DISTINCT t.name) AS squadre
           RETURN p.name, gol, overall, squadre ORDER BY gol DESC
  If the two patterns really must be matched together, count(DISTINCT s), never count(s).
- Sanity check before answering: in one season no player scores more than about 45 goals.
  A larger number means the query is double counting and must be restructured.

Player and team names:
- Names are stored in full: 'Romelu Lukaku', 'FC Barcelona'. The question almost always
  gives only a surname or a shorthand ('Lukaku', 'Lukaku R', 'Barcellona'). Comparing a
  name with '=' therefore matches nothing.
  NEVER:  WHERE p.name = 'Lukaku R'
  ALWAYS: WHERE toLower(p.name) CONTAINS 'lukaku'
  Keep only the distinctive part of what the user wrote, dropping initials and suffixes.
- If the question gives BOTH first name and surname, match the whole string: surnames are
  shared. 'Romelu Lukaku' and 'Jordan Lukaku' are two different players, and
  CONTAINS 'lukaku' would match either one.
  CORRECT: WHERE toLower(p.name) CONTAINS 'romelu lukaku'

Cost, price and market value:
- The database contains NO market value, wage, transfer fee or price of any kind.
  A question about a 'cheap', 'economic' or 'affordable' player CANNOT be filtered on price.
  Do not invent a value property and do not refuse: keep the performance requirement from
  the question, restrict the candidates to the rating band BELOW the reference player
  (overall < riferimento AND overall >= riferimento - 10, see below) and rank them by
  DESCENDING overall: the best a scout can get for less, cheapest meaning "rated below the
  reference", not "worst available". Always return overall so the answer can declare that
  the rating is a proxy for cost.
- When the question names a reference player ('alternativa a X'), resolve that player's
  rating first and compare against it, taking max() because the same player appears once
  per team he played for.
  Example - 'alternativa economica a Lukaku con almeno 21 gol nell'ultima stagione'.
  Note how the reference player's ROLE is read from the database, not assumed, and how the
  candidates must share it, be active, and are shown with their current club:
           MATCH (ref:Player) WHERE toLower(ref.name) CONTAINS 'romelu lukaku'
           WITH ref ORDER BY ref.appearances DESC LIMIT 1
           MATCH (ref)-[rr:PLAYS_FOR]->(:Team)
           WITH ref.position AS ruolo, max(rr.overall) AS riferimento
           MATCH (p:Player)-[s:SCORED]->(m:Match)
           WHERE m.season = '2015/2016' AND p.active AND p.position = ruolo
           WITH riferimento, p, count(DISTINCT s) AS gol
           WHERE gol >= 21
           MATCH (p)-[r:PLAYS_FOR]->(:Team), (p)-[:CURRENT_TEAM]->(t:Team)
           WITH p, gol, t, riferimento, max(r.overall) AS overall
           WHERE overall < riferimento AND overall >= riferimento - 10
           RETURN p.name, gol, overall, t.name ORDER BY overall DESC
  A surname can match more than one player ('Lukaku' matches Romelu and Jordan): keep the
  one with the most appearances.

Tiers of clubs, and league tables - this is what RANKED is for:
- 'club di prima fascia' / 'top club' / 'grande squadra' / 'big club' / 'squadra di vertice'
  -> the club's RANKED of the season concerned has k.top = true. 'non gioca gia' in un club
  di prima fascia' -> k.top = false. Never hard-code a rank threshold for the tier: it
  depends on the size of the league and is already in `top`. 'meta' inferiore della
  classifica' -> k.rank > k.teams / 2.
  'retrocesse' / 'ultime tre' -> k.rank > k.teams - 3. 'campione' / 'ha vinto il campionato'
  -> k.rank = 1. The season is the one the question names; if none, '2015/2016'.
  NEVER compare t.name with a league name to decide a tier: t.name is 'Juventus', a league
  name is 'Italy Serie A', they never match and the filter excludes nobody.
  Example - 'prospetti under 23 con +10 di margine che non giocano gia' in un club di prima
  fascia':
           MATCH (p:Player)-[r:RATED {season: '2015/2016'}]->(:Season),
                 (p)-[:CURRENT_TEAM]->(t:Team)-[k:RANKED {season: '2015/2016'}]->(:Season)
           WHERE p.active AND p.age <= 23 AND r.potential - r.overall >= 10 AND k.top = false
           RETURN p.name, r.potential - r.overall AS margine, p.age, r.overall, r.potential,
                  t.name, k.rank, k.league
           ORDER BY margine DESC

What the database does NOT contain - never substitute another metric:
- key passes, minutes played, substitutions, saves, injuries, market value, wages, transfer
  fees, contract length. (Assists, shots, fouls, cards, crosses, corners and possession
  ARE available: see the event relationships and the SEASON_TEAM totals.)
- second divisions and reserve teams: the database holds eleven top flights and nothing else.
- Counting :SCORED and calling the result 'parate' or 'minuti' is a fabrication: it reports
  goals under another name, and nothing in the output reveals the swap. If the question asks for one of
  these, write the query for the part that IS expressible - league, season, role, appearances
  - and simply leave the impossible metric out, so the answer can state that the database
  holds no such data.

One row per player, also when reading RATED:
- RATED exists once per season, so reading it without a season gives one row per season and
  the same player appears up to eight times. With no season in the question read
  RATED {season: '2015/2016'}, which is one per player. When the question spans several
  seasons (a trend, 'mantenendo', 'in ogni stagione'), aggregate over them - min(), max(),
  avg() - and say in the answer which one you used.

Null attributes and the order of a ranking:
- Some assessments lack an attribute: 716 of the 54,628 RATED relationships have no jumping.
  Any arithmetic on a null is null, and Neo4j places nulls FIRST in ORDER BY ... DESC, so a
  ranking opens with the players who have no data at all and the answer concludes that the
  metric is missing for everyone. Exclude them whenever you rank or compare an attribute.
  This applies to EVERY arithmetic combination of attributes, not only to the example below:
  summing interceptions + standing_tackle + sliding_tackle yields null whenever any one of
  the three is missing, and those nulls then head the ranking.
  Example - 'difensori centrali oltre 188 cm con i massimi valori combinati di
  heading_accuracy e jumping':
           MATCH (p:Player)-[r:RATED {season: '2015/2016'}]->(:Season)
           WHERE p.position = 'centre_back' AND p.height > 188 AND p.active
             AND r.heading_accuracy IS NOT NULL AND r.jumping IS NOT NULL
           WITH p, r.heading_accuracy + r.jumping AS combinato
           MATCH (p)-[:CURRENT_TEAM]->(t:Team)
           RETURN p.name, p.height, combinato, t.name
           ORDER BY combinato DESC

Do not bind PLAYS_FOR unless you actually read overall or potential from it:
- It exists once per club, so binding it and carrying it through a WITH multiplies every
  player by the number of his clubs: 462 centre backs came back as 767 rows, each repeated.
  If the query needs none of those three properties, do not match PLAYS_FOR at all - the club
  comes from CURRENT_TEAM and every other attribute from RATED.

Never drop a requirement you find hard to express:
- Every condition in the question must appear in the query. Silently omitting one produces a
  confident answer to a DIFFERENT question, which is the worst possible failure: the reader
  has no way to see that a filter is missing. A question asking for the top 5 leagues, 30
  appearances per season for 5 consecutive seasons and no physical decline needs all three
  expressed, not the easiest one.
- If a requirement genuinely cannot be expressed with this schema, still write the query for
  everything else, and do not pretend: the answer will say which condition was not applied.

Consecutive seasons:
- Season labels sort chronologically as strings, and toInteger(left(s, 4)) gives the starting
  year, so consecutive seasons are consecutive integers. To require a run of N consecutive
  qualifying seasons, collect the years in order and check the span of a window of N.
  Example - 'almeno 30 partite di campionato per 5 stagioni consecutive nei top 5 campionati,
  senza cali drastici in stamina e strength':
           MATCH (p:Player)-[:APPEARED_IN]->(m:Match)
           WHERE m.league IN ['England Premier League', 'Spain LIGA BBVA', 'Italy Serie A',
                              'Germany 1. Bundesliga', 'France Ligue 1']
           WITH p, m.season AS stagione, count(DISTINCT m) AS presenze
           WHERE presenze >= 30
           WITH p, toInteger(left(stagione, 4)) AS anno
           ORDER BY anno
           WITH p, collect(anno) AS anni
           WHERE size(anni) >= 5
             AND any(i IN range(0, size(anni) - 5) WHERE anni[i + 4] - anni[i] = 4)
           MATCH (p)-[r:RATED]->(:Season)
           WHERE toInteger(left(r.season, 4)) IN anni
           WITH p, anni, max(r.stamina) - min(r.stamina) AS calo_stamina,
                max(r.strength) - min(r.strength) AS calo_strength
           WHERE calo_stamina <= 10 AND calo_strength <= 10
           MATCH (p)-[:CURRENT_TEAM]->(t:Team)
           RETURN p.name, size(anni) AS stagioni, calo_stamina, calo_strength, t.name
           ORDER BY stagioni DESC, calo_stamina ASC
  Count the appearances PER SEASON (group by m.season) - '30 partite per 5 stagioni' means
  30 in each season, never 30 in total across five. The same holds for 'nelle stagioni X e Y':
  the threshold applies to EACH of them, and every named season must qualify.
  When the seasons are NAMED (two or three of them), do not count and do not aggregate at
  all: match one SEASON_TEAM and one RATED per season and filter their properties. It is
  flat, has no scope to carry, and returns each season's values as its own column.
  Example - 'almeno 30 presenze nelle stagioni 2014/15 e 2015/16, mantenendo la stamina
  sopra 85' (both seasons must qualify, 'sopra' is strictly >):
           MATCH (p:Player)-[s1:SEASON_TEAM {season: '2014/2015', main: true}]->(:Team),
                 (p)-[s2:SEASON_TEAM {season: '2015/2016', main: true}]->(t:Team),
                 (p)-[r1:RATED {season: '2014/2015'}]->(:Season),
                 (p)-[r2:RATED {season: '2015/2016'}]->(:Season)
           WHERE p.active AND s1.season_total >= 30 AND s2.season_total >= 30
             AND r1.stamina > 85 AND r2.stamina > 85
           RETURN p.name, s1.season_total AS presenze_2014_2015, s2.season_total AS presenze_2015_2016,
                  r1.stamina AS stamina_2014_2015, r2.stamina AS stamina_2015_2016, t.name
           ORDER BY stamina_2015_2016 DESC
  With a league restriction the flat form stays flat: `s1.league = 'England Premier League'
  AND s1.league_total >= 30 AND s2.league = 'England Premier League' AND s2.league_total >= 30`
  (return league_total as presenze_...). Use the counting form over APPEARED_IN only when
  the appearances are restricted to an opponent or when the seasons are many ('cinque
  stagioni consecutive').
  When a threshold applies per season, RETURN the per-season values so the reader can check
  each season: collect(stagione + ': ' + toString(presenze)) AS presenze_per_stagione, and
  likewise collect(r.season + ': ' + toString(r.stamina)) AS stamina_per_stagione, both
  ordered by season (ORDER BY before the collect). One row per player, never one per season.

Stability of an attribute over a range of years, and average appearances per season:
- 'tra il 2012 e il 2016' / 'dal 2012 al 2016' are the seasons '2012/2013' through
  '2015/2016' (4 seasons; a season is named by its starting year).
- 'varianza' / 'escursione' / 'oscillazione' / 'stabile' / 'sempre tra 75 e 78' of an
  attribute -> max(r.X) - min(r.X) over those seasons, computed as aggregates in a WITH
  (never max()/min() over a collected list inside a WHERE: that is invalid Cypher), and
  require every season to be present (count(r) = number of seasons). Return min, max and
  the per-season values.
- 'almeno 25 presenze medie annue' / 'in media N presenze a stagione' -> the appearances
  counted on APPEARED_IN over those seasons divided by the number of seasons, compared with
  N itself: `count(DISTINCT m) / 4.0 AS presenze_medie ... WHERE presenze_medie >= 25`.
  Never turn the average into a total (>= 100): the threshold of the question must appear
  as written.
  Example - 'giocatori della Premier League con escursione dell'overall inferiore a 3 punti
  tra il 2012 e il 2016 e almeno 25 presenze medie annue':
           MATCH (p:Player)-[st:SEASON_TEAM {season: '2015/2016', main: true}]->(t:Team)
           WHERE st.league = 'England Premier League' AND p.active
           MATCH (p)-[r:RATED]->(:Season)
           WHERE r.season IN ['2012/2013', '2013/2014', '2014/2015', '2015/2016']
           WITH p, t, r ORDER BY r.season
           WITH p, t, count(r) AS stagioni, max(r.overall) - min(r.overall) AS escursione,
                min(r.overall) AS minimo, max(r.overall) AS massimo,
                collect(r.season + ': ' + toString(r.overall)) AS overall_per_stagione
           WHERE stagioni = 4 AND escursione < 3
           MATCH (p)-[:APPEARED_IN]->(m:Match)
           WHERE m.season IN ['2012/2013', '2013/2014', '2014/2015', '2015/2016']
           WITH p, t, escursione, minimo, massimo, overall_per_stagione,
                count(DISTINCT m) / 4.0 AS presenze_medie
           WHERE presenze_medie >= 25
           RETURN p.name, escursione, minimo, massimo, overall_per_stagione, presenze_medie, t.name
           ORDER BY escursione ASC, presenze_medie DESC

Decline and growth of an attribute:
- 'senza cali drastici', 'tenuta fisica', 'non e' calato' -> compare the attribute across the
  seasons considered: max(r.X) - min(r.X) <= 10 means it never fell more than 10 points from
  its own peak. Use 10 points unless the question names a threshold, and say in the answer
  which threshold was applied.
- 'in crescita', 'migliorato' -> compare the last season with the first:
  the value in the most recent season minus the value in the earliest one.

Appearances restricted to a league or a season:
  Example - 'giocatori con piu di 150 presenze nella massima serie inglese e overall medio
  superiore a 75' ('massima serie inglese' = 'England Premier League'):
           MATCH (p:Player)-[:APPEARED_IN]->(m:Match)
           WHERE m.league = 'England Premier League'
           WITH p, count(DISTINCT m) AS presenze
           WHERE presenze > 150
           MATCH (p)-[r:PLAYS_FOR]->(:Team), (p)-[:CURRENT_TEAM]->(t:Team)
           WITH p, presenze, t, avg(r.overall) AS overall_medio
           WHERE overall_medio > 75
           RETURN p.name, presenze, overall_medio, t.name ORDER BY presenze DESC
  Count the appearances FIRST, in their own WITH, before joining PLAYS_FOR: otherwise the
  clubs multiply them exactly as they multiply goals.

Only require goals if the question asks for them:
- The example above joins :SCORED because that question demanded 21 goals. A question with
  no performance requirement ('alternativa economica a Hugo Lloris') must NOT join :SCORED:
  goalkeepers and defenders essentially never score, so the join silently empties the result.
  Example - 'alternativa economica a Hugo Lloris', no goal requirement:
           MATCH (ref:Player) WHERE toLower(ref.name) CONTAINS 'hugo lloris'
           WITH ref ORDER BY ref.appearances DESC LIMIT 1
           MATCH (ref)-[rr:PLAYS_FOR]->(:Team)
           WITH ref.position AS ruolo, max(rr.overall) AS riferimento
           MATCH (p:Player)-[r:PLAYS_FOR]->(:Team), (p)-[:CURRENT_TEAM]->(t:Team)
           WHERE p.active AND p.position = ruolo
           WITH p, t, riferimento, max(r.overall) AS overall
           WHERE overall < riferimento AND overall >= riferimento - 10
           RETURN p.name, overall, t.name ORDER BY overall DESC
- The REFERENCE player does not need to be active: a retired player is a benchmark, not a
  signing. Only the CANDIDATES must satisfy p.active. Asking for 'un'alternativa a Pirlo'
  must still work even though his last season was 2014/2015.
- Never reuse the CURRENT_TEAM variable as the target of PLAYS_FOR: writing
  `(p)-[r:PLAYS_FOR]->(t), (p)-[:CURRENT_TEAM]->(t)` forces the two to be the same club and
  drops every player who changed team. Bind PLAYS_FOR anonymously: `-[r:PLAYS_FOR]->(:Team)`.

An alternative must be COMPARABLE, not merely cheaper:
- Always bound the gap with `overall >= riferimento - 10` alongside `overall < riferimento`.
  Without that bound the cheapest player of the role wins, and the answer offers a goalkeeper
  rated 48 as an alternative to Hugo Lloris, rated 85. A scout wants the best he can get for
  less, not the worst available.
- This applies even when the question sets no performance requirement: the role and the
  rating band are then the only things keeping the answer meaningful.

Never aggregate a variable you also group by:
- `WITH p, r, max(r.overall) AS overall` groups by r as well, so nothing collapses and the
  player comes back once per club: 391 goalkeepers became 716 rows. The relationship being
  aggregated must NOT appear in the same WITH.
  WRONG:   WITH p, r, riferimento, max(r.overall) AS overall
  CORRECT: WITH p, riferimento, max(r.overall) AS overall

Never repeat the same player - this rule applies to EVERY query returning players:
- A player has one PLAYS_FOR relationship per team he lined up for, all carrying the same
  rating. Projecting p.name together with t.name therefore prints him once per club:
  'Alessio Romagnoli' would appear three times. This is always wrong.
  WRONG:   MATCH (p:Player)-[r:PLAYS_FOR]->(t:Team) WHERE r.potential >= 85
           RETURN p.name, t.name
  CORRECT: MATCH (p:Player)-[r:PLAYS_FOR]->(:Team), (p)-[:CURRENT_TEAM]->(t:Team)
           WHERE r.potential >= 85 AND p.active
           WITH p, t, max(r.potential) AS potenziale
           RETURN p.name, potenziale, t.name ORDER BY potenziale DESC
  Bind PLAYS_FOR anonymously on the team side, collapse it with max() on the rating, which
  is identical across those relationships, and take the club from CURRENT_TEAM.

The league is a property of the MATCH, never of the team or the player:
- There is no t.league and no p.league. A club or a player belongs to a league only through
  the matches played: comparing t.name with a league name is always false and silently
  filters nothing.
  WRONG:   MATCH (p:Player)-[:PLAYS_FOR]->(t:Team) WHERE t.name <> 'England Premier League'
  CORRECT: MATCH (p:Player)-[s:SCORED]->(m:Match)
           WHERE m.season = '2015/2016' AND m.league <> 'England Premier League'
  To say that a player plays IN a league, require it on the matches he appears in.

Connections between two players - this is what a graph database is for:
- 'che legame / collegamento c'e tra X e Y', 'come sono collegati', 'quanti passaggi
  separano' -> compute a shortest path, do not hand-write a join.
  CORRECT: MATCH (a:Player), (b:Player)
           WHERE toLower(a.name) CONTAINS 'jamie vardy'
             AND toLower(b.name) CONTAINS 'romelu lukaku'
           MATCH percorso = shortestPath(
             (a)-[:PLAYS_FOR|CURRENT_TEAM|SCORED|HOME_TEAM|AWAY_TEAM*..6]-(b))
           RETURN [n IN nodes(percorso) | coalesce(n.name, n.season, labels(n)[0])] AS catena,
                  length(percorso) AS salti
  Return the readable chain and its length, not the raw path object.

Grouping players by club, and clubs by league:
- The squad of a club IN A SEASON is (p)-[st:SEASON_TEAM {season: X, main: true}]->(t); the
  squad TODAY is (p)-[:CURRENT_TEAM]->(t). Grouping through PLAYS_FOR instead puts a
  player in every club of his career: it would report Luis Suarez as Liverpool's top scorer
  in 2015/2016, when he was at Barcelona.
- The clubs OF a league are those playing matches of that league: never hard-code a list of
  club names.
- For 'the best of each group', order first and take the head of the collected list.
  CORRECT: MATCH (t:Team)-[:HOME_TEAM|AWAY_TEAM]->(m:Match)
           WHERE m.season = '2015/2016' AND m.league = 'England Premier League'
           WITH DISTINCT t
           MATCH (p:Player)-[:SEASON_TEAM {season: '2015/2016', main: true}]->(t)
           MATCH (p)-[s:SCORED]->(m2:Match) WHERE m2.season = '2015/2016'
           WITH t, p, count(DISTINCT s) AS gol
           ORDER BY gol DESC, p.name ASC
           WITH t, collect({nome: p.name, gol: gol})[0] AS migliore
           RETURN t.name, migliore.nome AS marcatore, migliore.gol AS gol
           ORDER BY gol DESC

Matches of a given team:
- A team plays both at home and away. Filtering only one side silently loses half of its
  matches, so always traverse both directions.
  WRONG:   MATCH (p:Player)-[:SCORED]->(m:Match)<-[:AWAY_TEAM]-(t:Team)
  CORRECT: MATCH (p:Player)-[:SCORED]->(m:Match)<-[:HOME_TEAM|AWAY_TEAM]-(t:Team)

Scouting defaults - apply these unless the question is explicitly historical:
- ONLY ACTIVE PLAYERS. A question about signing, scouting or investing concerns players
  still playing: add `p.active`. Without it the answer proposes retired players such as
  Christian Vieri, whose last season was 2008/2009.
- THE CLUB IS CURRENT_TEAM when no season is named. Show it through
  (p)-[:CURRENT_TEAM]->(t:Team), never by projecting the team of PLAYS_FOR: that one lists
  every club of his career. When a season IS named, the club is SEASON_TEAM of that season.
- RESPECT THE ROLE. If the question names a role, filter on it:
  'centravanti' / 'punta' / 'attaccante centrale' -> p.position = 'striker'
  'ala' / 'esterno offensivo' -> 'winger';  'terzino' -> 'full_back'
  'esterno' / 'esterno alto' / 'fascia' with nothing else ->
      p.position IN ['winger', 'wide_midfielder']  (never a goalkeeper or a centre back:
      asking for an 'esterno' and returning Lloris or Chiellini is always wrong)
  'difensore centrale' -> 'centre_back';    'portiere' -> 'goalkeeper'
  'regista' / 'mediano' / 'centrocampista centrale' -> 'central_midfielder'
  'esterno di centrocampo' -> 'wide_midfielder'
  Generic 'difensore' -> p.role = 'defender', 'centrocampista' -> 'midfielder',
  'attaccante' -> p.role = 'forward'.
  When the question asks for an alternative to a named player, do not hard-code his role:
  read HIS p.position and require the candidates to share it.
- YOUNG PLAYERS. 'giovane' / 'prospetto' / 'promessa' / 'su cui investire' ALWAYS means
  p.age <= 23 together with p.active, even when the question already lists other criteria:
  those criteria add to the age filter, they do not replace it. A 42-year-old at the end of
  his career is not a prospect, and returning one contradicts the question.
  Example - 'prospetti su cui investire con overall almeno 75 e 10 punti di margine':
           MATCH (p:Player)-[r:PLAYS_FOR]->(:Team), (p)-[:CURRENT_TEAM]->(t:Team)
           WHERE p.active AND p.age <= 23 AND r.overall >= 75
             AND r.potential - r.overall >= 10
           WITH p, t, max(r.overall) AS overall, max(r.potential) AS potenziale
           RETURN p.name, potenziale - overall AS margine, p.age, p.position, overall, potenziale, t.name
           ORDER BY margine DESC
  Always return p.age and the current club for this kind of question: a scout judging a
  prospect needs to see how old he is and where he plays.
- A question that names no role still benefits from excluding goalkeepers when it is about
  attacking output, but never filter a role the question did not imply.
"""


ANSWER_SYSTEM_PROMPT = """Answer in Italian, concise and evidence-based, using only the rows you are given.

Shape of the answer - the reader also has the full table of rows below it, so the text is a
summary, not a copy of the table:
- One opening sentence with the total number of results and the ordering criterion of the
  query in a few words ("353 giocatori, ordinati per margine di crescita"). Do not repeat
  the conditions of the question: the reader wrote them.
- Then a numbered list of AT MOST 10 entries, in the order of the rows. Each entry is the
  player's name, his club in parentheses, and ONLY the figure the question is about (goals,
  overall, potential, the margin, the appearances) - one number, no label needed when the
  opening sentence already says what it is. Nothing else: no season, no age, no rank, no
  role, unless the question explicitly asked for them.
  That figure is the one the query ORDERs BY, copied from its column: never compute a
  difference, a sum or an average yourself. Entry 1 is row 1, entry 2 is row 2, and so on:
  never skip, merge or reorder rows. Example: "1. Tom Davies (Everton), +22";
  "3. Lionel Messi (FC Barcelona), 36 gol".
- Do NOT write any closing line about the remaining rows ("...e altri N"): it is added
  automatically. Stop after the last list entry.
- A single-row result is ONE sentence that answers the question - "Il capocannoniere della
  Serie A 2008/2009 e' Zlatan Ibrahimovic (Inter) con 25 gol" - with no count and no list.
  An aggregate is the number, stated once.

The rows are the exact result of the Cypher query shown to you. Read them before answering:
- A single row holding an aggregate (count, sum, avg, min, max) IS the answer: state that
  number explicitly. Never claim that no rows were found when a row is present.
- Always write numbers as digits (70, 131, 3.42), never spelled out as words.
- ONLY IF the rows are empty (Rows returned (0)): say that nothing in the database matches,
  plainly and in scouting terms - name the role, age, rating, season and club conditions the
  query applied - never in terms of the query ("la query non ha filtrato..."), naming only
  conditions that actually appear in the query, and never guess why. If the row count is
  greater than zero the result is NOT empty: open with the number of players found and list
  them. If the question
  named a reference player, say that he may not be in the database under that spelling
  rather than asserting something about the candidates.
- Italian adjectives in the question ('mancini', 'destri') describe an attribute of the
  players, not a person: never turn them into a player or team name.
- A player may appear once per team he lined up for: mention the team alongside the value.
- Always name the club of each player mentioned, exactly as the rows give it. Say which club
  it is: if the query read SEASON_TEAM of the season asked, present it as the club of that
  season ("con l'Udinese nel 2010/2011"); if it read CURRENT_TEAM, present it as the club he
  plays for today ("attualmente all'Udinese"), never as the club of a past season. If the
  question named a season but the query read CURRENT_TEAM, say so explicitly.
- Quote only values present in the rows. Never add players, teams or numbers of your own.
- Write every stored value in Italian, never in English. The rows use English codes:
  positions: goalkeeper -> portiere, centre_back -> difensore centrale, full_back -> terzino,
  central_midfielder -> centrocampista centrale, wide_midfielder -> esterno di centrocampo,
  striker -> centravanti, winger -> ala; departments (p.role): goalkeeper -> portiere,
  defender -> difensore, midfielder -> centrocampista, forward -> attaccante;
  preferred_foot: left -> sinistro, right -> destro; work rates: low/medium/high ->
  basso/medio/alto; active: true -> in attivita; playing-style classes (STYLE): slow/balanced/
  fast -> lenta/equilibrata/veloce, little/normal/lots -> poco/normale/molto, short/mixed/long
  -> corto/misto/lungo, organised/free_form -> organizzato/libero, safe/risky -> sicuro/
  rischioso, deep/medium/high -> bassa/media/alta, contain/press/double -> contenimento/
  pressing/raddoppi, narrow/wide -> stretta/larga, cover/offside_trap -> copertura/fuorigioco. When both p.role and p.position are
  present say e.g. "attaccante, centravanti". Names of players, clubs and leagues stay as
  stored.
- BEFORE answering, compare the question with the Cypher that was executed. Take each
  requirement of the question in turn and look for it in the query text, remembering that it
  may be expressed differently from the wording of the question: a role becomes
  p.position = 'centre_back', a league becomes m.league, an attribute becomes a property of
  the RATED relationship, 'mancino' / 'piede sinistro' becomes p.preferred_foot = 'left'
  and 'destro' becomes p.preferred_foot = 'right', 'giovane' / 'prospetto' becomes
  p.age <= 23, 'ultima stagione' becomes m.season = '2015/2016', a club becomes
  CURRENT_TEAM or SEASON_TEAM. Only when you genuinely cannot find a requirement, say so in the
  first line ("la query non ha filtrato X"), because the numbers then answer a narrower
  question. Do not raise a false alarm about a filter that is present, and never describe the
  result as satisfying a condition the query did not express.
- When a threshold was chosen by the query rather than stated in the question (for instance
  what counts as a drastic decline), state the value used.
- When the question named no season and the query read RATED {season: '2015/2016'} or
  m.season = '2015/2016', the figures are those of the current season, 2015/2016: say so
  once, in the opening sentence ("valutazioni 2015/2016"). If instead an attribute was
  aggregated over several seasons with max() or min(), the figure is the player's best or
  worst across the seasons considered, not his current level: say which, in a few words.
- If the query used RANKED to decide a club's tier ('prima fascia', 'top club'), state the
  definition in the first line: "prima fascia = prime 4 della classifica del campionato
  nella stagione X"; the database has no other notion of a club's level.
- If the question asked for a cheap, affordable or low-cost player, open the answer by
  stating that the database holds no market value or wage, and that the ranking uses the
  FIFA overall rating as a proxy for cost. Never present a price you do not have.
"""


MAX_ROWS_FOR_LLM = 15
REPAIR_ATTEMPTS = 4
# Con lo stesso seed OpenAI rende la generazione riproducibile a parita' di input:
# due domande uguali devono produrre lo stesso Cypher, e quindi la stessa risposta.
LLM_SEED = 7


def _stable_order(cypher: str) -> str:
    """Aggiunge il nome come spareggio a un ORDER BY che non lo ha.

    A parita' di margine, gol o overall l'ordine tra due giocatori e' quello
    interno di Neo4j: non un criterio, e non necessariamente stabile tra due
    esecuzioni. Il nome in coda all'ORDER BY rende la classifica riproducibile
    senza cambiarne il criterio.
    """
    last_return = re.search(r"\bRETURN\b(?!.*\bRETURN\b)", cypher, re.IGNORECASE | re.DOTALL)
    if not last_return:
        return cypher
    head, tail = cypher[:last_return.start()], cypher[last_return.start():]
    order = re.search(r"\bORDER\s+BY\s+(.+?)(\s+(?:SKIP|LIMIT)\b.*)?$", tail, re.IGNORECASE | re.DOTALL)
    returns = re.findall(r"\bRETURN\b(.*?)(?:\bORDER\s+BY\b|\bSKIP\b|\bLIMIT\b|$)", tail, re.IGNORECASE | re.DOTALL)
    if not order or not returns or re.search(r"\.name\b|\bnome\b", order.group(1), re.IGNORECASE):
        return cypher
    players = set(re.findall(r"\(\s*([A-Za-z_]\w*)\s*:\s*Player\b", cypher))
    name_item = None
    for item in returns[-1].split(","):
        match = re.match(r"\s*(\w+)\.name\s*(?:AS\s+(\w+))?\s*$", item.strip(), re.IGNORECASE)
        if match and match.group(1) in players:
            name_item = match.group(2) or f"{match.group(1)}.name"
            break
    if name_item is None:
        return cypher
    ordering = order.group(1).rstrip()
    rest = order.group(2) or ""
    return head + tail[:order.start()] + f"ORDER BY {ordering}, {name_item} ASC" + rest


def _harden_counts(cypher: str) -> str:
    """Rende DISTINCT ogni conteggio di relazioni.

    Un giocatore ha una PLAYS_FOR per club: se la query lega anche quella,
    ogni gol compare una volta per squadra e `count(s)` restituisce 38 x 4 = 152
    gol per Ibrahimovic, senza alcun errore visibile. Il prompt lo sconsiglia ma
    il modello non sempre obbedisce, e un numero sbagliato in silenzio e' il
    difetto peggiore per uno strumento di scouting.

    Contare le relazioni distinte e' sempre corretto: in assenza di duplicazione
    `count(DISTINCT s)` coincide con `count(s)`, quindi la riscrittura non puo'
    alterare un risultato gia' giusto.
    """
    relationship_vars = set(re.findall(r"\[\s*([A-Za-z_]\w*)\s*[:\]]", cypher))
    for name in relationship_vars:
        cypher = re.sub(
            rf"\bcount\(\s*{re.escape(name)}\s*\)",
            f"count(DISTINCT {name})",
            cypher,
            flags=re.IGNORECASE,
        )
    return cypher


# Metriche che il dataset non contiene e per cui non esiste alcun ripiego onesto.
# Sono escluse di proposito quelle con un surrogato dichiarato - il costo, che viene
# approssimato con il rating FIFA dicendolo apertamente: quelle si rispondono.
UNAVAILABLE_METRICS: dict[str, str] = {
    r"\bminut\w*\s+giocat\w*|\bminutagg\w*": "i minuti giocati",
    r"\binfortun\w*": "gli infortuni",
    # I tiri subiti ci sono, le parate no: la differenza tra i due e' il gol, e la
    # sottrazione non e' una parata (deviazioni, pali, respinte della difesa).
    r"\bparat\w*": "le parate",
    r"\bsostituzion\w*|\bsubentr\w*": "le sostituzioni",
    r"\bpassaggi\s+chiave|\bkey\s+pass\w*": "i passaggi chiave",
}


def _unavailable_metric(question: str) -> str | None:
    """Metrica assente dal dataset citata nella domanda, se c'e'.

    Senza questo controllo il modello sostituisce la metrica mancante con la piu
    somigliante che trova: a 'giocatori con piu di 10 assist' ha risposto contando
    i :SCORED, presentando i 36 gol di Higuain come 36 assist. Il numero e'
    plausibile e nulla nell'output rivela lo scambio, quindi la sostituzione va
    impedita prima che la query venga generata.
    """
    for pattern, label in UNAVAILABLE_METRICS.items():
        if re.search(pattern, question, re.IGNORECASE):
            return label
    return None


class MissingClub(ValueError):
    """Query che restituisce giocatori senza la loro squadra.

    Il prompt lo chiede, ma il modello non sempre obbedisce, e due domande
    dello stesso tipo finivano per avere una la squadra e l'altra no. Il
    controllo e' nel codice: la query torna al modello attraverso il ciclo di
    riparazione con un messaggio che dice esattamente cosa manca.
    """


def _missing_club(cypher: str) -> bool:
    """True se la query proietta un giocatore ma non ne RESTITUISCE la squadra.

    Non basta che CURRENT_TEAM o SEASON_TEAM siano attraversate: con la
    squadra legata a un nodo anonimo `(:Team)` le righe non la contengono, e
    la spiegazione l'ha scritta lo stesso - "Allan (Napoli)" - dalla sua
    memoria, non dai dati. Il nome del club deve essere una colonna.
    """
    players = set(re.findall(r"\(\s*([A-Za-z_]\w*)\s*:\s*Player\b", cypher))
    if not players:
        return False
    returns = re.findall(r"\bRETURN\b(.*?)(?:\bORDER\s+BY\b|\bSKIP\b|\bLIMIT\b|$)", cypher, re.IGNORECASE | re.DOTALL)
    if not returns:
        return False
    projection = returns[-1]
    if not any(re.search(rf"\b{re.escape(name)}\.name\b", projection) for name in players):
        return False
    teams = set(re.findall(r"\(\s*([A-Za-z_]\w*)\s*:\s*Team\b", cypher))
    if any(re.search(rf"\b{re.escape(t)}\.name\b", projection) for t in teams):
        return False
    # Il club puo' arrivare gia' collassato da un WITH precedente (collect(t.name)[0] AS squadra).
    if re.search(r"\b(squadr\w*|club|team)\b", projection, re.IGNORECASE) and re.search(r"\bcollect\s*\([^)]*\.name", cypher):
        return False
    return True


# Parole della domanda che impongono un filtro sul ruolo nella query.
ROLE_WORDS = re.compile(
    # Forme chiuse per le parole ambigue: 'regist\w*' prendeva "registrato",
    # 'median\w*' prenderebbe la mediana statistica.
    r"\bportier\w*|\bterzin\w*|\bdifensor\w*|\bcentrocampist\w*|\bmedian[oi]\b|\bregist[ai]\b"
    r"|\bestern\w*|\bal[ai]\b|\battaccant\w*|\bpunt[ae]\b|\bcentravant\w*|\btrequartist\w*"
    r"|\balternativ\w*|\bsostitut\w*|\bered[ei]\b|\bsimil\w+\s+a\b",
    re.IGNORECASE,
)


class MissingRole(ValueError):
    """Query che ignora il ruolo richiesto dalla domanda.

    'Alternativa a Hugo Lloris con overall 78-80 ed eta 20-22' non ha alcun
    portiere che la soddisfi: la risposta giusta e' "nessuno". Il modello,
    trovando il risultato vuoto o seguendo l'esempio piu vicino, lasciava
    cadere il ruolo e proponeva Martial e Shaw come alternative a un portiere.
    """


def _missing_role(question: str, cypher: str) -> bool:
    """True se la domanda nomina un ruolo (o chiede un'alternativa) e la query non lo filtra."""
    if not ROLE_WORDS.search(question):
        return False
    return not re.search(r"\.(position|role)\b", cypher)


# (parole nella domanda, filtro Cypher atteso) - nell'ordine in cui vanno provate: la forma
# piu' specifica ('difensore centrale', 'esterno di centrocampo') prima di quella generica.
ROLE_FILTERS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bdifensor\w*\s+central\w*|\bcentral[ei]\s+difensiv\w*", re.I), "p.position = 'centre_back'"),
    (re.compile(r"\bcentrocampist\w*\s+central\w*|\bregist[ai]\b|\bmedian[oi]\b", re.I), "p.position = 'central_midfielder'"),
    (re.compile(r"\bestern\w*\s+di\s+centrocampo", re.I), "p.position = 'wide_midfielder'"),
    (re.compile(r"\bestern\w*|\bal[ai]\b|\bfasci\w*", re.I), "p.position IN ['winger', 'wide_midfielder']"),
    (re.compile(r"\bcentravant\w*|\bpunt[ae]\b|\battaccant\w*\s+central\w*", re.I), "p.position = 'striker'"),
    (re.compile(r"\bterzin\w*", re.I), "p.position = 'full_back'"),
    (re.compile(r"\bportier\w*", re.I), "p.position = 'goalkeeper'"),
    (re.compile(r"\bdifensor\w*", re.I), "p.role = 'defender'"),
    (re.compile(r"\bcentrocampist\w*", re.I), "p.role = 'midfielder'"),
    (re.compile(r"\battaccant\w*", re.I), "p.role = 'forward'"),
]


def _expected_role_filter(question: str) -> str | None:
    """Il filtro sul ruolo che le parole della domanda impongono, se ce n'e' uno."""
    for words, cypher_filter in ROLE_FILTERS:
        if words.search(question):
            return cypher_filter
    return None


class WrongRoleValue(ValueError):
    """Ruolo filtrato con un valore che non esiste per quella proprieta'.

    Rimandata per il ruolo mancante, la query e' tornata con
    `p.position IN ['defender']`: 'defender' e' un valore di p.role, non di
    p.position, e Neo4j non se ne accorge - zero righe, e la risposta
    "nessun difensore in Premier" a una domanda che ne aveva quattro.
    """


POSITIONS = {"goalkeeper", "centre_back", "full_back", "central_midfielder", "wide_midfielder", "striker", "winger"}
ROLES = {"goalkeeper", "defender", "midfielder", "forward"}


def _wrong_role_value(cypher: str) -> str | None:
    """Il primo confronto di p.position / p.role con un valore inesistente, se c'e'."""
    for prop, allowed in (("position", POSITIONS), ("role", ROLES)):
        for match in re.finditer(rf"\b\w+\.{prop}\s*(?:=|<>|IN)\s*(\[[^\]]*\]|'[^']*')", cypher):
            values = re.findall(r"'([^']*)'", match.group(1))
            bad = [v for v in values if v not in allowed]
            if bad:
                other = "p.role" if prop == "position" else "p.position"
                belongs = [v for v in bad if v in (ROLES if prop == "position" else POSITIONS)]
                where = f" ('{belongs[0]}' is a value of {other})" if belongs else ""
                return f"{match.group(0).strip()}: '{bad[0]}' is not a value of p.{prop}{where}; p.{prop} takes only {sorted(allowed)}"
    return None


class MissingRequirement(ValueError):
    """Requisito riconoscibile nella domanda ma assente dalla query.

    'Esterni mancini con overall almeno 85' e' tornata con 16 giocatori, tra cui
    Cristiano Ronaldo, destro: il piede era sparito dalla query e nulla nella
    risposta lo diceva. Le formulazioni piu' lunghe della stessa domanda erano
    corrette. Per i requisiti che una regex sa riconoscere il controllo e' nel
    codice: la parola nella domanda deve avere una traccia nel Cypher.
    """


# (parole nella domanda, traccia attesa nel Cypher, cosa dire al modello)
REQUIRED_FILTERS: list[tuple[re.Pattern, re.Pattern, str]] = [
    # Il valore conta quanto il filtro: alla richiesta di aggiungere il piede il modello
    # ha risposto con preferred_foot = 'right' a una domanda sui mancini.
    # Anche la forma letterale della domanda ("preferred_foot = 'left'", "left-footed"):
    # scritta cosi' era invisibile alla tabella, e il piede e' sparito dalla query senza
    # che nulla lo dicesse - Hazard e Clyne, destri, tra i mancini.
    (re.compile(r"\bmancin\w*|\bpiede\s+sinistr\w*|\bsinistr[oi]\b|preferred_foot\s*(?:=|:)\s*['\"]?left|\bleft[\s-]*foot\w*|\bleft\b", re.I),
     re.compile(r"preferred_foot\s*(?:=|:)\s*'left'"), "left-footed players (p.preferred_foot = 'left', NOT 'right')"),
    (re.compile(r"\bdestr[oi]\b|\bdestrors\w*|\bpiede\s+destr\w*|preferred_foot\s*(?:=|:)\s*['\"]?right|\bright[\s-]*foot\w*", re.I),
     re.compile(r"preferred_foot\s*(?:=|:)\s*'right'"), "right-footed players (p.preferred_foot = 'right', NOT 'left')"),
    (re.compile(r"\bunder\s*\d+|\bet[àa]\b|\bann[io]\b|\bgiovan\w*|\bprospett\w*|\bpromess\w*", re.I),
     re.compile(r"\.age\b|birthday"), "the age (p.age)"),
    (re.compile(r"\b20\d\d\s*/\s*(?:20)?\d\d\b|\bstagion\w*|\b20(?:0[89]|1[0-6])\b", re.I),
     re.compile(r"season", re.I), "the season (m.season, r.season or SEASON_TEAM {season}; 'tra il 2012 e il 2016' is the seasons 2012/2013 through 2015/2016)"),
    # Solo un campionato SPECIFICO: "tra tutti i campionati" o "per campionato" non
    # sono filtri, e pretendere m.league li' ha fatto raggruppare per lega una
    # classifica di rigoristi, cambiando la domanda.
    (re.compile(r"\bserie\s*a\b|\bpremier\b|\bliga\b|\bbundesliga\b|\bligue\s*1\b|\beredivisie\b|\bekstraklasa\b"
                r"|\bmassima serie\b|\bcampionato\s+(?:italiano|inglese|spagnolo|tedesco|francese|olandese|portoghese|belga|scozzese|polacco|svizzero)\b", re.I),
     # Un confronto, non una proiezione: `RETURN st2.league AS league` non filtra nulla, e la
     # Premier e' sparita lasciando 105 giocatori di ogni campionato.
     re.compile(r"\.league\s*(?:=|<>|!=|\bIN\b|\bCONTAINS\b|\bSTARTS\b)|\bleague\s*:\s*'"), "the league AS A FILTER (st.league = '...' on SEASON_TEAM {season: '2015/2016', main: true} when no season is named, on SEASON_TEAM of the LAST season named otherwise, or m.league on Match only when counting matches; returning st.league as a column filters nothing; NEVER t.name, and never join HOME_TEAM|AWAY_TEAM to a SEASON_TEAM pattern)"),
    (re.compile(r"\brigor\w*", re.I), re.compile(r"penalty"), "penalty goals (s.penalty = true)"),
    # "partite giocate in casa" parla delle partite di una squadra, non delle presenze.
    (re.compile(r"\bpresenz\w*|\bpartite\s+giocat[aeio]\b(?!\s+(?:in\s+casa|fuori\s+casa|in\s+trasferta|dal))|\bapparizion\w*|\bda\s+titolare\b", re.I),
     re.compile(r"APPEARED_IN|\.appearances\b|\.season_total\b|\.league_total\b"), "the appearances (st.season_total, or st.league_total with st.league for a league, on SEASON_TEAM of the named season; count(DISTINCT m) over APPEARED_IN only for an opponent or many seasons)"),
    # Solo la classifica DI CAMPIONATO: "la classifica dei 10 rigoristi" e' una
    # classifica di giocatori e non chiede RANKED.
    (re.compile(r"\bprima fascia\b|\btop club\b|\bbig club\b|\bgrand[ei]\s+(?:club|squadr\w*)\b|\bretrocess\w*|\bsquadr\w*\s+di\s+vertice\b"
                r"|\bin\s+classifica\b|\bposizion\w*\s+(?:in|di|della)\s+classifica\b|\bmet[àa]\s+\w+\s+della\s+classifica\b"
                r"|\bclassifica\s+(?:di|del|della|dell')\s*(?:campionato|serie|premier|liga|bundesliga|ligue|eredivisie|\w+\s+\d{4})", re.I),
     re.compile(r"RANKED"), "a club's tier or league position (the RANKED relationship: k.top = true is 'prima fascia', never a comparison of t.name with a league name)"),
    (re.compile(r"\bdi testa\b|\bcolp[oi] di testa\b", re.I), re.compile(r"subtype"), "headers (s.subtype = 'header')"),
    (re.compile(r"\bautogol\b|\bautoret\w*", re.I), re.compile(r"OWN_GOAL"), "own goals (the OWN_GOAL relationship)"),
    (re.compile(r"\baltezz\w*|\balt[oi]\s+(?:almeno|oltre|più|piu|meno)?\s*\d|\bcm\b", re.I),
     re.compile(r"\.height\b"), "the height (p.height)"),
]


# Nomi italiani di attributi che contengono parole di altri requisiti: 'precisione di
# testa' e' r.heading_accuracy, ma "di testa" da solo e' la traccia dei gol di testa.
# Vanno tolti dalla domanda prima di cercare i requisiti, altrimenti il controllo
# pretende `s.subtype = 'header'` e la query finisce per esigere che il difensore
# abbia segnato di testa.
_ATTRIBUTE_PHRASES = re.compile(
    r"\bprecision\w*\s+(?:di\s+|nel\s+|nei\s+)?(?:colp[oi]\s+di\s+)?testa\b"
    r"|\bcolp[oi]\s+di\s+testa\s*(?:>=|<=|>|<|=|maggior\w*|minor\w*|superior\w*|inferior\w*|almeno|sopra|sotto|oltre)"
    r"|\bgioco\s+aereo\b|\bheading_accuracy\b|\bpenalties\b",
    re.IGNORECASE,
)


def _missing_requirement(question: str, cypher: str) -> str | None:
    """Descrizione del primo requisito della domanda che la query non esprime, o None."""
    stripped = _ATTRIBUTE_PHRASES.sub(" ", question)
    for words, trace, label in REQUIRED_FILTERS:
        if words.search(stripped) and not trace.search(cypher):
            return label
    # Un attributo nominato per nome ("overall aumentato di 5 punti", "stamina > 88") deve
    # comparire nel Cypher: "overall e' aumentato" era diventato st2.goals - st1.goals.
    for attribute in ("overall", "potential") + tuple(sorted(_ATTRIBUTES)):
        if re.search(rf"\b{attribute}(?:_rating)?\b", question, re.I) and not re.search(rf"\.{attribute}\b", cypher):
            return f"the attribute {attribute} (r.{attribute} on RATED, or PLAYS_FOR for overall/potential)"
    if re.search(r"\bvalutazion[ei]\s+complessiv\w*|\brating\b", question, re.I) and not re.search(r"\.overall\b", cypher):
        return "the overall rating (r.overall)"
    return None


class MissingPlayerName(ValueError):
    """Righe che descrivono giocatori senza dire chi sono.

    'Squadra, ruolo e gol dei 3 migliori marcatori' e' stata presa alla lettera:
    la query restituiva squadra, ruolo e gol, e la risposta diceva
    "1. Manchester United, centravanti, 26 gol" senza il nome di van Persie.
    """


def _missing_player_name(cypher: str) -> bool:
    """True se la proiezione legge proprieta' dei giocatori (o il loro club) ma non il nome."""
    players = set(re.findall(r"\(\s*([A-Za-z_]\w*)\s*:\s*Player\b", cypher))
    returns = re.findall(r"\bRETURN\b(.*?)(?:\bORDER\s+BY\b|\bSKIP\b|\bLIMIT\b|$)", cypher, re.IGNORECASE | re.DOTALL)
    if not players or not returns:
        return False
    projection = returns[-1]
    if any(re.search(rf"\b{re.escape(v)}\.name\b|(?<![\w.]){re.escape(v)}(?![\w.(])", projection) for v in players):
        return False
    # 'Lionel Messi che ruolo ha': il nome e' nel filtro, chi legge sa gia' di chi si parla.
    if any(re.search(rf"\b{re.escape(v)}\.name\b", cypher) for v in players):
        return False
    if re.search(r"\b(count|sum|avg|min|max|collect)\s*\(", projection, re.IGNORECASE) and not re.search(r"CURRENT_TEAM|SEASON_TEAM", cypher):
        return False  # un aggregato puro ('quanti giocatori') non descrive singoli giocatori
    describes = any(re.search(rf"\b{re.escape(v)}\.\w+", projection) for v in players)
    return describes or bool(re.search(r"CURRENT_TEAM|SEASON_TEAM", cypher))


class SingleMatchThreshold(ValueError):
    """Soglia da stagione applicata ai gol di una singola partita.

    'Squadre con complessivamente piu' di 50 gol in casa' e' diventata
    `WHERE m.home_goals + m.away_goals > 50` sulla singola partita: nessuna
    partita finisce 50-0, e la risposta e' stata "nessuna squadra".
    """


def _single_match_threshold(cypher: str) -> bool:
    """True se un valore di gol di partita, non aggregato, e' confrontato con una soglia >= 12."""
    goal = r"\w+\.(?:home_goals|away_goals)"
    aliases = re.findall(rf"(?<!\w)(?:{goal}\s*\+\s*{goal}|{goal})\s+AS\s+(\w+)", cypher, re.IGNORECASE)
    # Un alias definito dentro sum()/avg() e' un aggregato: non conta.
    aliases = [a for a in aliases if not re.search(rf"(?:sum|avg|max|min)\s*\([^)]*\bAS\s+{a}\b", cypher, re.I)
               and not re.search(rf"(?:sum|avg|max|min)\s*\([^)]*\)\s+AS\s+{a}\b", cypher, re.I)]
    candidates = [goal] + [re.escape(a) for a in aliases]
    for candidate in candidates:
        for m in re.finditer(rf"(?<![\w.]){candidate}\s*(?:>=|>)\s*(\d+)", cypher, re.IGNORECASE):
            if int(m.group(1)) >= 12:
                return True
    # `WITH m, sum(m.home_goals)` e' un aggregato raggruppato per la partita stessa:
    # una somma di un solo addendo, quindi ancora il valore della singola partita.
    for clause in re.finditer(r"\bWITH\b(.*?)(?=\bWHERE\b|\bMATCH\b|\bRETURN\b|\bWITH\b|\bORDER\b|$)", cypher, re.IGNORECASE | re.DOTALL):
        items = clause.group(1)
        for agg in re.finditer(rf"(?:sum|avg|max|min)\s*\(\s*(\w+)\.(?:home_goals|away_goals)", items, re.IGNORECASE):
            var = agg.group(1)
            if re.search(rf"(?<![\w.]){re.escape(var)}(?![\w.(])", items[:agg.start()]):
                return True
    return False


class DuplicatedPlayers(ValueError):
    """Righe che ripetono lo stesso giocatore, distinte solo dal club.

    'Top 10 rigoristi 2009/2010' e' tornata con 11 righe: Diamanti due volte,
    Livorno e West Ham, per un trasferimento a gennaio. Il prompt chiede di
    collassare i club con collect()[0]; qui si verifica sulle righe, dopo
    l'esecuzione, che nessun giocatore compaia due volte per questo motivo.
    """


_TEAM_COLUMN = re.compile(r"^t\.name$|squadr|team|club", re.IGNORECASE)
_DETAIL_COLUMN = re.compile(r"season|stagion|\bdate\b|\bdata\b|partit|match|minut|position|ruolo|avversari", re.IGNORECASE)


def _duplicated_players(cypher: str, rows: list[dict[str, Any]]) -> list[str]:
    """Nomi ripetuti in righe che differiscono solo per la colonna del club.

    Un giocatore puo' comparire piu' volte legittimamente - una riga per
    stagione, per esempio: quelle righe differiscono anche in altro. Sono
    segnalate solo le ripetizioni in cui, tolto il club, le righe coincidono.
    """
    players = set(re.findall(r"\(\s*([A-Za-z_]\w*)\s*:\s*Player\b", cypher))
    name_columns = [key for key in (rows[0] if rows else {}) if re.fullmatch(r"(\w+)\.name", key) and key.split(".")[0] in players]
    # `p.name AS player_name`: l'alias e' la colonna. Senza questo, bastava rinominare
    # la colonna per far passare 342.860 righe di attaccanti ripetuti.
    for var in players:
        name_columns += [alias for alias in re.findall(rf"\b{re.escape(var)}\.name\s+AS\s+(\w+)", cypher, re.IGNORECASE) if rows and alias in rows[0]]
    if not name_columns:
        return []
    # Due nomi per riga: la riga descrive una COPPIA (assist-man e marcatore), e un giocatore
    # compare legittimamente in piu' coppie.
    if len(set(name_columns)) >= 2:
        return []
    name_key = name_columns[0]
    # Una riga per stagione, per partita o per ruolo coperto e' un dettaglio chiesto: le
    # righe hanno una colonna che lo dice. Senza una colonna del genere ogni ripetizione e'
    # una relazione (RATED, PLAYS_FOR, SEASON_TEAM) legata e non aggregata: Gary Cahill
    # e' comparso tre volte, con i valori di tre stagioni diverse, in un elenco che chiedeva
    # un difensore per riga.
    per_detail = any(
        _DETAIL_COLUMN.search(key) or key.split(".")[0] in {"m", "a"}
        for key in rows[0]
        if key != name_key and key.split(".")[0] not in players  # p.position e' il ruolo, non un dettaglio
    )
    # Due giocatori possono chiamarsi allo stesso modo (due Gonzalo Castro, due Juanfran):
    # senza l'id nelle righe, la ripetizione e' un doppione solo se anche il club coincide.
    seen: dict[str, list[tuple]] = {}
    clubs: dict[str, list[tuple]] = {}
    duplicated: list[str] = []
    for row in rows:
        name = row.get(name_key)
        rest = tuple(sorted((k, str(v)) for k, v in row.items() if k != name_key and not _TEAM_COLUMN.search(k)))
        club = tuple(str(v) for k, v in row.items() if _TEAM_COLUMN.search(k))
        same_club = club in clubs.get(name, [])
        if name in seen and (rest in seen[name] or (not per_detail and same_club)) and name not in duplicated:
            duplicated.append(str(name))
        seen.setdefault(name, []).append(rest)
        clubs.setdefault(name, []).append(club)
    return duplicated


class NullRankedFirst(ValueError):
    """Classifica con un null nella colonna di ordinamento della prima riga.

    Neo4j mette i null PRIMA in ORDER BY ... DESC. "Top difensore nell'ultima
    stagione" ha risposto Djilobodji "con un overall di null": il modello aveva
    ordinato per max(r.overall) senza escludere le valutazioni vuote. Il prompt
    lo chiede; qui lo si verifica sulle righe, dove il difetto e' visibile con
    certezza.
    """


def _null_ranked_first(cypher: str, rows: list[dict[str, Any]]) -> str | None:
    """La colonna di ordinamento che vale null nella prima riga, se c'e'."""
    if not rows:
        return None
    order = re.search(r"\bORDER\s+BY\s+(.+?)(?:\bSKIP\b|\bLIMIT\b|$)", cypher, re.IGNORECASE | re.DOTALL)
    if not order:
        return None
    first = rows[0]
    for item in order.group(1).split(","):
        key = re.sub(r"\s+(?:ASC|DESC)\b.*$", "", item.strip(), flags=re.IGNORECASE).strip()
        if key in first and first[key] is None:
            return key
    return None


LEAGUES = [
    "Belgium Jupiler League", "England Premier League", "France Ligue 1", "Germany 1. Bundesliga",
    "Italy Serie A", "Netherlands Eredivisie", "Poland Ekstraklasa", "Portugal Liga ZON Sagres",
    "Scotland Premier League", "Spain LIGA BBVA", "Switzerland Super League",
]


class LeagueComparedToName(ValueError):
    """Nome di campionato confrontato con il nome di una squadra o di un giocatore.

    `t.name <> 'England Premier League'` e' sempre vero: nessuna squadra si chiama
    cosi'. Il modello lo ha usato per "non gioca in un club di prima fascia" e
    ha restituito tutti i 479 candidati, Dele Alli al Tottenham compreso, come
    non di prima fascia. Il campionato e' una proprieta' della partita
    (m.league) e della classifica (k.league), mai di squadre o giocatori.
    """


def _league_compared_to_name(cypher: str) -> str | None:
    """Il confronto incriminato, se c'e'."""
    for league in LEAGUES:
        match = re.search(rf"\b(\w+)\.name\s*(?:=|<>|!=|IN\s*\[[^\]]*)\s*'{re.escape(league)}'", cypher)
        if match:
            return match.group(0)
    return None


class SnapshotRatingWithSeason(ValueError):
    """Rating letto da PLAYS_FOR (ultimo snapshot) per una domanda su una stagione.

    'Under 23 con +10 tra potential e overall nel 2015/2016' e' stata risolta
    con r.overall di PLAYS_FOR, che e' l'ultima valutazione della carriera:
    354 giocatori invece dei 353 di RATED {season: '2015/2016'}. Una stagione
    nella domanda impone RATED di quella stagione.
    """


# Anche gli anni nudi ("tra il 2012 e il 2016", "dal 2013") e "annue": senza, una domanda
# su un intervallo di anni non attivava nessun controllo sulle stagioni.
_SEASON_WORDS = re.compile(r"\b20\d\d\s*/\s*(?:20)?\d\d\b|\bstagion\w*|\b20(?:0[89]|1[0-6])\b|\bannu[aeo]\w*|\ball'anno\b", re.I)


def _snapshot_rating_with_season(question: str, cypher: str) -> bool:
    if not _SEASON_WORDS.search(question):
        return False
    plays_for_vars = re.findall(r"\[\s*([A-Za-z_]\w*)\s*:\s*PLAYS_FOR\b", cypher)
    return any(re.search(rf"\b{re.escape(v)}\.(overall|potential)\b", cypher) for v in plays_for_vars)


class UnknownProperty(ValueError):
    """Riferimento a una proprieta' che non esiste nello schema.

    Neo4j non segnala un nome sbagliato: restituisce null. `m.home_team` ha
    raggruppato tutte le partite in una riga senza squadra e la risposta e'
    stata "nessuna squadra ha segnato piu' di 50 gol in casa". Lo schema e'
    noto: ogni `variabile.proprieta'` viene verificato prima dell'esecuzione.
    """


_ATTRIBUTES = {
    "crossing", "finishing", "heading_accuracy", "short_passing", "volleys", "dribbling",
    "curve", "free_kick_accuracy", "long_passing", "ball_control", "acceleration",
    "sprint_speed", "agility", "reactions", "balance", "shot_power", "jumping", "stamina",
    "strength", "long_shots", "aggression", "interceptions", "positioning", "vision",
    "penalties", "marking", "standing_tackle", "sliding_tackle", "gk_diving", "gk_handling",
    "gk_kicking", "gk_positioning", "gk_reflexes", "attacking_work_rate", "defensive_work_rate",
}
SCHEMA_PROPERTIES: dict[str, set[str]] = {
    "Player": {"id", "name", "birthday", "height", "weight", "role", "position", "age", "active", "last_season", "appearances", "preferred_foot"},
    "Team": {"id", "name"},
    "Season": {"name"},
    "Match": {"id", "date", "league", "season", "home_goals", "away_goals", "has_events", "home_possession", "away_possession"},
    "PLAYS_FOR": {"date", "overall", "potential", "preferred_foot"},
    "SCORED": {"minute", "penalty", "subtype", "event_id"},
    "OWN_GOAL": {"minute", "event_id"},
    "ASSISTED": {"minute", "event_id", "scorer_id", "team_id", "season"},
    "ASSISTED_GOALS_OF": {"goals", "seasons"},
    "SHOT": {"minute", "event_id", "subtype", "on_target", "blocked", "team_id", "x", "y", "season"},
    "FOUL": {"minute", "event_id", "subtype", "victim_id", "team_id", "x", "y", "season"},
    "FOULED": {"minute", "event_id", "subtype", "by_id", "season"},
    "CARD": {"minute", "event_id", "subtype", "card_type", "team_id", "x", "y", "season"},
    "CROSS": {"minute", "event_id", "subtype", "team_id", "x", "y", "season"},
    "CORNER": {"minute", "event_id", "subtype", "team_id", "x", "y", "season"},
    "APPEARED_IN": {"position", "team_id"},
    "RATED": {"season", "date", "overall", "potential"} | _ATTRIBUTES,
    "ASSESSED": {"season", "date", "overall", "potential"} | _ATTRIBUTES,
    "SEASON_TEAM": {"season", "main", "appearances", "season_total", "league", "league_total", "position",
                    "events_covered", "goals", "assists", "shots", "shots_on_target", "fouls_committed", "fouls_suffered",
                    "yellow_cards", "red_cards", "crosses", "corners", "shots_faced", "shots_on_target_faced"},
    "RANKED": {"season", "league", "rank", "teams", "top", "top_places", "points", "played", "won",
               "drawn", "lost", "goals_for", "goals_against", "goal_difference"},
    "CURRENT_TEAM": set(),
    "STYLE": {"season", "date", "build_up_speed", "build_up_speed_class", "build_up_dribbling", "build_up_dribbling_class",
              "build_up_passing", "build_up_passing_class", "build_up_positioning_class", "chance_creation_passing",
              "chance_creation_passing_class", "chance_creation_crossing", "chance_creation_crossing_class",
              "chance_creation_shooting", "chance_creation_shooting_class", "chance_creation_positioning_class",
              "defence_pressure", "defence_pressure_class", "defence_aggression", "defence_aggression_class",
              "defence_team_width", "defence_team_width_class", "defence_defender_line_class"},
    "HOME_TEAM": set(),
    "AWAY_TEAM": set(),
}


def _unknown_property(cypher: str) -> tuple[str, str, str] | None:
    """(variabile, proprieta', etichetta) del primo riferimento a una proprieta' inesistente."""
    bindings: dict[str, str] = {}
    for var, label in re.findall(r"\(\s*([A-Za-z_]\w*)\s*:\s*([A-Za-z_]\w*)", cypher):
        bindings[var] = label
    for var, rel_type in re.findall(r"\[\s*([A-Za-z_]\w*)\s*:\s*([A-Za-z_]\w*)", cypher):
        bindings[var] = rel_type
    for var, prop in re.findall(r"(?<![\w.'])([A-Za-z_]\w*)\.([A-Za-z_]\w*)", cypher):
        label = bindings.get(var)
        if label in SCHEMA_PROPERTIES and prop not in SCHEMA_PROPERTIES[label]:
            return var, prop, label
    return None


_TIER_WORDS = re.compile(r"\bprima fascia\b|\btop club\b|\bbig club\b|\bgrand[ei]\s+(?:club|squadr\w*)\b|\bsquadr\w*\s+di\s+vertice\b", re.I)


def _tier_note(question: str, cypher: str) -> str | None:
    """La definizione di 'prima fascia' usata dalla query, da premettere alla risposta.

    Il dataset non ha una nozione di livello di un club: la fascia e' la
    posizione in classifica, e la soglia scelta dalla query va detta. Il prompt
    lo chiede al modello, che non sempre lo fa; qui e' garantito.
    """
    found = _TIER_WORDS.search(question)
    if not found:
        return None
    term = found.group(0).lower()
    seasons = re.findall(r"'(20\d\d/20\d\d)'", cypher)
    season = f" nella stagione {seasons[0]}" if seasons else ""
    if re.search(r"\.top\b", cypher):
        return (
            f"Nota: per '{term}' si intende il 30% di testa della classifica del proprio "
            f"campionato{season} (mai meno di 4 squadre)."
        )
    threshold = re.search(r"\brank\s*(<=|<|>|>=)\s*(\d+)", cypher)
    if not threshold:
        return None
    op, n = threshold.group(1), int(threshold.group(2))
    top = n if op == "<=" else n - 1 if op == "<" else n if op == ">" else n - 1
    return (
        f"Nota: per '{term}' si intendono le prime {top} squadre della classifica del proprio "
        f"campionato{season}."
    )


# Nomi italiani delle colonne per cui una classifica viene ordinata: la frase di
# apertura deve dire il criterio, e il modello a volte lo omette.
_ORDER_LABELS = {
    "overall": "overall", "potential": "potenziale", "potenziale": "potenziale", "gol": "gol", "goals": "gol",
    "presenze": "presenze", "appearances": "presenze", "margine": "margine di crescita", "combinato": "valore combinato",
    "rigori": "gol su rigore", "autogol": "autogol", "age": "eta'", "height": "altezza", "weight": "peso",
    "stagioni": "numero di stagioni", "salti": "numero di salti", "overall_medio": "overall medio",
    "heading_accuracy": "precisione di testa", "jumping": "elevazione", "stamina": "resistenza", "strength": "forza",
    "sprint_speed": "velocita'", "acceleration": "accelerazione", "aggression": "aggressivita'", "reactions": "reattivita'",
    "marking": "marcatura", "standing_tackle": "contrasto", "sliding_tackle": "scivolata", "interceptions": "intercetti",
    "positioning": "posizionamento", "vision": "visione", "balance": "equilibrio", "agility": "agilita'",
    "dribbling": "dribbling", "ball_control": "controllo di palla", "short_passing": "passaggio corto",
    "long_passing": "passaggio lungo", "crossing": "cross", "finishing": "finalizzazione", "shot_power": "potenza di tiro",
    "long_shots": "tiro da fuori", "volleys": "volee", "free_kick_accuracy": "punizioni", "penalties": "rigori",
    "curve": "effetto", "gk_diving": "tuffo", "gk_handling": "presa", "gk_kicking": "rinvio", "gk_reflexes": "riflessi",
    "gk_positioning": "piazzamento",
    "assists": "assist", "assist": "assist", "shots": "tiri", "tiri": "tiri", "shots_on_target": "tiri in porta",
    "tiri_in_porta": "tiri in porta", "fouls_committed": "falli commessi", "falli": "falli", "fouls_suffered": "falli subiti",
    "yellow_cards": "cartellini gialli", "red_cards": "cartellini rossi", "cartellini": "cartellini", "crosses": "cross",
    "cross": "cross", "corners": "calci d'angolo", "corner": "calci d'angolo", "shots_faced": "tiri subiti",
    "shots_on_target_faced": "tiri in porta subiti", "tiri_subiti": "tiri subiti", "tiri_in_porta_subiti": "tiri in porta subiti",
    "events_covered": "partite coperte", "possesso": "possesso palla",
    "defence_pressure": "pressione difensiva", "build_up_speed": "velocita' di costruzione", "build_up_passing": "passaggio in costruzione",
    "chance_creation_crossing": "creazione da cross", "chance_creation_shooting": "creazione da tiro", "chance_creation_passing": "creazione da passaggio",
    "defence_aggression": "aggressivita' difensiva", "defence_team_width": "ampiezza difensiva", "build_up_dribbling": "dribbling in costruzione",
}


def _ordering_label(cypher: str) -> str | None:
    """Il criterio di ordinamento finale della query, in italiano, se e' una colonna nota."""
    last_return = re.search(r"\bRETURN\b(?!.*\bRETURN\b)(.*)$", cypher, re.IGNORECASE | re.DOTALL)
    if not last_return:
        return None
    order = re.search(r"\bORDER\s+BY\s+(.+?)(?:\bSKIP\b|\bLIMIT\b|$)", last_return.group(1), re.IGNORECASE | re.DOTALL)
    if not order:
        return None
    first = _split_top_level(order.group(1))[0].strip()
    descending = bool(re.search(r"\bDESC\b", first, re.IGNORECASE))
    key = re.sub(r"\s+(?:ASC|DESC)\b.*$", "", first, flags=re.IGNORECASE).strip()
    key = key.split(".")[-1] if re.fullmatch(r"\w+\.\w+", key) else key
    if key == "name":
        return "nome"
    label = _ORDER_LABELS.get(key.lower())
    if not label:
        return None
    return f"{label} {'decrescente' if descending else 'crescente'}"


def _ordering_column(cypher: str, rows: list[dict[str, Any]]) -> str | None:
    """La colonna delle righe che corrisponde alla prima chiave dell'ORDER BY finale."""
    if not rows:
        return None
    last_return = re.search(r"\bRETURN\b(?!.*\bRETURN\b)(.*)$", cypher, re.IGNORECASE | re.DOTALL)
    if not last_return:
        return None
    order = re.search(r"\bORDER\s+BY\s+(.+?)(?:\bSKIP\b|\bLIMIT\b|$)", last_return.group(1), re.IGNORECASE | re.DOTALL)
    if not order:
        # `WITH ... ORDER BY gol DESC RETURN ...`: l'ordine del WITH arriva al RETURN se
        # in mezzo non c'e' un'aggregazione.
        before = cypher[:last_return.start()]
        tail = re.split(r"\bORDER\s+BY\b", before, flags=re.IGNORECASE)
        if len(tail) < 2 or re.search(r"\b(?:count|sum|avg|min|max|collect)\s*\(", tail[-1], re.IGNORECASE):
            return None
        order = re.match(r"\s*(.+?)(?:\bSKIP\b|\bLIMIT\b|\bRETURN\b|\bWITH\b|\bMATCH\b|$)", tail[-1], re.IGNORECASE | re.DOTALL)
        if not order:
            return None
    key = re.sub(r"\s+(?:ASC|DESC)\b.*$", "", _split_top_level(order.group(1))[0].strip(), flags=re.IGNORECASE).strip()
    if key in rows[0]:
        return key
    alias = re.search(rf"{re.escape(key)}\s+AS\s+(\w+)", last_return.group(1), re.IGNORECASE)
    if alias and alias.group(1) in rows[0]:
        return alias.group(1)
    return None


def _format_value(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "sì" if value else "no"
    if isinstance(value, float):
        return f"{value:g}" if value == int(value) else f"{value:.2f}"
    if isinstance(value, list):
        return " · ".join(_format_value(v) for v in value)
    return str(value)


def _rebuild_list(answer: str, cypher: str, rows: list[dict[str, Any]], limit: int = 10) -> str:
    """Sostituisce l'elenco numerato scritto dal modello con uno costruito dalle righe.

    Il formato e' rigido - "N. Nome (Club), valore" - e ogni sua parte e' una
    colonna nota: il nome del giocatore, il club, la colonna dell'ORDER BY. Il
    modello, con l'overall in fondo alla riga, ha copiato lo sprint_speed (86)
    come overall (77) e ha invertito l'ordine di due giocatori. Un numero
    sbagliato accanto a un nome e' il difetto peggiore per uno scout, e qui
    non c'e' nulla da interpretare: lo scrive il codice.
    """
    if len(rows) < 2 or not re.search(r"^\s*\d+\.\s", answer, re.MULTILINE):
        return answer
    players = set(re.findall(r"\(\s*([A-Za-z_]\w*)\s*:\s*Player\b", cypher))
    name_key = next((k for k in rows[0] if re.fullmatch(r"(\w+)\.name", k) and k.split(".")[0] in players), None)
    if name_key is None:
        for var in players:
            alias = re.search(rf"\b{re.escape(var)}\.name\s+AS\s+(\w+)", cypher, re.IGNORECASE)
            if alias and alias.group(1) in rows[0]:
                name_key = alias.group(1)
                break
    # Una coppia (assist-man e marcatore): due colonne-nome, entrambe nell'elenco.
    name_keys = [k for k in rows[0] if k == name_key or (
        (re.fullmatch(r"(\w+)\.name", k) and k.split(".")[0] in players)
        or any(re.search(rf"\b{re.escape(v)}\.name\s+AS\s+{re.escape(k)}\b", cypher, re.IGNORECASE) for v in players))]
    if not name_keys:
        return answer
    name_key = name_keys[0]  # nell'ordine delle colonne: l'assist-man prima del marcatore
    club_key = next((k for k in rows[0] if k not in name_keys and _TEAM_COLUMN.search(k)), None)
    value_key = _ordering_column(cypher, rows)
    if not name_key or not value_key or value_key in name_keys or value_key == club_key:
        return answer
    if not club_key and len(name_keys) < 2:
        return answer
    if isinstance(rows[0].get(value_key), str):
        return answer  # ordinato per una stringa (un ruolo, una stagione): non e' una cifra

    def who(row: dict[str, Any]) -> str:
        names = " → ".join(str(row.get(k)) for k in name_keys)
        return f"{names} ({row.get(club_key)})" if club_key else names

    entries = [f"{i}. {who(row)}, {_format_value(row.get(value_key))}" for i, row in enumerate(rows[:limit], start=1)]
    lines = answer.splitlines()
    first = next(i for i, line in enumerate(lines) if re.match(r"^\s*\d+\.\s", line))
    head = "\n".join(lines[:first]).rstrip()
    return (head + "\n\n" if head else "") + "\n".join(entries)


_EVENT_TRACE = re.compile(
    r":\s*(?:ASSISTED|ASSISTED_GOALS_OF|SHOT|FOUL|FOULED|CARD|CROSS|CORNER)\b"
    r"|\.(?:assists|shots|shots_on_target|fouls_committed|fouls_suffered|yellow_cards|red_cards|crosses|corners|shots_faced|shots_on_target_faced|home_possession|away_possession)\b"
)


def _coverage_note(cypher: str) -> str | None:
    """La nota sulla copertura degli eventi, se la query li usa.

    La cronaca completa esiste per 8.466 partite su 25.979: una classifica di
    falli o di tiri senza questa avvertenza premia chi gioca in Inghilterra e
    condanna chi gioca in Belgio a zero. La scrive il codice, non il modello.
    """
    notes = []
    if _EVENT_TRACE.search(cypher):
        notes.append("Nota: gli eventi di gioco (assist, tiri, falli, cartellini, cross, corner, possesso) coprono "
                     "8.466 partite su 25.979 - Premier League completa; Liga, Serie A, Bundesliga e Ligue 1 in parte; "
                     "Belgio, Portogallo e Svizzera assenti. I totali vanno letti insieme alle partite coperte "
                     "(events_covered).")
    if re.search(r":\s*STYLE\b", cypher):
        notes.append("Nota: lo stile di gioco e' la rilevazione FIFA della squadra per stagione (288 club, stagioni "
                     "2009/10-2011/12 e 2013/14-2015/16; il 2012/13 manca): classi come 'high', 'fast', 'short' e "
                     "valori 20-80, non statistiche di partita.")
    return "\n".join(notes) if notes else None


def _answer_denies_rows(answer: str, rows: list[dict[str, Any]]) -> bool:
    """True se la risposta dice che non c'e' nulla mentre le righe ci sono.

    Con 353 righe in tabella la spiegazione ha aperto con "Nessun portiere in
    attivita' tra i 20 e i 22 anni": una frase di esempio del prompt, copiata.
    Il numero di righe e' nel prompt, ma un modello puo' ignorarlo: qui si
    verifica, e in caso si rigenera con l'istruzione esplicita.
    """
    if not rows:
        return False
    return bool(re.match(r"\s*(nessun|non (?:e'|è|risult|sono stat|ci sono)|no\b)", answer, re.IGNORECASE))


class OrderingNotReturned(ValueError):
    """ORDER BY su un'espressione calcolata che la query non restituisce.

    `ORDER BY r.potential - r.overall DESC` con RETURN di overall e potential
    separati: la spiegazione ha ricalcolato i margini da sola, sbagliandoli, e
    ha saltato due righe. La metrica che ordina la lista deve essere una
    colonna con un nome, cosi' la risposta la copia e non la calcola.
    """


def _ordering_not_returned(cypher: str) -> str | None:
    """L'espressione aritmetica dell'ORDER BY finale assente dalla proiezione, se c'e'.

    Solo l'ORDER BY che segue l'ultimo RETURN: un `WITH ... ORDER BY stagione`
    intermedio non ordina la lista e non deve essere giudicato.
    """
    last_return = re.search(r"\bRETURN\b(?!.*\bRETURN\b)(.*)$", cypher, re.IGNORECASE | re.DOTALL)
    if not last_return:
        return None
    tail = last_return.group(1)
    order = re.search(r"\bORDER\s+BY\s+(.+?)(?:\bSKIP\b|\bLIMIT\b|$)", tail, re.IGNORECASE | re.DOTALL)
    if not order:
        return None
    projection_text = re.split(r"\bORDER\s+BY\b", tail, flags=re.IGNORECASE)[0]
    projection = re.sub(r"\s+", "", projection_text)
    # Le voci della proiezione, con e senza alias: `r.overall AS overall` rende visibili
    # sia `r.overall` sia `overall`.
    visible: set[str] = set()
    for item in _split_top_level(projection_text):
        parts = re.split(r"\s+AS\s+", item.strip(), flags=re.IGNORECASE)
        visible.update(re.sub(r"\s+", "", part) for part in parts)
    for item in _split_top_level(order.group(1)):
        expr = re.sub(r"\s+(?:ASC|DESC)\b.*$", "", item.strip(), flags=re.IGNORECASE).strip()
        squeezed = re.sub(r"\s+", "", expr)
        if re.search(r"[-+*/]", expr) and squeezed not in projection:
            return expr
        # `ORDER BY r.overall` con overall assente dalle colonne: la risposta ha preso il
        # primo numero della riga - la heading_accuracy, 86 - e l'ha chiamato overall.
        if re.fullmatch(r"\w+\.\w+", squeezed) and squeezed not in visible:
            return expr
    return None


class CurrentTeamWithoutActive(ValueError):
    """CURRENT_TEAM letto per un elenco di giocatori senza richiedere p.active.

    CURRENT_TEAM e' il club dell'ULTIMA presenza: per chi non ha giocato
    nell'ultima stagione e' un club di anni prima presentato come attuale.
    "Non giochino gia' in un top club" senza p.active dava 361 giocatori
    contro i 320 della stessa domanda con "prima fascia": 41 con una
    valutazione 2015/2016 ma nessuna presenza. Un elenco che mostra il club
    attuale deve limitarsi a chi gioca ancora; la domanda su un singolo
    giocatore nominato e' esente (Pirlo ha un ultimo club anche da ritirato).
    """


def _current_team_without_active(cypher: str) -> bool:
    if not re.search(r"\bCURRENT_TEAM\b", cypher):
        return False
    players = set(re.findall(r"\(\s*([A-Za-z_]\w*)\s*:\s*Player\b", cypher))
    if any(re.search(rf"\b{re.escape(v)}\.active\b", cypher) for v in players):
        return False
    # Un giocatore nominato nel filtro: nessun elenco, nessun obbligo.
    if any(re.search(rf"\b{re.escape(v)}\.name\b[^,\n]*(?:CONTAINS|=)", cypher) for v in players):
        return False
    return True


_MULTI_SEASON = re.compile(
    r"\b20\d\d\s*/\s*(?:20)?\d\d\b.*\b20\d\d\s*/\s*(?:20)?\d\d\b|\bconsecutiv\w*|\b(?:ogni|ciascun\w*|per|in tutte le)\s+stagion\w*|\bstagioni\b"
    r"|\b(?:tra|dal|da)\s+(?:il\s+)?20(?:0[89]|1[0-6])\b.*\b(?:e|al|a)\s+(?:il\s+)?20(?:0[89]|1[0-6])\b|\bannu[aeo]\w*",
    re.I | re.S,
)
_KEPT = re.compile(r"\bmantenend\w*|\bmantien\w*|\bsempre\b|\bmai\s+(?:sotto|inferiore)|\bcostantemente\b|\bin\s+tutte\s+le\s+stagioni|\bsenza\s+(?:mai\s+)?scendere", re.I)


class AppearancesNotPerSeason(ValueError):
    """Presenze sommate su piu' stagioni quando la soglia vale per ciascuna.

    "Almeno 30 presenze nelle stagioni 2014/15 e 2015/16" e' stata risolta con
    `count(DISTINCT m)` sulle due stagioni insieme: 30 in totale, e 296
    giocatori invece dei 23 con 30 in ciascuna.
    """


_AVERAGE_APPEARANCES = re.compile(r"\bpresenz\w*\s+medi\w*|\bmedi[ae]\s+(?:di\s+)?(?:\d+\s+)?presenz\w*|\bin\s+media\b", re.I)


def _appearances_not_per_season(question: str, cypher: str) -> bool:
    if not _MULTI_SEASON.search(question):
        return False
    for clause in re.finditer(r"\bWITH\b(.*?)(?=\bWHERE\b|\bMATCH\b|\bRETURN\b|\bWITH\b|\bORDER\b|$)", cypher, re.IGNORECASE | re.DOTALL):
        items = clause.group(1)
        count = re.search(r"count\(\s*(?:DISTINCT\s+)?(\w+)\s*\)(\s*/)?", items, re.IGNORECASE)
        if not count or not re.search(rf"\(\s*{re.escape(count.group(1))}\s*:\s*Match\b", cypher) or re.search(r"\.season\b", items):
            continue
        # "25 presenze medie annue": la media e' il totale diviso le stagioni, un solo conteggio.
        if count.group(2) or _AVERAGE_APPEARANCES.search(question):
            continue
        return True
    return False


class RatedWithoutSeason(ValueError):
    """RATED letta senza vincolo di stagione in una domanda che nomina stagioni.

    "Mantenendo stamina > 85 nelle stagioni 2014/15 e 2015/16" e' diventata
    `MATCH (p)-[r:RATED]->() WHERE r.stamina > 85`: basta un anno qualsiasi
    della carriera. Gillet, 83 e 85 in quelle due stagioni, passava grazie a
    un 86 di anni prima.
    """


def _rated_without_season(question: str, cypher: str) -> bool:
    if not _SEASON_WORDS.search(question):
        return False
    # Il vincolo puo' stare anche sul nodo Season: (s:Season) ... s.name IN [...] o {name: ...}.
    if re.search(r"\w+\.name\s*(?:=|IN)\s*(?:\[[^\]]*)?'20\d\d/20\d\d'|Season\s*\{\s*name\s*:", cypher):
        return False
    for var in re.findall(r"\[\s*([A-Za-z_]\w*)\s*:\s*RATED\b(?![^\]]*season)", cypher):
        if not re.search(rf"\b{re.escape(var)}\.season\b", cypher):
            return True
    return False


class KeptAttributeWithMax(ValueError):
    """'Mantenendo X sopra N' risolto con max(X) > N invece di min(X) > N.

    Il massimo accetta chi ha toccato N una volta ed e' crollato dopo; il
    requisito di tenuta e' sul minimo.
    """


def _kept_attribute_with_max(question: str, cypher: str) -> bool:
    if not _KEPT.search(question):
        return False
    return bool(re.search(r"\bmax\(\s*\w+\.\w+\s*\)", cypher, re.IGNORECASE)) and not re.search(r"\bmin\(", cypher, re.IGNORECASE)


class GroupedByMeasure(ValueError):
    """Un valore per stagione usato come chiave di raggruppamento nel collect.

    `WITH p, collect(stagione) AS stagioni, presenze`: le stagioni finiscono
    nella stessa riga solo se le presenze coincidono. 4 giocatori invece di
    23, e nulla di visibile.
    """


def _grouped_by_measure(cypher: str) -> str | None:
    """L'alias di un conteggio per stagione che compare nudo in un WITH con collect()."""
    measures = set(re.findall(r"\b(?:count|sum|avg|min|max)\s*\([^)]*\)\s+AS\s+(\w+)", cypher, re.IGNORECASE))
    for clause in re.finditer(r"\bWITH\b(.*?)(?=\bWHERE\b|\bMATCH\b|\bRETURN\b|\bWITH\b|\bORDER\b|$)", cypher, re.IGNORECASE | re.DOTALL):
        items = clause.group(1)
        if not re.search(r"\bcollect\s*\(", items, re.IGNORECASE):
            continue
        for item in _split_top_level(items):
            bare = item.strip()
            if bare in measures and re.search(rf"\b(?:count|sum|avg|min|max)\s*\([^)]*\)\s+AS\s+{bare}\b", cypher[:clause.start()], re.IGNORECASE):
                return bare
    return None


def _split_top_level(text: str) -> list[str]:
    parts, depth, start = [], 0, 0
    for i, ch in enumerate(text):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(text[start:i]); start = i + 1
    parts.append(text[start:])
    return parts


class ThresholdBeforeMin(ValueError):
    """Soglia di tenuta applicata alle singole stagioni prima del min().

    `WHERE r.stamina > 85` prima di `min(r.stamina)` scarta le stagioni sotto
    soglia invece di far cadere il giocatore: chi ha 80 e 90 passa.
    """


def _threshold_before_min(question: str, cypher: str) -> str | None:
    """Con 'mantenendo': soglia sull'attributo di RATED nel WHERE (prima o senza min())."""
    if not _KEPT.search(question):
        return None
    for var in re.findall(r"\[\s*([A-Za-z_]\w*)\s*:\s*(?:RATED|ASSESSED)\b", cypher):
        # Forma piatta: un RATED per stagione nominata, filtrato direttamente. Legittima.
        if re.search(rf"\[\s*{re.escape(var)}\s*:\s*(?:RATED|ASSESSED)\s*\{{[^}}]*season\s*:\s*'", cypher):
            continue
        hit = re.search(rf"\bWHERE\b[^\n]*?\b{re.escape(var)}\.(\w+)\s*(?:>=|>)\s*\d+", cypher, re.IGNORECASE)
        if hit and hit.group(1) != "season":
            return f"{var}.{hit.group(1)}"
    return None


class RatedUnaggregated(ValueError):
    """Proprieta' di RATED restituita nuda con piu' stagioni in gioco: una riga per stagione.

    `RETURN p.name, r.stamina` con r.season IN [due stagioni] ripete il
    giocatore; il controllo sui doppioni lo segnalava con un messaggio
    fuorviante sui club.
    """


def _rated_unaggregated(cypher: str) -> str | None:
    returns = re.findall(r"\bRETURN\b(.*?)(?:\bORDER\s+BY\b|\bSKIP\b|\bLIMIT\b|$)", cypher, re.IGNORECASE | re.DOTALL)
    if not returns:
        return None
    for var in re.findall(r"\[\s*([A-Za-z_]\w*)\s*:\s*(?:RATED|ASSESSED)\b", cypher):
        single = re.search(rf"\[\s*{re.escape(var)}\s*:\s*(?:RATED|ASSESSED)\s*\{{[^}}]*season\s*:\s*'", cypher) or \
                 re.search(rf"\b{re.escape(var)}\.season\s*=\s*'", cypher)
        if single:
            continue
        for item in _split_top_level(returns[-1]):
            if re.search(rf"^\s*{re.escape(var)}\.(\w+)\s*(?:AS\s+\w+)?\s*$", item.strip()):
                return item.strip()
    return None


class AggregatedAndGrouped(ValueError):
    """Variabile aggregata e usata come chiave di raggruppamento nello stesso WITH.

    `WITH p, r, min(r.stamina) AS minimo, count(r) AS n`: ogni gruppo e' una
    sola r, n vale sempre 1, e "n = 2" non e' mai vero: zero righe.
    """


def _aggregated_and_grouped(cypher: str) -> str | None:
    for clause in re.finditer(r"\bWITH\b(.*?)(?=\bWHERE\b|\bMATCH\b|\bRETURN\b|\bWITH\b|\bORDER\b|$)", cypher, re.IGNORECASE | re.DOTALL):
        items = [item.strip() for item in _split_top_level(clause.group(1))]
        bare = {item for item in items if re.fullmatch(r"[A-Za-z_]\w*", item)}
        for item in items:
            for agg in re.finditer(r"\b(?:count|sum|avg|min|max|collect)\s*\(\s*(?:DISTINCT\s+)?([A-Za-z_]\w*)", item, re.IGNORECASE):
                if agg.group(1) in bare:
                    return agg.group(1)
    return None


class PerSeasonValuesNotReturned(ValueError):
    """Soglia per stagione applicata, ma i valori per stagione non restituiti.

    Chi legge "almeno 30 presenze in ciascuna stagione" deve poter vedere le
    presenze di ciascuna stagione nella tabella, non un totale o niente.
    """


def _per_season_values_not_returned(question: str, cypher: str) -> str | None:
    if not _MULTI_SEASON.search(question):
        return None
    returns = re.findall(r"\bRETURN\b(.*?)(?:\bORDER\s+BY\b|\bSKIP\b|\bLIMIT\b|$)", cypher, re.IGNORECASE | re.DOTALL)
    if not returns:
        return None
    projection = returns[-1]
    for clause in re.finditer(r"\bWITH\b(.*?)(?=\bWHERE\b|\bMATCH\b|\bRETURN\b|\bWITH\b|\bORDER\b|$)", cypher, re.IGNORECASE | re.DOTALL):
        items = clause.group(1)
        # Solo un conteggio di PARTITE raggruppato per stagione (le presenze di quella
        # stagione), non un count(r) sulle valutazioni.
        if not any(re.fullmatch(r"\w+\.season\s+AS\s+\w+", item.strip(), re.IGNORECASE) for item in _split_top_level(items)):
            continue
        for var, alias in re.findall(r"\bcount\s*\(\s*(?:DISTINCT\s+)?(\w+)\s*\)\s+AS\s+(\w+)", items, re.IGNORECASE):
            if not re.search(rf"\(\s*{re.escape(var)}\s*:\s*Match\b", cypher):
                continue
            if not re.search(rf"\b{re.escape(alias)}\b", projection) and not re.search(rf"\bcollect\s*\([^)]*\b{re.escape(alias)}\b", cypher, re.IGNORECASE):
                return alias
    if _KEPT.search(question):
        for var, attr in re.findall(r"\bmin\(\s*(\w+)\.(\w+)\s*\)", cypher, re.IGNORECASE):
            # Basta che l'attributo compaia in un collect, anche attraverso un alias.
            if not re.search(rf"\bcollect\s*\([^)]*{re.escape(attr)}", cypher, re.IGNORECASE):
                return f"{var}.{attr}"
    return None


class WrongComparison(ValueError):
    """Operatore di confronto in disaccordo con le parole della domanda.

    "Stamina sempre sopra 85" e' diventata `>= 85`: 28 giocatori invece di
    23. 'sopra', 'piu' di', 'oltre', 'superiore a' sono strettamente
    maggiore; 'almeno', 'minimo', 'non meno di', 'mai sotto' sono maggiore o
    uguale; lo stesso al contrario per 'sotto', 'meno di', 'al massimo'.
    """


_COMPARISONS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(?:mai|non)\s+(?:sotto|inferiore\s+a|meno\s+di)\s+(?:i\s+|il\s+|gli\s+)?(\d+)", re.I), ">="),
    (re.compile(r"\b(?:almeno|minimo|non\s+meno\s+di|pari\s+o\s+superiore\s+a|maggiore\s+o\s+uguale\s+a)\s+(?:i\s+|il\s+|gli\s+|a\s+)?(\d+)", re.I), ">="),
    (re.compile(r"\b(?:al\s+massimo|non\s+pi[uù]\s+di|fino\s+a|minore\s+o\s+uguale\s+a|pari\s+o\s+inferiore\s+a)\s+(?:i\s+|il\s+|gli\s+|a\s+)?(\d+)", re.I), "<="),
    (re.compile(r"\b(?:sopra|oltre|pi[uù]\s+di|maggiore\s+di|superiore\s+a|superiori\s+a|maggiori\s+di)\s+(?:i\s+|il\s+|gli\s+|a\s+)?(\d+)", re.I), ">"),
    (re.compile(r"\b(?:sotto|meno\s+di|minore\s+di|inferiore\s+a|inferiori\s+a|minori\s+di)\s+(?:i\s+|il\s+|gli\s+|a\s+)?(\d+)", re.I), "<"),
]


def _wrong_comparison(question: str, cypher: str) -> str | None:
    """Il primo numero della domanda confrontato con l'operatore sbagliato, se c'e'."""
    expected: dict[str, str] = {}
    taken: set[int] = set()
    for pattern, operator in _COMPARISONS:  # le forme piu' specifiche vengono prima
        for match in pattern.finditer(question):
            if any(match.start() <= pos < match.end() for pos in taken):
                continue
            taken.update(range(match.start(), match.end()))
            expected.setdefault(match.group(1), operator)
    for number, operator in expected.items():
        used = set(re.findall(rf"(>=|<=|>|<)\s*{number}\b", cypher))
        if not used:
            # "minimo 30 presenze" restituite ma mai filtrate: 169 giocatori invece di 23.
            # "25 presenze medie annue" su 4 stagioni era diventato `presenze >= 100`: la soglia
            # va confrontata com'e', con la media, non ripiegata in un altro numero.
            average = re.search(rf"\b{number}\b[^.,;]{{0,40}}\bmedi[aeo]\w*|\bmedi[aeo]\w*[^.,;]{{0,40}}\b{number}\b", question, re.I)
            hint = (f" - it is an AVERAGE per season: compute it (count(DISTINCT m) / <number of seasons>.0, or avg over "
                    f"the per-season counts) and compare that value {operator} {number}; never multiply {number} by the "
                    "number of seasons") if average else ""
            return f"{number}: the question sets a threshold ({operator} {number}) but the query never compares anything with {number}{hint}"
        if operator not in used:
            return f"{number}: the question says {operator} {number}, the query uses {' / '.join(sorted(used))} {number}"
    return None


class TeamVariableReused(ValueError):
    """La stessa variabile Team come bersaglio di due relazioni diverse.

    `(p)-[:SEASON_TEAM {season: '2014/2015'}]->(t), (p)-[:SEASON_TEAM
    {season: '2015/2016'}]->(t)` impone lo stesso club nelle due stagioni:
    16 giocatori invece di 23, spariti i 7 che hanno cambiato squadra. Lo
    stesso con CURRENT_TEAM e PLAYS_FOR sulla stessa `t`.
    """


def _team_variable_reused(cypher: str) -> str | None:
    targets: dict[str, set[str]] = {}
    for spec, var in re.findall(r"-\[\s*(?:\w+)?\s*:\s*([^\]]+)\]->\s*\(\s*([A-Za-z_]\w*)\s*(?::\s*Team)?\s*\)", cypher):
        rel_type = spec.split("{")[0].split("*")[0].strip()
        if rel_type not in ("SEASON_TEAM", "CURRENT_TEAM", "PLAYS_FOR"):
            continue
        season = re.search(r"season\s*:\s*'([^']+)'", spec)
        targets.setdefault(var, set()).add(rel_type + (f" {season.group(1)}" if season else ""))
    for var, specs in targets.items():
        if len(specs) > 1:
            return f"{var} ({', '.join(sorted(specs))})"
    return None


class CurrentTeamForPastSeason(ValueError):
    """CURRENT_TEAM usato per una domanda su una stagione passata.

    "Rigoristi 2009/2010" con CURRENT_TEAM: il club e' quello del 2015/2016 e,
    con p.active aggiunto per coerenza, spariscono Lampard e tutti i ritirati.
    Il club di quella stagione e' SEASON_TEAM {season: '2009/2010', main: true},
    e i ritirati vi compaiono.
    """


def _past_seasons(question: str) -> list[str]:
    """Le stagioni nominate nella domanda diverse dall'ultima, in forma '2009/2010'."""
    seasons = []
    for start, end in re.findall(r"\b(20\d\d)\s*/\s*(20\d\d|\d\d)\b", question):
        full = f"{start}/{end if len(end) == 4 else '20' + end}"
        if full != "2015/2016" and full not in seasons:
            seasons.append(full)
    return seasons


def _current_team_for_past_season(question: str, cypher: str) -> str | None:
    past = _past_seasons(question)
    if past and re.search(r"\bCURRENT_TEAM\b", cypher):
        return past[0]
    return None


class FilteredNotReturned(ValueError):
    """Un valore usato in un filtro che le righe non mostrano.

    "Almeno 30 presenze in Serie A nelle due stagioni" restituiva stamina e
    club ma non le presenze: il filtro era invisibile, e verificarlo
    impossibile. La regola non dipende dalla formulazione: cio' su cui la
    query filtra e' per definizione rilevante, e va in tabella.
    """


_CONTEXT_PROPERTIES = {"season", "league", "main", "name", "active", "id", "top"}


def _filtered_not_returned(cypher: str) -> str | None:
    """Il primo termine di un confronto nel WHERE che non compare nella proiezione finale."""
    last_return = re.search(r"\bRETURN\b(?!.*\bRETURN\b)(.*)$", cypher, re.IGNORECASE | re.DOTALL)
    if not last_return:
        return None
    projection = re.split(r"\bORDER\s+BY\b|\bSKIP\b|\bLIMIT\b", last_return.group(1), flags=re.IGNORECASE)[0]
    squeeze = lambda text: re.sub(r"\s+", "", text)
    visible = squeeze(projection)
    # Gli alias sono colonne solo se compaiono come voce a se' della proiezione,
    # non dentro un'espressione: `stamina_2015 - stamina_2014 AS differenziale`
    # non mostra le due stamina.
    columns = {squeeze(item.split(" AS ")[-1] if re.search(r"\sAS\s", item) else item) for item in _split_top_level(projection)}
    # alias definiti ovunque (WITH o RETURN): `espr AS alias`, anche con parentesi annidate
    aliases: dict[str, str] = {}
    for clause in re.finditer(r"\b(?:WITH|RETURN)\b(.*?)(?=\bWHERE\b|\bMATCH\b|\bRETURN\b|\bWITH\b|\bORDER\b|\bLIMIT\b|\bSKIP\b|$)", cypher, re.IGNORECASE | re.DOTALL):
        for item in _split_top_level(clause.group(1)):
            parts = re.split(r"\s+AS\s+", item.strip(), flags=re.IGNORECASE)
            if len(parts) == 2 and re.fullmatch(r"\w+", parts[1].strip()):
                aliases[squeeze(parts[0])] = parts[1].strip()
    term = r"(\w+(?:\.\w+)?(?:\s*[-+*/]\s*\w+(?:\.\w+)?)*)"
    for clause in re.finditer(r"\bWHERE\b(.*?)(?=\bWITH\b|\bMATCH\b|\bRETURN\b|\bORDER\b|$)", cypher, re.IGNORECASE | re.DOTALL):
        for expr, literal in re.findall(term + r"\s*(?:>=|<=|>|<)\s*(-?\d+)", clause.group(1)):
            key = squeeze(expr)
            prop = key.split(".")[-1] if "." in key and not re.search(r"[-+*/]", key) else None
            if prop in _CONTEXT_PROPERTIES:
                continue
            components = re.findall(r"\w+(?:\.\w+)?", key)
            entity = components[0].split(".")[0]
            alias = aliases.get(key)
            shown = (
                key in columns
                or (alias is not None and alias in columns)
                or re.search(rf"\b(?:min|max|avg|collect)\([^)]*{re.escape(key.split('.')[-1])}", visible, re.IGNORECASE)
                or any(re.search(rf"\b{re.escape(a)}\b", visible) for k, a in aliases.items() if re.search(rf"\b(?:min|max|avg|collect)\([^)]*{re.escape(key.split('.')[-1])}", k, re.IGNORECASE))
                # un'espressione le cui componenti sono tutte in tabella si legge da sole
                or (len(components) > 1 and all(c in visible for c in components))
                # il filtro definisce un aggregato sull'entita' stessa: WHERE m.home_goals >= 6 ... count(m)
                or re.search(rf"\b(?:count|sum|avg|collect|min|max)\(\s*(?:DISTINCT\s+)?{re.escape(entity)}\b", visible, re.IGNORECASE)
            )
            if not shown:
                return expr.strip() + (f" (alias `{alias}`)" if alias else "")
    return None


class DoubleCountedMatches(ValueError):
    """Somma dei gol di partite raggiunte dalle squadre: ogni partita conta due volte."""


def _double_counted_matches(cypher: str) -> bool:
    """True se la query raggiunge le partite da entrambe le squadre e ne somma i gol totali.

    (t:Team)-[:HOME_TEAM|AWAY_TEAM]->(m) tocca ogni partita una volta per
    squadra: la somma di home_goals + away_goals raddoppia, e la Liga
    2015/2016 passa da 1043 a 2086 gol senza alcun errore.
    """
    both_sides = re.search(r"HOME_TEAM\s*\|\s*AWAY_TEAM|AWAY_TEAM\s*\|\s*HOME_TEAM", cypher)
    total = re.search(r"sum\(\s*\w+\.(home_goals|away_goals)\s*\+\s*\w+\.(home_goals|away_goals)\s*\)", cypher, re.IGNORECASE)
    return bool(both_sides and total)


class LeagueMembershipViaMatches(ValueError):
    """Appartenenza a un campionato espressa contando le partite, senza una stagione.

    'Difensori in Premier League alti piu' di 190 cm' non nomina una stagione
    e non chiede presenze: chiede chi gioca OGGI in Premier. La stessa domanda
    in due forme e' passata una volta dai gol di testa segnati in Premier (un
    solo difensore) e una volta dal conteggio delle presenze in carriera
    (Jelle van Damme, 4 partite anni fa, oggi allo Standard Liegi, e una
    colonna "presenze" mai richiesta che decideva l'ordine). L'appartenenza
    e' st.league su SEASON_TEAM {season: '2015/2016', main: true}: una
    relazione per giocatore, nessun conteggio.
    """


_LEAGUE_WORDS = re.compile(
    r"\bserie\s*a\b|\bpremier\b|\bliga\b|\bbundesliga\b|\bligue\s*1\b|\beredivisie\b|\bekstraklasa\b"
    r"|\bmassima serie\b|\bcampionato\s+(?:italiano|inglese|spagnolo|tedesco|francese|olandese|portoghese|belga|scozzese|polacco|svizzero)\b",
    re.I,
)
# Parole che rendono legittimo contare le partite di un campionato: presenze, gol, o un
# confronto tra campionati / squadre.
_MATCH_METRIC_WORDS = re.compile(
    r"\bpresenz\w*|\bpartit\w*|\bapparizion\w*|\btitolar\w*|\bgol\b|\breti?\b|\bsegna\w*|\bmarcat\w*"
    r"|\bcapocannonier\w*|\brigor\w*|\bautogol\b|\bautoret\w*|\bcampionati\b|\bsquadr\w*|\bclub\b"
    r"|\bclassific\w*|\bvittori\w*|\bsconfitt\w*|\bpareggi\w*|\bpunti\b|\bcollega\w*|\blegam\w*"
    r"|\bassist\w*|\btir[oi]\b|\bfall[oi]\b|\bcartellin\w*|\bammonizion\w*|\bespulsion\w*|\bcross\b|\bcorner\b|\bcalci\s+d'angolo|\bpossess\w*",
    re.I,
)


def _league_membership_via_matches(question: str, cypher: str) -> str | None:
    """Il filtro sulla lega letto dalle partite quando la domanda non lo giustifica, se c'e'."""
    if not _LEAGUE_WORDS.search(question) or _SEASON_WORDS.search(question) or _MATCH_METRIC_WORDS.search(question):
        return None
    matches = set(re.findall(r"\(\s*([A-Za-z_]\w*)\s*:\s*Match\b", cypher))
    for var in matches:
        found = re.search(rf"\b{re.escape(var)}\.league\s*(?:=|IN|<>)[^\n]*", cypher)
        if found:
            return found.group(0).strip()
    inline = re.search(r":\s*Match\s*\{[^}]*\bleague\s*:[^}]*\}", cypher)
    return inline.group(0) if inline else None


class OrderedByUnaskedAppearances(ValueError):
    """Elenco ordinato per presenze quando la domanda non le nomina.

    Le presenze erano entrate nella query solo per esprimere il campionato, e
    il modello, trovandosele davanti, le ha usate per ordinare e la risposta
    le ha presentate come criterio ("ordinati per presenze"). Il criterio di
    ordinamento e' la prima cosa che la risposta dichiara: deve essere una
    metrica che chi legge riconosce nella propria domanda.
    """


_APPEARANCE_WORDS = re.compile(r"\bpresenz\w*|\bpartit\w*|\bapparizion\w*|\btitolar\w*|\bgiocat[oe]\b|\bappearances\b", re.I)


def _ordered_by_unasked_appearances(question: str, cypher: str) -> str | None:
    """La chiave di ORDER BY finale che e' un conteggio di presenze non richiesto, se c'e'."""
    if _APPEARANCE_WORDS.search(question):
        return None
    last_return = re.search(r"\bRETURN\b(?!.*\bRETURN\b)(.*)$", cypher, re.IGNORECASE | re.DOTALL)
    if not last_return:
        return None
    order = re.search(r"\bORDER\s+BY\s+(.+?)(?:\bSKIP\b|\bLIMIT\b|$)", last_return.group(1), re.IGNORECASE | re.DOTALL)
    if not order:
        return None
    first = re.sub(r"\s+(?:ASC|DESC)\b.*$", "", _split_top_level(order.group(1))[0].strip(), flags=re.IGNORECASE).strip()
    if re.fullmatch(r"(?:\w+\.)?(?:presenze|appearances|partite|season_total|league_total)", first, re.IGNORECASE):
        return first
    matches = set(re.findall(r"\(\s*([A-Za-z_]\w*)\s*:\s*Match\b", cypher))
    for var in matches:
        if re.search(rf"\bcount\(\s*(?:DISTINCT\s+)?{re.escape(var)}\s*\)\s+AS\s+{re.escape(first)}\b", cypher, re.IGNORECASE):
            return first
    return None


class ThresholdsWithoutRankingMetric(ValueError):
    """Piu' soglie, nessuna metrica di classifica, e un ordine scelto dal modello.

    'Difensori in Premier alti piu' di 190 cm e pesanti piu' di 185 lbs con
    heading_accuracy > 82 e jumping > 80' e' uscita una volta ordinata per
    altezza, una per presenze, una per heading_accuracy: tre elenchi diversi
    degli stessi giocatori, con tre numeri diversi accanto ai nomi. Una soglia
    e' un filtro, non un criterio; quando la domanda non ne indica uno,
    l'ordine e' l'overall, la sola metrica che ogni scout riconosce, e tutte
    le forme della stessa domanda danno lo stesso elenco.
    """


_RANKING_WORDS = re.compile(
    r"\btop\b|\bmiglior\w*|\bpeggior\w*|\bclassific\w*|\bprim[oi]\s+\d|\bordin\w*|\bmassim[oi]\b|\bminim[oi]\b"
    r"|\bpi[uù]\s+(?:alt[oi]|fort[ei]|veloc[ei]|prolific\w*|giovan[ei]|vecchi\w*|esperti|present\w*|assist|tiri|falli|cartellin\w*|cross|corner|gol)\b|\bmaggior\s+numero\b"
    r"|\bcombinat\w*|\bsomma\w*|\bmargin\w*|\bcrescit\w*|\bcal[oi]\b|\bmedi[ao]\b",
    re.I,
)
_THRESHOLD = re.compile(r"(?:>=|<=|>|<|\bsopra\b|\bsotto\b|\boltre\b|\balmeno\b|\bmaggior[ei]\s+di\b|\bminor[ei]\s+di\b|\bsuperior[ei]\s+a\b|\binferior[ei]\s+a\b|\bpi[uù]\s+di\b|\bmeno\s+di\b)\s*\d+", re.I)


def _thresholds_without_ranking_metric(question: str, cypher: str) -> str | None:
    """La chiave di ORDER BY finale che non e' l'overall, quando la domanda impone solo soglie."""
    if len(_THRESHOLD.findall(question)) < 2 or _RANKING_WORDS.search(question) or _MATCH_METRIC_WORDS.search(question):
        return None
    if re.search(r"\boverall\b", question, re.I):
        return None  # una soglia sull'overall lo rende gia' la metrica naturale
    last_return = re.search(r"\bRETURN\b(?!.*\bRETURN\b)(.*)$", cypher, re.IGNORECASE | re.DOTALL)
    if not last_return or not re.search(r"\(\s*\w+\s*:\s*Player\b", cypher):
        return None
    order = re.search(r"\bORDER\s+BY\s+(.+?)(?:\bSKIP\b|\bLIMIT\b|$)", last_return.group(1), re.IGNORECASE | re.DOTALL)
    if not order:
        return "(no ORDER BY)"
    first = re.sub(r"\s+(?:ASC|DESC)\b.*$", "", _split_top_level(order.group(1))[0].strip(), flags=re.IGNORECASE).strip()
    if re.fullmatch(r"(?:\w+\.)?overall\w*", first, re.IGNORECASE):
        return None
    return first


class RatingNotCurrent(ValueError):
    """Attributi o gol letti su tutta la carriera per una domanda senza stagione.

    E' uno strumento di scouting: 'difensori con heading_accuracy > 82' chiede
    chi ha quel valore OGGI. La stessa domanda in due forme e' stata letta una
    volta su RATED {season: '2015/2016'} (Cahill) e una volta come massimo di
    carriera (Cahill, Hangeland, Kaboul e Distin, quest'ultimo con il valore
    di anni prima): due risposte a due domande diverse. Senza una stagione
    nella domanda, e senza una richiesta esplicita di carriera o di andamento,
    la stagione e' quella corrente, 2015/2016, su RATED e su SCORED.
    """


# Parole che spostano la domanda fuori dalla stagione corrente: una carriera intera,
# un andamento, piu' stagioni, un riferimento al passato.
_CAREER_WORDS = re.compile(
    r"\bcarrier\w*|\bdi\s+sempre\b|\btutt[oa]\s+(?:il|la|le|i)\s+(?:database|serie|stagioni|storia)|\bcomplessiv\w*|\bstoric\w*"
    r"|\bcrescit\w*|\bmiglior(?:at|ament)\w*|\bcal[oi]\b|\bcalat\w*|\bpeggior(?:at|ament)\w*|\bevoluzion\w*|\bandament\w*|\btrend\b|\bnel\s+tempo\b"
    r"|\bnegli\s+anni\b|\bpassat\w*|\bsempre\b|\bmai\b|\bcostant\w*|\bmantenend\w*|\bmantien\w*|\bconsecutiv\w*|\bdivers[ei]\b"
    r"|\bmedi[ao]\b|\bin\s+totale\b|\btotal[ei]\b|\bultim[ei]\s+\d|\bda\s+quando\b|\bex\b|\britirat\w*|\bera\b|\berano\b|\bstat[oi]\b",
    re.I,
)


def _rating_not_current(question: str, cypher: str) -> str | None:
    """La lettura di RATED o SCORED non ristretta al 2015/2016 in una domanda senza stagione, se c'e'."""
    if not question.strip() or _SEASON_WORDS.search(question) or _CAREER_WORDS.search(question):
        return None
    if re.search(r"\bRANKED\b|shortestPath", cypher):
        return None
    for var, rel in re.findall(r"\[\s*([A-Za-z_]\w*)\s*:\s*(RATED|ASSESSED|STYLE)\b", cypher):
        inline = re.search(rf"\[\s*{re.escape(var)}\s*:\s*{rel}\s*\{{[^}}]*\bseason\s*:\s*'([^']*)'", cypher)
        where = re.search(rf"\b{re.escape(var)}\.season\s*(?:=\s*'([^']*)'|IN\s*(\[[^\]]*\]))", cypher)
        season = inline.group(1) if inline else (where.group(1) or where.group(2)) if where else None
        if season != "2015/2016":
            return f"{rel} bound as `{var}`" + (f" restricted to {season}" if season else " without a season")
    if re.search(r"\[\s*\w*\s*:\s*(?:RATED|ASSESSED|STYLE)\s*\]", cypher):
        return "RATED or STYLE bound without a variable and without a season"
    event_type = re.search(rf":\s*{_EVENT_TYPES}\b", cypher)
    if event_type:
        matches = set(re.findall(r"\(\s*([A-Za-z_]\w*)\s*:\s*Match\b", cypher))
        events = re.findall(rf"\[\s*([A-Za-z_]\w*)\s*:\s*{_EVENT_TYPES}\b", cypher)
        seasons = {m.group(1) for var in matches | set(events) for m in re.finditer(rf"\b{re.escape(var)}\.season\s*=\s*'([^']*)'", cypher)}
        seasons |= set(re.findall(rf":\s*(?:Match|{_EVENT_TYPES})\s*\{{[^}}]*\bseason\s*:\s*'([^']*)'", cypher))
        label = f"the events ({event_type.group(0).strip()})"
        if not seasons:
            return f"{label} counted over every season"
        if seasons != {"2015/2016"}:
            return f"{label} restricted to {', '.join(sorted(seasons))}"
    return None


class SeasonTeamWithoutSeason(ValueError):
    """SEASON_TEAM legata senza stagione: una relazione per stagione, il giocatore si ripete.

    "Tra il 2012 e il 2016" ha disorientato il modello, che ha scritto
    `SEASON_TEAM {main: true}` senza `season`: Nzonzi e Cahill quattro volte,
    e il messaggio sui doppioni non diceva perche'. Il club in un intervallo
    di anni e' quello dell'ultima stagione dell'intervallo.
    """


def _season_team_without_season(cypher: str) -> str | None:
    """La variabile di una SEASON_TEAM che nessun vincolo di stagione restringe, se c'e'."""
    for match in re.finditer(r"\[\s*([A-Za-z_]\w*)?\s*:\s*SEASON_TEAM\b(\s*\{[^}]*\})?\s*\]", cypher):
        var, props = match.group(1), match.group(2) or ""
        if re.search(r"\bseason\s*:", props):
            continue
        if var and re.search(rf"\b{re.escape(var)}\.season\s*(?:=|\bIN\b)", cypher):
            continue
        return var or "SEASON_TEAM"
    return None


class GrowthNotRanked(ValueError):
    """Crescita filtrata come differenza ma non restituita ne' usata per ordinare.

    "Overall cresciuto di almeno 5 punti tra il 2014/15 e il 2015/16" e' tornata
    con i dieci giocatori giusti, ordinati pero' per overall 2015/16 e senza la
    colonna della crescita: Mahrez (+11) terzo dietro Smalling (+5), e il
    lettore a fare 83 - 78 da solo. La metrica della domanda e' la differenza:
    e' una colonna con un nome, ed e' la chiave dell'ORDER BY.
    """


_GROWTH_WORDS = re.compile(r"\bcresc\w*|\baument\w*|\bmiglior(?:at|ament)\w*|\bprogress\w*|\bguadagn\w*|\bmargin\w*|\bdifferenz\w*|\bcal[oi]\b|\bcalat\w*|\bpeggior(?:at|ament)\w*|\bpers[oi]\s+\w*\s*punt", re.I)


def _growth_not_ranked(question: str, cypher: str) -> str | None:
    """La differenza filtrata nel WHERE che non e' colonna e chiave di ordinamento, se c'e'."""
    if not _GROWTH_WORDS.search(question):
        return None
    last_return = re.search(r"\bRETURN\b(?!.*\bRETURN\b)(.*)$", cypher, re.IGNORECASE | re.DOTALL)
    if not last_return:
        return None
    squeeze = lambda text: re.sub(r"[\s()]", "", text)  # `(r2.overall - r1.overall)` e' la stessa espressione
    difference = r"\(?\s*((?:\w+\.)?\w+\s*-\s*(?:\w+\.)?\w+)\s*\)?"
    # `max(r.potential) - max(r.overall) AS crescita` e' la stessa differenza di
    # `r.potential - r.overall`: si confrontano le proprieta' sottratte, non il testo.
    aggregated = r"\(?\s*((?:\w+\()?(?:\w+\.)?\w+\)?\s*-\s*(?:\w+\()?(?:\w+\.)?\w+\)?)\s*\)?"
    signature = lambda text: tuple(re.findall(r"(\w+)\)?\s*(?:-|$)", re.sub(r"[\s()]", "", text) + "-")[:2])
    defined = re.findall(aggregated + r"\s+AS\s+(\w+)", cypher)
    aliases = {squeeze(e): a for e, a in defined}
    by_signature = {signature(e): a for e, a in defined}
    originals = {squeeze(e): re.sub(r"^\(\s*|\s*\)$", "", e.strip()) for e, _ in defined}
    for clause in re.finditer(r"\bWHERE\b(.*?)(?=\bWITH\b|\bMATCH\b|\bRETURN\b|\bORDER\b|$)", cypher, re.IGNORECASE | re.DOTALL):
        # Solo la crescita MINIMA (>= 5, > 0) e' la metrica della domanda; un limite superiore
        # ('senza cali drastici': max - min <= 10) e' un vincolo, e la classifica e' un'altra.
        filtered = re.findall(difference + r"\s*(?:>=|>)\s*-?\d+", clause.group(1))
        # Filtrata attraverso il suo alias (WITH ... AS crescita WHERE crescita >= 5): stessa cosa.
        filtered += [originals[e] for e, a in aliases.items() if re.search(rf"(?<![\w.]){re.escape(a)}\s*(?:>=|>)\s*-?\d+", clause.group(1))]
        for expr in filtered:
            key = squeeze(expr)
            if re.fullmatch(r"\d+-\d+", key):
                continue
            alias = aliases.get(key) or by_signature.get(signature(expr))
            tail = last_return.group(1)
            projection = re.split(r"\bORDER\s+BY\b", tail, flags=re.IGNORECASE)[0]
            shown = (alias is not None and re.search(rf"(?<![\w.]){re.escape(alias)}(?![\w.(])", projection)) or key in squeeze(projection)
            order = re.search(r"\bORDER\s+BY\s+(.+?)(?:\bSKIP\b|\bLIMIT\b|$)", tail, re.IGNORECASE | re.DOTALL)
            first = squeeze(re.sub(r"\s+(?:ASC|DESC)\b.*$", "", _split_top_level(order.group(1))[0].strip(), flags=re.IGNORECASE)) if order else ""
            ranked = first == key or (alias is not None and first == alias)
            if not shown or not ranked:
                return expr.strip()
    return None


class LeagueOnEarlierSeason(ValueError):
    """Appartenenza al campionato letta su una stagione precedente all'ultima in gioco.

    "Giocatori in Premier League con overall cresciuto tra il 2014/15 e il
    2015/16" ha letto la Premier su SEASON_TEAM del 2014/15: fuori Darmian
    (Torino), Kante (Caen) e Ighalo, arrivati in Premier nel 2015. "In Premier
    League" e' dove il giocatore gioca oggi, cioe' nell'ultima delle stagioni
    che la domanda tocca.
    """


def _league_on_earlier_season(cypher: str) -> str | None:
    """La SEASON_TEAM che filtra il campionato su una stagione precedente all'ultima della query."""
    seasons = set(re.findall(r"'(20\d\d/20\d\d)'", cypher))
    if len(seasons) < 2:
        return None
    latest = max(seasons)
    last_return = re.search(r"\bRETURN\b(?!.*\bRETURN\b)(.*)$", cypher, re.IGNORECASE | re.DOTALL)
    projection = last_return.group(1) if last_return else ""
    latest_targets = set()
    for var, props, target in re.findall(r"\[\s*([A-Za-z_]\w*)\s*:\s*SEASON_TEAM\s*(\{[^}]*\})\s*\]\s*->\s*\(\s*([A-Za-z_]\w*)?", cypher):
        season = re.search(r"season\s*:\s*'(20\d\d/20\d\d)'", props)
        if season and season.group(1) == latest and target:
            latest_targets.add(target)
    for var, props, target in re.findall(r"\[\s*([A-Za-z_]\w*)\s*:\s*SEASON_TEAM\s*(\{[^}]*\})\s*\]\s*->\s*\(\s*([A-Za-z_]\w*)?", cypher):
        season = re.search(r"season\s*:\s*'(20\d\d/20\d\d)'", props)
        if not season or season.group(1) == latest:
            continue
        if re.search(r"\bleague\s*:", props) or re.search(rf"\b{re.escape(var)}\.league\s*(?:=|IN)", cypher):
            return f"{var} (SEASON_TEAM {season.group(1)})"
        # Il club mostrato e' quello della stagione precedente mentre l'ultima e' in gioco.
        if target and latest_targets and re.search(rf"\b{re.escape(target)}\.name\b", projection) \
                and not any(re.search(rf"\b{re.escape(t)}\.name\b", projection) for t in latest_targets):
            return f"{var} (SEASON_TEAM {season.group(1)}), whose club {target}.name is the one returned"
    return None


class UnusedBinding(ValueError):
    """Relazione legata a una variabile mai usata: un filtro di esistenza silenzioso.

    "Overall cresciuto tra il 2014/15 e il 2015/16" teneva un
    `st1:SEASON_TEAM {season: '2014/2015'}` che nessuna clausola leggeva: il
    pattern esige comunque che la relazione esista, e Ighalo, Antonio, Wilson
    e Cathcart - in Championship nel 2014/15, fuori dal grafo, ma con il rating
    FIFA di quella stagione - sparivano: 6 invece di 10.
    """


_SILENT_FILTER_TYPES = {"SEASON_TEAM", "RATED", "ASSESSED", "APPEARED_IN", "SCORED", "OWN_GOAL", "PLAYS_FOR", "RANKED",
                        "ASSISTED", "SHOT", "FOUL", "FOULED", "CARD", "CROSS", "CORNER", "STYLE"}


def _unused_binding(cypher: str) -> str | None:
    """La prima relazione legata a una variabile che non compare in nessun'altra clausola."""
    for match in re.finditer(r"\[\s*([A-Za-z_]\w*)\s*:\s*([A-Za-z_]+)\b([^\]]*)\]", cypher):
        var, rel, props = match.group(1), match.group(2), match.group(3)
        if rel not in _SILENT_FILTER_TYPES:
            continue
        # Una proprieta' inline diversa da season/main ({league: ...}, {top: ...}) e' un filtro
        # voluto: RANKED {season, league} seleziona i club di un campionato. {season, main}
        # da soli non dicono nulla: sono il residuo di un pattern non usato.
        if any(key not in {"season", "main"} for key in re.findall(r"(\w+)\s*:", props)):
            continue
        rest = cypher[:match.start()] + cypher[match.end():]
        if re.search(rf"(?<![\w.]){re.escape(var)}(?![\w])", rest):
            continue
        # Il nodo di partenza, se non e' il giocatore: `(t:Team)-[k:RANKED]->()` con t usato.
        source = re.search(r"\(\s*([A-Za-z_]\w*)\s*(?::\s*\w+)?\s*(?:\{[^}]*\})?\s*\)\s*<?-\s*$", cypher[:match.start()])
        if source and source.group(1) not in re.findall(r"\(\s*([A-Za-z_]\w*)\s*:\s*Player\b", cypher):
            before = cypher[:source.start()]
            if re.search(rf"(?<![\w.]){re.escape(source.group(1))}(?![\w])", before + cypher[match.end():]):
                continue
        # La relazione serve anche solo a raggiungere il nodo: `-[st:SEASON_TEAM]->(t:Team)`
        # con t.name in RETURN e' usata.
        target = re.match(r"\s*-?>?\s*\(\s*([A-Za-z_]\w*)", cypher[match.end():])
        if target:
            node = target.group(1)
            after = cypher[match.end() + target.end():]
            if re.search(rf"(?<![\w.]){re.escape(node)}(?![\w])", cypher[:match.start()] + after):
                continue
        return f"{var}:{rel}"
    return None


class HardcodedClubs(ValueError):
    """Elenco di club scritto a mano, o fascia usata al posto del campionato.

    "Miglior marcatore di ogni squadra di Premier League" e' tornata una volta
    con 19 nomi di club scritti dal modello (mancava il Norwich) e una volta
    con `k.top = true`, che sono le prime della classifica, non il campionato:
    8 squadre invece di 20. I club di un campionato sono RANKED {season,
    league}, e nessuna delle due forme lo dice.
    """


def _hardcoded_clubs(question: str, cypher: str) -> str | None:
    """La lista di nomi di club, o il k.top non richiesto, se ci sono."""
    names = re.search(r"\b\w+\.name\s+IN\s*\[([^\]]*)\]", cypher)
    if names and len(re.findall(r"'[^']*'", names.group(1))) >= 3:
        return f"a hand-written list of {len(re.findall(chr(39) + '[^' + chr(39) + ']*' + chr(39), names.group(1)))} club names"
    top = re.search(r"\b(\w+)\.top\s*=\s*(?:true|false)", cypher, re.IGNORECASE)
    if top and not _TIER_WORDS.search(question) and not re.search(r"\bnon\s+(?:gioc\w*|milit\w*)\s+(?:gia'?\s+)?in\b", question, re.I):
        return f"`{top.group(0)}`, the tier of the club"
    return None


class GoalkeeperOwnShots(ValueError):
    """Tiri subiti da un portiere letti come i tiri che ha fatto lui.

    `(p)-[:SHOT]->(m)` con p portiere sono i (rarissimi) tiri del portiere,
    non quelli che ha subito: quelli sono st.shots_faced e
    st.shots_on_target_faced su SEASON_TEAM, calcolati a caricamento dai tiri
    degli avversari nelle partite in cui era in campo.
    """


def _goalkeeper_own_shots(question: str, cypher: str) -> bool:
    if not re.search(r"\bportier\w*|\bgoalkeeper\b", question, re.I) or not re.search(r"\bsubit\w*|\bconcess\w*|\bfaced\b|\bverso\s+la\s+porta\b", question, re.I):
        return False
    return bool(re.search(r"\)\s*-\s*\[\s*\w*\s*:\s*SHOT\b", cypher))


class UpperBoundOnJoinedCount(ValueError):
    """Limite superiore applicato a un conteggio di relazioni: chi ha zero sparisce.

    "Meno di 5 cartellini gialli" con `MATCH (p)-[c:CARD]` e poi `count(c) < 5`
    esclude ogni difensore senza cartellini, che e' proprio chi soddisfa meglio
    la condizione. Il totale su SEASON_TEAM ha lo zero.
    """


_UPPER_BOUND_WORDS = re.compile(r"\bmeno\s+di\b|\bal\s+massimo\b|\bnon\s+pi[uù]\s+di\b|\bfino\s+a\b|\bsotto\b|\binferior[ei]\s+a\b|\bminor[ei]\s+di\b|\bsenza\s+(?:cartellin|falli|espulsion)", re.I)
_EVENT_TYPES = r"(?:SCORED|ASSISTED|SHOT|FOUL|FOULED|CARD|CROSS|CORNER|OWN_GOAL)"


def _upper_bound_on_joined_count(question: str, cypher: str) -> str | None:
    """L'alias del conteggio di relazioni-evento confrontato con < o <=, se c'e'."""
    if not _UPPER_BOUND_WORDS.search(question):
        return None
    event_vars = re.findall(rf"(?<!OPTIONAL MATCH \()\[\s*([A-Za-z_]\w*)\s*:\s*{_EVENT_TYPES}\b", cypher)
    for var in event_vars:
        # OPTIONAL MATCH conserva lo zero: legittimo.
        binding = re.search(rf"\[\s*{re.escape(var)}\s*:", cypher)
        preceding = cypher[:binding.start()].rstrip()
        if re.search(r"OPTIONAL\s+MATCH\s*\([^)]*\)\s*-\s*$", preceding, re.IGNORECASE):
            continue
        for alias in re.findall(rf"count\(\s*(?:DISTINCT\s+)?{re.escape(var)}\s*\)\s+AS\s+(\w+)", cypher, re.IGNORECASE):
            if re.search(rf"(?<![\w.]){re.escape(alias)}\s*(?:<=|<)\s*\d+", cypher):
                return alias
    return None


class EventsWithoutSeason(ValueError):
    """Relazione-evento letta senza stagione in una domanda che la nomina.

    "Chi ha fornito piu' assist nel 2015/2016" e' tornata "Messi, 89": gli
    assist di tutta la carriera, perche' la riparazione precedente aveva
    tolto `m.season` dal conteggio. RATED aveva il suo controllo, gli eventi
    no.
    """


def _events_without_season(question: str, cypher: str) -> str | None:
    """La relazione-evento senza vincolo di stagione, se la domanda ne nomina una."""
    if not _SEASON_WORDS.search(question):
        return None
    for var, rel, props in re.findall(rf"\[\s*([A-Za-z_]\w*)?\s*:\s*({_EVENT_TYPES})\b\s*(\{{[^}}]*\}})?\s*\]", cypher):
        if props and re.search(r"\bseason\s*:", props):
            continue
        if var and re.search(rf"\b{re.escape(var)}\.season\s*(?:=|\bIN\b)", cypher):
            continue
        # La stagione puo' stare sulla partita raggiunta: (p)-[a:ASSISTED]->(m:Match) ... m.season = ...
        pattern = re.search(rf"\[\s*{re.escape(var) if var else ''}\s*:\s*{rel}\b[^\]]*\]\s*->\s*\(\s*([A-Za-z_]\w*)?\s*:?\s*Match\s*(\{{[^}}]*\}})?", cypher)
        if pattern:
            node, node_props = pattern.group(1), pattern.group(2)
            if node_props and re.search(r"\bseason\s*:", node_props):
                continue
            if node and re.search(rf"\b{re.escape(node)}\.season\s*(?:=|\bIN\b)", cypher):
                continue
        return f"{var + ':' if var else ''}{rel}"
    return None


class LeagueNotAsked(ValueError):
    """Filtro sul campionato che la domanda non contiene.

    "Portieri con piu' tiri subiti nel 2014/15 e 2015/16 con gk_reflexes >
    78" e' tornata con `s2.league = 'England Premier League'`, copiato da un
    esempio: 6 portieri invece di 26, e Handanovic, Zieler e Mandanda spariti
    senza che nulla lo dicesse. Un filtro in piu' e' silenzioso quanto uno in
    meno.
    """


_LEAGUE_TOKENS = {
    "England Premier League": r"\bpremier\b|\bingles\w*|\binghilterra\b|\bengland\b",
    "Spain LIGA BBVA": r"\bliga\b|\bspagn\w*|\bspain\b",
    "Italy Serie A": r"\bserie\s*a\b|\bitalian\w*|\bitalia\b|\bitaly\b",
    "Germany 1. Bundesliga": r"\bbundesliga\b|\btedesc\w*|\bgermania\b|\bgermany\b",
    "France Ligue 1": r"\bligue\b|\bfrances\w*|\bfrancia\b|\bfrance\b",
    "Netherlands Eredivisie": r"\beredivisie\b|\bolandes\w*|\bolanda\b|\bnetherlands\b",
    "Portugal Liga ZON Sagres": r"\bportoghes\w*|\bportogallo\b|\bportugal\b|\bsagres\b",
    "Belgium Jupiler League": r"\bbelg\w*|\bjupiler\b",
    "Scotland Premier League": r"\bscozzes\w*|\bscozia\b|\bscotland\b",
    "Poland Ekstraklasa": r"\bekstraklasa\b|\bpolacc\w*|\bpolonia\b|\bpoland\b",
    "Switzerland Super League": r"\bsvizzer\w*|\bswitzerland\b|\bsuper\s+league\b",
}


def _league_not_asked(question: str, cypher: str) -> str | None:
    """Il campionato filtrato dalla query che la domanda non nomina, se c'e'."""
    if re.search(r"\btop\s*5\b|\bgrandi\s+campionati|\bmassimi\s+campionati|\bcampionat\w*|\bleg[ah]\b|\bleague\b", question, re.I):
        return None  # 'top 5 campionati', 'per campionato': la lega e' in gioco per definizione
    for league in set(re.findall(r"league\s*(?:=|:)\s*'([^']+)'", cypher)) | set(re.findall(r"league\s+IN\s*\[([^\]]*)\]", cypher)):
        for name in re.findall(r"[A-Za-z][^,'\[\]]*", league) if "'" not in league and "," in league else [league.strip("' ")]:
            name = name.strip()
            tokens = _LEAGUE_TOKENS.get(name)
            if tokens and not re.search(tokens, question, re.I):
                return name
    return None


class SuperlativeWithoutLimit(ValueError):
    """Superlativo singolare senza LIMIT 1: "il giocatore con piu' assist" -> 1.485 righe.

    'Chi ha fornito piu' assist', 'il portiere con piu' tiri subiti', 'il
    capocannoniere' chiedono UNA risposta: la classifica intera e' un'altra
    domanda ('i giocatori con piu' assist', 'top 10').
    """


_SINGULAR_SUPERLATIVE = re.compile(
    r"\b(?:il|la|lo|l')\s+(?:giocator[e]|portier[e]|difensor[e]|centrocampist[a]|attaccant[e]|calciator[e]|esterno|terzino|squadra|club|team)\b[^,;?]*?\b(?:pi[uù]|maggior|miglior|massim|peggior)"
    r"|\bchi\s+(?:ha|e'|è|ha\s+avuto|ha\s+fornito|ha\s+segnato|ha\s+subito|ha\s+commesso)\b[^,;?]*?\b(?:pi[uù]|il\s+maggior|il\s+miglior|il\s+massimo)\b"
    r"|\bil\s+(?:capocannonier\w*|miglior\s+marcator\w*|migliore?)\b|\bqual\s+e'\s+il\b|\bqual\s+è\s+il\b",
    re.I,
)
_PLURAL_MARKERS = re.compile(r"\btop\s*\d*\b|\bprim[ei]\s+\d|\bclassific\w*|\bquali\b|\b(?:i|le|gli)\s+(?:\d+|dieci|cinque|tre|venti)\b|\bgiocatori\b|\bportieri\b|\bdifensori\b|\bcentrocampisti\b|\battaccanti\b|\bsquadre\b|\bcoppie\b|\b(?:per|di)\s+(?:ogni|ciascun)", re.I)


def _superlative_without_limit(question: str, cypher: str) -> bool:
    """True se la domanda chiede UN solo migliore e la query non ha LIMIT 1."""
    if not _SINGULAR_SUPERLATIVE.search(question) or _PLURAL_MARKERS.search(question):
        return False
    return not re.search(r"\bLIMIT\s+1\b", cypher, re.IGNORECASE)


class RankingWithoutLimit(ValueError):
    """Classifica senza numero e senza soglia, restituita per intero.

    "Quali coppie giocatore-assistman hanno prodotto piu' gol insieme" e'
    tornata con 8.775 righe: la domanda chiede i primi, non tutti. Senza un
    numero nella domanda e senza una soglia che delimiti l'insieme, la
    classifica e' LIMIT 10 - la stessa convenzione dell'elenco nella risposta.
    """


_PLURAL_SUPERLATIVE = re.compile(
    r"\b(?:quali|le|i|gli)\s+(?:\w+\s+){0,3}?(?:giocatori|portieri|difensori|centrocampisti|attaccanti|esterni|terzini|squadre|club|coppie)\b[^,;?]*?\b(?:pi[uù]|maggior\s+numero|miglior[ei]|massim[oi])\b"
    r"|\b(?:giocatori|portieri|difensori|centrocampisti|attaccanti|esterni|terzini|squadre|coppie)\s+con\s+(?:il\s+maggior\s+numero|pi[uù])\b",
    re.I,
)


def _ranking_without_limit(question: str, cypher: str) -> bool:
    """True se la domanda e' una classifica senza numero ne' soglia e la query non ha LIMIT."""
    if not _PLURAL_SUPERLATIVE.search(question):
        return False
    if re.search(r"\b\d+\b|\bdieci\b|\bcinque\b|\btre\b|\bventi\b|\btop\b|\bprim[ei]\b", question) or _THRESHOLD.search(question):
        return False
    if re.search(r"\b(?:per|di)\s+(?:ogni|ciascun)", question, re.I):
        return False  # uno per gruppo: il limite e' il gruppo
    return not re.search(r"\bLIMIT\s+\d+", cypher, re.IGNORECASE)


class AverageNotComputed(ValueError):
    """'Presenze medie annue' confrontate senza calcolare una media.

    "Almeno 25 presenze medie annue tra il 2012 e il 2016" e' stata letta come
    `st.appearances >= 25` della sola stagione 2015/16: 45 giocatori invece di
    39. La media e' un totale diviso il numero di stagioni, o avg(); una
    proprieta' di una sola stagione non lo e'.
    """


def _average_not_computed(question: str, cypher: str) -> bool:
    if not _AVERAGE_APPEARANCES.search(question):
        return False
    # Una divisione vera, non la barra di '2012/2013'.
    return not re.search(r"\bavg\s*\(|(?<![\d'])\s*/\s*(?:\d+(?:\.\d+)?|toFloat\(|size\()", cypher, re.IGNORECASE)


class ConsecutiveWindowMismatch(ValueError):
    """Finestra di N stagioni consecutive con l'aritmetica sbagliata: zero righe.

    `anni[i + 4]` confrontato con `anni[i] + 1`: cinque stagioni consecutive
    coprono quattro anni, non uno, e nessun giocatore soddisfa la condizione.
    Il risultato vuoto sembra una risposta ("nessuno"), ed e' un errore.
    """


def _consecutive_window_mismatch(cypher: str) -> str | None:
    """La coppia (ampiezza della finestra, differenza richiesta) incoerente, se c'e'."""
    windows = {int(k) for k in re.findall(r"\w+\[\s*i\s*\+\s*(\d+)\s*\]", cypher)}
    if not windows:
        return None
    k = max(windows)
    for span in re.findall(r"\[\s*i\s*\+\s*\d+\s*\]\s*\)*\s*-\s*(?:toInteger\(left\()?\w+\[\s*i\s*\]\s*(?:,\s*4\s*\)\))?\s*=\s*(\d+)", cypher):
        if int(span) != k:
            return f"a window ending at [i + {k}] compared with a span of {span}"
    for span in re.findall(r"\w+\[\s*i\s*\]\s*(?:,\s*4\s*\)\))?\s*\+\s*(\d+)\s*=\s*(?:toInteger\(left\()?\w+\[\s*i\s*\+\s*\d+\s*\]", cypher):
        if int(span) != k:
            return f"a window ending at [i + {k}] compared with a span of {span}"
    return None


class SeasonsInvented(ValueError):
    """Elenco di stagioni scritto dal modello in una domanda che non ne nomina.

    "Cinque stagioni consecutive senza cali" leggeva RATED su
    `IN ['2011/2012', ..., '2015/2016']`: le cinque stagioni sono quelle
    qualificanti di CIASCUN giocatore (IN anni), non un intervallo deciso a
    priori; il calo veniva misurato sulle stagioni sbagliate.
    """


def _seasons_invented(question: str, cypher: str) -> str | None:
    """La lista letterale di stagioni presente nella query ma non nella domanda, se c'e'."""
    if re.search(r"\b20\d\d\b", question):
        return None  # la domanda nomina anni: le stagioni possono essere scritte
    lists = re.findall(r"season\s+IN\s*\[([^\]]*)\]", cypher, re.IGNORECASE)
    for literal in lists:
        seasons = re.findall(r"'(20\d\d/20\d\d)'", literal)
        if len(seasons) >= 2:
            return f"season IN [{', '.join(seasons)}]"
    return None


class ListTypeMismatch(ValueError):
    """Appartenenza a una lista di tipo diverso: sempre falsa, senza errore.

    `collect(stagione) AS anni` raccoglie stringhe ('2012/2013');
    `toInteger(left(r.season, 4)) IN anni` confronta interi con stringhe e
    non e' mai vero. Il calo veniva misurato su nulla e 42 giocatori passavano.
    """


def _list_type_mismatch(cypher: str) -> str | None:
    """Il confronto `IN lista` tra un intero e una lista di stringhe (o viceversa), se c'e'."""
    strings = {alias for var, alias in re.findall(r"collect\(\s*(?:DISTINCT\s+)?(\w+)\s*\)\s+AS\s+(\w+)", cypher)
               if re.search(rf"\b\w+\.season\s+AS\s+{re.escape(var)}\b", cypher)}
    strings |= {alias for alias in re.findall(r"collect\(\s*(?:DISTINCT\s+)?\w+\.season\s*\)\s+AS\s+(\w+)", cypher)}
    integers = {alias for var, alias in re.findall(r"collect\(\s*(?:DISTINCT\s+)?(\w+)\s*\)\s+AS\s+(\w+)", cypher)
                if re.search(rf"toInteger\([^)]*\)\)?\s+AS\s+{re.escape(var)}\b", cypher)}
    for alias in strings:
        if re.search(rf"toInteger\([^\n]*?\)\s+IN\s+{re.escape(alias)}\b", cypher):
            return f"toInteger(...) IN {alias}, but {alias} holds season strings like '2012/2013'"
    for alias in integers:
        if re.search(rf"\b\w+\.season\s+IN\s+{re.escape(alias)}\b", cypher):
            return f"r.season IN {alias}, but {alias} holds integer years"
    return None


class RoleMismatch(ValueError):
    """Ruolo filtrato con un valore valido ma diverso da quello della domanda.

    "Ruolo esterno" e' diventato `p.role = 'forward'`: un valore che esiste,
    quindi `_wrong_role_value` taceva, ma gli esterni sono winger e
    wide_midfielder, e Messi e James Rodriguez sono usciti come esterni
    mancini al posto di Robben, Bale, Di Maria e Dembele.
    """


_DEPARTMENTS = {
    "goalkeeper": {"goalkeeper"},
    "defender": {"centre_back", "full_back"},
    "midfielder": {"central_midfielder", "wide_midfielder"},
    "forward": {"striker", "winger"},
}


def _role_mismatch(question: str, cypher: str) -> str | None:
    """Il filtro sul ruolo usato dalla query quando non corrisponde a quello della domanda."""
    expected = _expected_role_filter(question)
    if not expected:
        return None
    if "alternativ" in question.lower() or "sostitut" in question.lower():
        return None  # il ruolo viene letto dal giocatore di riferimento
    used_positions = set(re.findall(r"\.position\s*(?:=|IN)\s*(?:'([a-z_]+)'|\[([^\]]*)\])", cypher))
    positions = set()
    for single, many in used_positions:
        positions |= {single} if single else set(re.findall(r"'([a-z_]+)'", many))
    roles = set(re.findall(r"\.role\s*=\s*'([a-z_]+)'", cypher))
    if not positions and not roles:
        return None  # se ne occupa _missing_role
    expected_positions = set(re.findall(r"'([a-z_]+)'", expected)) if ".position" in expected else set()
    expected_role = re.search(r"\.role\s*=\s*'([a-z_]+)'", expected)
    if expected_positions:
        if positions & expected_positions:
            return None
        return f"the question's role is `{expected}`, the query filters " + (f"position {sorted(positions)}" if positions else f"role {sorted(roles)}")
    department = expected_role.group(1)
    if department in roles or (positions and positions <= _DEPARTMENTS[department]):
        return None
    return f"the question's role is `{expected}`, the query filters " + (f"role {sorted(roles)}" if roles else f"position {sorted(positions)}")


class LimitOneUnasked(ValueError):
    """LIMIT 1 su una domanda che chiede un elenco.

    "Alternativa economica a Hugo Lloris" e' tornata con il solo Buffon: il
    modello, rimandato per un giocatore ripetuto, ha "risolto" con LIMIT 1
    invece di aggregare. Un elenco ridotto a una riga e' una risposta a
    un'altra domanda.
    """


def _limit_one_unasked(question: str, cypher: str) -> bool:
    if not re.search(r"\bLIMIT\s+1\b", cypher, re.IGNORECASE):
        return False
    if _SINGULAR_SUPERLATIVE.search(question) or re.search(r"\bchi\b|\bquale\b|\bqual\b|\buno\b|\buna\b(?!\s+lista)|\bil\s+primo\b|\bcapocannonier", question, re.I):
        return False
    # LIMIT 1 su un giocatore di riferimento (WITH ref ... LIMIT 1) e' legittimo: conta solo
    # quello che chiude la query.
    tail = cypher[cypher.upper().rfind("RETURN"):]
    return bool(re.search(r"\bLIMIT\s+1\b", tail, re.IGNORECASE))


class UnorderedHead(ValueError):
    """Testa di una lista raccolta senza ordinarla prima: un elemento qualunque.

    `collect({nome, gol})[0]` senza ORDER BY gol DESC nella clausola precedente
    ha eletto Almen Abdi miglior marcatore del Watford: il primo che Neo4j ha
    incontrato, non il primo per gol.
    """


def _unordered_head(cypher: str) -> bool:
    clauses = re.split(r"(?=\bWITH\b|\bRETURN\b|\bMATCH\b|\bUNWIND\b)", cypher, flags=re.IGNORECASE)
    for index, clause in enumerate(clauses):
        if not re.search(r"\bcollect\s*\(", clause, re.IGNORECASE):
            continue
        aliases = re.findall(r"collect\s*\([^)]*(?:\)[^)]*)*\)\s*(?:\[\s*0\s*\])?\s+AS\s+(\w+)", clause, re.IGNORECASE)
        head_here = re.search(r"collect\s*\((?:[^()]|\([^()]*\))*\)\s*\[\s*0\s*\]", clause, re.IGNORECASE)
        head_later = any(re.search(rf"\b{re.escape(a)}\s*\[\s*0\s*\]", c) for a in aliases for c in clauses[index + 1:])
        if not (head_here or head_later):
            continue
        previous = " ".join(clauses[max(0, index - 2):index])
        if not re.search(r"\bORDER\s+BY\b", previous, re.IGNORECASE):
            return True
    return False


class RepairExhausted(RuntimeError):
    """Riparazioni esaurite: porta con se' la traccia di ogni tentativo."""

    def __init__(self, last: Exception, trace: list[dict[str, str]]) -> None:
        super().__init__(str(last))
        self.trace = trace


class GuardrailViolations(ValueError):
    """Tutte le violazioni trovate in una query, in un solo messaggio."""

    def __init__(self, violations: list[ValueError]) -> None:
        self.violations = violations
        if len(violations) == 1:
            super().__init__(str(violations[0]))
        else:
            lines = [f"The query has {len(violations)} problems; fix ALL of them in one rewrite:"]
            lines += [f"{i}. {v}" for i, v in enumerate(violations, start=1)]
            super().__init__("\n".join(lines))


class UnsafeCypher(ValueError):
    """Query non di sola lettura.

    Va tenuta distinta dagli errori di sintassi: quelli si fanno correggere al
    modello, questa no. Riproporla al modello significherebbe trasformare una
    richiesta di scrittura respinta in una query di lettura che la asseconda,
    aggirando di fatto il controllo.
    """


def _extract_cypher(text: str, question: str = "") -> str:
    match = re.search(r"```(?:cypher)?\s*(.*?)```", text, re.IGNORECASE | re.DOTALL)
    candidate = match.group(1) if match else text
    candidate = candidate.strip().rstrip(";")
    if not candidate.upper().startswith(("MATCH", "WITH", "CALL")):
        raise ValueError("The model did not return a read-only Cypher query")
    mutation = re.search(r"\b(CREATE|DELETE|DETACH|DROP|MERGE|REMOVE|SET)\b", candidate, re.IGNORECASE)
    if mutation:
        raise UnsafeCypher(mutation.group(1).upper())
    if not settings.guardrails:
        return candidate  # ablazione: nessun controllo e nessuna riscrittura
    # Tutti i controlli vengono valutati insieme: un difetto per giro di riparazione
    # non basta a una domanda con quattro difetti, e il modello corregge meglio quando
    # vede l'elenco completo.
    violations: list[ValueError] = []
    if _missing_role(question, candidate):
        expected_role = _expected_role_filter(question)
        violations.append(MissingRole(
            "The question names a role (or asks for an alternative to a named player, whose "
            "role must be shared), but the query filters neither p.position nor p.role. Add "
            + (f"exactly `{expected_role}` " if expected_role else "the role filter ")
            + "and keep every other condition of the question. If nothing "
            "satisfies all of them, an empty result is the correct answer."
        ))
    if _limit_one_unasked(question, candidate):
        violations.append(LimitOneUnasked(
            "The query ends with LIMIT 1, but the question asks for a list (alternatives, players "
            "with..., candidates): one row answers a different question. Remove the LIMIT; if a "
            "player was repeated, aggregate the relationship (max over PLAYS_FOR) instead of cutting "
            "the list."
        ))
    if _unordered_head(candidate):
        violations.append(UnorderedHead(
            "The query takes the head of a collected list ([0]) without an ORDER BY in the clause "
            "before the collect: the first element is arbitrary, not the best. Add `WITH t, p, gol "
            "ORDER BY gol DESC` (then the name) right before `WITH t, collect(...)[0]`."
        ))
    mismatched_role = _role_mismatch(question, candidate)
    if mismatched_role:
        violations.append(RoleMismatch(
            f"Wrong role: {mismatched_role}. 'esterno' is p.position IN ['winger', 'wide_midfielder'], "
            "'difensore' is p.role = 'defender', 'centravanti' is p.position = 'striker': use exactly "
            "the filter the question's role maps to."
        ))
    wrong_role = _wrong_role_value(candidate)
    if wrong_role:
        expected_role = _expected_role_filter(question)
        violations.append(WrongRoleValue(
            f"{wrong_role}. Neo4j returns no rows for an unknown value instead of an error. "
            + (f"The question's role is `{expected_role}`: use that." if expected_role else
               "p.role holds the department (goalkeeper, defender, midfielder, forward), p.position the detailed role.")
        ))
    hidden = _filtered_not_returned(candidate)
    if hidden:
        violations.append(FilteredNotReturned(
            f"`{hidden}` is used in a filter but is not a column of the result: the reader cannot "
            "verify the condition. RETURN every value the query filters on as its OWN column "
            "(the property, or the alias it already has after a WITH - add that alias to RETURN "
            "as a separate item, not only inside an expression), next to the name and the club."
        ))
    reused = _team_variable_reused(candidate)
    if reused:
        violations.append(TeamVariableReused(
            f"The same Team variable is the target of different relationships: {reused}. That "
            "forces them to be the same club and silently drops every player who changed team. "
            "Use a distinct variable per relationship (t1, t2) or an anonymous (:Team)."
        ))
    comparison = _wrong_comparison(question, candidate)
    if comparison:
        violations.append(WrongComparison(
            f"Wrong comparison operator for {comparison}. 'sopra', 'piu' di', 'oltre', 'superiore a' "
            "mean strictly >; 'almeno', 'minimo', 'non meno di', 'mai sotto' mean >=; 'sotto', "
            "'meno di', 'inferiore a' mean <; 'al massimo', 'non piu' di', 'fino a' mean <=."
        ))
    grouped = _aggregated_and_grouped(candidate)
    if grouped:
        violations.append(AggregatedAndGrouped(
            f"`{grouped}` is both aggregated and listed as a grouping key in the same WITH: "
            "every group holds a single one, counts are always 1 and minimums are trivial. "
            f"Remove the bare `{grouped}` from that WITH and keep only the aggregates. If you meant "
            "the BEST of each group (the top scorer of each club), do not aggregate: ORDER BY the "
            "metric DESC and take the head of the collected list per group - WITH t, "
            "collect({nome: p.name, gol: gol})[0] AS migliore - as in the schema example."
        ))
    per_season = _per_season_values_not_returned(question, candidate)
    if per_season:
        violations.append(PerSeasonValuesNotReturned(
            f"The threshold applies per season but `{per_season}` is not returned per season: "
            f"collect it - collect(stagione + ': ' + toString({per_season})) AS "
            f"{per_season.split('.')[-1]}_per_stagione, ordered by season - and RETURN that "
            "column so the reader can check each season."
        ))
    measure = _grouped_by_measure(candidate)
    if measure:
        violations.append(GroupedByMeasure(
            f"`{measure}` is a per-season value and appears bare in a WITH that collects the "
            "seasons: the rows are grouped by it, so two seasons of the same player end up in "
            f"one row only if their {measure} coincide. Aggregate it instead - "
            f"collect(stagione + ': ' + toString({measure})) AS {measure}_per_stagione, or "
            f"min({measure}) - never list it as a grouping key next to a collect()."
        ))
    threshold = _threshold_before_min(question, candidate)
    if threshold:
        violations.append(ThresholdBeforeMin(
            f"`{threshold}` is filtered in a WHERE before min(): that drops the seasons below "
            "the threshold instead of dropping the player who has one. For an attribute KEPT "
            "above N, remove that WHERE, aggregate WITH p, min(...) AS minimo, count(r) AS n, "
            "and filter afterwards: WHERE n = <number of seasons> AND minimo > N."
        ))
    if _appearances_not_per_season(question, candidate):
        violations.append(AppearancesNotPerSeason(
            "The question sets a threshold per season, but the matches are counted over all the "
            "seasons together. Count them per season - WITH p, m.season AS stagione, "
            "count(DISTINCT m) AS presenze WHERE presenze >= N - then require every named season "
            "to qualify (WITH p, collect(stagione) AS stagioni WHERE size(stagioni) = <number of "
            "seasons>), and return the per-season values as collect(stagione + ': ' + "
            "toString(presenze)) AS presenze_per_stagione, ordered by season."
        ))
    if _rated_without_season(question, candidate):
        violations.append(RatedWithoutSeason(
            "RATED is read without a season, so any season of the career qualifies. Restrict it "
            "to the seasons the question names - WHERE r.season IN [...] - and require every one "
            "of them to be present (count(r) = <number of seasons>)."
        ))
    if _kept_attribute_with_max(question, candidate):
        violations.append(KeptAttributeWithMax(
            "The question asks for an attribute KEPT above a threshold ('mantenendo', 'sempre', "
            "'mai sotto'): that is min(attribute) > N over the seasons considered, never max(). "
            "Return the per-season values too, as collect(r.season + ': ' + "
            "toString(r.stamina)) AS stamina_per_stagione, so the reader can check each season."
        ))
    unaggregated = _rated_unaggregated(candidate)
    if unaggregated:
        violations.append(RatedUnaggregated(
            f"`{unaggregated}` is returned unaggregated while several seasons of RATED are in "
            "play: the player comes back once per season. Aggregate it - min(), max(), avg() - "
            "or collect(r.season + ': ' + toString(...)) AS ..._per_stagione, one row per player."
        ))
    past_season = _current_team_for_past_season(question, candidate)
    if past_season:
        violations.append(CurrentTeamForPastSeason(
            f"The question is about season {past_season}, but the club is read through "
            "CURRENT_TEAM, the club of 2015/2016: read it with MATCH (p)-[st:SEASON_TEAM "
            f"{{season: '{past_season}', main: true}}]->(t:Team) and do NOT require p.active - a "
            "retired player belongs in the ranking of a past season."
        ))
    elif _current_team_without_active(candidate):
        violations.append(CurrentTeamWithoutActive(
            "The query shows the club through CURRENT_TEAM but does not require p.active: for "
            "a player who did not appear in 2015/2016 that is the club of his last appearance, "
            "years ago, presented as current. Add `p.active` to the WHERE clause (keep every "
            "other condition)."
        ))
    ordering = _ordering_not_returned(candidate)
    if ordering:
        alias = ordering.split(".")[-1] if re.fullmatch(r"\w+\.\w+", ordering) else "margine"
        violations.append(OrderingNotReturned(
            f"The list is ordered by `{ordering}` but that value is not returned: the reader "
            "cannot see the ranking criterion and the answer would show another number in its "
            f"place. Return it as a named column right after p.name (e.g. `{ordering} AS "
            f"{alias}`) and ORDER BY that alias."
        ))
    if _snapshot_rating_with_season(question, candidate):
        violations.append(SnapshotRatingWithSeason(
            "The question names a season, but overall/potential are read from PLAYS_FOR, which "
            "is the latest snapshot of the career. Read them from the RATED relationship of "
            "that season: MATCH (p)-[r:RATED {season: '<season>'}]->(:Season) ... r.overall, "
            "r.potential. The preferred foot is p.preferred_foot on the Player node."
        ))
    compared = _league_compared_to_name(candidate)
    if compared:
        violations.append(LeagueComparedToName(
            f"`{compared}` compares a team's or player's name with a league name: they never "
            "match, so the filter excludes nobody. The league is m.league on Match or k.league "
            "on RANKED. A club's tier is k.top on RANKED (true = 'prima fascia')."
        ))
    unknown = _unknown_property(candidate)
    if unknown:
        var, prop, label = unknown
        homes = sorted(name for name, props in SCHEMA_PROPERTIES.items() if prop in props)
        where = f" '{prop}' exists on {', '.join(homes)}" + (
            f": the preferred foot is p.preferred_foot on the Player node - write `p.preferred_foot = ...` "
            "and do not bind any relationship for it." if prop == "preferred_foot" else "."
        ) if homes else ""
        violations.append(UnknownProperty(
            f"{var}.{prop} does not exist: {label} has no property '{prop}', and Neo4j returns "
            f"null for it instead of an error.{where} Available on {label}: "
            f"{', '.join(sorted(SCHEMA_PROPERTIES[label])) or 'none'}. A team's goals are on "
            "the Match reached through (t:Team)-[:HOME_TEAM]->(m:Match); a player's club is "
            "CURRENT_TEAM or SEASON_TEAM; attributes live on RATED."
        ))
    missing = _missing_requirement(question, candidate)
    if missing:
        violations.append(MissingRequirement(
            f"The question mentions {missing}, but the query does not express it anywhere. "
            "Add that condition and keep every other one; if nothing satisfies all of them, "
            "an empty result is the correct answer."
        ))
    if _missing_player_name(candidate):
        violations.append(MissingPlayerName(
            "The rows describe players (their club, role or attributes) without their name: "
            "a scout reading 'Manchester United, striker, 26 goals' cannot tell who scored "
            "them. Return p.name first, then everything else the question asks for."
        ))
    if _single_match_threshold(candidate):
        violations.append(SingleMatchThreshold(
            "The query compares the goals of a SINGLE match against a threshold no match can "
            "reach: the question is about a total over a season, a league or a career. "
            "Aggregate over the matches of each TEAM - MATCH (t:Team)-[:HOME_TEAM]->(m:Match) "
            "... WITH t, sum(m.home_goals) AS gol WHERE gol > N - never `WITH m, sum(...)`, "
            "which groups by the match itself and leaves one match per row."
        ))
    unpinned = _season_team_without_season(candidate)
    if unpinned:
        violations.append(SeasonTeamWithoutSeason(
            f"SEASON_TEAM (`{unpinned}`) is bound without a season: it exists once per player per "
            "season, so every player comes back once per season. Always give it the season - "
            "SEASON_TEAM {season: '<season>', main: true}. For a question with no season or with "
            "a range of years, the club is that of the last season, '2015/2016'."
        ))
    mismatch = _list_type_mismatch(candidate)
    if mismatch:
        violations.append(ListTypeMismatch(
            f"Type mismatch in a list membership: {mismatch}. The comparison is never true and Neo4j "
            "raises no error. Compare like with like: r.season IN <list of strings>, or "
            "toInteger(left(r.season, 4)) IN <list of years>."
        ))
    invented_seasons = _seasons_invented(question, candidate)
    if invented_seasons:
        violations.append(SeasonsInvented(
            f"The query restricts to `{invented_seasons}`, but the question names no season: that list "
            "was invented. The seasons in play are the ones the data selects for EACH player: "
            "collect the qualifying seasons and filter RATED on them (r.season IN stagioni when the "
            "list holds season strings, toInteger(left(r.season, 4)) IN anni when it holds years). "
            "Never add an OR on a fixed season."
        ))
    window = _consecutive_window_mismatch(candidate)
    if window:
        violations.append(ConsecutiveWindowMismatch(
            f"The consecutive-seasons check is arithmetically wrong ({window}): N consecutive seasons "
            "span N - 1 years, so a window anni[i + 4] must satisfy anni[i + 4] - anni[i] = 4. As "
            "written no player can qualify and the answer would be an empty list."
        ))
    if _average_not_computed(question, candidate):
        violations.append(AverageNotComputed(
            "The question asks for an AVERAGE per season ('presenze medie annue', 'in media'), but the "
            "query computes no average: count the appearances over the seasons named and divide by "
            "their number (count(DISTINCT m) / 4.0 AS presenze_medie), or avg() over per-season "
            "counts, and compare THAT with the threshold. A single season's appearances are not an "
            "average."
        ))
    if _ranking_without_limit(question, candidate):
        violations.append(RankingWithoutLimit(
            "The question asks for the players (or clubs, or pairs) with the MOST of something, names "
            "no number and sets no threshold: it is a ranking, not the whole population. ORDER BY the "
            "metric DESC (then the name) and LIMIT 10, the default size of a ranking here."
        ))
    if _superlative_without_limit(question, candidate):
        violations.append(SuperlativeWithoutLimit(
            "The question asks for ONE player or club ('il giocatore con piu'...', 'chi ha ... piu'...'), "
            "but the query has no LIMIT 1: it would return the whole ranking. ORDER BY the metric "
            "DESC (then p.name) and LIMIT 1; keep every other condition."
        ))
    extra_league = _league_not_asked(question, candidate)
    if extra_league:
        violations.append(LeagueNotAsked(
            f"The query filters the league '{extra_league}', but the question never names a league: "
            "that condition was invented (probably copied from an example) and silently drops every "
            "player of the other leagues. Remove it and keep the conditions the question states."
        ))
    unpinned_event = _events_without_season(question, candidate)
    if unpinned_event:
        violations.append(EventsWithoutSeason(
            f"The question names a season, but `{unpinned_event}` is counted over every season: "
            "restrict it - e.season = '<season>' on the relationship (every event carries the "
            "season of its match) or m.season on the Match - and keep every other condition."
        ))
    if _goalkeeper_own_shots(question, candidate):
        violations.append(GoalkeeperOwnShots(
            "The question is about the shots a goalkeeper FACED, but the query reads (p)-[:SHOT]->(m): "
            "those are shots he took himself. The shots faced are st.shots_faced and "
            "st.shots_on_target_faced on his SEASON_TEAM {season: ..., main: true} with st.position = "
            "'goalkeeper' - flat properties, no join; for two named seasons bind one SEASON_TEAM per "
            "season and sum them. Return st.events_covered as well."
        ))
    bounded = _upper_bound_on_joined_count(question, candidate)
    if bounded:
        violations.append(UpperBoundOnJoinedCount(
            f"`{bounded}` is an upper bound on a count of matched relationships: a player with ZERO "
            "has no relationship to match and is silently dropped, though he satisfies the bound best. "
            "Read the total from SEASON_TEAM (st.yellow_cards, st.fouls_committed, st.shots...), where "
            "zero exists, or use OPTIONAL MATCH before counting."
        ))
    hardcoded = _hardcoded_clubs(question, candidate)
    if hardcoded:
        violations.append(HardcodedClubs(
            f"The query selects clubs through {hardcoded}: a written list is always incomplete and "
            "k.top is the top tier ('prima fascia'), not the league. The clubs of a league in a season "
            "are exactly (t:Team)-[k:RANKED {season: '2015/2016', league: '<league>'}]->(:Season); the "
            "players of a league are those with SEASON_TEAM {season: '2015/2016', main: true} and "
            "st.league = '<league>'."
        ))
    unused = _unused_binding(candidate)
    if unused:
        violations.append(UnusedBinding(
            f"`{unused}` is bound but never used: the pattern still requires that relationship to "
            "exist, so it silently drops every player who lacks it (a player rated in 2014/2015 but "
            "playing outside the eleven leagues that season has no SEASON_TEAM for it). Remove the "
            "pattern if the question does not need it; if it does, read something from it."
        ))
    earlier = _league_on_earlier_season(candidate)
    if earlier:
        violations.append(LeagueOnEarlierSeason(
            f"The league or the club comes from {earlier}, an earlier season than the last one the "
            "query uses: 'in Premier League' and 'the club' mean the LAST season in play. Move the "
            "league filter to the SEASON_TEAM of that season (st2.league = '...') - keep it as a "
            "filter, do not drop it - and return the club of that season (t2.name). Players who joined "
            "the league later would otherwise be dropped."
        ))
    membership = _league_membership_via_matches(question, candidate)
    if membership:
        violations.append(LeagueMembershipViaMatches(
            f"The question names a league but no season and asks for no appearances or goals: "
            f"it asks who plays in that league TODAY, yet the query reads it from the matches "
            f"(`{membership}`), which counts the whole career, adds an appearance column nobody "
            "asked for and, through :SCORED, drops every player who never scored there. Express "
            "it as MATCH (p)-[st:SEASON_TEAM {season: '2015/2016', main: true}]->(t:Team) WHERE "
            "st.league = '<league>' AND p.active, return t.name as the club, and do not count "
            "matches at all."
        ))
    not_current = _rating_not_current(question, candidate)
    if not_current:
        violations.append(RatingNotCurrent(
            f"The question names no season and asks for no career total or trend, so it is about "
            f"TODAY, the season 2015/2016 - but the query reads {not_current}. A scout wants the "
            "profile the player has now, not his best season years ago. Read RATED {season: "
            "'2015/2016'} (one per player: return r.<attribute> directly, no max()/min()) and, for "
            "goals, filter m.season = '2015/2016'. Keep every other condition."
        ))
    growth = _growth_not_ranked(question, candidate)
    if growth:
        violations.append(GrowthNotRanked(
            f"The question is about a growth or a difference, filtered as `{growth}`, but that "
            "difference is not a column of the result and the ranking criterion, so the reader has "
            "to compute it and the list opens with someone else. Return it as a named column "
            f"(`{growth} AS crescita`), right after p.name, and ORDER BY crescita DESC."
        ))
    no_metric = _thresholds_without_ranking_metric(question, candidate)
    if no_metric:
        violations.append(ThresholdsWithoutRankingMetric(
            f"The question only sets thresholds and names no ranking metric, but the list is ordered "
            f"by `{no_metric}`: a threshold is a filter, not a ranking criterion, and another wording "
            "of the same question would come out in a different order. Return r.overall AS overall "
            "(from RATED {season: '2015/2016'}, or max(r.overall) from PLAYS_FOR) as the column "
            "right after p.name and ORDER BY overall DESC, keeping every filtered value as its own "
            "column."
        ))
    unasked = _ordered_by_unasked_appearances(question, candidate)
    if unasked:
        violations.append(OrderedByUnaskedAppearances(
            f"The list is ordered by `{unasked}`, an appearance count the question never mentions: "
            "the reader would not recognise the criterion. Order by the metric the question is "
            "about; when it only sets thresholds on several attributes, return r.overall AS "
            "overall and ORDER BY overall DESC. Drop the appearance count unless the question asks "
            "for appearances."
        ))
    if _double_counted_matches(candidate):
        violations.append(DoubleCountedMatches(
            "The query reaches the matches through (t:Team)-[:HOME_TEAM|AWAY_TEAM]->(m) and "
            "sums home_goals + away_goals: every match has two teams, so each match is "
            "counted twice and the totals double. Start from MATCH (m:Match) and sum over "
            "the matches directly, without traversing from Team."
        ))
    if _missing_club(candidate):
        violations.append(MissingClub(
            "The query returns players (p.name) without their club as a column (t.name). Every "
            "list of players must include it - bind the Team to a variable and RETURN t.name; "
            "the explanation may only name a club it finds in the rows. If the question names "
            "a season, read it with "
            "MATCH (p)-[st:SEASON_TEAM {season: '<that season>', main: true}]->(t:Team) and "
            "return t.name (main: true is exactly one per player per season); otherwise read it with "
            "MATCH (p)-[:CURRENT_TEAM]->(t:Team) and return t.name. Add that MATCH after "
            "the aggregation and carry t through the WITH clauses."
        ))
    if len(violations) == 1:
        raise violations[0]
    if violations:
        raise GuardrailViolations(violations)
    return _stable_order(_harden_counts(candidate))


class ScoutAssistant:
    def __init__(self, graph: GraphStore) -> None:
        self.graph = graph
        self.client = OpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else None

    def answer(self, question: str) -> dict[str, Any]:
        if not self.client:
            return {
                "question": question,
                "answer": "LLM API non configurata. Inserisci OPENAI_API_KEY nel file .env.",
                "cypher": None,
                "rows": [],
                "subgraph": None,
                "repairs": [],
                "mode": "configuration_required",
            }

        missing = _unavailable_metric(question) if settings.guardrails else None
        if missing:
            return {
                "question": question,
                "answer": (
                    f"Il database non contiene {missing}: l'European Soccer Database registra "
                    "formazioni, gol con minuto e tipo, attributi FIFA per stagione e risultati, "
                    "ma non questa metrica. Rispondere richiederebbe di sostituirla con un'altra "
                    "grandezza, e il numero risultante sembrerebbe corretto pur non essendolo. "
                    "Sono invece disponibili: gol (anche su rigore o di testa), assist, tiri e tiri "
                    "in porta (anche subiti dai portieri), falli commessi e subiti, cartellini, cross, "
                    "calci d'angolo, possesso palla, presenze per campionato e stagione, ruolo, età, e "
                    "tutti gli attributi tecnici e fisici stagione per stagione."
                ),
                "cypher": None,
                "rows": [],
                "subgraph": None,
                "repairs": [],
                "mode": "unsupported",
            }

        # Ogni fase viene cronometrata: la latenza end-to-end da sola non dice
        # dove va il tempo, e la risposta lo dichiara insieme ai risultati.
        timings: dict[str, int] = {}
        started = time.perf_counter()
        try:
            cypher, rows, repairs, timings, trace = self._cypher_with_repair(question)
        except UnsafeCypher as operation:
            return {
                "question": question,
                "answer": (
                    "Richiesta rifiutata. L'assistente interroga il grafo in sola lettura: "
                    f"la domanda richiede un'operazione di scrittura ({operation}), che non "
                    "viene mai eseguita sul database."
                ),
                "cypher": None,
                "rows": [],
                "subgraph": None,
                "repairs": [],
                "mode": "rejected",
            }
        except Exception as error:
            # Le riparazioni sono esaurite. Restituire l'errore Cypher grezzo come
            # 500 non dice nulla a chi sta usando l'applicazione: meglio una
            # risposta che spieghi che la domanda non e' stata tradotta.
            detail = str(error).strip().splitlines()[0]
            trace = getattr(error, "trace", [])
            return {
                "question": question,
                "answer": (
                    "Non sono riuscito a tradurre questa domanda in una query valida, "
                    f"nemmeno dopo {REPAIR_ATTEMPTS} correzioni. Succede di solito "
                    "quando la domanda richiede un dato che il grafo non collega. "
                    f"Ultimo errore: {detail}"
                ),
                "cypher": None,
                "rows": [],
                "subgraph": None,
                "repairs": [step["error"] for step in trace],
                "trace": trace,
                "mode": "failed",
            }

        t0 = time.perf_counter()
        subgraph = build_evidence_subgraph(self.graph, cypher)
        timings["evidence_ms"] = round((time.perf_counter() - t0) * 1000)

        # Le righe vanno all'LLM troncate: un risultato ampio supera da solo la
        # finestra di contesto del modello e fa fallire la richiesta.
        sample = rows[:MAX_ROWS_FOR_LLM]
        omitted = len(rows) - len(sample)
        rows_note = f"Rows returned ({len(rows)}"
        rows_note += f", showing the first {len(sample)})" if omitted else ")"

        t0 = time.perf_counter()
        explanation = self.client.chat.completions.create(
            model=settings.openai_model,
            temperature=0,
            seed=LLM_SEED,
            messages=[
                {"role": "system", "content": ANSWER_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Question: {question}\n"
                        f"Cypher executed: {cypher}\n"
                        f"{rows_note}: {json.dumps(sample, default=str)}"
                    ),
                },
            ],
        )
        answer = explanation.choices[0].message.content or "Nessuna risposta generata."
        if _answer_denies_rows(answer, rows):
            retry = self.client.chat.completions.create(
                model=settings.openai_model,
                temperature=0,
                seed=LLM_SEED,
                messages=[
                    {"role": "system", "content": ANSWER_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"Question: {question}\n"
                            f"Cypher executed: {cypher}\n"
                            f"{rows_note}: {json.dumps(sample, default=str)}\n\n"
                            f"The result is NOT empty: {len(rows)} rows were returned. Your previous "
                            f"answer wrongly said nothing was found. Start with the number of results "
                            "and describe them from the rows."
                        ),
                    },
                ],
            )
            answer = retry.choices[0].message.content or ""
            if _answer_denies_rows(answer, rows):
                answer = (
                    f"Trovati {len(rows)} risultati che soddisfano le condizioni della query: "
                    "sono elencati nella tabella qui sotto."
                )
            repairs = repairs + ["explanation regenerated: it denied the rows returned"]
        # La chiusura "...e altri N nella tabella" la calcola il codice: il modello
        # ha scritto "altri 0" e perfino "altri N" copiando il segnaposto del prompt.
        answer = re.sub(r"\s*(?:\.{3}|…)?\s*\be altri \S+ nella tabella[^\n]*", "", answer).rstrip()
        # L'elenco "N. Nome (Club), valore" lo scrive il codice dalle righe: il modello ha
        # copiato la cifra sbagliata (lo sprint_speed al posto dell'overall) e invertito
        # due giocatori.
        answer = _rebuild_list(answer, cypher, rows)
        listed = len(re.findall(r"^\s*\d+\.\s", answer, re.MULTILINE))
        # Il criterio di ordinamento e' la prima cosa che l'apertura deve dire: senza,
        # il numero accanto a ogni nome non ha un significato dichiarato.
        ordering = _ordering_label(cypher)
        if listed >= 2 and ordering and not re.search(r"\bordinat\w*\b|\bin ordine\b", answer, re.IGNORECASE):
            first_line, _, rest = answer.partition("\n")
            answer = f"{first_line.rstrip()} Elenco ordinato per {ordering}.\n{rest}" if rest else f"{first_line.rstrip()} Elenco ordinato per {ordering}."
        if listed and len(rows) > listed:
            answer += f"\n\n…e altri {len(rows) - listed} nella tabella dei risultati."
        note = _tier_note(question, cypher)
        if note:
            answer = f"{note}\n\n{answer}"
        coverage = _coverage_note(cypher)
        if coverage:
            answer = f"{coverage}\n\n{answer}"
        timings["explain_ms"] = round((time.perf_counter() - t0) * 1000)
        timings["total_ms"] = round((time.perf_counter() - started) * 1000)
        return {
            "question": question,
            "answer": answer,
            "cypher": cypher,
            "rows": rows,
            "subgraph": subgraph,
            "repairs": repairs,
            # I tentativi scartati, con il loro Cypher: senza, una risposta sbagliata
            # arrivata dopo una riparazione non si puo' ricostruire.
            "trace": trace,
            "timings": timings,
            "mode": "graph_rag",
        }

    def _is_one_player(self, name: str, rows: list[dict[str, Any]]) -> bool:
        """True se le righe con quel nome sono piu' dei giocatori che lo portano.

        Le righe non contengono l'id, e tre Rafinha diversi (Barcellona, Bayern,
        Gent) con lo stesso numero di posizioni coperte sono indistinguibili da un
        giocatore ripetuto tre volte. Il grafo lo sa: se i giocatori con quel nome
        sono almeno quante le righe, sono omonimi, non un doppione.
        """
        occurrences = sum(1 for row in rows if name in {str(v) for v in row.values()})
        try:
            namesakes = self.graph.query("MATCH (p:Player {name: $name}) RETURN count(p) AS n", {"name": name})[0]["n"]
        except Exception:
            return True
        return occurrences > namesakes

    def _cypher_with_repair(self, question: str) -> tuple[str, list[dict[str, Any]], list[str], dict[str, int], list[dict[str, str]]]:
        """Genera il Cypher e, se il database lo rifiuta, lo fa correggere al modello.

        L'errore di Neo4j e' la descrizione piu' precisa di cosa non va nella
        query: restituirlo al modello costa una chiamata e recupera la maggior
        parte degli errori di scope (`Variable not defined`) e di sintassi.
        """
        messages: list[dict[str, str]] = [
            {"role": "system", "content": CYPHER_SYSTEM_PROMPT},
            {"role": "user", "content": f"Schema:\n{SCHEMA_DESCRIPTION}\n\nQuestion: {question}"},
        ]
        repairs: list[str] = []
        trace: list[dict[str, str]] = []
        generate_ms = 0.0
        database_ms = 0.0

        for attempt in range(REPAIR_ATTEMPTS + 1):
            t0 = time.perf_counter()
            completion = self.client.chat.completions.create(
                model=settings.openai_model,
                temperature=0 if attempt == 0 else 0.1,
                seed=LLM_SEED,
                messages=messages,
            )
            generate_ms += (time.perf_counter() - t0) * 1000
            raw = completion.choices[0].message.content or ""
            try:
                cypher = _extract_cypher(raw, question)
                t0 = time.perf_counter()
                rows = self.graph.query(cypher)
                database_ms += (time.perf_counter() - t0) * 1000
                null_key = _null_ranked_first(cypher, rows) if settings.guardrails else None
                if null_key:
                    raise NullRankedFirst(
                        f"The first row of the ranking has {null_key} = null: Neo4j sorts nulls "
                        "FIRST in ORDER BY ... DESC, so the answer would crown a player with no "
                        "data. Exclude missing values before ranking - WHERE <attribute> IS NOT "
                        "NULL on every property the ordering depends on - and keep every other "
                        "condition."
                    )
                duplicated = [name for name in _duplicated_players(cypher, rows) if self._is_one_player(name, rows)] if settings.guardrails else []
                if duplicated:
                    raise DuplicatedPlayers(
                        f"The rows repeat the same player ({', '.join(duplicated[:3])}): if the rows are "
                        "identical or differ only in attribute values, a relationship that exists once "
                        "per season (RATED) or per club (PLAYS_FOR) is bound but not aggregated - "
                        "aggregate it (max, min, avg) or collect it, one row per player; if you "
                        "joined (t)-[:HOME_TEAM|AWAY_TEAM]->(m:Match) to read the league, remove that "
                        "join and use st.league on SEASON_TEAM instead. If the rows differ by club, "
                        "he has one SEASON_TEAM or PLAYS_FOR per club. For a season use "
                        "SEASON_TEAM {season: X, main: true}, which is one per player; for the "
                        "current club use CURRENT_TEAM; never project the team of PLAYS_FOR. "
                        "Each player must appear exactly once."
                    )
                return cypher, rows, repairs, {
                    "generate_ms": round(generate_ms),
                    "database_ms": round(database_ms),
                    "attempts": attempt + 1,
                }, trace
            except UnsafeCypher:
                raise
            except Exception as error:
                # Di un GuardrailViolations la prima riga dice solo "3 problemi": nel
                # registro delle riparazioni vanno i singoli difetti, altrimenti una
                # risposta sbagliata non si puo' diagnosticare dopo.
                found = error.violations if isinstance(error, GuardrailViolations) else [error]
                lines = [str(v).strip().splitlines()[0] for v in found]
                # Il nome della classe di ogni violazione: e' cio' che il benchmark conta.
                kinds = [type(v).__name__ if isinstance(v, ValueError) and not isinstance(v, GuardrailViolations) else "Neo4jError" for v in found]
                trace.append({"cypher": raw.strip(), "error": " | ".join(lines), "kinds": kinds})
                if attempt == REPAIR_ATTEMPTS:
                    raise RepairExhausted(error, trace) from error
                repairs.extend(lines)
                messages.append({"role": "assistant", "content": raw})
                hint = ""
                undefined = re.search(r"Variable `(\w+)` not defined", str(error))
                if undefined:
                    hint = (
                        f"\n\nSCOPE: `{undefined.group(1)}` was defined in an earlier WITH and then dropped "
                        f"by a later WITH that did not list it. Add `{undefined.group(1)}` to EVERY WITH "
                        "between its definition and its use, next to the grouping variable p."
                    )
                messages.append({
                    "role": "user",
                    "content": (
                        f"That query failed with:\n{error}{hint}\n\n"
                        "Rewrite it so it runs, keeping EVERY filter and threshold of the "
                        "original question: a repaired query that silently drops a condition "
                        "is worse than the error it fixed. Re-read the question and check "
                        "each requirement is still expressed. Remember that every variable "
                        "you use in RETURN or ORDER BY must still be in scope: a WITH clause "
                        "drops everything it does not list, so carry forward the variables "
                        "you need. Return only the corrected Cypher."
                    ),
                })

        raise RuntimeError("Cypher non recuperabile")  # pragma: no cover - irraggiungibile
