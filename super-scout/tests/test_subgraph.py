"""Test della riscrittura Cypher che produce il sotto-grafo di evidenza.

Sono test puri: non toccano Neo4j, verificano solo l'analisi sintattica.
"""

import re

from app.subgraph import (
    MAX_NODES, _depth_mask, derive_evidence_query, derive_traversal_query, _analyze,
    relationship_filters, relationship_types, season_literals,
)


def normalized(query: str) -> str:
    return " ".join(query.split())


class TestDepthMask:
    def test_maschera_il_contenuto_delle_parentesi(self):
        mask = _depth_mask("MATCH (p:Player) RETURN p")
        assert "Player" not in mask
        assert "MATCH" in mask and "RETURN" in mask

    def test_maschera_le_stringhe(self):
        # Una parola chiave dentro una stringa non deve essere scambiata per clausola.
        mask = _depth_mask("MATCH (t:Team) WHERE t.name = 'RETURN WITH' RETURN t")
        assert len(re.findall(r"\bRETURN\b", mask)) == 1

    def test_conserva_gli_indici(self):
        text = "MATCH (p:Player) RETURN p"
        assert len(_depth_mask(text)) == len(text)


class TestDeriveEvidenceQuery:
    def test_proietta_le_entita_al_posto_degli_scalari(self):
        derived = derive_evidence_query(
            "MATCH (p:Player)-[r:PLAYS_FOR]->(t:Team) WHERE r.overall > 85 RETURN p.name, r.overall"
        )
        assert "RETURN DISTINCT p, t, r" in normalized(derived)
        assert "p.name" not in derived

    def test_conserva_i_filtri_originali(self):
        derived = derive_evidence_query(
            "MATCH (p:Player)-[r:PLAYS_FOR]->(t:Team) WHERE r.preferred_foot = 'left' RETURN p.name"
        )
        assert "r.preferred_foot = 'left'" in derived

    def test_conserva_order_by_se_le_variabili_sono_in_scope(self):
        derived = derive_evidence_query(
            "MATCH (p:Player)-[r:PLAYS_FOR]->(t:Team) RETURN p.name ORDER BY r.overall DESC"
        )
        assert "ORDER BY r.overall DESC" in derived

    def test_scarta_order_by_su_alias_definito_nella_return(self):
        # `nome` esiste solo nella proiezione che stiamo sostituendo: tenerlo
        # produrrebbe una query non valida.
        derived = derive_evidence_query(
            "MATCH (p:Player)-[r:PLAYS_FOR]->(t:Team) RETURN p.name AS nome ORDER BY nome"
        )
        assert "nome" not in derived

    def test_dopo_un_with_lo_scope_e_quello_del_with(self):
        derived = derive_evidence_query(
            "MATCH (t:Team)-[:HOME_TEAM]->(m:Match) WITH t, sum(m.home_goals) AS goals "
            "WHERE goals > 50 RETURN t.name, goals"
        )
        assert "RETURN DISTINCT t, goals" in normalized(derived)
        # `m` non e' piu' visibile dopo il WITH: proiettarlo sarebbe un errore.
        assert not re.search(r"DISTINCT[^\n]*\bm\b", normalized(derived))

    def test_limita_sempre_il_numero_di_nodi(self):
        derived = derive_evidence_query("MATCH (p:Player) RETURN p.name")
        assert f"LIMIT {MAX_NODES}" in derived

    def test_riduce_un_limit_troppo_grande(self):
        derived = derive_evidence_query("MATCH (p:Player) RETURN p.name LIMIT 5000")
        assert f"LIMIT {MAX_NODES}" in derived
        assert "5000" not in derived

    def test_rispetta_un_limit_piu_piccolo(self):
        derived = derive_evidence_query("MATCH (p:Player) RETURN p.name LIMIT 5")
        assert "LIMIT 5" in derived

    def test_rifiuta_union(self):
        assert derive_evidence_query("MATCH (p:Player) RETURN p.name UNION MATCH (t:Team) RETURN t.name") is None

    def test_rifiuta_query_senza_return(self):
        assert derive_evidence_query("MATCH (p:Player) WHERE p.name = 'x'") is None

    def test_non_scambia_una_funzione_per_un_nodo(self):
        derived = derive_evidence_query(
            "MATCH (t:Team)-[:HOME_TEAM]->(m:Match) RETURN count(m)"
        )
        # `count` non e' una variabile di pattern.
        assert not re.search(r"\bcount\b", normalized(derived).split("RETURN DISTINCT")[1])


class TestDeriveTraversalQuery:
    def test_ricostruisce_la_traversata_collassata_dall_aggregazione(self):
        analysis = _analyze(
            "MATCH (t:Team)-[:HOME_TEAM]->(m:Match) WHERE m.season = '2015/2016' "
            "WITH t, sum(m.home_goals) AS total ORDER BY total DESC LIMIT 5 RETURN t.name, total"
        )
        query, anchor = derive_traversal_query(analysis)
        assert anchor == "t"
        # Stessi filtri della query originale, ancorati ai nodi gia' selezionati.
        assert "m.season = '2015/2016'" in query
        assert "AND elementId(t) = anchorId" in query
        assert "UNWIND $ids AS anchorId" in query
        assert "RETURN DISTINCT t, m" in normalized(query)

    def test_usa_where_quando_la_query_non_ne_ha_uno(self):
        analysis = _analyze(
            "MATCH (t:Team)-[:HOME_TEAM]->(m:Match) WITH t, count(m) AS n RETURN t.name, n"
        )
        query, _ = derive_traversal_query(analysis)
        assert "WHERE elementId(t) = anchorId" in query

    def test_proietta_solo_variabili_ancora_vive_con_piu_with(self):
        # Query "alternativa a X": il primo WITH scarta ref/rr, il secondo tiene p.
        analysis = _analyze(
            "MATCH (ref:Player)-[rr:PLAYS_FOR]->(:Team) WHERE toLower(ref.name) CONTAINS 'lukaku' "
            "WITH max(rr.overall) AS riferimento "
            "MATCH (p:Player)-[s:SCORED]->(m:Match) WHERE m.season = '2015/2016' "
            "WITH riferimento, p, count(s) AS gol WHERE gol >= 21 "
            "MATCH (p)-[r:PLAYS_FOR]->(t:Team) WHERE r.overall < riferimento "
            "WITH p, gol, max(r.overall) AS overall RETURN p.name, gol, overall"
        )
        query, anchor = derive_traversal_query(analysis)
        assert anchor == "p"
        # ref e rr sono morte dopo il primo WITH: proiettarle sarebbe un errore.
        projection = normalized(query).split("RETURN DISTINCT")[1]
        assert "ref" not in projection and "rr" not in projection
        assert "t" in projection.split(", ")[0:5] or " t" in projection

    def test_nessuna_traversata_senza_aggregazione(self):
        analysis = _analyze("MATCH (p:Player)-[r:PLAYS_FOR]->(t:Team) RETURN p.name")
        assert derive_traversal_query(analysis) is None


class TestPathVariables:
    """Le domande sui collegamenti producono un cammino: e' l'evidenza migliore."""

    def test_riconosce_la_variabile_di_cammino(self):
        derived = derive_evidence_query(
            "MATCH (a:Player), (b:Player) WHERE a.name = 'x' AND b.name = 'y' "
            "MATCH percorso = shortestPath((a)-[:PLAYS_FOR*..6]-(b)) "
            "RETURN [n IN nodes(percorso) | n.name] AS catena, length(percorso) AS salti"
        )
        projection = normalized(derived).split("RETURN DISTINCT")[1]
        assert "percorso" in projection
        assert "a" in projection and "b" in projection

    def test_non_scambia_un_confronto_per_un_cammino(self):
        # `WHERE r.overall = (...)` non dichiara una variabile di cammino.
        derived = derive_evidence_query(
            "MATCH (p:Player)-[r:PLAYS_FOR]->(t:Team) WHERE r.overall > 85 RETURN p.name"
        )
        projection = normalized(derived).split("RETURN DISTINCT")[1]
        assert projection.strip().startswith("p, t, r")


class TestEdgeFilters:
    """Gli archi dell'evidenza sono quelli che la query ha attraversato."""

    RIGORISTI = (
        "MATCH (p:Player)-[s:SCORED]->(m:Match) WHERE m.season = '2009/2010' AND s.penalty = true "
        "WITH p, count(DISTINCT s) AS rigori ORDER BY rigori DESC LIMIT 10 "
        "MATCH (p)-[st:SEASON_TEAM {season: '2009/2010'}]->(t:Team) RETURN p.name, rigori, t.name"
    )

    def test_estrae_i_tipi_nominati(self):
        assert relationship_types(self.RIGORISTI) == ["SCORED", "SEASON_TEAM"]

    def test_estrae_le_alternative(self):
        assert relationship_types("MATCH (a)-[:PLAYS_FOR|CURRENT_TEAM*..6]-(b) RETURN a") == ["PLAYS_FOR", "CURRENT_TEAM"]

    def test_nessun_tipo_se_il_pattern_non_ne_nomina(self):
        assert relationship_types("MATCH (a:Player)-[*..6]-(b:Player) RETURN a") == []

    def test_estrae_le_stagioni_citate(self):
        assert season_literals(self.RIGORISTI) == ["2009/2010"]
        assert season_literals("MATCH (p:Player) RETURN p.name") == []


class TestTraversalJoiner:
    def test_aggancia_il_filtro_all_ultimo_segmento(self):
        """Un WHERE in un segmento precedente non e' piu' aperto: serve WHERE, non AND."""
        cypher = (
            "MATCH (p:Player)-[s:SCORED]->(m:Match) WHERE m.season = '2009/2010' "
            "WITH p, count(s) AS rigori ORDER BY rigori DESC LIMIT 10 "
            "MATCH (p)-[st:SEASON_TEAM {season: '2009/2010'}]->(t:Team) "
            "WITH p, rigori, collect(t.name) AS squadre RETURN p.name, rigori, squadre"
        )
        analysis = _analyze(cypher)
        query, anchor = derive_traversal_query(analysis, with_at=analysis["with_at"])
        assert anchor == "p"
        assert "(t:Team) WHERE elementId(p) IN $ids" in normalized(query)

    def test_il_primo_with_rigioca_i_gol(self):
        cypher = (
            "MATCH (p:Player)-[s:SCORED]->(m:Match) WHERE m.season = '2009/2010' AND s.penalty = true "
            "WITH p, count(s) AS rigori MATCH (p)-[st:SEASON_TEAM {season: '2009/2010'}]->(t:Team) "
            "RETURN p.name, rigori, t.name"
        )
        analysis = _analyze(cypher)
        first_with = _depth_mask(analysis["body"]).upper().find("WITH")
        query, _ = derive_traversal_query(analysis, with_at=first_with)
        assert "s.penalty = true AND elementId(p) = anchorId" in normalized(query)
        assert "RETURN DISTINCT p, m, s" in normalized(query)


class TestRelationshipFilters:
    def test_legge_le_uguaglianze_sulle_relazioni(self):
        filters = relationship_filters(
            "MATCH (p:Player)-[s:SCORED]->(m:Match) WHERE m.season = '2009/2010' AND s.penalty = true "
            "MATCH (p)-[st:SEASON_TEAM {season: '2009/2010', main: true}]->(t:Team) RETURN p.name"
        )
        assert filters == {"SCORED": {"penalty": True}, "SEASON_TEAM": {"season": "2009/2010", "main": True}}

    def test_ignora_i_filtri_sui_nodi(self):
        # m.season riguarda il nodo Match, non una relazione.
        assert relationship_filters("MATCH (p:Player)-[s:SCORED]->(m:Match) WHERE m.season = '2009/2010' RETURN p") == {}
