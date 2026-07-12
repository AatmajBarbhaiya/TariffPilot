"""
TariffPilot — front-end UI (single scrolling page, light theme).

Run from the project root:
    conda activate nlp
    streamlit run streamlit_app.py

  1. Split (containers / production): set BACKEND_URL and this UI becomes THIN —
     it calls the FastAPI backend over HTTP and does no retrieval itself.
         BACKEND_URL=http://backend:8000 streamlit run streamlit_app.py

  2. Monolith (quick local dev): leave BACKEND_URL unset and it imports the
     retrieval pipeline in-process (needs the full deps + Database/ locally).
         conda activate nlp && streamlit run streamlit_app.py

Layout (no sidebar, no navbar):
  • a lighthearted typewriter welcome types itself (blinking cursor) on first load
  • country + product on one line, robot AI toggle (emoji) centered below, Search
  • the metallic tax-card / invoice renders at the end, after a search
"""
import os
import re
import time

import requests
import streamlit as st

import config

# When set, the UI is thin and everything goes through the backend API.
BACKEND_URL = os.environ.get("BACKEND_URL", "").rstrip("/")

# Capture the real backend config at startup so the robot AI toggle can turn the
# WHOLE LLM off (local vLLM + Fireworks) and restore it, without losing values.
_REAL_KEY = os.environ.get("FIREWORKS_API_KEY", "")
_REAL_LOCAL_ENABLED = config.LLM.ENABLED

# label shown to the user -> reporter_country code stored in the DB
COUNTRIES = {
    "🇺🇸 USA": "USA",
    "🇬🇧 UK": "GBR",
    "🇪🇺 EU": "EU",
    "🇦🇪 UAE": "ARE",
}

WELCOME = (
    "Take a breath—solving complex HS codes and surprise customs duties is my "
    "entire personality. I am your AI tariff assistant, specializing exclusively "
    "in medical equipment and ammunition imports across the US, UK, EU, and UAE. "
    "While I can navigate trade regulations in seconds, treat my assessments as "
    "step one of your two-step verification process, and always double-check the "
    "official details before you bet a shipping container on them."
)


# ── data source (HTTP backend, or in-process fallback) ───────────────────────
def classify_query(query, country, use_llm):
    """Route a classify request to the backend (thin mode) or the in-process
    pipeline (monolith). `use_llm` gates the WHOLE LLM (local vLLM + Fireworks)."""
    if BACKEND_URL:
        r = requests.post(
            f"{BACKEND_URL}/api/classify",
            json={"query": query, "country": country, "use_llm": use_llm},
            timeout=35,
        )
        r.raise_for_status()
        return r.json()
    # in-process monolith — import lazily so the thin UI image (no retrieval
    # deps) never touches these when BACKEND_URL is set.
    from retrieval import classify
    if use_llm:
        config.LLM.ENABLED = _REAL_LOCAL_ENABLED
        config.FIREWORKS.API_KEY = _REAL_KEY
    else:
        config.LLM.ENABLED = False
        config.FIREWORKS.API_KEY = ""
    return classify(query, country)


st.set_page_config(page_title="TariffPilot", page_icon="🤖", layout="wide")

# ── session state ────────────────────────────────────────────────────────────
st.session_state.setdefault("result", None)
st.session_state.setdefault("country_label", list(COUNTRIES)[0])
st.session_state.setdefault("typed", False)
st.session_state.setdefault("ai_on", _REAL_LOCAL_ENABLED or bool(_REAL_KEY))


# ── theming ──────────────────────────────────────────────────────────────────
def inject_css(ai_on):
    # light theme only
    bg = "linear-gradient(160deg,#d7e6fb 0%,#eef5ff 55%,#ffffff 100%)"
    text, muted = "#12243f", "rgba(18,36,63,.62)"
    accent = "#1f6feb"
    in_bg, in_bd = "#ffffff", "#c7d6ec"
    # blink the emoji only when the AI is off ("going blink")
    blink = "animation: eyeblink 2.2s infinite;" if not ai_on else ""

    st.markdown(f"""
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&display=swap');
      /* strip Streamlit chrome */
      [data-testid="stSidebar"], [data-testid="collapsedControl"] {{ display:none !important; }}
      [data-testid="stHeader"] {{ display:none !important; }}
      #MainMenu, footer {{ visibility:hidden; }}

      .stApp {{ background:{bg} fixed; }}
      .block-container {{ padding: 2% 15% 15% 15% !important; max-width:100% !important; }}
      .stApp, .stMarkdown, h1,h2,h3,h4,h5,p,label,.stCaption {{ color:{text}; }}

      /* inputs tuned to the theme */
      .stTextInput input, div[data-baseweb="select"] > div {{
        background:{in_bg} !important; color:{text} !important;
        border-color:{in_bd} !important; }}

      /* app name */
      .brand {{ font-family:'Space Grotesk',-apple-system,system-ui,sans-serif;
                font-size:2rem; font-weight:800; letter-spacing:-.5px;
                color:{text}; margin-bottom:.6rem; }}

      /* welcome paragraph — Space Grotesk display */
      .welcome {{ font-family:'Space Grotesk',-apple-system,system-ui,sans-serif;
                  font-size:32px; line-height:1.08; font-weight:700;
                  letter-spacing:-1px; color:{text}; }}
      .cursor {{ color:{accent}; font-weight:400;
                 animation: blink 1s steps(1) infinite; }}
      @keyframes blink {{ 50% {{ opacity:0; }} }}

      /* robot AI toggle — the emoji itself is the (borderless) button */
      .st-key-ai_emoji {{ display:flex; justify-content:center; }}
      .st-key-ai_emoji button {{ border:none !important; background:transparent !important;
        box-shadow:none !important; padding:.1rem .3rem !important; }}
      .st-key-ai_emoji button:hover {{ background:transparent !important; transform:scale(1.1); }}
      .st-key-ai_emoji button:focus {{ box-shadow:none !important; }}
      .st-key-ai_emoji button p {{ font-size:2.6rem !important; line-height:1; margin:0; {blink} }}
      @keyframes eyeblink {{ 0%,88%,100% {{ opacity:1; }} 94% {{ opacity:.15; }} }}
      .botcap {{ font-size:.78rem; color:{muted}; margin-top:.1rem; text-align:center; }}

      /* ── metallic tax-card (theme-independent silver) ─────────────────────── */
      .tax-card {{
        position:relative;
        background: linear-gradient(135deg,#eceef0 0%,#c6cace 22%,#f6f7f8 48%,
                    #b9bdc2 74%,#dadde0 100%);
        border:1px solid #9aa0a6; border-radius:16px;
        box-shadow: 0 12px 34px rgba(0,0,0,.28),
                    inset 0 1px 0 rgba(255,255,255,.7),
                    inset 0 -1px 0 rgba(0,0,0,.15);
        color:#1b1e21; padding:30px 34px;
        font-family: "Courier New", ui-monospace, monospace; overflow:hidden; }}
      .tax-card::before {{ content:""; position:absolute; inset:0;
        background: linear-gradient(115deg, transparent 30%,
                    rgba(255,255,255,.35) 46%, transparent 62%); pointer-events:none; }}
      .tc-head {{ display:flex; justify-content:space-between; align-items:flex-start;
                 border-bottom:2px solid rgba(0,0,0,.35); padding-bottom:12px; margin-bottom:16px; }}
      .tc-title {{ font-size:1.3rem; font-weight:800; letter-spacing:2px; }}
      .tc-sub {{ font-size:.8rem; opacity:.7; }}
      .tc-row {{ display:flex; justify-content:space-between; padding:7px 0;
                border-bottom:1px dashed rgba(0,0,0,.18); }}
      .tc-k {{ opacity:.72; }}  .tc-v {{ font-weight:700; text-align:right; }}
      .tc-total {{ display:flex; justify-content:space-between; gap:16px;
                  flex-wrap:wrap; margin-top:14px; padding-top:12px;
                  border-top:2px solid rgba(0,0,0,.35);
                  font-size:1.15rem; font-weight:800; }}
      .tc-duty {{ text-align:right; font-size:.95rem; max-width:66%; }}
      .tc-flag {{ background:rgba(150,40,40,.10); border-left:3px solid #a33;
                 padding:8px 12px; margin:8px 0; border-radius:4px; font-size:.85rem; }}
      .tc-foot {{ margin-top:16px; font-size:.75rem; opacity:.7; }}
      .tax-card a {{ color:#294a7a; }}
      .tc-stamp {{ display:inline-block; border:2px solid; border-radius:6px;
                  padding:2px 10px; font-weight:800; letter-spacing:1px;
                  transform:rotate(-4deg); font-size:.8rem; }}
      .tax-card.empty, .tax-card.review {{
        background: linear-gradient(135deg,#e9ebee,#cfd3d7);
        font-family: inherit; text-align:center; padding:38px 34px; }}
    </style>
    """, unsafe_allow_html=True)


# ── typewriter welcome (blinking cursor, first load only) ────────────────────
def welcome_block():
    nl = chr(10)
    if not st.session_state.typed:
        ph = st.empty()
        acc = ""
        for ch in WELCOME:
            acc += ch
            ph.markdown(f"<div class='welcome'>{acc.replace(nl, '<br>')}"
                        f"<span class='cursor'>|</span></div>", unsafe_allow_html=True)
            time.sleep(0.015)
        st.session_state.typed = True
        ph.markdown(f"<div class='welcome'>{WELCOME.replace(nl, '<br>')}"
                    f"<span class='cursor'>|</span></div>", unsafe_allow_html=True)
    else:
        st.markdown(f"<div class='welcome'>{WELCOME.replace(nl, '<br>')}"
                    f"<span class='cursor'>|</span></div>", unsafe_allow_html=True)


# ── HOME ─────────────────────────────────────────────────────────────────────
def robot_toggle():
    """The emoji itself is the toggle: 🤖 eyes-open (AI on) ↔ 😴 blinking (AI off).
    Purely UI state — the gating is applied at search time in classify_query()."""
    ai_on = st.session_state.ai_on
    if st.button("🤖" if ai_on else "😴", key="ai_emoji",
                 help="Toggle the AI (LLM). Off = free keyword + vector only.",
                 use_container_width=True):
        st.session_state.ai_on = not ai_on
        st.rerun()

    caption_text = "AI on · I see all" if ai_on else "AI off · Going Blind"
    st.markdown(f"<div class='botcap'>{caption_text}</div>", unsafe_allow_html=True)


def page_home():
    st.markdown("<div class='brand'>📦 TariffPilot</div>", unsafe_allow_html=True)
    welcome_block()
    st.write("")

    # country + product description on the same line
    c1, c2 = st.columns([1, 3])
    country_label = c1.selectbox(
        "Importing country", list(COUNTRIES),
        index=list(COUNTRIES).index(st.session_state.country_label))
    query = c2.text_input("What are you shipping?",
                          placeholder="e.g. sterile disposable syringes, 5 ml")

    # LLM toggle emoji, centered, below the line
    _, mid, _ = st.columns([3, 2, 3])
    with mid:
        robot_toggle()

    go = st.button("🔎 Search", type="primary", use_container_width=True)

    st.caption("Try:  shotgun shells 12 gauge  ·  MRI scanner  ·  "
               "vaccine for human medicine  ·  laptop (out of scope)")

    if go and query.strip():
        with st.spinner("Assessing…"):
            try:
                result = classify_query(query, COUNTRIES[country_label],
                                        st.session_state.ai_on)
            except requests.RequestException as e:
                st.error(f"Backend unreachable: {e}")
                result = None
        if result:
            st.session_state.result = result
            st.session_state.country_label = country_label
    elif go:
        st.info("Type a product description first 🙂")


# ── CARD ─────────────────────────────────────────────────────────────────────
def _row(k, v):
    return f"<div class='tc-row'><span class='tc-k'>{k}</span><span class='tc-v'>{v}</span></div>"


_CUR_SYM = {"USD_cents": "¢", "USD": "$", "GBP": "£", "EUR": "€"}


def _format_duty(duty):
    """Render a duty for display, honoring specific/compound — not just the
    ad-valorem %. For specific/compound the truest form is the original tariff
    expression (e.g. '51¢ each + 6.25% on the case...'), which the adapters keep
    in notes as  general)='<...>'. Falls back to structured fields, then to a
    plain %."""
    if not duty:
        return "—"
    if duty.get("duty_type") in ("specific", "compound"):
        m = re.search(r"""general\)=(['"])(.*?)\1""", duty.get("notes") or "")
        if m:
            return m.group(2)
        sym = _CUR_SYM.get(duty.get("currency"), duty.get("currency") or "")
        amt, unit = duty.get("specific_amount"), (duty.get("specific_unit") or "")
        parts = []
        if amt is not None:
            parts.append(f"{amt:g}{sym} each" if unit == "each"
                         else f"{amt:g}{sym}/{unit}".rstrip("/"))
        if duty.get("ad_valorem_rate"):
            parts.append(f"{duty['ad_valorem_rate']:g}%")
        return " + ".join(parts) or "—"
    r = duty.get("ad_valorem_rate")
    if r is None:
        return "—"
    return "Free" if r == 0 else f"{r:g}%"


def metallic_invoice(r, country_label):
    card = r["card"]
    duty = card.get("duty") or {}
    rate = _format_duty(duty)
    stamp = {"high": "#2e7d32", "medium": "#b26a00"}.get(r["confidence"], "#777")

    rows = _row("HS code", card["hs6"])
    rows += _row("Description", (card["description"] or "")[:70])
    rows += _row("Category", card["category"])
    if duty.get("national_code"):
        rows += _row("National line", duty["national_code"])
    if duty.get("source"):
        rows += _row("Duty source", duty["source"])

    flags = ""
    for f in card.get("restrictions", []):
        flags += (f"<div class='tc-flag'>🔒 <b>{f['flag_type']}</b> — {f['description']}"
                  f" &nbsp;<a href='{f['source_url']}' target='_blank'>source</a></div>")
    if not flags:
        flags = "<div class='tc-foot'>No import restrictions on file for this country.</div>"

    src = (f"<a href='{duty['source_url']}' target='_blank'>duty source ↗</a>"
           if duty.get("source_url") else "")

    return f"""
    <div class='tax-card'>
      <div class='tc-head'>
        <div><div class='tc-title'>TARIFF ASSESSMENT</div>
             <div class='tc-sub'>Ref #{card['hs6']} · {country_label}</div></div>
        <div class='tc-stamp' style='color:{stamp};border-color:{stamp}'>
             {r['confidence'].upper()} · {r['path']}</div>
      </div>
      {rows}
      <div class='tc-total'><span>MFN DUTY</span><span class='tc-duty'>{rate}</span></div>
      {flags}
      <div class='tc-foot'>{src} &nbsp; · &nbsp; every number carries its source URL.</div>
    </div>
    """


def page_card():
    r = st.session_state.result
    if not r:
        return                      # nothing to show until the first search

    country_label = st.session_state.country_label
    st.write("")
    st.markdown(f"#### Importing into: {country_label}")

    if r["decision"] == "classified" and r.get("card"):
        st.markdown(metallic_invoice(r, country_label), unsafe_allow_html=True)
    elif r["decision"] == "out_of_scope":
        st.markdown(f"<div class='tax-card review'>🚫 <b>Out of scope</b><br><br>{r['reason']}</div>",
                    unsafe_allow_html=True)
    else:
        st.markdown(f"<div class='tax-card review'>🔎 <b>Needs review</b> "
                    f"<i>({r['path']})</i><br><br>{r['reason']}</div>",
                    unsafe_allow_html=True)

    if r.get("candidates"):
        with st.expander("🔍 Why — candidate signals"):
            st.dataframe(
                [{"hs6": c["hs6"], "signals": ", ".join(c["signals"]),
                  "keyword": round(c["keyword_score"], 2),
                  "vector_sim": round(c["vector_similarity"], 3),
                  "description": (c["description"] or "")[:60]}
                 for c in r["candidates"]],
                hide_index=True, use_container_width=True,
            )


# ── render ───────────────────────────────────────────────────────────────────
inject_css(st.session_state.ai_on)
page_home()          # welcome + inputs (a search stores the result)
page_card()          # the card renders at the end, after the inputs
