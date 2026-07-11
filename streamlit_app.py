"""
TariffPilot — front-end UI.

Two ways to run:

  1. Split (containers / production): set BACKEND_URL and this UI becomes THIN —
     it calls the FastAPI backend over HTTP and does no retrieval itself.
         BACKEND_URL=http://backend:8000 streamlit run streamlit_app.py

  2. Monolith (quick local dev): leave BACKEND_URL unset and it imports the
     retrieval pipeline in-process (needs the full deps + Database/ locally).
         conda activate nlp && streamlit run streamlit_app.py

Type a product description, pick the importing country, get a sourced result
card (HS code + duty + restrictions + why). The LLM toggle switches Signal 2 +
the arbiter's LLM path on/off — OFF is free (keyword+vector only).
"""
import os

import requests
import streamlit as st

# When set, the UI is thin and everything goes through the backend API.
BACKEND_URL = os.environ.get("BACKEND_URL", "").rstrip("/")


# ── data source (HTTP backend, or in-process fallback) ───────────────────────
def classify_query(query, country, use_llm):
    if BACKEND_URL:
        r = requests.post(
            f"{BACKEND_URL}/api/classify",
            json={"query": query, "country": country, "use_llm": use_llm},
            timeout=35,
        )
        r.raise_for_status()
        return r.json()
    # in-process monolith fallback — import lazily so the thin UI image (which
    # has no retrieval deps) never touches these when BACKEND_URL is set.
    import config
    from retrieval import classify
    _real = os.environ.get("FIREWORKS_API_KEY", "")
    config.FIREWORKS.API_KEY = _real if use_llm else ""
    return classify(query, country)


def llm_status():
    """Return a small dict for the sidebar: whether the LLM path can run."""
    if BACKEND_URL:
        try:
            s = requests.get(f"{BACKEND_URL}/health", timeout=5).json().get("llm", {})
            return {
                "reachable": True,
                "local_reachable": s.get("local_reachable"),
                "fireworks": bool(s.get("fireworks_configured")),
            }
        except Exception:
            return {"reachable": False, "local_reachable": None, "fireworks": False}
    from llm.client import backend_status
    s = backend_status()
    return {
        "reachable": True,
        "local_reachable": s.get("local_reachable"),
        "fireworks": bool(os.environ.get("FIREWORKS_API_KEY", "")),
    }


st.set_page_config(page_title="TariffPilot", page_icon="📦", layout="centered")

# ── sidebar ─────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Settings")
    # label shown to the user -> reporter_country code stored in the DB
    COUNTRIES = {
        "🇺🇸 United States (USA)": "USA",
        "🇬🇧 United Kingdom (GBR)": "GBR",
        "🇪🇺 European Union (EU)": "EU",
        "🇦🇪 United Arab Emirates (ARE)": "ARE",
    }
    country_label = st.selectbox("Importing country", list(COUNTRIES), index=0)
    country = COUNTRIES[country_label]

    _st = llm_status()
    use_llm = st.checkbox(
        "Use LLM (Signal 2 + arbiter)", value=_st["fireworks"],
        help="ON → ~1–2 LLM calls per query (droplet/Fireworks). "
             "OFF → free keyword+vector only.",
    )

    st.divider()
    st.caption("**Backend status**")
    if BACKEND_URL:
        st.write(f"- mode: split → `{BACKEND_URL}`")
        st.write(f"- backend reachable: {'✅' if _st['reachable'] else '❌'}")
    else:
        st.write("- mode: in-process (monolith)")
    st.write(f"- local LLM reachable: {'✅' if _st['local_reachable'] else '❌'}")
    st.write(f"- Fireworks key: {'✅' if _st['fireworks'] else '❌ (set in .env)'}")
    st.write(f"- LLM this session: {'🟢 on' if use_llm and _st['fireworks'] else '⚪ off'}")


# ── card renderer ───────────────────────────────────────────────────────────
def _chip(label):
    return (f"<span style='background:#eef;border-radius:10px;padding:2px 8px;"
            f"margin-right:6px;font-size:0.8em'>{label}</span>")


def render(r, country_label="", country_code=""):
    # Make the destination unmistakable — every duty/restriction below is for
    # THIS importing country.
    if country_label:
        st.markdown(f"### Importing into: {country_label}")
        st.caption(f"All duty rates and restrictions shown are for **{country_code}**.")

    dec = r["decision"]
    if dec == "classified":
        st.success(f"✅ **Classified** as HS **{r['hs6']}**  ·  confidence: "
                   f"**{r['confidence']}**  ·  via *{r['path']}*")
    elif dec == "needs_review":
        # distinct sub-reasons — the reason string says which one
        st.warning(f"🔎 **Needs review** ({r['path']}) — {r['reason']}")
        if r["path"] == "ambiguous":
            st.info("💡 Tip: enable **Use LLM** in the sidebar — it usually "
                    "resolves ambiguous candidates like these.")
    else:
        st.error(f"🚫 **Out of scope** — {r['reason']}")

    if dec == "classified":
        st.caption(r["reason"])

    card = r.get("card")
    if card:
        st.subheader(f"{card['hs6']} — {card['description']}")
        chips = "".join(_chip(x) for x in [
            card["category"], f"ch {card['chapter']}", f"heading {card['heading']}",
            *(f"signal: {s}" for s in r["signals_agreed"]),
        ])
        st.markdown(chips, unsafe_allow_html=True)

        # duty
        duty = card.get("duty")
        st.markdown("#### Duty")
        if not duty:
            st.info("No duty rate on file for this country.")
        else:
            c1, c2, c3 = st.columns(3)
            c1.metric(f"MFN ({duty['source']})", f"{duty['ad_valorem_rate']}%")
            if duty.get("baseline_wits"):
                c2.metric("WITS avg", f"{duty['baseline_wits']['ad_valorem_rate']}%")
            if duty.get("national_code"):
                c3.metric("National code", duty["national_code"])
            if duty.get("warning"):
                st.warning(duty["warning"])
            notes = duty.get("notes") or ""
            if "Overlays" in notes:
                overlay = notes[notes.index("Overlays"):]
                st.info(f"⚠️ US trade-action overlay in effect — {overlay}")
            st.markdown(f"[🔗 duty source]({duty['source_url']})")

        # restrictions
        st.markdown("#### Restrictions")
        if not card["restrictions"]:
            st.write("None on file for this country.")
        for f in card["restrictions"]:
            st.warning(f"🔒 **{f['flag_type']}** — {f['description']}")
            st.markdown(f"[🔗 source]({f['source_url']})")

    # the "why" — always show the candidates the signals nominated
    if r["candidates"]:
        with st.expander("🔍 Why — candidate signals", expanded=(dec != "classified")):
            st.dataframe(
                [{"hs6": c["hs6"], "signals": ", ".join(c["signals"]),
                  "keyword": round(c["keyword_score"], 2),
                  "vector_sim": round(c["vector_similarity"], 3),
                  "description": (c["description"] or "")[:60]}
                 for c in r["candidates"]],
                hide_index=True, use_container_width=True,
            )

    with st.expander("🛠 Raw result (debug)"):
        st.json(r)


# ── main ────────────────────────────────────────────────────────────────────
st.title("📦 TariffPilot")
st.caption("HS-code classification + tariff/restriction lookup, with a source "
           "URL behind every number.")

query = st.text_input("Product description",
                      placeholder="e.g. sterile disposable syringes, 5 ml")
go = st.button("Classify", type="primary")

if go and query.strip():
    with st.spinner("Classifying…"):
        try:
            result = classify_query(query, country, use_llm)
        except requests.RequestException as e:
            st.error(f"Backend unreachable: {e}")
            result = None
    if result:
        render(result, country_label, country)
elif go:
    st.info("Enter a product description first.")

st.divider()
st.caption("Try: *shotgun shells 12 gauge* · *MRI scanner* · *vaccine for human "
           "medicine* · *laptop computer* (out of scope)")
