"""Parse a duty expression string into structured fields.

Handles the shapes both APIs emit:
  "Free"                 -> ad_valorem 0%
  "2.00 %" / "30%"       -> ad_valorem
  "1.7¢/kg"              -> specific  (amount, unit, currency)
  "23.7¢/pf.liter"       -> specific  (dotted unit — spirits, per proof litre)
  "0.9¢/kg + 2.4%"       -> compound  (both)
  "16.00 GBP / 100 kg"   -> specific  (UK per-quantity form)
  "51¢ each + 6.25% ..." -> compound  (WATCHES — per-unit specific, no slash,
                                       plus one or more ad-valorem components)

For watches (Ch.91) the ad-valorem components apply to *different* value bases
(case, strap, battery), so a single ad_valorem_rate can't capture them exactly
— we record the FIRST component as a representative headline and rely on the
adapter to keep the full original string in `notes`. duty_type is still
correctly flagged `compound` so the specific per-unit charge is never lost.
"""
import re

_CURRENCY = {
    "¢": "USD_cents", "cent": "USD_cents", "cents": "USD_cents",
    "$": "USD", "usd": "USD", "£": "GBP", "gbp": "GBP", "€": "EUR", "eur": "EUR",
}

_CUR = r"¢|cents?|USD|GBP|EUR|\$|£|€"


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
    # (a) per-quantity form: amount + currency, then "/ <qty> <unit>" — unit may
    #     contain a dot (pf.liter). e.g. "1.7¢/kg", "23.7¢/pf.liter", "16 GBP/100 kg"
    spec = re.search(
        rf"([\d.]+)\s*({_CUR})\s*/\s*([\d,]*\s*[A-Za-z][A-Za-z.]*)",
        t, re.IGNORECASE)
    # (b) per-unit form: amount + currency + "each"/"per <thing>" (no slash) —
    #     the watch style. e.g. "51¢ each", "$1.61 each"
    spec_each = re.search(
        rf"([\d.]+)\s*({_CUR})\s*(each|per\s+[A-Za-z]+)", t, re.IGNORECASE)

    if pct:
        out["ad_valorem_rate"] = float(pct.group(1))
    if spec:
        out["specific_amount"] = float(spec.group(1))
        out["currency"] = _CURRENCY.get(spec.group(2).lower(), spec.group(2))
        out["specific_unit"] = spec.group(3).strip()
    elif spec_each:
        out["specific_amount"] = float(spec_each.group(1))
        out["currency"] = _CURRENCY.get(spec_each.group(2).lower(), spec_each.group(2))
        out["specific_unit"] = "each"

    has_spec = bool(spec or spec_each)
    if pct and has_spec:
        out["duty_type"] = "compound"
    elif has_spec:
        out["duty_type"] = "specific"
    else:
        out["duty_type"] = "ad_valorem"
    return out
