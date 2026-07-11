"""
TariffPilot backend API (ARCHITECTURE.md §2, §7).

Wraps the retrieval pipeline behind HTTP so the Streamlit UI *and* the hackathon
judge talk to it over the network instead of importing it in-process. The
serving path is READ-ONLY over SQLite + Chroma; the LLM (Signal 2 + arbiter) is
a remote OpenAI-compatible endpoint (AMD droplet primary, Fireworks fallback)
selected by env in config.py — nothing here bakes a key or a URL.

Endpoints:
  GET  /health                     liveness + LLM backend status
  POST /api/classify               {query, country[, use_llm]} -> result + card
  GET  /api/card/{hs6}/{country}   sourced card for a known code
"""
import threading
from contextlib import contextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import config
from llm.client import backend_status
from retrieval import classify
from retrieval.card import build_card

app = FastAPI(title="TariffPilot API", version="1.0")

# The UI reaches the API over the compose network (server-to-server, no browser
# CORS). Allowing all origins lets the judge harness / curl hit it directly too.
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


class ClassifyRequest(BaseModel):
    query: str
    country: str = "USA"
    use_llm: bool = True          # demo toggle; the judged default keeps LLM on


# The demo's LLM on/off is a per-request override of the env-configured backend.
# It mutates module-level config, so serialize the OFF path; the default ON path
# (what the judge hits) takes no lock. Real control is env, not this flag.
_llm_lock = threading.Lock()


@contextmanager
def _llm_disabled_if(off):
    if not off:
        yield
        return
    with _llm_lock:
        saved_local, saved_fw = config.LLM.ENABLED, config.FIREWORKS.API_KEY
        config.LLM.ENABLED, config.FIREWORKS.API_KEY = False, ""
        try:
            yield
        finally:
            config.LLM.ENABLED, config.FIREWORKS.API_KEY = saved_local, saved_fw


@app.get("/")
def root():
    return {"service": "TariffPilot API", "docs": "/docs", "health": "/health"}


@app.get("/health")
def health():
    return {"status": "ok", "llm": backend_status()}


@app.post("/api/classify")
def api_classify(req: ClassifyRequest):
    with _llm_disabled_if(not req.use_llm):
        return classify(req.query, req.country)


@app.get("/api/card/{hs6}/{country}")
def api_card(hs6: str, country: str):
    card = build_card(hs6, country)
    if card is None:
        return {"error": "not_found", "hs6": hs6, "country": country}
    return card
