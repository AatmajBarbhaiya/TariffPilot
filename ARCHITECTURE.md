# TariffPilot — Deployment & Runtime Architecture

How the app is packaged, hosted, and served. The data-layer design lives in
[`README.md`](README.md); the national-enrichment roadmap in
[`NATIONAL_SOURCES.md`](NATIONAL_SOURCES.md). This document is the source of
truth for **Docker, the local AMD LLM, and the Fireworks fallback**.

> All AMD-image / model / Fireworks facts below were verified against live
> sources (ghcr registry, HuggingFace file listings, Fireworks docs) in
> **July 2026**. Where the catalog rotates (Fireworks model IDs, image build
> tags), the value lives in an env var, not in code.

---

## 1. Target environment

AMD hackathon cloud instance:

| Resource | Spec | Consequence |
|---|---|---|
| GPU | AMD (model unknown until probed) | Image choice depends on arch — see §4 |
| RAM | **4 GB** | Binding constraint; sized for a 2–4B model |
| vCPU | **2** | GPU does inference (`-ngl 99`); CPU only orchestrates |
| Delivery | **Docker** | One `docker compose` brings up the whole app |

Design goal: **everything on the one AMD box** (best AMD-hackathon story,
simplest ops) if it fits in 4 GB — and §5 shows it does, tightly. The app is
built so the LLM can be moved off-box to Fireworks with a pure env swap, so
"one box" is a default, not a lock-in.

---

## 2. Topology — two containers, one compose

```
┌──────────────── AMD instance (4GB / 2vCPU / AMD GPU) ────────────────┐
│  docker compose                                                      │
│  ┌─────────────── app ───────────────┐   ┌──────── llm ───────────┐  │
│  │ FastAPI + uvicorn        :8000    │   │ llama.cpp llama-server │  │
│  │  • POST /api/classify             │──►│ OpenAI-compat /v1 :8080│  │
│  │  • GET  /api/card/{hs6}/{country} │   │ Qwen GGUF Q4_K_M       │  │
│  │  • GET  /  (static/index.html)    │   │ json_schema grammar    │  │
│  │ retrieval/ (3 signals + arbiter)  │   └───────────┬────────────┘  │
│  │ Database/ (SQLite + Chroma, baked)│               │ GPU (-ngl 99) │
│  └───────────────┬───────────────────┘               │               │
└──────────────────┼───────────────────────────────────┼───────────────┘
                   │ fallback (same OpenAI client,     │
                   │  different base_url/key)          │
                   ▼                                   │
        Fireworks  api.fireworks.ai/inference/v1  ◄────┘  (if local down)
```

**`app`** — the FastAPI service. Holds the retrieval pipeline and both data
stores (baked into the image; together ~1 MB). Stateless at request time: the
serving path only **reads** SQLite/Chroma, never writes.

**`llm`** — `llama.cpp`'s `llama-server`, exposing an OpenAI-compatible
`/v1/chat/completions` on `:8080`, backed by a small Qwen GGUF on the GPU. Does
two things only: **Signal-2 scope routing** and **Arbiter** decisions — both
short, both strict-JSON.

The two services talk over the compose network. The app's LLM client points at
`http://llm:8080/v1` by default and at Fireworks when configured — nothing else
changes.

---

## 3. The LLM's job (and why a 3B model is enough)

The pipeline is designed (README §4) so the LLM is **never the thing that
invents an HS code**. Signals 1 (SQL keyword) and 3 (Chroma vector) *nominate*
candidates from the real taxonomy; the LLM only:

1. **Signal 2 — scope routing**: map user text → `{chapters[], headings[],
   in_scope}`. Used as a filter mask + a guardrail ("laptop" → `in_scope:false`).
2. **Arbiter**: given a short candidate list *with official descriptions*,
   return **one of the given hs6 values** or `abstain`, + a one-line reason.

Both are tiny classification tasks with a closed output space — well within a
3B instruct model. Correctness is enforced structurally, not hoped for:

**Grammar-constrained JSON.** `llama-server` accepts
`response_format: {type:"json_schema", json_schema:{…}}` and enforces it at the
sampler (JSON-Schema → GBNF internally). We make the **Arbiter's `hs6` field an
`enum` of exactly the candidate codes** — so a hallucinated code is
*grammatically impossible* on the local path. Rules:

- Keep schemas **flat** — enums + short strings. Unsupported JSON-Schema
  keywords are silently skipped.
- **Never** send `grammar` and `json_schema` in the same request (hard error).
- Fireworks enforces JSON too but is less strict about enums → the app
  **re-validates** every returned hs6 against the candidate set regardless of
  backend (belt-and-suspenders hallucination guard).

---

## 4. Choosing the llama.cpp image — probe the GPU first

Image choice is **not** about size; it's about GPU architecture. Run
`scripts/probe_gpu.sh` (`ls /dev/kfd`, `rocminfo`/`lspci`) on the instance and
follow the table:

| GPU class detected | Image | Why |
|---|---|---|
| **Instinct / CDNA** (MI100/210/300X, gfx908/90a/942) | `ghcr.io/ggml-org/llama.cpp:server-rocm` | The ROCm image is built for these archs natively (no `HSA_OVERRIDE` needed). **Vulkan cannot see CDNA cards** — would silently fall to CPU. |
| **Consumer / RDNA** (RX 6000/7000, gfx1030/110x) | `ghcr.io/ggml-org/llama.cpp:server-vulkan` | 293 MB, driver-agnostic. If ROCm rejects the arch, set `HSA_OVERRIDE_GFX_VERSION=10.3.0` (RDNA2) / `11.0.0` (RDNA3). |
| **No GPU / broken driver** | `ghcr.io/ggml-org/llama.cpp:server` (CPU) | Zero-risk demo fallback; a 1.7B Q4 handles two short calls acceptably. |

⚠️ **Disk, not RAM, is the ROCm gotcha**: the `server-rocm` image is ~7.3 GB
compressed / **~15–20 GB on disk**. Run `df -h` before pulling — this is the
most likely silent killer on a small instance.

**Run flags** (encoded in `docker-compose.yml`):

```bash
# ROCm service
docker run --device /dev/kfd --device /dev/dri --group-add video \
  --security-opt seccomp=unconfined \
  -v ./models:/models -p 8080:8080 \
  ghcr.io/ggml-org/llama.cpp:server-rocm \
  -m /models/model.gguf -ngl 99 -c 2048 --parallel 1 \
  --threads 2 --no-warmup --cache-ram 0 --host 0.0.0.0 --port 8080
# Vulkan: same, image :server-vulkan, only --device /dev/dri (no /dev/kfd)
```

**Mandatory flags on this box** (each is a real OOM/latency footgun):
- `--cache-ram 0` — the host prompt-cache **defaults to 8 GiB** and would grow
  RSS past the limit over successive requests. Non-negotiable here.
- `-c 2048` — prompts are short candidate lists; 2k is plenty.
- `--parallel 1`, `--no-warmup`, `--threads 2`, keep **mmap ON** (don't pass
  `--no-mmap` — file-backed weights stay reclaimable).

---

## 5. Model choice & RAM budget

Q4_K_M GGUF, chosen by what the "4 GB" actually is:

| Model | Q4_K_M | ~RSS @2k ctx | Use when |
|---|---|---|---|
| **Qwen2.5-3B-Instruct** | 2.1 GB | ~2.5 GB | 4 GB = **system RAM** → **primary** |
| Qwen3-4B-Instruct-2507 | 2.5 GB | ~3.2 GB VRAM | 4 GB = **VRAM** (separate sys RAM) only |
| **Qwen3-1.7B** | 1.1 GB | ~1.6 GB | safe **fallback** / CPU image |

Qwen3-1.7B is a hybrid-thinking model → disable thinking for latency:
`--chat-template-kwargs '{"enable_thinking":false}'`. (Llama-3.2-3B is
deliberately skipped: same size as Qwen2.5-3B, weaker instruction-following,
larger KV cache.)

**RAM budget — worst case, 4 GB = total system RAM:**

| Component | RSS |
|---|---|
| OS + Docker daemon | ~0.5 GB |
| `llama-server` + Qwen2.5-3B Q4_K_M @2k | ~2.5 GB |
| FastAPI app | ~0.15 GB |
| Chroma + default ONNX MiniLM embedder | ~0.35 GB |
| **Total** | **~3.5 GB — fits, tight (no spike headroom)** |

If it's too tight → drop to **Qwen3-1.7B** (total ~2.5 GB); the json_schema
grammar keeps output valid regardless of model size, so this costs reliability
almost nothing. Both services get a Docker `mem_limit` and a `/health`
healthcheck so an OOM is **visible**, not a silent hang.

**No `torch` in the serving image.** Chroma's default embedder is ONNX MiniLM,
so query-time embedding needs no `sentence-transformers`/PyTorch. Serving deps:
`fastapi`, `uvicorn`, `chromadb`, `openai`, `requests`. (`sentence-transformers`
stays a *build/ingest-time* dependency only.)

---

## 6. Fireworks — where it helps, where it doesn't

Base `https://api.fireworks.ai/inference/v1`, `Authorization: Bearer <key>`,
OpenAI-SDK drop-in. Model IDs are the full `accounts/fireworks/models/…` path
and live in env (the serverless catalog rotates; a 404 = retirement).

| Use | When | Model (env-configurable) |
|---|---|---|
| **Runtime fallback** | local `llm` down/slow/timeout | `accounts/fireworks/models/gpt-oss-20b` (cheap, fast) |
| **Offline keyword drafting** (README TODO #1) | one batch, host-side | `accounts/fireworks/models/deepseek-v4-pro` (strong; batch API −50%) |
| **Eval** — paraphrase gen + LLM-judge in `evaluate.py` | offline | either of the above |

**Not** used for: embeddings (local ONNX) or anything in the data layer. The
answer to "do we even need Fireworks?" is: **not for the happy path** — the AMD
box runs the whole thing — but it's the reliability net that makes a live demo
safe, and the muscle for the two offline batch jobs.

**Three-layer fallback, applied per LLM call, automatically:**

```
local llama-server ──(down/timeout/bad JSON)──► Fireworks ──(no key/down)──► degrade
                                                                              │
   Signal 2: skip the filter (no scope narrowing, pipeline still runs)  ◄─────┤
   Arbiter:  abstain → return top-3 "needs human review"                ◄─────┘
```

The pipeline **never 500s on LLM failure** — it degrades to a weaker-but-honest
answer. This is the README §4 "must degrade gracefully" rule, implemented.

---

## 7. Repo layout (target)

New files marked `+`; the data layer already exists.

```
Tariffpilot_rag_db/
  ARCHITECTURE.md              this file
  config.py                    + env: LLM_BASE_URL/MODEL/API_KEY, FIREWORKS_*, thresholds, paths
  app/
    main.py                    + FastAPI: /api/classify, /api/card, static mount
    static/index.html          + search box → result card (duty, restrictions, source URLs, signal badges)
  retrieval/
    signals.py                 + keyword_search / toc_route / vector_search
    arbiter.py                 + merge → fast path → LLM arbitrate → abstain
    card.py                    + hs6 → sourced result card (SQL joins; reuse sanity_check.py logic)
    evaluate.py                + test_set → top-1/top-3 accuracy + signal agreement
  llm/
    client.py                  + OpenAI-SDK client; local→Fireworks→None chain; chat_json(msgs, schema)
  scripts/
    probe_gpu.sh               + detect GPU arch → print which image to use
    fetch_model.sh             + download GGUF (3B primary, 1.7B fallback)
    draft_keywords.py          + offline Fireworks batch → hs_taxonomy.keywords
  docker/
    Dockerfile.app             + python:3.12-slim; bakes code + Database/
    docker-compose.yml         + app + llm (ROCm/Vulkan) services, mem limits, healthchecks
    docker-compose.cpu.yml     + override: CPU image + 1.7B (no-GPU / laptop dev)
    .env.example               + every env var documented
  data_ingestion/  init_database/  Database/  sanity_check.py   (existing)
```

**Ingestion is not a runtime service.** It's a one-off, run host-side or via
`docker compose run app python -m data_ingestion.ingest_taxonomy`. The live app
only reads the baked stores.

---

## 8. Runbook

**Local dev (no GPU, e.g. a laptop):**
```bash
scripts/fetch_model.sh qwen3-1.7b
docker compose -f docker/docker-compose.yml -f docker/docker-compose.cpu.yml up
# → open http://localhost:8000
```

**On the AMD box:**
```bash
scripts/probe_gpu.sh            # → tells you rocm | vulkan | cpu
df -h                           # ROCm image needs ~20 GB free before pull
scripts/fetch_model.sh qwen2.5-3b
docker compose up -d            # picks the image per .env LLM_IMAGE
curl localhost:8080/health      # llama-server up?
docker stats                    # confirm total < 4 GB under a classify burst
```

**Swap to Fireworks (no local LLM):** set `LLM_BASE_URL`,
`LLM_API_KEY=$FIREWORKS_API_KEY`, `LLM_MODEL=accounts/fireworks/models/…` in
`.env` and don't start the `llm` service. Nothing in the app code changes.

---

## 9. Verification (demo-readiness gates)

1. **End-to-end, no GPU** — CPU compose + Qwen3-1.7B → classify
   *"shotgun shells"* → expect `930621` card with the licence warning and live
   source URLs.
2. **Fallback chain** — stop `llm` → still answers via Fireworks; also unset
   `FIREWORKS_API_KEY` → graceful top-3 abstain, **HTTP 200, never a 500**.
3. **Grammar guarantee** — arbiter enum = candidate list; `evaluate.py` asserts
   every returned hs6 ∈ candidates across all 8 test rows.
4. **Accuracy number** — `python -m retrieval.evaluate` prints top-1/top-3
   per category. This is the demo metric.
5. **On the AMD box** — `probe_gpu.sh` picks the image; `curl :8080/health` OK;
   `docker stats` stays under 4 GB during a burst.
