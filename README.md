# TariffPilot

**An HS-code classifier + tariff / landed-cost lookup, with a `source_url` behind
every number.** Type a product in plain English ("shotgun shells", "whisky", "MRI
machine") and TariffPilot returns the 6-digit HS classification, the applicable
import duty, any licensing restrictions, and — crucially — the official
government URL each figure came from. The reasoning step runs on a **self-hosted
LLM on an AMD Instinct MI300X** (vLLM on ROCm), with Fireworks as an optional
cloud fallback.

## 🔗 Live demo

**👉 [http://134.199.196.55:8501/](http://134.199.196.55:8501/)**

The full stack is already running on the AMD Instinct MI300X — just open the link
and try a query (e.g. `whisky` → USA, or `MRI machine` → UK). No setup required.
The instructions below are only needed to run it yourself.

> **Scope:** 4 product families — **Medical** (HS Ch. 30 + headings 9018–9022),
> **Arms & Ammunition** (9305, 9306), **Watches** (9101, 9102), **Spirits &
> Liqueurs** (2208) — across **4 markets**: **USA · UK · EU · UAE**.

---

## What we built

- **A 3-signal RAG classifier** (keyword + semantic vector + LLM arbitration) that
  maps free text → HS-6, and **cannot hallucinate a code**: the LLM only ever
  *chooses among* candidates that the keyword/vector signals already nominated
  and that are validated against the real taxonomy.
- **Provenance on every value** — each duty rate and restriction carries the
  government `source_url` it was scraped from (WITS, USITC, UK Trade Tariff).
- **A three-tier deployment on AMD hardware** — Streamlit UI → FastAPI backend →
  vLLM LLM server, all running on a single MI300X droplet.
- **A reproducible evaluation harness** over a labelled test set
  (`tests/evaluate.py`) reporting top-1 / top-3 accuracy and asserting zero
  invented codes.

---

## AMD / compute usage

The LLM reasoning path (Signal 2 scope-routing + the arbiter's tie-break) is
served **entirely on AMD silicon** — no proprietary hosted model in the primary
path.

| | Detail |
|---|---|
| **Hardware** | AMD Instinct **MI300X** (1× GPU, 192 GB VRAM) — DigitalOcean vLLM 1-Click droplet |
| **Stack** | **ROCm 7.2.4**, Ubuntu 24.04 |
| **Serving engine** | **vLLM 0.23.0** (`0.23.0+rocm723`), OpenAI-compatible endpoint |
| **Model** | `openai/gpt-oss-20b` (reasoning model; called with `reasoning_effort=low`) |
| **Footprint** | ~155 GiB KV cache allocated; ~6.3M-token cache, ~48× max concurrency |
| **How the app uses it** | The backend calls the vLLM endpoint through the OpenAI SDK. The URL/model are pure env config (`LLM_BASE_URL`, `LLM_MODEL`) — the *same* `chat_json()` code runs against local vLLM or Fireworks with no code change. |

**Graceful degradation:** if no LLM backend is reachable, the pipeline does **not**
crash — it drops the LLM scope-filter and the arbiter abstains to a top-3
suggestion list. AMD-served LLM is the quality path, not a hard dependency.

---

## Quickstart

> **Judging?** You don't need any of this — the app is live at
> **[http://134.199.196.55:8501/](http://134.199.196.55:8501/)**. This section is
> for running it on your own machine or your own AMD box.

### Option A — run locally (monolith)

The UI imports the retrieval pipeline in-process. Needs the prebuilt DB (ships in
`Database/`). LLM is optional: set `FIREWORKS_API_KEY`, or point `LLM_BASE_URL` at
a vLLM server, or leave both unset to run keyword+vector only.

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py            # http://localhost:8501
```

To use a remote vLLM (e.g. the AMD droplet) from your laptop:

```bash
export LLM_LOCAL_ENABLED=1
export LLM_BASE_URL=http://<droplet-ip>:8000/v1
export LLM_MODEL=gpt-oss-20b
streamlit run streamlit_app.py
```

### Option B — the full three-tier deployment (as demoed on the MI300X)

All three processes run on the droplet. vLLM ships pre-installed inside the
`rocm` Docker container on the DigitalOcean 1-Click image.

```bash
# 1) LLM — start vLLM inside the rocm container (serves on :8000)
docker exec -d rocm bash -lc \
  "vllm serve openai/gpt-oss-20b --served-model-name gpt-oss-20b \
   --host 0.0.0.0 --port 8000 --gpu-memory-utilization 0.90 > /root/vllm.log 2>&1"

# 2) Backend API (:8080) — talks to vLLM on localhost
cd TariffPilot && python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
LLM_LOCAL_ENABLED=1 LLM_BASE_URL=http://localhost:8000/v1 LLM_MODEL=gpt-oss-20b \
  uvicorn app.main:app --host 127.0.0.1 --port 8080

# 3) UI (:8501) — thin client that calls the backend
BACKEND_URL=http://localhost:8080 \
  streamlit run streamlit_app.py --server.address 0.0.0.0 --server.port 8501
```

Ports come from these launch commands, not the repo. **Firewall:** expose only
`8501` (UI) to the outside; keep vLLM `:8000` internal.

**Smoke-test the API:**

```bash
curl localhost:8080/health
curl -s localhost:8080/api/classify -H 'Content-Type: application/json' \
  -d '{"query":"whisky","country":"USA","use_llm":true}'
# → {"decision":"classified","hs6":"220830", ... ,"source":"local"}
```

---

## Where the code lives (main code path)

The one function to read is **`retrieval/pipeline.py :: classify(query, country)`** —
it orchestrates the whole chain. Follow it in this order:

| Step | File | What it does |
|---|---|---|
| **Entry (in-process)** | `retrieval/pipeline.py` | `classify()` — the whole chain in one call |
| **Entry (HTTP API)** | `app/main.py` | FastAPI: `POST /api/classify`, `GET /health`, `GET /api/card/{hs6}/{country}` |
| **Entry (UI)** | `streamlit_app.py` | Front-end; thin (calls backend) when `BACKEND_URL` is set, else monolith |
| **Signals** | `retrieval/signals.py` | Signal 1 keyword (FTS5) · Signal 2 scope-route (LLM) · Signal 3 vector (Chroma) |
| **Decision** | `retrieval/arbiter.py` | Merge candidates → fast-path / LLM-path / abstain, with the no-hallucination guard |
| **Result card** | `retrieval/card.py` | hs6 → fully sourced card (SQL joins: duty + restrictions + URLs) |
| **LLM plumbing** | `llm/client.py` | `chat_json(messages, schema)` — one entry point, local vLLM → Fireworks → None |
| **Config** | `config.py` | All env-driven settings: LLM backends, thresholds, paths, scope map |
| **Eval** | `tests/evaluate.py` | `python -m tests.evaluate` → top-1/top-3 accuracy on `tests/test.json` |

---

## External services

| Service | Role | Where configured |
|---|---|---|
| **AMD MI300X + vLLM** (self-hosted) | **Primary LLM** — Signal 2 scope-routing + arbiter tie-break | `LLM_BASE_URL`, `LLM_MODEL` in `config.py` |
| **Fireworks AI** | **Optional fallback LLM** (`gpt-oss-20b`); skipped entirely if no API key | `FIREWORKS_API_KEY` (unset by default) |
| **WITS** (World Bank) | MFN duty-rate averages, all reporters | `data_ingestion/ingest_wits_rates.py` |
| **USITC HTS API** | US customs-grade national duty (10-digit codes, Section-301 overlays) | `enrich/us_adapter.py` |
| **UK Trade Tariff API** | UK customs-grade national duty | `enrich/uk_adapter.py` |
| **HuggingFace Hub** | Model weights (gpt-oss-20b) + Chroma's ONNX MiniLM embedder | pulled at vLLM launch / first query |
| **HS taxonomy dataset** (GitHub `datasets/harmonized-system`) | Source of the HS-2022 code list | `data_ingestion/ingest_taxonomy.py` |

External-service keys are **never committed** — `config.py` reads them from the
environment / a git-ignored `.env`. EU national duties have no public JSON API
(TARIC), so EU falls back to the WITS average.

---

## How classification works (3-signal design)

```
user text ──┬─► Signal 1: SQL keyword match (FTS5/bm25)  precise, cheap, brittle
            ├─► Signal 2: scope routing via LLM          narrows scope only
            └─► Signal 3: Chroma vector search           semantic, paraphrase-robust
                     │
                     ▼
              Arbiter (validate + decide)
                     │
                     ▼
   result card: hs6 + description + duty + restrictions + source URLs
```

**The integrity rule:** Signals 1 & 3 *nominate* candidates; Signal 2 only
*narrows scope*; the Arbiter only *chooses among* nominated, taxonomy-validated
candidates. No layer may invent a code — the LLM's output schema is an **enum of
exactly the candidate codes + `"abstain"`**, so a made-up HS-6 is grammatically
impossible, and it's re-validated against the candidate set in Python anyway.

Arbiter decision ladder:
1. **Scope guardrail** — out-of-scope input with zero keyword hits → `out_of_scope`.
2. **Qualify** — keep a candidate if a keyword nominated it *or* vector similarity ≥ `VECTOR_MIN_SIM` (0.30).
3. **Fast path (no LLM)** — keyword #1 and vector #1 agree and similarity ≥ `FASTPATH_MIN_SIM` (0.50) → classify, confidence **high**.
4. **LLM path** — the model picks one code from the candidate enum → classify, confidence **medium**.
5. **Abstain** — nothing converged → `needs_review` + top-3 suggestions.

Every path returns the same shape (`decision`, `hs6`, `confidence`, `path`,
`signals_agreed`, `candidates`, `reason`), and `classify()` attaches the sourced
result card.

---

## Data layer & provenance

Two stores live under `Database/` (both prebuilt and shipped in the repo; every
script resolves the path via `__file__`, so it runs from any working directory):

- **SQLite** (`Database/tariff_pilot.db`) — the structured/provenance side:
  `hs_taxonomy` (codes + hand-drafted keyword synonyms), `duty_rates` (WITS
  averages **plus** USITC/UK national rows; `source_url` required on every row),
  `restrictions_flags` (licensing law, manually curated with verified citations),
  and `test_set` (labelled ground truth).
- **Chroma** (`Database/chroma_db`) — collection `hs_taxonomy`, 384-dim vectors
  (default `all-MiniLM-L6-v2`), with hs6/chapter/heading/category metadata usable
  as query-time `where` filters.

Rebuild the data layer from scratch (only needed if you change scope — uncomment
`sentence-transformers` in `requirements.txt` first):

```bash
python -m init_database.init_db
python -m data_ingestion.ingest_taxonomy
python -m data_ingestion.ingest_wits_rates
python -m data_ingestion.seed_restrictions_and_tests
python -m data_ingestion.seed_keywords
python -m enrich.refresh          # national duty numbers (USA + UK)
python sanity_check.py            # verify coverage + provenance
```

Run `python sanity_check.py` any time for the current coverage report (row counts
+ end-to-end lookups, all with source URLs).

---

## Evaluation

```bash
python -m tests.evaluate            # all cases
python -m tests.evaluate 10 --seed 42   # reproducible random batch
```

Reports top-1 / top-3 accuracy and the decision path per example, plus a
hallucination-guard assertion (0 invented codes). Test cases live in
`tests/test.json` — the single source of truth for ground truth.

---

## Known limitations (upfront)

- **EU duties** use the WITS HS-6 average — TARIC has no public JSON API;
  US/UK have customs-grade national rows.
- **Section-301** overlays (US Ch.-99, e.g. `9903.88.15`) are surfaced as a note,
  not yet folded into a computed landed cost.
- **Restrictions** are manually curated with verified citations (no uniform API
  for licensing law) — few but trustworthy rows, bounded by scope.
- The embedding model (`all-MiniLM-L6-v2`) is a solid default; swapping it forces
  a full re-embed since the vector dimension changes.
