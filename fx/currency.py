"""Currency stage: one spend figure, in EUR, per SYSTEMCONCEPT section 13.

Converts the local amount at the ECB daily rate of the posting date. The
submission's own group amounts turned out to be unconverted copies of the local
amounts, so provided figures are treated as a cross-check, never as the source.

Spend semantics, decided once and applied here: **net**. Credit notes carry
their sign into every sum, because the net figure is what actually flowed and
therefore what one negotiates over. Gross and the credit volume are reported
alongside.
"""

import pandas as pd

from core.models import CurrencyBreakdown, CurrencyReport
from core.run import get_logger, record_step, step_path
from core.table import load_table, write_table
from fx.ecb import rates_for

STEP = "currency"
ARTIFACT_NAME = "currency_report.json"
RATES_NAME = "ecb_rates.csv"


def run_currency(
    run_id: str, rates: pd.DataFrame, rate_source: str = "ecb"
) -> CurrencyReport:
    """Convert the run's amounts to EUR and freeze the rates that were used."""
    logger = get_logger(run_id)
    table = load_table(run_id)

    currencies = table["currency"].astype(str).str.strip()
    amount = table["amount_local_value"]
    rate, rate_date = rates_for(rates, currencies, table["posting_date_value"])

    table["fx_rate"] = rate
    table["fx_rate_date"] = rate_date
    table["amount_eur"] = amount / rate
    table["flag_missing_fx_rate"] = amount.notna() & table["amount_eur"].isna()

    frozen = _freeze_rates(run_id, rates, currencies)
    write_table(run_id, table, STEP, note=f"amounts converted to EUR ({rate_source} rates)")

    include = table["include_spend_analysis"].astype(bool)
    eur = table["amount_eur"].where(include)
    non_eur = currencies.isin(set(currencies) - {"", "EUR"})
    unconverted = (
        non_eur
        & table["amount_group_value"].notna()
        & (table["amount_group_value"] == table["amount_local_value"])
    )

    report = CurrencyReport(
        row_count=len(table),
        rate_source=rate_source,
        rates_frozen_to=frozen,
        spend_net_eur=float(eur.sum()),
        spend_gross_eur=float(eur[eur > 0].sum()),
        credit_volume_eur=float(-eur[eur < 0].sum()),
        converted_rows=int((include & eur.notna()).sum()),
        flagged_rows=int(table["flag_missing_fx_rate"].sum()),
        group_unconverted_rows=int(unconverted.sum()),
        breakdown=_breakdown(table, currencies, include),
    )

    target = step_path(run_id, STEP)
    path = target / ARTIFACT_NAME
    path.write_bytes(report.model_dump_json(indent=2).encode("utf-8"))
    record_step(run_id, STEP, [path, step_path(run_id, STEP) / RATES_NAME])
    logger.info(
        "currency conversion complete: net spend %.2f EUR from %d row(s), %d without a rate",
        report.spend_net_eur,
        report.converted_rows,
        report.flagged_rows,
    )
    return report


def submitted_fx_rates(run_id: str) -> pd.DataFrame | None:
    """Best-effort fallback: the FX sheet the submission itself shipped.

    A static table without dates -- the same rate applies to every day. Used only
    when the ECB history cannot be fetched.
    """
    from ingestion.storage import load_dataframe
    from triage.workbook_triage import load_datasets

    fx = next((d for d in load_datasets(run_id) if d.role == "fx_rates"), None)
    if fx is None:
        return None

    frame = load_dataframe(run_id, fx)
    currency_column = next((c for c in frame.columns if "currency" in c.lower()), None)
    rate_column = next(
        (c for c in frame.columns if "rate" in c.lower() and c != currency_column), None
    )
    if currency_column is None or rate_column is None:
        return None

    rates = pd.to_numeric(frame[rate_column].str.replace(",", "."), errors="coerce")
    wide = pd.DataFrame(
        {
            str(code).strip().upper(): [value]
            for code, value in zip(frame[currency_column], rates)
            if str(code).strip() and pd.notna(value)
        },
        # One nominal date, far in the past, so forward-fill covers every booking.
        index=pd.to_datetime(["1999-01-01"]),
    )
    return wide.drop(columns=["EUR"], errors="ignore") if not wide.empty else None


def load_report(run_id: str) -> CurrencyReport:
    path = step_path(run_id, STEP) / ARTIFACT_NAME
    return CurrencyReport.model_validate_json(path.read_bytes())


def has_report(run_id: str) -> bool:
    return (step_path(run_id, STEP) / ARTIFACT_NAME).is_file()


def _freeze_rates(run_id: str, rates: pd.DataFrame, currencies: pd.Series) -> str:
    """Store the slice of the rate table the run actually used.

    Reproducibility must not depend on the ECB being reachable later or on rates
    being revised: the run carries its own copy.
    """
    needed = sorted((set(currencies) - {"", "EUR"}) & set(rates.columns))
    frozen = rates[needed] if needed else rates
    path = step_path(run_id, STEP) / RATES_NAME
    frozen.to_csv(path, index_label="Date")
    return f"{len(frozen):,} days x {len(frozen.columns)} currencies"


def _breakdown(
    table: pd.DataFrame, currencies: pd.Series, include: pd.Series
) -> list[CurrencyBreakdown]:
    entries = []
    for currency in sorted(set(currencies.where(include, "")) - {""}):
        rows = include & (currencies == currency)
        rates = table.loc[rows, "fx_rate"]
        entries.append(
            CurrencyBreakdown(
                currency=currency,
                rows=int(rows.sum()),
                sum_local=float(table.loc[rows, "amount_local_value"].sum()),
                rate_min=float(rates.min()) if rates.notna().any() else None,
                rate_max=float(rates.max()) if rates.notna().any() else None,
                sum_eur=float(table.loc[rows, "amount_eur"].sum()),
            )
        )
    return entries
