"""USITC HTS adapter — HS6 → national MFN duty (USA).

The HTS declares the MFN rate on the 8-digit subheading (`general` = column-1
MFN); 10-digit statistical suffixes inherit it (their `general` is blank). So
for an HS6 we scan all lines under it, take the `general` from the first
rate-bearing line, and record a representative 10-digit `national_code`. The
column-2 rate (`other`) and any Chapter-99 overlays (Section 301, e.g.
9903.88.15) are folded into `notes`.
"""
import re

from enrich.base import get_json, DutyLine
from enrich import duty_parser

_HDR = {"Accept": "application/json", "User-Agent": "TariffPilot/1.0"}
_BASE = "https://hts.usitc.gov/reststop/exportList"
_heading_cache = {}


def _digits(htsno):
    return re.sub(r"\D", "", htsno or "")


def fetch_heading(hs4):
    """All HTS lines under a 4-digit heading (cached per heading)."""
    if hs4 not in _heading_cache:
        to = str(int(hs4) + 1)
        url = f"{_BASE}?from={hs4}&to={to}&format=JSON&styles=false"
        _heading_cache[hs4] = get_json(url, _HDR) or []
    return _heading_cache[hs4]


def enrich_code(hs6):
    """Return a DutyLine for this HS6, or None if the HTS has no line for it."""
    lines = [l for l in fetch_heading(hs6[:4])
             if _digits(l.get("htsno", "")).startswith(hs6)]
    if not lines:
        return None

    rate_line = next((l for l in lines if (l.get("general") or "").strip()), None)
    if not rate_line:
        return None
    parsed = duty_parser.parse_duty(rate_line["general"])

    # representative 10-digit line for national_code + unit of quantity
    ten = next((l for l in lines if len(_digits(l["htsno"])) == 10), rate_line)
    national = _digits(ten["htsno"])
    units = ten.get("units") or rate_line.get("units") or []
    uoq = units[0] if units else None

    other = (rate_line.get("other") or "").strip()
    footnotes = sorted({f.get("value", "").strip()
                        for l in lines for f in (l.get("footnotes") or [])
                        if f.get("value")})
    nat_lines = sorted({_digits(l["htsno"]) for l in lines
                        if len(_digits(l["htsno"])) >= 8})

    notes = f"USITC MFN(general)={rate_line['general']!r}; column-2(other)={other!r}."
    if footnotes:
        notes += " Overlays: " + " ".join(footnotes)
    if len(nat_lines) > 1:
        notes += f" National lines under HS6: {nat_lines}."

    return DutyLine(
        hs6=hs6, reporter="USA", source="USITC",
        source_url=f"https://hts.usitc.gov/search?query={national}",
        national_code=national, tariff_type="MFN",
        duty_type=parsed["duty_type"], ad_valorem_rate=parsed["ad_valorem_rate"],
        specific_amount=parsed["specific_amount"], specific_unit=parsed["specific_unit"],
        currency=parsed["currency"], unit_of_quantity=uoq, notes=notes,
    )
