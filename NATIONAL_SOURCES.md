# National Tariff Sources — Findings & Enrichment Design

> Companion to `README.md` §5 TODO #8 (enrich `duty_rates` with real national
> values). Every endpoint and sample below was **probed live on 2026-07-07**;
> HTTP status and a real payload are recorded so this isn't from memory.

---

## 0. TL;DR

> **Status (2026-07-10): UK + US enrichment BUILT.** `enrich/` pulls both APIs
> into `duty_rates` — 85 USITC (USA) + 85 UK_TARIFF (GBR) national rows with
> 10-digit codes + Section-301 overlays. Run `python -m enrich.refresh`. EU
> (TARIC) remains deferred — no JSON API.

| Country | Source | Public JSON API? | Auth | Verdict |
|---|---|---|---|---|
| **UK (GBR)** | UK Trade Tariff API (v2) | ✅ Yes, excellent | None | ✅ **Built** — `enrich/uk_adapter.py`. Heading → declarable commodities → ERGA OMNES (geo 1011) MFN duty. |
| **USA** | USITC HTS REST | ✅ Yes | None | ✅ **Built** — `enrich/us_adapter.py` + `duty_parser.py`. 8-digit parent `general` = MFN; Ch. 99 overlays → `notes`. |
| **EU** | TARIC | ❌ No public JSON | — | **Deferred.** Daily TARIC3 **XML** bulk export is the only real path; EU cards fall back to the WITS average. |

Two findings that change the current priority order:

1. **~~The DB is stale~~ — RESOLVED (2026-07-07).** The DB *was* holding
   100 rows with **0 EU** (USA=61, GBR=39) — an earlier WITS re-run had never
   landed. The re-run has since completed: `duty_rates` now = **205 rows, all 3
   reporters (EU 74, GBR 70, USA 61)**, 83 distinct hs6. The WITS-918 live probe
   in §4 remains the confirmation that EU data is genuinely served. Enrichment
   can now proceed on a complete baseline.
2. **No specific/compound rows exist yet** — every in-scope code WITS returned
   came back `ad_valorem` (or 0%). So "NEEDS ENRICHMENT: specific duty" is
   currently *theoretical* for this scope. The genuine enrichment payoff is:
   precise national **10-digit codes**, **Chapter 99 / Section 301 overlays**
   (which WITS hides as "Free"), and **FTA preference rates** — not per-unit
   specific duties, which are rare in these exact 85 codes.

---

## 1. UK — Trade Tariff API  ✅ best-in-class

- **Base:** `https://www.trade-tariff.service.gov.uk/api/v2`
- **Auth:** none. **Format:** JSON:API (`included[]` holds related objects).
- **Freshness:** same API that powers gov.uk; rebuilt nightly from HMRC CDS.

**Endpoints used**
```
GET /commodities/{10-digit}      # e.g. 9306210000 → full measure set
GET /headings/{4-digit}          # to enumerate child commodities of an HS4
```

**Verified live (2026-07-07)** — `9306210000` (shotgun cartridges):
- `Third country duty` (= MFN) → **`2.00 %`** as a structured `duty_expression`
  object (`base`, `formatted_base`), not a string to parse.
- Full FTA preference list (CA/CH/AU/… all `0.00 %`), quotas, VAT, footnotes.
- **Import-control measures are in the same response** → can auto-populate
  `restrictions_flags` for GBR (README TODO #6) instead of manual curation.
- `3004900000` (medicaments, "Other") → MFN **`0.00 %`**. Matches expectation.

**Why first:** structured duties (no string parsing), covers *both* duty
enrichment *and* GBR restrictions, no auth, deep-linkable `source_url`
(`.../commodities/{code}`).

---

## 2. USA — USITC HTS REST  ✅ works, needs a parser

- **Base:** `https://hts.usitc.gov/reststop`
- **Auth:** none. **Format:** flat JSON array of tariff lines.

**Endpoints used**
```
GET /exportList?from={hts}&to={hts}&format=JSON&styles=false   # range of lines
GET /search?keyword={text}                                     # keyword lookup
```
⚠️ Range gotcha: `from`/`to` must span the heading (e.g. `from=9306&to=9307`)
to return the 10-digit children; a tight range returns only the header line.

**Verified live (2026-07-07)** — heading `9306`:
- `9306.21.00.00` (shotgun cartridges) → `general:"Free"`, `other:"30%"`,
  `units:["No."]`, footnote **`"See 9903.88.15"`**.
- `9306.29.00.00` → `general:"Free"`, `other:"45%"`.

**Two things WITS cannot show, that this does:**
- **Column-2 rates** (`other`) — the non-MFN statutory rate.
- **Chapter 99 overlays** — `9903.88.15` is the **Section 301 China** surtax.
  "MFN Free **but +25% if origin CN**" is the demo-defining detail; WITS reports
  a flat "Free" and hides it entirely.

**Parser needed** — duties are strings. Grammar to handle:
| String form | Shape | Parse to |
|---|---|---|
| `Free` | ad valorem | `0.0 %` |
| `2.5%` | ad valorem | `2.5 %` |
| `1.8¢/kg` | **specific** | `specific_amount=1.8`, `specific_unit=cents/kg` |
| `0.9¢/kg + 2.4%` | **compound** | both fields populated |

---

## 3. EU — TARIC  ⚠️ no public JSON API

- **Probed live (2026-07-07):** TARIC consultation site `HTTP 200`,
  Access2Markets `HTTP 200` — both are **HTML UIs, not JSON APIs**.
- **No official REST/JSON endpoint** for programmatic duty lookup.

**Options, ranked by realism**
1. **TARIC3 daily XML bulk export** (DG TAXUD) — the correct production answer:
   full snapshot + daily deltas. But it's a full XML-schema ingestion project
   (measures, geographical areas, duty expressions as separate XML entities).
2. Scrape the consultation site — fragile, discouraged, ToS-risky.
3. **Hackathon stance (recommended):** keep the WITS `918` number as the EU
   baseline, attach a per-code deep-link `source_url` to the TARIC/Access2Markets
   page, and state the gap openly (fits README §6 "known limitations"). WITS
   `918` is confirmed working live (see §4), so EU at least gets a real % — it
   just isn't 10-digit customs-grade.

---

## 4. WITS EU (918) is live — DB re-run now complete ✅

Re-probed `reporter/918` to diagnose the (then) 0 EU rows:
```
GET .../TRN/reporter/918/partner/000/product/300490/year/2022/datatype/reported
→ HTTP 200, dataSets[0].series present, observations["0"] = [0,0,null,0,...]
```
So WITS **does** serve the EU (0% for medicaments, as expected) — the empty EU
column was a **stale-DB artifact, not an API limitation**. The idempotent
`ingest_wits_rates.py` re-run has since been executed: `duty_rates` now carries
**EU (74), GBR (70), USA (61)**. Baseline is complete; enrichment can proceed.

---

## 5. Enrichment design — the ad-valorem question (answered)

**Q: does enrich overwrite the WITS ad-valorem with national values?**
**A: No — it's additive, not destructive.** WITS and the national schedule
answer different questions and we keep both:

- **WITS row** = HS6 *simple-average* ad-valorem across all national lines.
  Coarse, but comparable across countries. Keep as the baseline.
- **National row(s)** = one per real 10-digit line under the HS6, carrying the
  precise ad-valorem *and* the specific/compound amount WITS can't express,
  *and* the overlays (Ch. 99 / preferences).

Concretely: add a `source` discriminator already in the row
(`'WITS'` vs `'USITC'`/`'UK-TARIFF'`) and let both coexist per (hs6, reporter).
The result card prefers the national row when present, falls back to WITS,
and always shows which `source` + `source_url` it used. **Nothing WITS wrote is
mutated** — that preserves provenance and lets the demo show "WITS says 0%,
but USITC 10-digit says Free MFN + Section 301 25%."

**Open decision for you:** when a national ad-valorem exists, should the card
(a) *replace* the WITS number silently, or (b) *show both* side by side?
Recommendation: **(b)** — it's the honesty/explainability pitch, and it's one
extra line in `card.py`.

### Columns enrich fills (already exist, empty)
`specific_amount`, `specific_unit`, `currency`, `unit_of_quantity`,
`national_code` (8–10 digit), plus `notes` for the Ch. 99 overlay text.

### Proposed module (maps to README §5 TODO #8)
```
enrich/
  base.py        # DutyLine dataclass; Adapter protocol: fetch(hs6)->list[DutyLine]
  uk_adapter.py  # heading -> child commodities -> structured duty_expressions
  us_adapter.py  # exportList range -> filter hs6 prefix -> duty_parser
  eu_adapter.py  # WITS passthrough + TARIC deep-link (XML upgrade = later)
  duty_parser.py # US string grammar -> (ad_valorem, specific_amount, unit, ccy)
  refresh.py     # per-country delete-then-insert (idempotent, like WITS ingest)
  diff.py        # new vs old -> change_log rows (unblocks TODO #10)
```
Reuse the ingest infra from `ingest_wits_rates.py`: shared session, 45s timeout
+ exponential backoff, commit every N, drive codes from `hs_taxonomy`.

**Build order:** WITS re-run (TODO #1) → UK adapter (duties **+** GBR
restrictions) → US adapter + `duty_parser` → `diff`/`change_log` → EU last.
