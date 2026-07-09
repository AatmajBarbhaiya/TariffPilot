"""
Central configuration — everything env-driven so the same code runs on a laptop,
the AMD box, or against Fireworks with no edits. See ARCHITECTURE.md §5/§6.

Nothing here imports heavy deps; safe to import from anywhere.

NOTE: Fireworks is OPTIONAL. With no FIREWORKS_API_KEY set (the current state),
the Fireworks backend simply skips itself and the pipeline degrades gracefully
to keyword + vector signals — no crash, no hard dependency.
"""
import os
from pathlib import Path

# --- paths (anchored to this file, CWD-independent) -------------------------
ROOT = Path(__file__).resolve().parent
DB_PATH = str(ROOT / "Database" / "tariff_pilot.db")
CHROMA_PATH = str(ROOT / "Database" / "chroma_db")
CHROMA_COLLECTION = "hs_taxonomy"


def _env(name, default=""):
    return os.environ.get(name, default).strip()


# --- LLM backends -----------------------------------------------------------
# Local llama-server (OpenAI-compatible) is tried first; Fireworks is the
# fallback. Either can be the sole backend depending on which envs are set.
class LLM:
    # Local llama.cpp server (see ARCHITECTURE.md §4). api_key is a dummy —
    # llama-server ignores auth.
    BASE_URL = _env("LLM_BASE_URL", "http://localhost:8080/v1")
    MODEL = _env("LLM_MODEL", "local")
    API_KEY = _env("LLM_API_KEY", "sk-noauth")
    TIMEOUT = float(_env("LLM_TIMEOUT", "6"))          # seconds, local is close
    ENABLED = _env("LLM_LOCAL_ENABLED", "1") != "0"


class FIREWORKS:
    BASE_URL = _env("FIREWORKS_BASE_URL", "https://api.fireworks.ai/inference/v1")
    MODEL = _env("FIREWORKS_MODEL", "accounts/fireworks/models/gpt-oss-20b")
    API_KEY = _env("FIREWORKS_API_KEY", "")            # empty => backend skipped
    TIMEOUT = float(_env("FIREWORKS_TIMEOUT", "20"))


# --- retrieval thresholds (calibrate with retrieval/evaluate.py) ------------
# Min cosine similarity for a vector hit to count as a real candidate.
VECTOR_MIN_SIM = float(_env("VECTOR_MIN_SIM", "0.30"))
# Fast path (no LLM) requires keyword+vector to agree AND the vector to be at
# least this confident.
FASTPATH_MIN_SIM = float(_env("FASTPATH_MIN_SIM", "0.50"))
KEYWORD_K = int(_env("KEYWORD_K", "5"))
VECTOR_K = int(_env("VECTOR_K", "5"))
TOP_N = int(_env("TOP_N", "3"))                        # abstain -> top-N suggestions


# --- static scope map (Signal 2 / consistency guard) ------------------------
# Deliberately tiny + hand-maintained: the whole supported universe. Signal 2
# routes user text onto these; anything else is out of scope.
SCOPE_CHAPTERS = {
    "30": "pharmaceuticals & medical preparations (medicaments, vaccines, dressings)",
}
SCOPE_HEADINGS = {
    "9018": "medical/surgical/dental instruments & appliances",
    "9019": "mechano-therapy, massage, respiration apparatus",
    "9020": "breathing appliances & gas masks",
    "9021": "orthopaedic, prosthetic, hearing, pacemaker appliances",
    "9022": "x-ray & ionising-radiation apparatus",
    "9305": "parts & accessories of firearms",
    "9306": "ammunition & parts",
}

# Valid heading/chapter sets used to sanity-check LLM output + build filters.
ALL_HEADINGS = set(SCOPE_HEADINGS)
ALL_CHAPTERS = set(SCOPE_CHAPTERS)
