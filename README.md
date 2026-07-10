# TariffPilot — Data Layer & Retrieval Architecture

RAG-based HS-code classifier + tariff/landed-cost lookup with **provenance
threaded through every number** (each rate and restriction carries its
`source_url`). Hackathon scope: 2 categories × 3 countries, per
`TariffPilot_Data_Ingestion_Spec.md`.

| Dimension | Choice |
|---|---|
| Categories | **Medical** (HS Ch. 30 + headings 9018–9022) · **Ammunition** (Ch. 93 headings 9305, 9306) |
| Countries (WITS reporter codes) | **USA** (840) · **GBR** (826) · **EU** (918) |
| Taxonomy revision | HS 2022 ("H6 / 2022") — 85 six-digit codes in scope (77 medical, 8 ammunition) |

---

## 1. Repo layout

Organized into folders by role. Both data stores (SQLite + Chroma) live under
`Database/`; every script resolves that path via `__file__`, so they run
correctly from **any** working directory (see bug #10). Package imports
(`import config`, `from data_ingestion…`, `from retrieval…`) resolve when you
run from **inside the project root `Tariffpilot_rag_db/`** — the single,
uniform convention (see the run block below).

**Data layer** (build the DB + vectors):

| Path | Role | Run order |
|---|---|---|
| `init_database/init_db.py` | Creates SQLite schema (`Database/tariff_pilot.db`): 5 tables + indexes, WAL mode | 1 |
| `init_database/test_db.ipynb` | Scratch notebook for poking at the schema/data | — |
| `data_ingestion/ingest_db.py` | Chroma client helper — persistent store at `Database/chroma_db`, collection `hs_taxonomy`, cosine space | (imported) |
| `data_ingestion/ingest_taxonomy.py` | GitHub `datasets/harmonized-system` CSV → filter to scope → **dual-write** vectors to Chroma + rows to SQL `hs_taxonomy` | 2 |
| `data_ingestion/ingest_wits_rates.py` | WITS (World Bank) MFN duty rates for all in-scope codes × 3 reporters → `duty_rates`. Idempotent | 3 |
| `data_ingestion/seed_restrictions_and_tests.py` | Seeds `restrictions_flags` (licensing) and `test_set` (ground truth) | 4 |
| `data_ingestion/seed_keywords.py` | Populates `hs_taxonomy.keywords` (85/85 hand-drafted synonyms for Signal 1) | 5 |
| `enrich/` | National duty enrichment — USITC HTS + UK Trade Tariff APIs → 170 customs-grade rows in `duty_rates` (`refresh.py`, `us_adapter.py`, `uk_adapter.py`, `duty_parser.py`) | 6 |
| `sanity_check.py` | Coverage report + end-to-end lookup, all with source URLs | any time |

**Retrieval layer** (§4 — the classifier):

| Path | Role |
|---|---|
| `config.py` | Env-driven settings: LLM/Fireworks backends, thresholds, paths, scope map |
| `llm/client.py` | `chat_json(messages, schema)` — one entry point, local→Fireworks→None fallback |
| `retrieval/signals.py` | Signal 1 keyword (FTS5) · Signal 2 scope route (LLM) · Signal 3 vector (Chroma) |
| `retrieval/arbiter.py` | Merge candidates → fast path / LLM path / abstain, with hallucination guard |
| `retrieval/card.py` | hs6 → fully sourced result card (SQL joins) |
| `retrieval/pipeline.py` | `classify(query, country)` — the whole chain in one call |
| `retrieval/evaluate.py` | Run `test_set` → top-1 / top-3 accuracy (the demo metric) |

**Docs / meta:** `ARCHITECTURE.md` (Docker + AMD local-LLM + Fireworks deploy
design) · `NATIONAL_SOURCES.md` (live-verified UK/US/EU tariff APIs for TODO #3)
· `requirements.txt` (`requests`, `chromadb`, `sentence-transformers` for
ingest; `openai` for retrieval).

Run **from inside `Tariffpilot_rag_db/`** (needs the `nlp` conda env, or
`pip install -r requirements.txt`):

```bash
cd Tariffpilot_rag_db
python -m init_database.init_db
python -m data_ingestion.ingest_taxonomy
python -m data_ingestion.ingest_wits_rates
python -m data_ingestion.seed_restrictions_and_tests
python -m data_ingestion.seed_keywords
python -m enrich.refresh          # national duty numbers (USA + UK)
python sanity_check.py            # verify the data layer
python -m retrieval.evaluate      # accuracy on test_set (LLM optional)
```

## 2. Data stores

**SQLite (`Database/tariff_pilot.db`)** — the structured/provenance side
(row counts below reflect the current DB as of 2026-07-07):

- `hs_taxonomy` — 85 codes: `hs6` PK, description, chapter, heading,
  `category_tag`, `keywords` (**85/85 populated** — synonym layer for Signal 1,
  seeded by `data_ingestion/seed_keywords.py`), `hs_revision`.
- `duty_rates` — **375 rows**, additive across sources:
  - **WITS** 205 rows (HS6-average MFN, all 3 reporters: EU 74, GBR 70, USA 61).
  - **USITC** 85 rows (USA) + **UK_TARIFF** 85 rows (GBR) — customs-grade national
    enrichment (`enrich/`, TODO done): exact MFN rate + 10-digit `national_code`
    + `unit_of_quantity`, with US column-2 and **Chapter-99 overlays** (Section
    301, e.g. `9903.88.15`) captured in `notes`. All in-scope national duties are
    `ad_valorem` (no specific/compound in scope; parser handles them regardless).
  - EU has no national row yet (TARIC has no JSON API — see `NATIONAL_SOURCES.md`);
    EU cards fall back to the WITS average.
  - `source` ∈ `WITS | USITC | UK_TARIFF | TARIC | WTO_TDF`; `source_url`
    **required** (0 missing). Cards prefer the national row and show the WITS
    average as an explainability baseline.
- `restrictions_flags` — **48 rows** = 16 distinct codes × USA/EU/GBR, each with
  a `source_url`. Coverage: **ammunition** (`930621/629/630/690`, ATF /
  Directive 2021/555 / Firearms Act), **firearm parts** (`930510/520/591/599`),
  and **alkaloid/controlled medicaments** (`300341–349`, `300441–449`, DEA /
  UN 1961 Convention / Misuse of Drugs Act). Citations manually verified.
- `test_set` — 8 labelled examples: `example_id`, `product_description`,
  `correct_hs6`, `correct_national_code` (empty — fills with TODO #8),
  `category_tag`, `ruling_reference`, `source_url`. **3 of 8 verified against
  real CROSS rulings** (`901831`→HQ H343563, `901832`→HQ 965580,
  `300490`→HQ 963707); the remaining 5 are still `TODO-CROSS` (see TODO #5).
- `change_log` — empty; reserved for WTO–IMF Tariff Tracker feed.

**Chroma (`Database/chroma_db`)** — the semantic side: collection `hs_taxonomy`,
85 vectors, 384-dim (default `all-MiniLM-L6-v2` — **not final**; swapping the
model requires re-embedding the whole collection since dimensions change).
Metadata per vector: hs6, chapter, heading, category_tag → usable as `where`
filters at query time.

## 3. Bug log (found & fixed)

| # | Bug | Fix |
|---|---|---|
| 1 | EU never ingested — old script only listed USA/GBR | Added reporter 918; ingestion now driven by `hs_taxonomy` (85 codes) instead of 3 hardcoded ones |
| 2 | SDMX positional parsing wrong — read index 5 as "specific-lines count"; it's actually `MIN_RATE` | Verified real attribute order against live response: OBS_VALUE=0, TOTALNOOFLINES=7, **NBR_NA_LINES=10** |
| 3 | Ammunition duties silently reported as "0% ad valorem" — WITS can't express specific duties as a % | `NBR_NA_LINES > 0` now tags the row `specific`/`compound` + "NEEDS ENRICHMENT" note instead of a fake 0% |
| 4 | `partner` column stored `'MFN'` (a rate type, not a partner) | Stores `'WLD'` (world = MFN baseline) |
| 5 | Re-running taxonomy ingest crashed (`add` on duplicate IDs) | `upsert`; WITS ingest deletes its own rows first → both idempotent |
| 6 | WITS read-timeouts killed codes silently | 60 s timeout, 4 retries w/ exponential backoff, shared session, commit every 10 rows |
| 7 | `hs_taxonomy` table missing from SQL entirely (spec requires it) | Created + populated; Chroma and SQL now mirror each other |
| 8 | Test label `300220` (vaccines) doesn't exist in HS 2022 — split into `300241`/`300242` | Fixed to `300241` in seed file; seed re-run now applied (verified in DB) |
| 9 | DB was **stale** — held 0 EU rows despite reporter 918 being in the code (an earlier WITS re-run never landed) | Completed WITS re-run; `duty_rates` now has all 3 reporters (EU 74, GBR 70, USA 61) |
| 10 | Folder restructure (`Tarrifplot`→`Tarrifpilot`, flat→subfolders) broke every script's **CWD-relative** paths (`"tariff_pilot.db"`, `"./chroma_db"`) — running from the repo root silently created a stray empty DB | Paths now anchored via `Path(__file__).resolve()` to `Database/`, so scripts work from any CWD |

## 4. Retrieval architecture — 3-signal design (**implemented**)

> Built in `retrieval/` (`signals.py`, `arbiter.py`, `card.py`, `pipeline.py`,
> `evaluate.py`) + LLM plumbing in `config.py` / `llm/client.py`. Run
> `python -m retrieval.evaluate` from the project root (`Tariffpilot_rag_db/`).
> Current baseline **with no LLM running** (keyword+vector only): top-1 38% /
> top-3 62%, 0 hallucination-guard violations — the fast path fires only on
> strong agreement; ambiguous cases abstain to top-3 and are what the LLM
> arbiter (Signal 2 + arbitration) will resolve once a model is served.

```
user text ──┬─► Signal 1: SQL keyword match      (precise, cheap, brittle)
            ├─► Signal 2: TOC routing, LLM       (scope filter, not a decider)
            └─► Signal 3: Chroma vector search   (semantic, handles paraphrase)
                     │
                     ▼
              Arbiter (validate + decide)
                     │
                     ▼
   result card: hs6 + description + duty + restrictions + source URLs
```

Design rule that keeps this honest: **Signals 1 & 3 nominate candidates;
Signal 2 only narrows scope; the Arbiter only chooses among nominated,
taxonomy-validated candidates.** No layer may invent a code.

### Signal 1 — SQL keyword match (`signals.keyword_search`)

Tokenize input (lowercase, strip stopwords + 1-char tokens), match against
`description` **and** `keywords` in `hs_taxonomy`. Uses SQLite **FTS5** (a
porter-stemmed, word-boundary virtual table built on the fly over the 85 rows),
ranked by **bm25**. Falls back to a per-token `LIKE` chain only if FTS5 isn't
compiled in — that fallback matches *substrings* (`"ct"` hits "produ**ct**s"),
which FTS5 avoids.

Grounded results (real DB, after keyword seeding):
`"vaccine"` → `300241`/`300242` ✓ · `"shotgun shells"` → `930621` ✓ ·
`"buckshot"` → `930621` ✓ · `"MRI machine"` → `901813` ✓ ·
`"laptop computer"` → weak/none ✓ (no confident match → guardrail holds).

Output: `[{hs6, score, signal: "keyword"}]` — may be empty; never wrong-but-confident.

### Signal 2 — TOC routing (scope resolution) — **optional**

One cheap LLM call via `llm.chat_json` (**backend-agnostic**: local llama-server
first, Fireworks fallback — *not* a specific vendor). Prompt = the static
chapter/heading scope map from `config.py` (Ch. 30 = pharma; 9018–9022 = medical
instruments; 9305 = arms parts; 9306 = ammunition) + the user text. Strict
`json_schema` output, **enum-constrained** to the known chapters/headings:
`{chapters: [...], headings: [...], in_scope: bool}`, then re-validated in Python.

- Used **only as a filter mask** for Signal 3 (Chroma `where` on `heading`) and
  as the out-of-scope guardrail.
- **Degrades gracefully**: no LLM reachable / no key / bad JSON → `toc_route`
  returns `None` and the pipeline applies **no** scope filter (never blocks).
  This is the current default state (no model served yet).

### Signal 3 — Chroma vector search (`signals.vector_search`)

Always runs (cheap at 85 vectors), **no LLM** — Chroma's own local ONNX
embedder (`all-MiniLM-L6-v2`) does the encoding. `collection.query(query_texts=
[input], n_results=5, where={"heading": {"$in": scope}} if scope else None)`.
Cosine distance → similarity (`1 − distance`), carried as confidence. Catches
paraphrases with zero keyword overlap.

Output: `[{hs6, similarity, signal: "vector"}]`.

### Arbiter (`arbiter.arbitrate`)

Merge candidates by hs6, tracking which signals nominated each (+ their scores),
then decide via a strict ladder:

0. **Scope guardrail:** Signal 2 says `in_scope=false` **and** zero keyword hits
   → `out_of_scope` (don't classify a laptop as medicine).
1. **Qualify:** a candidate survives if a keyword nominated it **or** its vector
   similarity ≥ `VECTOR_MIN_SIM` (0.30). None survive → abstain.
2. **Fast path (no LLM):** keyword's **#1** and vector's **#1** are the *same*
   code, and its similarity ≥ `FASTPATH_MIN_SIM` (0.50) → `classified`,
   confidence **high**.
3. **LLM path:** `chat_json` picks one code from the candidate list (schema
   `hs6` enum = *exactly* the candidate codes + `"abstain"`, so a made-up code
   is grammatically impossible; re-validated ∈ candidates too) → `classified`,
   confidence **medium**.
4. **Abstain:** nothing converged / LLM abstained → `needs_review` + top-3.

Every path returns the same shape (`decision`, `hs6`, `confidence`, `path`,
`signals_agreed`, `candidates`, `reason`). `pipeline.classify()` then attaches
the sourced **card** (`card.build_card`, same joins as `sanity_check.py`):
`hs_taxonomy` description → `duty_rates` (rate + type + source URL; surfaces the
"NEEDS ENRICHMENT" warning) → `restrictions_flags` (licence + source URL). The
card also reports *which signals agreed* — the explainability pitch.

### Module layout (built)

```
config.py         # env-driven: LLM/Fireworks backends, thresholds, paths, scope map
llm/
  client.py       # chat_json(messages, schema) — local -> Fireworks -> None
retrieval/
  signals.py      # keyword_search(q) · toc_route(q) · vector_search(q, scope)
  arbiter.py      # merge + guardrail + fast path + LLM path + abstain
  card.py         # hs6 -> sourced result card (SQL joins)
  pipeline.py     # classify(query, country) — orchestrates the whole chain
  evaluate.py     # run test_set through classify(), report top-1/top-3
```

Evaluation: `python -m retrieval.evaluate` runs all `test_set` rows and prints
top-1 / top-3 accuracy per category + the decision path per example + a
hallucination-guard assertion (0 invented codes). This is the demo's accuracy
number — **top-1 38% / top-3 62% with no LLM served** (keyword+vector only);
the 3 abstain cases are what Signal 2 + the LLM arbiter will convert.

## 5. Open TODOs (priority order)

**✅ Done since last revision:**

- ~~Verify the WITS re-run completed~~ — done; `duty_rates` = 205 rows, all 3
  reporters (EU 74, GBR 70, USA 61). See bug #9.
- ~~Re-run `seed_restrictions_and_tests.py`~~ — done; `300241` vaccine fix and
  live ATF `source_url` are in the DB.
- ~~Extend `restrictions_flags` across the ammunition/parts range~~ — done;
  now `930621/629/630/690` + `930510/520/591/599` × 3 countries.
- ~~Add a licence flag for alkaloid (controlled-substance) medicaments~~ — done;
  `300341–349` + `300441–449` × 3 countries (DEA / UN 1961 / Misuse of Drugs Act).
- ~~Populate `hs_taxonomy.keywords`~~ — done; 85/85 via `seed_keywords.py`.
  Signal 1 synonym cases ("shotgun shells"→`930621`, "MRI machine"→`901813`)
  now resolve; "laptop" still 0 rows (guardrail holds).

**Open (priority order):**

- ~~Build `retrieval/` + `evaluate.py`~~ — done; 3 signals + arbiter + sourced
  card + eval harness. Baseline top-1 38% / top-3 62% with no LLM (see §4).
- ~~Enrich `duty_rates` with national numbers (USA + UK)~~ — done; `enrich/`
  pulls USITC HTS + UK Trade Tariff → 170 national rows with 10-digit codes and
  Section-301 overlays. EU/TARIC still pending (no JSON API). `python -m
  enrich.refresh`.

1. **Serve the local LLM** (llama-server per ARCHITECTURE.md) + wire Signal 2
   and the arbiter's LLM path — the 3 abstain cases should convert; re-run
   `evaluate.py` to measure the lift. Then build `app/` + `docker/` (Phase 4).
2. **Real CROSS ruling numbers** into `test_set` (replace `TODO-CROSS`) — each
   with the ruling's own URL; use the ruling to audit the guessed `correct_hs6`.
   **Done: 3/8** (`901831`, `901832`, `300490`). **Remaining: 5** (`300241`,
   `901812`, `930630` ×2, `930690`).
3. **EU national enrichment** — the one gap left in `duty_rates`: TARIC has no
   public JSON API, so EU still uses the WITS average. Needs the TARIC3 daily XML
   export (see `NATIONAL_SOURCES.md`), a larger job than the UK/US REST adapters.
4. **Landed-cost calculator** handling all three duty shapes (ad valorem /
   specific / compound). National `ad_valorem` + 10-digit codes are now in place
   for US/UK; add freight/insurance inputs and the specific/compound arithmetic.
5. **Change monitoring** — populate `change_log` from the WTO–IMF Tariff Tracker
   (or a diff step added to `enrich.refresh`, per `NATIONAL_SOURCES.md`).
6. **Embedding model swap** (MiniLM is a placeholder) — keep the model name in `config.py`; a swap forces full re-embedding.

## 6. Known limitations (be upfront in the demo)

- WITS ad-valorem averages are **HS6 simple averages** — coarse. US/UK now have
  **customs-grade national rows** (`enrich/`, exact MFN + 10-digit code); EU
  still uses the WITS average (no TARIC JSON API — TODO #3).
- US cards carry a **Section 301 note** (Ch. 99 overlay, e.g. `9903.88.15`) in
  `notes` — surfaced as text, not yet computed into landed cost (TODO #4).
- Specific/compound duties are handled by the parser but **none occur in the
  85 in-scope codes** — every national duty here is ad-valorem or Free.
- Restrictions are **manually curated with verified citations** (there is no
  uniform API for licensing law). Coverage now spans ammunition, firearm parts,
  and controlled/alkaloid medicaments across all 3 countries (48 rows), but is
  still **bounded by scope**: in-scope codes outside those families carry no flag
  yet. "Curated" = few but verified rows, not unreliable rows.
- `keywords` are **hand-drafted synonyms** (85/85) — human-skimmable in
  `seed_keywords.py`; good coverage for lay terms, but not exhaustive. Signal 1
  is no longer jargon-only.
