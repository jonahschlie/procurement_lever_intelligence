"""ECB daily reference rates.

The rate history ships with the repository rather than being fetched at run
time. Conversion then needs no network, works on a locked-down deployment, and
-- most importantly -- a run made today can be reproduced in a year against the
same rates. ``fetch_ecb_history()`` refreshes the file when it goes stale.

The ECB convention is units of currency per 1 EUR, so converting reads

    amount_eur = amount_local / rate

Rates exist only for trading days. Lookups forward-fill from the last published
day, which is the ECB's own convention for weekends and holidays.
"""

import io
import ssl
import urllib.request
import zipfile
from pathlib import Path

import certifi
import pandas as pd

from core.config import ECB_RATES_FILE, ECB_RATES_URL


def load_reference_rates(path: Path = ECB_RATES_FILE) -> pd.DataFrame:
    """The rate history shipped with the repository."""
    return parse_ecb_csv(Path(path).read_text(encoding="utf-8"))


def fetch_ecb_history(url: str = ECB_RATES_URL, timeout: float = 30.0) -> pd.DataFrame:
    """Refresh the history from the ECB. Not used at run time, only to update the file.

    An explicit certificate bundle is needed because a standalone Python build
    does not read the operating system's trust store.
    """
    context = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(url, timeout=timeout, context=context) as response:
        payload = response.read()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        name = next(n for n in archive.namelist() if n.endswith(".csv"))
        return parse_ecb_csv(archive.read(name).decode("utf-8"))


def parse_ecb_csv(text: str) -> pd.DataFrame:
    """Parse either layout: the shipped file's 'date', or the ECB download's 'Date'."""
    frame = pd.read_csv(io.StringIO(text)).rename(columns=str.strip)
    date_column = next(column for column in frame.columns if column.lower() == "date")
    frame[date_column] = pd.to_datetime(frame[date_column])
    frame = frame.set_index(date_column).sort_index()
    frame.index.name = "Date"
    # The ECB download ends every line with a comma, which pandas reads as a column.
    frame = frame[[c for c in frame.columns if not str(c).startswith("Unnamed")]]
    return frame.apply(pd.to_numeric, errors="coerce")


def daily_rates(rates: pd.DataFrame) -> pd.DataFrame:
    """Reindex to every calendar day, forward-filling non-trading days."""
    full = pd.date_range(rates.index.min(), rates.index.max(), freq="D")
    return rates.reindex(full).ffill()


def rates_for(
    rates: pd.DataFrame, currencies: pd.Series, dates: pd.Series
) -> tuple[pd.Series, pd.Series]:
    """Vectorised lookup: the rate and the actual rate date used per row.

    EUR is 1 by definition. Anything without a currency, without a date, with an
    unknown currency or with a date outside the published range gets no rate --
    flagging that is the caller's job, guessing is nobody's.
    """
    daily = daily_rates(rates)
    stacked = daily.stack()  # (date, currency) -> rate

    normalized = pd.to_datetime(dates).dt.normalize()
    keys = pd.MultiIndex.from_arrays([normalized, currencies.astype(str).str.strip()])
    looked_up = pd.Series(keys.map(stacked), index=currencies.index, dtype="float64")

    rate = looked_up.where(currencies.astype(str).str.strip() != "EUR", 1.0)
    rate_date = normalized.where(rate.notna())
    return rate, rate_date
