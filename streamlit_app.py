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
import html
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

# label shown to the user -> reporter_country code stored in the DB.
# NOTE: st.selectbox renders option labels as PLAIN TEXT (no HTML/CSS) — flag
# emoji here would only ever render via the viewer's OS/browser font, which is
# unreliable on minimal Linux installs (e.g. a headless AMD server). Real flag
# ICONS (image-based, render identically everywhere) are added separately via
# _flag_span() wherever we control raw HTML below.
COUNTRIES = {
    "USA": "USA",
    "UK": "GBR",
    "EU": "EU",
    "UAE": "ARE",
}

# reporter_country -> flag-icons CSS class (ISO 3166-1 alpha-2; 'eu' is a
# flag-icons special-case for the EU flag).
COUNTRY_FLAG = {"USA": "us", "GBR": "gb", "EU": "eu", "ARE": "ae"}


def _flag_span(country_code):
    """Image-based flag icon (flag-icons CDN) — renders the same on every
    OS/browser, unlike emoji flags which depend on the viewer's font stack."""
    cls = COUNTRY_FLAG.get(country_code, "")
    return f"<span class='fi fi-{cls}' title='{country_code}'></span> " if cls else ""

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
      @import url('https://cdn.jsdelivr.net/npm/flag-icons@7/css/flag-icons.min.css');
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

      /* welcome paragraph — Space Grotesk display.
         Ghost/overlay trick: an invisible full-text copy reserves the TRUE
         final height (via the browser's own wrapping) so the layout below
         never shifts as the visible copy types itself out on top of it. */
      .welcome-wrap {{ position:relative; }}
      .welcome-text {{ font-family:'Space Grotesk',-apple-system,system-ui,sans-serif;
                       font-size:32px; line-height:1.08; font-weight:700;
                       letter-spacing:-1px; color:{text}; }}
      .welcome-ghost {{ visibility:hidden; }}
      .welcome-live {{ position:absolute; top:0; left:0; width:100%; }}
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

      /* image-based flag icons (flag-icons CDN) — sized to sit inline with text */
      .fi {{ display:inline-block; vertical-align:-2px; width:1.15em; height:.85em;
             border-radius:2px; box-shadow:0 0 0 1px rgba(0,0,0,.12); }}

      /* "why" candidate table — plain HTML (not st.dataframe) so descriptions
         can truncate with an ellipsis and show the full text on hover. */
      .why-table {{ width:100%; border-collapse:collapse; font-size:.85rem; color:{text}; }}
      .why-table th, .why-table td {{ text-align:left; padding:6px 10px;
             border-bottom:1px solid rgba(0,0,0,.12); }}
      .why-table td span[title] {{ cursor:help; border-bottom:1px dotted rgba(0,0,0,.35); }}
    </style>
    """, unsafe_allow_html=True)


# ── typewriter welcome (blinking cursor, first load only) ────────────────────
def _welcome_html(current_text):
    """Ghost/overlay markup: an invisible copy of the FULL text reserves the
    real final height (browser-computed, not guessed), while the visible
    `current_text` (growing as it types) sits absolutely-positioned on top —
    so nothing below this block ever shifts as the animation progresses."""
    nl = chr(10)
    ghost = WELCOME.replace(nl, "<br>")
    live = current_text.replace(nl, "<br>")
    return (f"<div class='welcome-wrap'>"
            f"<div class='welcome-text welcome-ghost' aria-hidden='true'>{ghost}</div>"
            f"<div class='welcome-text welcome-live'>{live}<span class='cursor'>|</span></div>"
            f"</div>")


def welcome_placeholder():
    """Reserve the welcome paragraph's position in the layout NOW (empty for
    the moment) so widgets placed after this call still render immediately —
    the typing animation is filled in later, via animate_welcome()."""
    return st.empty()


def animate_welcome(ph):
    """Type WELCOME into `ph` character by character (first load only, with a
    blinking cursor); static render on later reruns. Call this AFTER the rest
    of the page's widgets so the sleep-based animation doesn't block them from
    reaching the browser first."""
    if not st.session_state.typed:
        acc = ""
        for ch in WELCOME:
            acc += ch
            ph.markdown(_welcome_html(acc), unsafe_allow_html=True)
            time.sleep(0.015)
        st.session_state.typed = True
        ph.markdown(_welcome_html(WELCOME), unsafe_allow_html=True)
    else:
        ph.markdown(_welcome_html(WELCOME), unsafe_allow_html=True)


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
    welcome_ph = welcome_placeholder()   # reserve the paragraph's slot now
    st.write("")

    # country + product description on the same line — these render
    # immediately, while the welcome paragraph above is still typing.
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

    # NOW animate the paragraph into its reserved slot above — all the
    # widgets above are already live in the browser at this point.
    animate_welcome(welcome_ph)

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
def _trunc(text, limit=70):
    """Truncate with a trailing '…'; the FULL text shows as a native browser
    tooltip on hover (title attr — no JS). Short text passes through unchanged
    (still HTML-escaped either way)."""
    text = text or ""
    escaped = html.escape(text)
    if len(text) <= limit:
        return escaped
    short = html.escape(text[:limit].rstrip())
    return f"<span title='{escaped}'>{short}…</span>"


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
    country_code = COUNTRIES.get(country_label, "")

    rows = _row("HS code", card["hs6"])
    rows += _row("Description", _trunc(card["description"], 70))
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
             <div class='tc-sub'>Ref #{card['hs6']} · {_flag_span(country_code)}{country_label}</div></div>
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
    country_code = COUNTRIES.get(country_label, "")
    st.write("")
    st.markdown(f"#### {_flag_span(country_code)}Importing into: {country_label}",
                unsafe_allow_html=True)

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
            # plain HTML table (not st.dataframe) so the description column can
            # truncate with an ellipsis and show the full text on hover.
            rows_html = "".join(
                f"<tr><td>{html.escape(c['hs6'])}</td>"
                f"<td>{html.escape(', '.join(c['signals']))}</td>"
                f"<td>{round(c['keyword_score'], 2)}</td>"
                f"<td>{round(c['vector_similarity'], 3)}</td>"
                f"<td>{_trunc(c['description'], 60)}</td></tr>"
                for c in r["candidates"]
            )
            st.markdown(f"""
            <table class='why-table'>
              <thead><tr><th>hs6</th><th>signals</th><th>keyword</th>
                         <th>vector_sim</th><th>description (hover for full)</th></tr></thead>
              <tbody>{rows_html}</tbody>
            </table>
            """, unsafe_allow_html=True)


# ── render ───────────────────────────────────────────────────────────────────
inject_css(st.session_state.ai_on)
page_home()          # welcome + inputs (a search stores the result)
page_card()          # the card renders at the end, after the inputs
