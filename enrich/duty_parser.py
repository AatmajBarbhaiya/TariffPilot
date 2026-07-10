"""Parse a duty expression string into structured fields.

Handles the shapes both APIs emit:
  "Free"                 -> ad_valorem 0%
  "2.00 %" / "30%"       -> ad_valorem
  "1.7¢/kg"              -> specific  (amount, unit, currency)
  "0.9¢/kg + 2.4%"       -> compound  (both)
  "16.00 GBP / 100 kg"   -> specific  (UK per-quantity form)

For our in-scope 85 codes the national duties are all Free/ad-valorem, so the
specific/compound branches are dormant-but-correct (kept so enrichment stays
right if a per-unit duty ever appears in scope). See README bug #3.
"""
import re

_CURRENCY = {
    "¢": "USD_cents", "cent": "USD_cents", "cents": "USD_cents",
    "$": "USD", "usd": "USD", "£": "GBP", "gbp": "GBP", "€": "EUR", "eur": "EUR",
}


def parse_duty(text):
    """Return {duty_type, ad_valorem_rate, specific_amount, specific_unit,
    currency}. Unknown/empty input → all-None ad_valorem shell."""
    out = {"duty_type": "ad_valorem", "ad_valorem_rate": None,
           "specific_amount": None, "specific_unit": None, "currency": None}
    if not text or not text.strip():
        return out
    t = text.strip()

    if t.lower() == "free":
        out["ad_valorem_rate"] = 0.0
        return out

    pct = re.search(r"([\d.]+)\s*%", t)
    # amount + currency symbol/code, then "/ <qty> <unit>"
    spec = re.search(
        r"([\d.]+)\s*(¢|cents?|USD|GBP|EUR|\$|£|€)\s*/\s*([\d,]*\s*[A-Za-z%]+)",
        t, re.IGNORECASE)

    if pct:
        out["ad_valorem_rate"] = float(pct.group(1))
    if spec:
        out["specific_amount"] = float(spec.group(1))
        out["currency"] = _CURRENCY.get(spec.group(2).lower(), spec.group(2))
        out["specific_unit"] = spec.group(3).strip()

    if pct and spec:
        out["duty_type"] = "compound"
    elif spec:
        out["duty_type"] = "specific"
    else:
        out["duty_type"] = "ad_valorem"
    return out
