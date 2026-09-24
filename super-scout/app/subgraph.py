"""Derivazione del sotto-grafo di evidenza a partire dal Cypher eseguito.

Il sotto-grafo non viene chiesto all'LLM: un'evidenza generata dal modello
sarebbe essa stessa allucinabile, e non proverebbe nulla. Viene invece derivata
in modo deterministico dalla query che ha effettivamente prodotto la risposta,
riscrivendone la sola proiezione finale in modo da restituire le entita del
grafo al posto degli scalari, e poi rileggendo dal database gli archi che le
collegano. Cio che viene disegnato e' quindi esattamente cio che Neo4j ha
attraversato per rispondere.

La riscrittura e' best-effort: qualunque query che non si riesca a riscrivere in
modo sicuro produce None, e l'API risponde senza sotto-grafo invece che con un
sotto-grafo sbagliato.
"""

from __future__ import annotations

import re
from typing import Any

from neo4j.graph import Node, Path

from .graph import GraphStore


MAX_NODES = 60
MAX_EDGES = 250
MAX_EXPANSION = 45

_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"

# Parole che compaiono in ORDER BY / SKIP / LIMIT senza essere variabili.
_TAIL_NOISE = {"order", "by", "skip", "limit", "asc", "desc", "distinct", "null", "nulls", "first", "last"}

_CLAUSE_KEYWORDS = r"\b(?:RETURN|ORDER\s+BY|SKIP|LIMIT|MATCH|OPTIONAL\s+MATCH|WHERE|UNWIND|CALL|MERGE|UNION)\b"


def _depth_mask(text: str) -> str:
    """Copia di `text` in cui stringhe e contenuto di parentesi sono spazi.

    Gli indici restano allineati all'originale, cosi una regex eseguita sulla
    maschera individua solo le clausole di primo livello e la posizione trovata
    puo essere usata per affettare il testo vero.
    """
    out: list[str] = []
    depth = 0
    quote: str | None = None
    escaped = False
    for char in text:
        if quote:
            out.append(" ")
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in "'\"`":
            quote = char
            out.append(" ")
            continue
        if char in "([{":
            depth += 1
            out.append(" ")
            continue
        if char in ")]}":
            depth = max(0, depth - 1)
            out.append(" ")
            continue
        out.append(char if depth == 0 else " ")
    return "".join(out)


def _last_top_level(mask: str, pattern: str) -> int:
    matches = list(re.finditer(pattern, mask, re.IGNORECASE))
    return matches[-1].start() if matches else -1


def _all_top_level(mask: str, pattern: str) -> list[int]:
    return [match.start() for match in re.finditer(pattern, mask, re.IGNORECASE)]


def _pattern_variables(text: str) -> list[str]:
    """Variabili legate a nodi e relazioni nei pattern, in ordine di comparsa."""
    found: list[str] = []
    # (p:Player), (p), (p {...})  -- la parentesi non deve essere una chiamata a funzione
    for match in re.finditer(r"(?<![\w.$])\(\s*(" + _IDENT + r")\s*(?=[:){])", text):
        found.append(match.group(1))
    # [r:PLAYS_FOR], [r], [r*..2]
    for match in re.finditer(r"\[\s*(" + _IDENT + r")\s*(?=[:*\]{])", text):
        found.append(match.group(1))
    # percorso = shortestPath((a)-[*..6]-(b)): la variabile di cammino contiene
    # gia' tutti i nodi e gli archi attraversati, ed e' l'evidenza migliore che
    # una domanda sui collegamenti possa produrre.
    for match in re.finditer(r"(?<![\w.])(" + _IDENT + r")\s*=\s*(?:[A-Za-z_]\w*\s*)?\(", text):
        found.append(match.group(1))
    return _dedupe(found)


def _split_top_level_commas(text: str) -> list[str]:
    mask = _depth_mask(text)
    parts: list[str] = []
    start = 0
    for index, char in enumerate(mask):
        if char == ",":
            parts.append(text[start:index])
            start = index + 1
    parts.append(text[start:])
    return [part for part in parts if part.strip()]


def _projection_aliases(projection: str) -> list[str]:
    """Nomi visibili a valle di un WITH: l'alias se c'e', altrimenti la variabile."""
    aliases: list[str] = []
    for item in _split_top_level_commas(projection):
        alias = re.search(r"\bAS\s+(" + _IDENT + r")\s*$", item.strip(), re.IGNORECASE)
        if alias:
            aliases.append(alias.group(1))
            continue
        bare = re.fullmatch(r"\s*(" + _IDENT + r")\s*", item)
        if bare:
            aliases.append(bare.group(1))
    return aliases


def _dedupe(names: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for name in names:
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


def _scope_at_end(text: str, mask: str) -> list[str]:
    """Variabili ancora visibili alla fine di un frammento di query.

    Un WITH cancella tutto cio' che non elenca: proiettare una variabile
    introdotta prima produrrebbe `Variable not defined`.
    """
    with_at = _last_top_level(mask, r"\bWITH\b")
    if with_at < 0:
        return _pattern_variables(text)

    after_with = with_at + len("WITH")
    next_clause = re.search(_CLAUSE_KEYWORDS, mask[after_with:], re.IGNORECASE)
    projection_end = after_with + (next_clause.start() if next_clause else len(mask) - after_with)
    scope = _projection_aliases(text[after_with:projection_end])
    scope += _pattern_variables(text[projection_end:])
    return _dedupe(scope)


def _analyze(cypher: str) -> dict[str, Any] | None:
    """Scompone la query nelle parti che servono a costruire l'evidenza."""
    text = cypher.strip().rstrip(";")
    mask = _depth_mask(text)

    if re.search(r"\bUNION\b", mask, re.IGNORECASE):
        return None

    return_at = _last_top_level(mask, r"\bRETURN\b")
    if return_at < 0:
        return None

    body = text[:return_at]
    body_mask = mask[:return_at]

    # Lo scope finale e' definito dall'ultimo WITH di primo livello, se esiste.
    with_at = _last_top_level(body_mask, r"\bWITH\b")
    scope = _scope_at_end(body, body_mask)
    if not scope:
        return None

    # ORDER BY / SKIP / LIMIT originali: si conservano solo se ogni identificativo
    # che citano e' ancora in scope, altrimenti l'ordinamento dell'evidenza
    # non corrisponderebbe a quello della risposta e va scartato.
    return_clause = text[return_at:]
    return_mask = mask[return_at:]
    tail_match = re.search(r"\b(?:ORDER\s+BY|SKIP|LIMIT)\b", return_mask, re.IGNORECASE)
    tail = ""
    if tail_match:
        candidate = return_clause[tail_match.start():]
        roots = {
            name.lower()
            for name in re.findall(r"(?<![\w.])(" + _IDENT + r")", candidate)
            if name.lower() not in _TAIL_NOISE
        }
        if roots <= {name.lower() for name in scope}:
            tail = candidate.strip()

    if re.search(r"\bLIMIT\b", tail, re.IGNORECASE):
        tail = re.sub(
            r"\bLIMIT\s+(\d+)",
            lambda m: f"LIMIT {min(int(m.group(1)), MAX_NODES)}",
            tail,
            flags=re.IGNORECASE,
        )
    else:
        tail = f"{tail} LIMIT {MAX_NODES}".strip()

    return {"text": text, "body": body, "scope": scope, "tail": tail, "with_at": with_at}


def derive_evidence_query(cypher: str) -> str | None:
    """Riscrive `cypher` in una query che restituisce le entita invece degli scalari."""
    analysis = _analyze(cypher)
    if analysis is None:
        return None
    return (
        f"{analysis['body'].strip()}\n"
        f"RETURN DISTINCT {', '.join(analysis['scope'])}\n"
        f"{analysis['tail']}"
    )


def derive_traversal_query(analysis: dict[str, Any], anchor_count: int = 5,
                           with_at: int | None = None) -> tuple[str, str] | None:
    """Query che ricostruisce la traversata collassata da un'aggregazione.

    Quando la query originale aggrega (`WITH t, sum(...)`), lo scope finale
    conserva solo il raggruppamento: il cammino percorso per calcolare la somma
    sparisce, e l'evidenza si riduce a nodi isolati. Questa query riesegue la
    porzione che precede il WITH, con gli stessi filtri, ancorandola alle entita
    gia selezionate: gli archi che restituisce sono quelli che l'aggregazione ha
    davvero attraversato, non un intorno arbitrario.
    """
    if with_at is None:
        with_at = analysis["with_at"]
    if with_at < 0:
        return None

    pre_body = analysis["text"][:with_at].strip()
    # Le variabili da proiettare sono quelle vive alla fine del frammento, non
    # tutte quelle mai nominate: una query con piu' WITH ne ha gia' scartate,
    # e riproiettarle darebbe `Variable not defined`.
    pre_vars = _scope_at_end(pre_body, _depth_mask(pre_body))
    anchors = [name for name in pre_vars if name in analysis["scope"]]
    if not anchors or len(pre_vars) < 2:
        return None

    anchor = anchors[0]
    pre_mask = _depth_mask(pre_body)
    # Il filtro va agganciato all'ULTIMO segmento: un WHERE piu' indietro, prima
    # di un altro MATCH o WITH, non e' piu' aperto e `AND` non compilerebbe.
    last_clause = max([-1] + _all_top_level(pre_mask, r"\b(?:MATCH|WITH|UNWIND)\b"))
    joiner = "AND" if re.search(r"\bWHERE\b", pre_mask[last_clause:], re.IGNORECASE) else "WHERE"
    projection = ", ".join(pre_vars)

    # La forma per-ancora richiede di importare anchorId in una subquery: se il
    # frammento contiene gia' un WITH, quel WITH scarterebbe anchorId e la query
    # non compilerebbe. In quel caso si ripiega su un filtro piatto, applicato in
    # coda dove l'ancora e' certamente ancora in scope.
    if _last_top_level(pre_mask, r"\bWITH\b") >= 0:
        query = (
            f"{pre_body} {joiner} elementId({anchor}) IN $ids\n"
            f"RETURN DISTINCT {projection}\n"
            f"LIMIT {MAX_EXPANSION}"
        )
        return query, anchor

    # Il budget si divide tra le entita ancora: un LIMIT unico lo esaurirebbe
    # sulle prime, lasciando le ultime senza archi e facendole sembrare isolate.
    per_anchor = max(2, MAX_EXPANSION // max(1, anchor_count))
    query = (
        "UNWIND $ids AS anchorId\n"
        "CALL {\n"
        "  WITH anchorId\n"
        f"  {pre_body} {joiner} elementId({anchor}) = anchorId\n"
        f"  RETURN DISTINCT {projection}\n"
        f"  LIMIT {per_anchor}\n"
        "}\n"
        f"RETURN DISTINCT {projection}\n"
        f"LIMIT {MAX_EXPANSION}"
    )
    return query, anchor


def relationship_types(cypher: str) -> list[str]:
    """I tipi di relazione nominati nei pattern della query.

    L'evidenza deve mostrare gli archi che la query ha attraversato, non tutti
    quelli che esistono tra i nodi trovati: per una domanda sul 2009/2010 il
    sotto-grafo disegnava le SEASON_TEAM di ogni stagione, le PLAYS_FOR e le
    CURRENT_TEAM, 61 archi tra 21 nodi di cui uno solo per nodo era pertinente.
    Una lista vuota significa che la query non nomina tipi (es. `[*..6]`) e
    l'evidenza li accetta tutti.
    """
    types: list[str] = []
    for spec in re.findall(r"\[[^\]]*\]", cypher):
        match = re.search(r":\s*([A-Za-z_][\w|\s]*)", spec)
        if not match:
            continue
        for name in match.group(1).split("|"):
            name = name.strip()
            if name and name not in types:
                types.append(name)
    return types


def relationship_filters(cypher: str) -> dict[str, dict[str, Any]]:
    """Le uguaglianze che la query impone alle relazioni, per tipo.

    `[s:SCORED] ... WHERE s.penalty = true` -> {'SCORED': {'penalty': True}}.
    Un giocatore che nella stessa partita segna un rigore e un gol su azione ha
    due SCORED verso quella partita: l'evidenza di una domanda sui rigori deve
    disegnare solo il rigore, come la query ha fatto per contarlo.
    """
    var_types: dict[str, str] = {}
    for var, rel_type in re.findall(r"\[\s*([A-Za-z_]\w*)\s*:\s*([A-Za-z_]\w*)", cypher):
        var_types[var] = rel_type
    filters: dict[str, dict[str, Any]] = {}
    for var, prop, literal in re.findall(r"\b([A-Za-z_]\w*)\.(\w+)\s*=\s*(true|false|'[^']*'|-?\d+)\b", cypher, re.IGNORECASE):
        if var not in var_types:
            continue
        value: Any
        if literal.lower() in ("true", "false"):
            value = literal.lower() == "true"
        elif literal.startswith("'"):
            value = literal[1:-1]
        else:
            value = int(literal)
        filters.setdefault(var_types[var], {})[prop] = value
    # Le mappe inline: [st:SEASON_TEAM {season: '2009/2010', main: true}]
    for var, body in re.findall(r"\[\s*([A-Za-z_]\w*)\s*:\s*[A-Za-z_]\w*\s*\{([^}]*)\}", cypher):
        for prop, literal in re.findall(r"(\w+)\s*:\s*(true|false|'[^']*'|-?\d+)", body, re.IGNORECASE):
            value = literal.lower() == "true" if literal.lower() in ("true", "false") else (literal[1:-1] if literal.startswith("'") else int(literal))
            filters.setdefault(var_types[var], {})[prop] = value
    return filters


def season_literals(cypher: str) -> list[str]:
    """Le stagioni citate come costanti nella query, es. '2009/2010'."""
    return _dedupe(re.findall(r"'(20\d\d/20\d\d)'", cypher))


def _caption(labels: list[str], properties: dict[str, Any]) -> str:
    if properties.get("name"):
        return str(properties["name"])
    if "Match" in labels:
        season = properties.get("season", "")
        home, away = properties.get("home_goals"), properties.get("away_goals")
        if home is not None and away is not None:
            return f"{season} · {home}-{away}".strip(" ·")
        return str(season) or "Match"
    return labels[0] if labels else "?"


def _jsonable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return str(value)


def _iter_nodes(value: Any):
    """Estrae i nodi da un valore restituito dal driver.

    Un valore puo' essere un nodo, un cammino (che ne contiene molti) o una
    lista prodotta da collect(): tutti e tre vanno appiattiti.
    """
    if isinstance(value, Node):
        yield value
    elif isinstance(value, Path):
        yield from value.nodes
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_nodes(item)


def _collect_nodes(records: list[Any], into: dict[str, dict[str, Any]], limit: int, expanded: bool = False) -> bool:
    """Aggiunge i nodi dei record; restituisce True se il limite e' stato raggiunto."""
    for record in records:
        for value in [node for item in record.values() for node in _iter_nodes(item)]:
            if value.element_id in into:
                continue
            if len(into) >= limit:
                return True
            labels = list(value.labels)
            properties = {key: _jsonable(item) for key, item in dict(value).items()}
            into[value.element_id] = {
                "id": value.element_id,
                "labels": labels,
                "label": labels[0] if labels else "Node",
                "caption": _caption(labels, properties),
                "properties": properties,
                "expanded": expanded,
            }
    return len(into) >= limit


def _edges_between(graph: GraphStore, node_ids: list[str], types: list[str] | None = None,
                   seasons: list[str] | None = None,
                   filters: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Gli archi tra i nodi dell'evidenza, limitati a cio' che la query ha usato.

    `types` restringe ai tipi nominati nel Cypher; `seasons` scarta gli archi
    con una proprieta' `season` diversa da quelle citate nella query (SEASON_TEAM
    e RATED esistono una volta per stagione). Entrambi vuoti = nessun filtro.
    """
    try:
        records = graph.raw_query(
            """
            MATCH (a)-[r]->(b)
            WHERE elementId(a) IN $ids AND elementId(b) IN $ids
              AND (size($types) = 0 OR type(r) IN $types)
              AND (size($seasons) = 0 OR r.season IS NULL OR r.season IN $seasons)
            RETURN elementId(r) AS id, elementId(a) AS source, elementId(b) AS target,
                   type(r) AS type, properties(r) AS properties
            LIMIT $cap
            """,
            {"ids": node_ids, "cap": MAX_EDGES, "types": types or [], "seasons": seasons or []},
        )
    except Exception:
        return []
    edges = [
        {
            "id": record["id"],
            "source": record["source"],
            "target": record["target"],
            "type": record["type"],
            "properties": {key: _jsonable(value) for key, value in (record["properties"] or {}).items()},
        }
        for record in records
    ]
    if filters:
        edges = [
            edge for edge in edges
            if all(edge["properties"].get(prop) == value for prop, value in (filters.get(edge["type"]) or {}).items())
        ]
    return edges


def build_evidence_subgraph(graph: GraphStore, cypher: str) -> dict[str, Any] | None:
    """Esegue la query di evidenza e ne restituisce nodi e archi serializzati."""
    analysis = _analyze(cypher)
    if analysis is None:
        return None

    evidence_query = derive_evidence_query(cypher)
    try:
        records = graph.raw_query(evidence_query)
    except Exception:
        # Una riscrittura non valida non deve mai far fallire la risposta.
        return None

    nodes: dict[str, dict[str, Any]] = {}
    truncated = _collect_nodes(records, nodes, MAX_NODES)
    if not nodes:
        return None

    types = relationship_types(cypher)
    seasons = season_literals(cypher)
    filters = relationship_filters(cypher)
    queries = [evidence_query]

    # Un'aggregazione collassa la traversata che l'ha prodotta: le partite in cui
    # dieci rigoristi hanno segnato i loro rigori. Si rigioca sempre quella
    # traversata, non solo quando i nodi restano scollegati: prima, bastava che
    # tra giocatori e squadre esistesse un arco qualsiasi perche' l'evidenza del
    # conteggio - i gol - non venisse mai disegnata. Con piu' WITH si rigioca il
    # primo tratto (gli input dell'aggregazione) e l'ultimo (il club letto dopo).
    body_mask = _depth_mask(analysis["body"])
    with_positions = _all_top_level(body_mask, r"\bWITH\b")
    anchor_ids = list(nodes)
    for with_at in _dedupe([with_positions[0], with_positions[-1]] if with_positions else []):
        traversal = derive_traversal_query(analysis, anchor_count=len(anchor_ids), with_at=with_at)
        if traversal is None:
            continue
        traversal_query, _ = traversal
        try:
            extra = graph.raw_query(traversal_query, {"ids": anchor_ids})
        except Exception:
            extra = []
        if extra:
            truncated = _collect_nodes(extra, nodes, MAX_NODES + MAX_EXPANSION, expanded=True) or truncated
            queries.append(traversal_query)
            # Il budget per ancora e' un campione: se un'ancora lo esaurisce
            # (Lampard: 10 rigori, 4 mostrati) l'evidenza va dichiarata parziale.
            per_anchor = max(2, MAX_EXPANSION // max(1, len(anchor_ids)))
            per_anchor_hits: dict[str, int] = {}
            for record in extra:
                first = next((n for item in record.values() for n in _iter_nodes(item) if n.element_id in anchor_ids), None)
                if first is not None:
                    per_anchor_hits[first.element_id] = per_anchor_hits.get(first.element_id, 0) + 1
            if any(count >= per_anchor for count in per_anchor_hits.values()) or len(extra) >= MAX_EXPANSION:
                truncated = True

    edges = _edges_between(graph, list(nodes), types, seasons, filters)

    return {
        "nodes": list(nodes.values()),
        "edges": edges,
        "queries": queries,
        "truncated": truncated,
    }
