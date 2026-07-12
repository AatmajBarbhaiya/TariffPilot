"""National-tariff enrichment.

Pulls exact national duty numbers from the USITC HTS (USA) and UK Trade Tariff
(UK) APIs into `duty_rates` as rows tagged source='USITC'/'UK_TARIFF' —
*additive* to the WITS baseline, never overwriting it.
"""
