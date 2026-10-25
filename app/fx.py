"""Currencies Scope2Cash can quote and invoice in, and an honest way to show mixed-currency totals.

Supported codes: a subset of PayPal's REST currency list (https://developer.paypal.com/reference/currency-codes/,
checked 2026-10-18). All six are two-decimal currencies, so PayPal's `value` strings ("1234.50") work unchanged for
Invoicing v2, Orders v2 and Payouts v1. Zero-decimal currencies (JPY, HUF, TWD) are deliberately left out.

Conversion is ONLY used for (a) roll-ups that would otherwise add up different currencies (dashboard KPIs, the cash-flow
chart, the scoreboard sort) and (b) turning the USD market range into a starting hourly rate for a non-USD quote.
Invoices are always created in the quote's own currency; nothing is converted on a PayPal call. Rates are a static
snapshot of the European Central Bank's euro foreign exchange reference rates, labelled with their date in the UI.
They are reference rates for information, not PayPal's conversion rate (which includes a spread).
"""
from __future__ import annotations

from typing import Dict

SUPPORTED = ("USD", "EUR", "GBP", "CAD", "AUD", "SGD")
NAMES = {"USD": "US dollar", "EUR": "Euro", "GBP": "Pound sterling", "CAD": "Canadian dollar",
         "AUD": "Australian dollar", "SGD": "Singapore dollar"}
SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "CAD": "CA$", "AUD": "A$", "SGD": "S$"}
REPORTING = "USD"

# ECB euro foreign exchange reference rates (units of currency per 1 EUR), as published for 2026-10-16:
# https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml (fetched 2026-10-18).
RATES_DATE = "2026-10-16"
RATES_SOURCE = "European Central Bank euro foreign exchange reference rates"
RATES_URL = "https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html"
PER_EUR: Dict[str, float] = {"EUR": 1.0, "USD": 1.1253, "GBP": 0.84385, "CAD": 1.5935, "AUD": 1.6171, "SGD": 1.4395}


def normalize(code: str | None, default: str = "USD") -> str:
    """Upper-case and validate a currency code. Raises ValueError for anything outside SUPPORTED."""
    c = (code or default).strip().upper()
    if c not in SUPPORTED:
        raise ValueError(f"currency {c!r} is not supported; use one of {', '.join(SUPPORTED)}")
    return c


def rate(src: str, dst: str) -> float:
    """Units of `dst` per 1 unit of `src` at the snapshot's reference rates."""
    return PER_EUR[normalize(dst)] / PER_EUR[normalize(src)]


def convert(amount: float, src: str, dst: str = REPORTING) -> float:
    if normalize(src) == normalize(dst):
        return round(float(amount), 2)
    return round(float(amount) * rate(src, dst), 2)


def fmt(amount: float, currency: str = "USD", decimals: int = 0) -> str:
    """'$1,200', '€900', '£450.00', 'CA$1,000' (symbol from SYMBOLS; unknown codes fall back to 'XYZ 1,000')."""
    sym = SYMBOLS.get((currency or "USD").upper())
    num = f"{float(amount):,.{decimals}f}"
    return f"{sym}{num}" if sym else f"{currency} {num}"


def label() -> str:
    return f"ECB reference rates of {RATES_DATE}"


def meta() -> Dict:
    """What the UI shows next to any converted ('≈') figure."""
    return {"reporting": REPORTING, "date": RATES_DATE, "source": RATES_SOURCE, "url": RATES_URL,
            "usd_per_unit": {c: round(rate(c, REPORTING), 6) for c in SUPPORTED},
            "supported": [{"code": c, "name": NAMES[c], "symbol": SYMBOLS[c]} for c in SUPPORTED],
            "note": (f"≈ figures convert non-USD amounts to USD at the {RATES_SOURCE} of {RATES_DATE} (a fixed snapshot, "
                     "for information only; PayPal's own conversion rate differs). Per-currency amounts are exact.")}
