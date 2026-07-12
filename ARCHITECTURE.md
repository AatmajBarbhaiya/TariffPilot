# TariffPilot — Deployment & Runtime Architecture

How the app is packaged, hosted, and served. The data-layer design lives in
[`README.md`](README.md); the national-enrichment roadmap in
[`NATIONAL_SOURCES.md`](NATIONAL_SOURCES.md). This document is the source of
truth for **Docker, the local AMD LLM (vLLM on ROCm), and the Fireworks
fallback**.

> AMD / vLLM / Fireworks facts below were verified against live sources (vLLM
> docs, Docker Hub `rocm/vllm`, Fireworks docs) in **July 2026**. Where a
> catalog rotates (Fireworks model IDs, image tags, served model names), the
> value lives in an **env var**, not in code.

> **Serving engine: vLLM, not llama.cpp.** The AMD instance offers vLLM only
> (no llama.cpp/GGUF path). This changes the *serving* layer — image, model
> format, model-name handling — but **not one line of application code**: vLLM
> exposes the same OpenAI-compatible `/v1/chat/completions`, so the existing
> `OpenAI(base_url=…)` client (`llm/client.py`) talks to it unchanged. That
> backend-agnosticism is the whole point of the base_url design.

---

## 1. Target environment

AMD hackathon cloud instance:

| Resource | Spec | Consequence |
|---|---|---|
| GPU | **AMD, ~20 GB VRAM** (ROCm) | Comfortably runs a 7B FP16 or a quantized 14B — see §5 |
| vCPU | **20** | Ample; CPU orchestrates, GPU does inference |
| System RAM | generous (not the binding constraint) | The old 4 GB ceiling is gone |
| Delivery | **Docker** | One `docker compose` brings up the whole app |

Design goal: **everything on the one AMD box** (best AMD-hackathon story,
simplest ops). Unlike the earlier 4 GB sizing, 20 GB VRAM is *not* a binding
constraint — the LLM choice is now about quality, not survival. The app still
moves the LLM off-box to Fireworks with a pure env swap, so "one box" is a
default, not a lock-in.

---

## 2. Topology — two containers, one compose

```
┌──────────────── AMD instance (20 vCPU / ~20 GB VRAM / AMD GPU) ──────────┐
│  docker compose                                                          │
│  ┌─────────────── app ───────────────┐   ┌────────── llm ────────────┐   │
│  │ FastAPI + uvicorn        :8000    │   │ vLLM OpenAI server  :8080 │   │
│  │  • POST /api/classify             │──►│ /v1/chat/completions      │   │
│  │  • GET  /api/card/{hs6}/{country} │   │ Qwen 7B FP16 / 14B AWQ    │   │
│  │  • GET  /  (static/index.html)    │   │ json_schema (xgrammar)    │   │
│  │ retrieval/ (3 signals + arbiter)  │   └────────────┬──────────────┘   │
│  │ Database/ (SQLite + Chroma, baked)│                │ ROCm GPU         │
│  └───────────────┬───────────────────┘                │                  │
└──────────────────┼────────────────────────────────────┼──────────────────┘
                   │ fallback (same OpenAI client,      │
                   │  different base_url/key/model)     │
                   ▼                                    │
        Fireworks  api.fireworks.ai/inference/v1  ◄─────┘  (if local down)
```

**`app`** — the FastAPI service. Holds the retrieval pipeline and both data
stores (baked into the image; together ~1 MB). Stateless at request time: the
serving path only **reads** SQLite/Chroma, never writes.

**`llm`** — vLLM's OpenAI-compatible server (`vllm serve …`), exposing
`/v1/chat/completions` on `:8080`, backed by a Qwen instruct model on the GPU.
Does two things only: **Signal-2 scope routing** and **Arbiter** decisions —
both short, both strict-JSON.

The two services talk over the compose network. The app's LLM client points at
`http://llm:8080/v1` by default and at Fireworks when configured — nothing else
changes.

> **Port note:** vLLM defaults to `:8000`, which collides with the FastAPI app.
> Serve vLLM with `--port 8080` so `LLM_BASE_URL=http://llm:8080/v1` holds.

---

## 3. The LLM's job (and why a mid-size instruct model is plenty)

The pipeline is designed (README §4) so the LLM is **never the thing that
invents an HS code**. Signals 1 (SQL keyword) and 3 (Chroma vector) *nominate*
candidates from the real taxonomy; the LLM only:

1. **Signal 2 — scope routing**: map user text → `{chapters[], headings[],
   in_scope}`. Used as a filter mask + a guardrail ("laptop" → `in_scope:false`).
2. **Arbiter**: given a short candidate list *with official descriptions*,
   return **one of the given hs6 values** or `abstain`, + a one-line reason.

Both are tiny classification tasks with a closed output space — trivial for a
7–14B instruct model. Correctness is enforced structurally, not hoped for:

**Grammar-constrained JSON on vLLM.** vLLM supports OpenAI's
`response_format: {type:"json_schema", json_schema:{…}}` and enforces it with a
**guided-decoding backend** (xgrammar by default; outlines / lm-format-enforcer
selectable). Constraint is applied at the sampler — the model *cannot* emit
tokens that violate the schema. We make the **Arbiter's `hs6` field an `enum`
of exactly the candidate codes**, so a hallucinated code is *grammatically
impossible* on the local path. Rules:

- Keep schemas **flat** — enums + short strings.
- vLLM ignores the OpenAI `"strict": true` flag (harmless) — it uses the schema
  regardless.
- The app **re-validates** every returned hs6 against the candidate set on
  *every* backend (belt-and-suspenders — the guard holds even if a backend
  ignores the schema). See `_extract_json` + the arbiter's post-check.

> **vLLM-native option (not used, on purpose):** vLLM also accepts
> `extra_body={"guided_choice": [<codes…>, "abstain"]}`, which is an even
> tighter fit for the arbiter's pick-one-of-N task. We deliberately stay on the
> portable `response_format` so **the same `chat_json()` call works on vLLM,
> Fireworks, and any other OpenAI-compatible backend** with no per-backend
> branch. Switch to `guided_choice` only if a vLLM version mishandles
> `response_format`.

---

## 4. Serving with vLLM on ROCm

vLLM has first-class AMD/ROCm support. Use the AMD-maintained image and let
vLLM pull weights from HuggingFace at first launch (no separate GGUF fetch
step — that whole script is gone).

**Image:** `rocm/vllm` (Docker Hub, AMD-maintained ROCm build). Large (~tens of
GB on disk) — run `df -h` before pulling; disk, not VRAM, is the usual gotcha.

**Sanity-probe the GPU first** (`scripts/probe_gpu.sh`): `ls /dev/kfd`,
`rocminfo` — confirm ROCm sees the card and note the `gfx` arch. If ROCm rejects
a consumer RDNA arch, set `HSA_OVERRIDE_GFX_VERSION` (e.g. `11.0.0` for RDNA3).

**Run (encoded in `docker-compose.yml`):**

```bash
docker run --device /dev/kfd --device /dev/dri --group-add video \
  --security-opt seccomp=unconfined --shm-size 8g \
  -p 8080:8080 \
  -e HF_TOKEN=$HF_TOKEN \                       # only for gated models; Qwen is open
  rocm/vllm \
  vllm serve Qwen/Qwen2.5-7B-Instruct \
    --port 8080 --host 0.0.0.0 \
    --gpu-memory-utilization 0.90 \
    --max-model-len 4096 \                      # prompts are short candidate lists
    --dtype auto
```

**Flags that matter on this box:**
- `--gpu-memory-utilization 0.90` — vLLM pre-allocates the KV cache to this
  fraction of VRAM at startup. 0.90 of ~20 GB leaves headroom for the OS/driver.
- `--max-model-len 4096` — our prompts are tiny; a small context keeps the KV
  cache cheap and startup fast. Don't leave it at the model's full length.
- `--dtype auto` — FP16/BF16 for an unquantized model; for a quantized model
  vLLM detects AWQ/GPTQ from the checkpoint (`--quantization` optional).
- `--shm-size 8g` on the container — vLLM uses shared memory; the Docker default
  (64 MB) can crash it.

> ⚠️ **Quantization on ROCm is kernel-dependent.** FP16/BF16 is guaranteed;
> **AWQ/GPTQ kernels vary by ROCm/vLLM build**. Verify a quantized model
> actually loads on *your* image before relying on it — the safe default below
> is a 7B in FP16, which has no kernel dependency.

---

## 5. Model choice & VRAM budget

With ~20 GB VRAM the model is chosen for quality, not survival. Weights ≈
`params × bytes/param` (2 bytes FP16, ~0.5 byte 4-bit), plus KV cache + vLLM
overhead.

| Model | Format | ~VRAM (weights) | Use when |
|---|---|---|---|
| **Qwen2.5-7B-Instruct** | FP16/BF16 | ~15 GB | **Primary — safe default.** No quant kernels; guaranteed on ROCm. Fits 20 GB with KV cache at `--max-model-len 4096`. |
| Qwen2.5-14B-Instruct-AWQ | AWQ 4-bit | ~9–10 GB | **Best quality — if AWQ kernels load on your ROCm build.** Verify first (see §4 warning). |
| Qwen2.5-14B-Instruct-GPTQ-Int4 | GPTQ 4-bit | ~9–10 GB | Alternative 14B if AWQ is unavailable but GPTQ builds. |
| Qwen2.5-3B-Instruct | FP16 | ~6 GB | Fallback if a bigger model won't load; still ample for these two closed-output tasks. |

**Recommended:** start on **Qwen2.5-7B-Instruct FP16** (reliable, no kernel
risk), then try **14B AWQ** for a quality bump once you've confirmed the quant
kernels work on the box. The json_schema grammar keeps output valid regardless
of model size, so dropping to a smaller model costs almost nothing in
reliability — only a little classification nuance.

**No fine-tuning.** Raw instruct weights + few-shot candidate descriptions +
the enum-constrained schema are sufficient; the model only picks among
pre-nominated real codes, never generates one. (See README §4.)

**No `torch` in the *app* image.** Chroma's default embedder is ONNX MiniLM, so
query-time embedding needs no `sentence-transformers`/PyTorch in the FastAPI
container. App serving deps: `fastapi`, `uvicorn`, `chromadb`, `openai`,
`requests`. (The heavy PyTorch/ROCm stack lives only in the `rocm/vllm` `llm`
container, and `sentence-transformers` stays a build/ingest-time dep.)

---

## 6. Fireworks — where it helps, where it doesn't

Base `https://api.fireworks.ai/inference/v1`, `Authorization: Bearer <key>`,
OpenAI-SDK drop-in — the *same* wrapper as local vLLM, only `base_url`/`api_key`
/`model` differ. Model IDs are the full `accounts/fireworks/models/…` path and
live in env (the serverless catalog rotates; a 404 = retirement).

| Use | When | Model (env-configurable) |
|---|---|---|
| **Runtime fallback** | local `llm` down/slow/timeout | `accounts/fireworks/models/gpt-oss-20b` (cheap, fast) |
| **Offline keyword drafting** (README TODO #1) | one batch, host-side | a strong model (batch API is cheaper) |
| **Eval** — paraphrase gen + LLM-judge in `evaluate.py` | offline | either of the above |
| **Laptop dev** (no AMD GPU) | vLLM can't run on a Mac / CPU-only box | Fireworks is the *only* LLM path locally |

**Not** used for: embeddings (local ONNX) or anything in the data layer. The
answer to "do we even need Fireworks?" is: **not for the happy path** — the AMD
box runs the whole thing — but it's the reliability net that makes a live demo
safe, the muscle for offline batch jobs, and the **only** LLM available during
laptop development (vLLM needs a GPU; there is no CPU-compose fallback like the
old llama.cpp plan had).

**Three-layer fallback, applied per LLM call, automatically:**

```
local vLLM ──(down/timeout/bad JSON)──► Fireworks ──(no key/down)──► degrade
                                                                      │
   Signal 2: skip the filter (no scope narrowing, pipeline runs)  ◄───┤
   Arbiter:  abstain → return top-3 "needs human review"          ◄───┘
```

The pipeline **never 500s on LLM failure** — it degrades to a weaker-but-honest
answer (README §4 "must degrade gracefully", implemented in `llm/client.py`'s
backend loop + the arbiter's abstain paths). Transient 429/5xx/timeout are
retried by the SDK (`FIREWORKS_MAX_RETRIES` / `LLM_MAX_RETRIES`) before a call
is declared failed.

---

## 7. Repo layout (target)

New files marked `+`; the data layer already exists.

```
Tariffpilot_rag_db/
  ARCHITECTURE.md              this file
  config.py                    env: LLM_BASE_URL/MODEL/API_KEY/TIMEOUT/MAX_RETRIES, FIREWORKS_*, thresholds, paths
  app/
    main.py                    + FastAPI: /api/classify, /api/card, static mount
    static/index.html          + search box → result card (duty, restrictions, source URLs, signal badges)
  retrieval/
    signals.py                 keyword_search / toc_route / vector_search
    arbiter.py                 merge → fast path → LLM arbitrate → abstain
    card.py                    hs6 → sourced result card (SQL joins)
    evaluate.py                test_set → top-1/top-3 accuracy + signal agreement
  llm/
    client.py                  OpenAI-SDK client; local→Fireworks→None chain; chat_json(msgs, schema)
  scripts/
    probe_gpu.sh               + rocminfo / ls /dev/kfd → confirm ROCm sees the GPU
    draft_keywords.py          + offline Fireworks batch → hs_taxonomy.keywords
  docker/
    Dockerfile.app             + python:3.12-slim; bakes code + Database/
    docker-compose.yml         + app + llm (rocm/vllm) services, healthchecks
    .env.example               + every env var documented
  data_ingestion/  init_database/  Database/  sanity_check.py   (existing)
```

Gone vs. the llama.cpp plan: `fetch_model.sh` (vLLM auto-pulls from HF) and
`docker-compose.cpu.yml` (no CPU/GGUF fallback — laptop dev uses Fireworks).

**Ingestion is not a runtime service.** It's a one-off, run host-side or via
`docker compose run app python -m data_ingestion.ingest_taxonomy`. The live app
only reads the baked stores.

---

## 8. Runbook

**Local dev (no GPU, e.g. a Mac laptop):** there is no local model — point the
LLM at Fireworks and run the app directly.
```bash
# .env: LLM_LOCAL_ENABLED=0 + a FIREWORKS_API_KEY set
conda activate nlp
streamlit run streamlit_app.py        # or: uvicorn app.main:app --reload
```

**On the AMD box:**
```bash
scripts/probe_gpu.sh                   # confirm ROCm sees the GPU (gfx arch)
df -h                                  # rocm/vllm image is large — check disk first
docker compose up -d                   # starts app + vLLM (rocm/vllm)
curl localhost:8080/health             # vLLM up? (200 = ready)
curl localhost:8080/v1/models          # confirms the served model NAME (see below)
# quick structured-output smoke test:
curl localhost:8080/v1/chat/completions -H 'Content-Type: application/json' -d '{
  "model":"Qwen/Qwen2.5-7B-Instruct",
  "messages":[{"role":"user","content":"reply as JSON {\"m\":\"..\"}"}],
  "response_format":{"type":"json_schema","json_schema":{"name":"r","strict":true,
    "schema":{"type":"object","properties":{"m":{"type":"string"}},"required":["m"]}}}}'
```

**Enable the local vLLM backend** (in `.env`):
```bash
LLM_LOCAL_ENABLED=1
LLM_BASE_URL=http://llm:8080/v1        # or http://localhost:8080/v1 outside compose
LLM_MODEL=Qwen/Qwen2.5-7B-Instruct     # ⚠ MUST match what vLLM serves (see §9)
```

**Swap to Fireworks-only** (no local LLM): set `LLM_LOCAL_ENABLED=0` and keep a
`FIREWORKS_API_KEY`. Nothing in the app code changes.

---

## 9. The one gotcha that will bite: the model NAME must match

llama.cpp ignored the `model` field; **vLLM does not.** vLLM serves under the
exact string you launched it with (the HF id, or `--served-model-name <alias>`),
and rejects a mismatched name with a **404 "model not found"**. So:

- `LLM_MODEL` in `.env` **must equal** the `vllm serve <model>` argument (or its
  `--served-model-name`). Confirm the live value with `curl :8080/v1/models`.
- `LLM_MODEL="local"` (the old llama.cpp placeholder) **will break** on vLLM.

Everything else — `LLM_API_KEY` (vLLM ignores auth; the dummy `sk-noauth` is
fine), the request body, the response envelope — is identical to Fireworks.

---

## 10. Verification (demo-readiness gates)

1. **End-to-end via Fireworks** (laptop, no GPU) — `LLM_LOCAL_ENABLED=0` +
   key → classify *"shotgun shells"* → expect `930621` card with the licence
   warning and live source URLs.
2. **Local vLLM path** (AMD box) — `curl :8080/health` OK; `:8080/v1/models`
   shows the expected name; the json_schema smoke test returns valid JSON;
   `LLM_MODEL` matches → a classify call takes the `llm` path.
3. **Fallback chain** — stop the `llm` container → still answers via Fireworks;
   also unset `FIREWORKS_API_KEY` → graceful top-3 abstain, **HTTP 200, never a
   500**.
4. **Grammar guarantee** — arbiter enum = candidate list; `evaluate.py` asserts
   every returned/candidate hs6 ∈ taxonomy across all test rows (guard = 0).
5. **Accuracy number** — `python -m tests.evaluate` prints top-1/top-3 per
   category. This is the demo metric.
6. **VRAM headroom** (AMD box) — `rocm-smi` stays within budget during a
   classify burst; no OOM on model load at `--gpu-memory-utilization 0.90`.
