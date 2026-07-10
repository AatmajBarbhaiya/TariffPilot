"""
TariffPilot — test UI.

Run from the project root:
    conda activate nlp
    streamlit run streamlit_app.py

Type a product description, pick the importing country, get a sourced result
card (HS code + duty + restrictions + why). The LLM toggle in the sidebar
switches Signal 2 + the arbiter's LLM path on/off — OFF is free (keyword+vector
only); ON makes ~1–2 Fireworks calls per classification.
"""
import os

import streamlit as st

import config
from llm.client import backend_status
from retrieval import classify

# The real key lives in os.environ (loaded from .env by config); capture it here
# so the sidebar toggle can enable/disable Fireworks at runtime without losing it.
_REAL_KEY = os.environ.get("FIREWORKS_API_KEY", "")

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

    use_llm = st.checkbox(
        "Use LLM (Signal 2 + arbiter)", value=bool(_REAL_KEY),
        help="ON → ~1–2 Fireworks calls per query. OFF → free keyword+vector only.",
    )
    # Apply the toggle by (un)setting the key config.FIREWORKS reads at call time.
    config.FIREWORKS.API_KEY = _REAL_KEY if use_llm else ""

    st.divider()
    s = backend_status()
    st.caption("**Backend status**")
    st.write(f"- local reachable: {'✅' if s['local_reachable'] else '❌'}")
    st.write(f"- Fireworks key: {'✅' if _REAL_KEY else '❌ (set in .env)'}")
    st.write(f"- LLM this session: {'🟢 on' if use_llm and _REAL_KEY else '⚪ off'}")


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
        result = classify(query, country)
    render(result, country_label, country)
elif go:
    st.info("Enter a product description first.")

st.divider()
st.caption("Try: *shotgun shells 12 gauge* · *MRI scanner* · *vaccine for human "
           "medicine* · *laptop computer* (out of scope)")
