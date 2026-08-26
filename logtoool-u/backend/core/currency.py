"""
Currency code resolution.

Several log formats (AFS/Netcetera, Debit Portal, Cardinal, VFlex) carry the
transaction currency as a raw ISO 4217 *numeric* code (e.g. "840") straight
from the upstream payload, rather than the alpha-3 code ("USD") the rest of
the tool expects. resolve_currency_code() normalizes either shape to the
alpha-3 code so every downstream consumer -- storage, analysis summaries,
the API, the UI -- sees one consistent representation.
"""
import logging
from typing import Any, Dict, Optional

import pycountry

logger = logging.getLogger("logtool.currency")


def resolve_currency_code(value: Optional[str]) -> Optional[str]:
    """Normalizes a raw currency value to its ISO 4217 alpha-3 code.

    - Numeric ISO 4217 code ("840") -> alpha-3 ("USD").
    - Alpha code in any case ("usd") -> uppercased alpha-3 ("USD").
    - Unrecognized or empty value -> returned unchanged (stripped), so a
      value the tool has never seen before is never silently dropped.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    if text.isdigit():
        currency = pycountry.currencies.get(numeric=text.zfill(3))
        if currency is not None:
            return currency.alpha_3
        logger.warning("Unrecognized numeric currency code: %r", text)
        return text

    currency = pycountry.currencies.get(alpha_3=text.upper())
    return currency.alpha_3 if currency is not None else text.upper()


def resolve_transaction_currency(transaction: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Returns a shallow copy of a transaction-shaped dict (one with a
    "currency" key) with that field passed through resolve_currency_code().
    None/empty-safe -- passed through unchanged. Used by each custom
    parser's LLens-adapter layer to normalize the currency captured by the
    unmodified, customer-provided parsing logic above it, without touching
    that original logic."""
    if not transaction:
        return transaction
    resolved = dict(transaction)
    resolved["currency"] = resolve_currency_code(resolved.get("currency"))
    return resolved
