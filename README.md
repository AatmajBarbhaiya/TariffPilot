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
correctly from **any** working directory (see bug #10).

| Path | Role | Run order |
|---|---|---|
| `init_database/init_db.py` | Creates SQLite schema (`Database/tariff_pilot.db`): 5 tables + indexes, WAL mode | 1 |
| `init_database/test_db.ipynb` | Scratch notebook for poking at the schema/data | — |
| `data_ingestion/ingest_db.py` | Chroma client helper — persistent store at `Database/chroma_db`, collection `hs_taxonomy`, cosine space | (imported) |
| `data_ingestion/ingest_taxonomy.py` | Pulls GitHub `datasets/harmonized-system` CSV → filters to scope → **dual-writes**: upserts vectors into Chroma AND rows into SQL `hs_taxonomy` | 2 |
| `data_ingestion/ingest_wits_rates.py` | Pulls WITS (World Bank) MFN duty rates for all in-scope codes × 3 reporters → `duty_rates`. Idempotent; retries with backoff; commits periodically | 3 |
| `data_ingestion/seed_restrictions_and_tests.py` | Seeds `restrictions_flags` (licensing) and `test_set` (ground-truth examples) | 4 |
| `sanity_check.py` | Coverage report + end-to-end lookup: code → description → rate → restriction, all with source URLs | 5 (any time) |
| `Database/` | `tariff_pilot.db` + `chroma_db/` — the two data stores | — |
| `requirements.txt` | `requests`, `chromadb`, `sentence-transformers` | — |
| `NATIONAL_SOURCES.md` | Live-verified national tariff APIs (UK/US/EU) + enrichment design for TODO #8 | — |

Run from the **repo root** (the parent of `Tarrifpilot/`) so the
`from Tarrifpilot.data_ingestion...` package import in `ingest_taxonomy.py`
resolves:

```bash
python -m Tarrifpilot.init_database.init_db
python -m Tarrifpilot.data_ingestion.ingest_taxonomy
python -m Tarrifpilot.data_ingestion.ingest_wits_rates
python -m Tarrifpilot.data_ingestion.seed_restrictions_and_tests
python Tarrifpilot/sanity_check.py
```

## 2. Data stores

**SQLite (`Database/tariff_pilot.db`)** — the structured/provenance side
(row counts below reflect the current DB as of 2026-07-07):

- `hs_taxonomy` — 85 codes: `hs6` PK, description, chapter, heading,
  `category_tag`, `keywords` (⚠ still 0/85 populated — see §5), `hs_revision`.
- `duty_rates` — **205 rows**, one per (code × reporter) that WITS served:
  **all 3 reporters present** (EU 74, GBR 70, USA 61), 83 distinct hs6. duty_type
  `ad_valorem | specific | compound`, rate, `source_url` **required**,
  `retrieved_date`, notes. Indexed on `(hs6, reporter_country)`. Every in-scope
  code WITS returned is `ad_valorem` — no specific/compound rows yet (the
  per-unit values come from national enrichment, TODO #8 / `NATIONAL_SOURCES.md`).
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

## 4. Retrieval architecture — 3-signal design (planned)

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

### Signal 1 — SQL keyword match

Tokenize input (lowercase, strip stopwords), match tokens against
`description` and `keywords` in `hs_taxonomy`, score by hit count.

Grounded prototype results (already tested against the real DB):
`"vaccine"` → `300241`, `300242` ✓ · `"syringe"` → `901831` ✓ ·
`"shotgun shells"` → **0 rows** ✗ (descriptions say *cartridges*).

Conclusion: works for technical jargon, fails on synonyms →
**populate the `keywords` column** (e.g. `930621`: "shotgun shells, shells,
buckshot, birdshot"; `300241`: "vaccine, immunization, jab, shot"). ~85 rows,
one-time effort, can be LLM-drafted then human-skimmed. Prefer SQLite **FTS5**
(porter stemming, ranked matches) over `LIKE` chains; plain fallback:
`WHERE description LIKE '%tok%' OR keywords LIKE '%tok%'` per token, ordered
by tokens hit.

Output: `[{hs6, score, matched_tokens, signal: "keyword"}]` — may be empty; never wrong-but-confident.

### Signal 2 — TOC routing (scope resolution)

One cheap LLM call (Gemini 2.5 Flash) with the **static chapter/heading map**
(a dozen lines: Ch. 30 = pharmaceuticals; 9018–9022 = medical instruments;
9305 = arms parts; 9306 = ammunition) + the user text →
returns `{chapters: [...], headings: [...], in_scope: bool}`.

- Used **only as a filter mask** for Signal 3 (Chroma metadata `where`
  filter on `heading`/`chapter`) and as a candidate-consistency check.
- `in_scope: false` → short-circuit: "outside supported categories" —
  the guardrail against classifying laptops as medicine.
- Must degrade gracefully: no API key / timeout / malformed JSON → skip
  the signal (no filter), never block the pipeline.
- Strict output contract: JSON only; validate headings against the known
  set; drop anything unrecognized.

### Signal 3 — Chroma vector search (semantic fallback)

Always runs (cheap at 85 vectors). `collection.query(query_texts=[input],
n_results=5, where={"heading": {"$in": scope}} if scope else None)`.
Convert cosine distance → similarity; carry it as confidence. This is what
catches *"liquid formulation to protect against viral infections"* → vaccines
with zero keyword overlap.

Output: `[{hs6, similarity, signal: "vector"}]`.

### Arbiter

Merge candidates keyed by hs6, tracking which signals nominated each.

1. **Fast path (no LLM):** keyword ∩ vector agree on one code, vector
   similarity above threshold, consistent with Signal 2 scope → return it.
   Confidence: high.
2. **LLM path:** send Gemini the candidate list **with official taxonomy
   descriptions** + user text → must return one of the given hs6 values (or
   `abstain`) + one-line justification. Reject any code not in the candidate
   list (validate against `hs_taxonomy` — hallucination guard).
3. **Abstain path:** no candidates / arbiter abstains → return top-3
   suggestions with "needs human review" instead of a wrong answer.

Result card assembly (already proven end-to-end by `sanity_check.py`):
join chosen hs6 → `hs_taxonomy` (description) → `duty_rates` (rate + type +
source URL; surface the "NEEDS ENRICHMENT" warning for specific/compound) →
`restrictions_flags` (license warnings + source URL). Card also shows *which
signals agreed* — that's the explainability pitch.

### Proposed module layout

```
retrieval/
  signals.py      # keyword_search(q), toc_route(q), vector_search(q, scope)
  arbiter.py      # merge + fast path + LLM arbitration + validation
  card.py         # hs6 -> sourced result card (SQL joins)
  evaluate.py     # run test_set through classify(), report top-1/top-3 accuracy
config.py         # model names (embedder, LLM), thresholds, API keys via env
```

Evaluation loop: `evaluate.py` runs all `test_set` rows through the full
chain and prints top-1 / top-3 accuracy per category + a per-example
signal-agreement breakdown. This is the demo's accuracy number.

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

**Open (priority order):**

1. **Populate `hs_taxonomy.keywords`** — still 0/85. Unblocks Signal 1 on
   synonyms (evidence: "shotgun shells" finds nothing today). ~85 rows, one-time,
   LLM-drafted then human-skimmed.
2. **Build `retrieval/`** per §4, then `evaluate.py` against `test_set`.
3. **Real CROSS ruling numbers** into `test_set` (replace `TODO-CROSS`) — each
   with the ruling's own URL; use the ruling to audit the guessed `correct_hs6`.
   **Done: 3/8** (`901831`, `901832`, `300490`). **Remaining: 5** (`300241`,
   `901812`, `930630` ×2, `930690`).
4. **Enrich `duty_rates` with absolute/specific values (not just WITS ad-valorem)**
   — WITS gives only a percentage (and reports specific duties as `0%`). Fill the
   already-existing but empty columns from national sources: `specific_amount`,
   `specific_unit` (e.g. `USD/1000 units`), `currency`, `unit_of_quantity`, and
   the precise `national_code` (8–10 digit). **APIs already live-verified — see
   `NATIONAL_SOURCES.md`** (UK Trade Tariff + USITC HTS are ready; EU needs the
   TARIC XML export). Biggest correctness gap for landed cost.
5. **Landed-cost calculator** handling all three duty shapes (ad valorem /
   specific / compound) once TODO #4 supplies the absolute values.
6. **Change monitoring** — populate `change_log` from the WTO–IMF Tariff Tracker
   (or the diff step of the enrichment refresh, per `NATIONAL_SOURCES.md`).
7. **Embedding model swap** (MiniLM is a placeholder) — keep the model name in `config.py`; a swap forces full re-embedding.

## 6. Known limitations (be upfront in the demo)

- WITS ad-valorem averages are **HS6 simple averages** of national tariff
  lines — fine for a demo, not customs-grade precision (that's what national
  8–10-digit enrichment is for).
- Specific/compound duties are **flagged, not quantified** until enrichment.
- Restrictions are **manually curated with verified citations** (there is no
  uniform API for licensing law). Coverage now spans ammunition, firearm parts,
  and controlled/alkaloid medicaments across all 3 countries (48 rows), but is
  still **bounded by scope**: in-scope codes outside those families carry no flag
  yet. "Curated" = few but verified rows, not unreliable rows.
- `keywords` empty until TODO #1 → Signal 1 currently jargon-only.
