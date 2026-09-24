from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "super-scout-dev"
    # Interruttore per l'ablazione sperimentale: con GUARDRAILS=off il Cypher generato
    # viene eseguito cosi' com'e' (resta solo il rifiuto delle scritture), le righe non
    # vengono controllate e le metriche assenti non vengono intercettate.
    guardrails: bool = True

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
