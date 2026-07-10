"""UK Trade Tariff adapter — HS6 → national MFN duty (GBR).

Zero-padding HS6→10-digit is unreliable (sub-split codes 404), so we list a
heading's declarable commodities via `/headings/{hs4}` (one cached call), filter
to those under the HS6, pick a representative (prefer the "Other" catch-all),
then read its ERGA OMNES (geo 1011 = all third countries) "Third country duty"
= the MFN baseline.
"""
from enrich.base import get_json, DutyLine
from enrich import duty_parser

_HDR = {"Accept": "application/json"}
_API = "https://www.trade-tariff.service.gov.uk/api/v2"
_heading_cache = {}


def fetch_heading_commodities(hs4):
    """[(commodity_id, description)] for declarable commodities (cached)."""
    if hs4 not in _heading_cache:
        data = get_json(f"{_API}/headings/{hs4}", _HDR)
        comms = []
        for i in (data or {}).get("included", []):
            if i["type"] == "commodity":
                a = i["attributes"]
                gid = a.get("goods_nomenclature_item_id")
                if a.get("declarable") and gid:
                    comms.append((gid, a.get("description_plain", "")))
        _heading_cache[hs4] = comms
    return _heading_cache[hs4]


def _extract_mfn(commodity_json):
    """(base_expression, geo) for the third-country (MFN) duty; prefer ERGA OMNES."""
    inc = {(i["type"], i["id"]): i for i in commodity_json.get("included", [])}
    fallback = None
    for i in commodity_json.get("included", []):
        if i["type"] != "measure":
            continue
        mt = inc.get(("measure_type", i["relationships"]["measure_type"]["data"]["id"]))
        if not mt or mt["attributes"]["description"] != "Third country duty":
            continue
        geo = (i["relationships"].get("geographical_area", {}).get("data") or {}).get("id")
        de_ref = i["relationships"].get("duty_expression", {}).get("data")
        de = inc.get(("duty_expression", de_ref["id"])) if de_ref else None
        base = de["attributes"]["base"] if de else None
        if base is None:
            continue
        if geo == "1011":                 # ERGA OMNES = canonical MFN
            return base, geo
        if fallback is None:
            fallback = (base, geo)
    return fallback if fallback else (None, None)


def enrich_code(hs6):
    """Return a DutyLine for this HS6, or None if no UK commodity/duty found."""
    comms = [c for c in fetch_heading_commodities(hs6[:4]) if c[0].startswith(hs6)]
    if not comms:
        return None
    # prefer the "Other" catch-all (the usual civilian import line), else first
    comms.sort(key=lambda c: 0 if c[1].strip().lower() == "other" else 1)

    for gid, _desc in comms:
        cj = get_json(f"{_API}/commodities/{gid}", _HDR)
        if not cj:
            continue
        base, geo = _extract_mfn(cj)
        if base is None:
            continue
        parsed = duty_parser.parse_duty(base)
        notes = f"UK Trade Tariff third-country(MFN) duty={base!r} (geo {geo})."
        if len(comms) > 1:
            notes += f" National lines under HS6: {[c[0] for c in comms]}."
        return DutyLine(
            hs6=hs6, reporter="GBR", source="UK_TARIFF",
            source_url=f"https://www.trade-tariff.service.gov.uk/commodities/{gid}",
            national_code=gid, tariff_type="MFN",
            duty_type=parsed["duty_type"], ad_valorem_rate=parsed["ad_valorem_rate"],
            specific_amount=parsed["specific_amount"], specific_unit=parsed["specific_unit"],
            currency=parsed["currency"], notes=notes,
        )
    return None
