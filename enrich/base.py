"""Shared HTTP + the DutyLine record every adapter returns.

HTTP mirrors ingest_wits_rates.py: one shared Session, generous timeout,
exponential-backoff retries, silent on 4xx (our fault) / 404 (no such code).
"""
import time
from dataclasses import dataclass

import requests

REQUEST_TIMEOUT = 30
MAX_RETRIES = 4

_session = None


def session():
    global _session
    if _session is None:
        _session = requests.Session()
    return _session


def get_json(url, headers=None):
    """GET → parsed JSON dict, or None on 404 / 4xx / exhausted retries."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = session().get(url, headers=headers or {}, timeout=REQUEST_TIMEOUT)
            if r.status_code == 200:
                return r.json()
            if r.status_code < 500:          # 404 / 4xx: no point retrying
                return None
        except requests.exceptions.RequestException:
            pass
        if attempt < MAX_RETRIES:
            time.sleep(2 ** attempt)         # 2s, 4s, 8s
    return None


@dataclass
class DutyLine:
    """One enriched national duty row, ready to insert into `duty_rates`."""
    hs6: str
    reporter: str                    # 'USA' | 'GBR'
    source: str                      # 'USITC' | 'UK_TARIFF'
    source_url: str
    national_code: str = None        # 8–10 digit line
    tariff_type: str = "MFN"
    duty_type: str = "ad_valorem"    # ad_valorem | specific | compound
    ad_valorem_rate: float = None
    specific_amount: float = None
    specific_unit: str = None
    currency: str = None
    unit_of_quantity: str = None
    notes: str = ""
