from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .graph import GraphStore
from .rag import ScoutAssistant


STATIC_DIR = Path(__file__).resolve().parent / "static"

graph = GraphStore()


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    graph.close()


app = FastAPI(title="Super-Scout API", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
assistant = ScoutAssistant(graph)


class Question(BaseModel):
    question: str = Field(min_length=3, max_length=1000)


@app.get("/", include_in_schema=False)
def ui() -> FileResponse:
    # La pagina cambia a ogni build: il browser deve richiederla, non riusarla.
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/health")
def health() -> dict[str, str]:
    try:
        graph.verify()
    except Exception as error:
        raise HTTPException(status_code=503, detail=f"Neo4j unavailable: {error}") from error
    return {"status": "ok"}


@app.post("/ask")
def ask(payload: Question) -> dict:
    try:
        return assistant.answer(payload.question)
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
