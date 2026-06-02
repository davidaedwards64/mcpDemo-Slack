"""FastAPI app — Phase 1: static Slack token, SSE streaming chat."""

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from .agent import run_agent

load_dotenv()

SESSION_SECRET = os.environ.get("SESSION_SECRET", "dev-secret-change-me")

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)

STATIC_DIR = Path(__file__).parent.parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class ChatRequest(BaseModel):
    message: str


@app.get("/", response_class=HTMLResponse)
async def index():
    return (STATIC_DIR / "index.html").read_text()


@app.post("/chat")
async def chat(req: ChatRequest):
    return StreamingResponse(
        run_agent(req.message),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
