from typing import Any

from neo4j import GraphDatabase, Record

from .config import settings


SCHEMA = """
CREATE CONSTRAINT player_id IF NOT EXISTS FOR (p:Player) REQUIRE p.id IS UNIQUE;
CREATE CONSTRAINT team_id IF NOT EXISTS FOR (t:Team) REQUIRE t.id IS UNIQUE;
CREATE CONSTRAINT match_id IF NOT EXISTS FOR (m:Match) REQUIRE m.id IS UNIQUE;
"""


class GraphStore:
    def __init__(self) -> None:
        self.driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
        )

    def close(self) -> None:
        self.driver.close()

    def verify(self) -> bool:
        with self.driver.session() as session:
            session.run("RETURN 1").consume()
        return True

    def create_schema(self) -> None:
        with self.driver.session() as session:
            for statement in SCHEMA.strip().split(";"):
                if statement.strip():
                    session.run(statement).consume()

    def query(self, cypher: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        with self.driver.session() as session:
            result = session.run(cypher, parameters or {})
            return [record.data() for record in result]

    def raw_query(self, cypher: str, parameters: dict[str, Any] | None = None) -> list[Record]:
        """Come `query`, ma senza appiattire i record.

        `record.data()` converte un nodo nel solo dizionario delle proprieta,
        perdendo etichette ed elementId: per costruire il sotto-grafo servono le
        entita originali del driver.
        """
        with self.driver.session() as session:
            result = session.run(cypher, parameters or {})
            return list(result)
