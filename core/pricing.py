"""What a model call cost, in euros.

The token counts come from the API and are measured. The price per token does
not: it is a list price kept in core.config, and the sidebar shows the tokens
beside the euros so a stale price is visible rather than silently believed.

Converted at the ECB reference rate already shipped for the spend conversion, so
the project has one idea of what a euro is.
"""

from functools import lru_cache

from core.config import TOKEN_PRICES_USD

PER_MILLION = 1_000_000


def price_for(model: str) -> tuple[float, float] | None:
    """The (input, output) USD price per million tokens for a model name.

    The API answers with a dated build -- "gpt-5-mini-2025-08-07" -- so the table
    is matched by longest prefix. Longest, not first: "gpt-5-mini" has to win over
    "gpt-5" for a name that starts with both.
    """
    matches = [key for key in TOKEN_PRICES_USD if model.startswith(key)]
    if not matches:
        return None
    return TOKEN_PRICES_USD[max(matches, key=len)]


def cost_eur(model: str, input_tokens: int, output_tokens: int) -> float:
    """Zero for a model with no price, rather than a figure nobody can defend."""
    price = price_for(model)
    if price is None:
        return 0.0
    usd = (input_tokens * price[0] + output_tokens * price[1]) / PER_MILLION
    return usd / usd_per_eur()


@lru_cache(maxsize=1)
def usd_per_eur() -> float:
    """The most recent USD rate in the reference file, cached for the process."""
    from fx.ecb import load_reference_rates

    return float(load_reference_rates()["USD"].dropna().iloc[-1])
