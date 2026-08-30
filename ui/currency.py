"""Currency screen: the spend in one currency, and how it got there."""

import pandas as pd
import streamlit as st

from fx.currency import has_report, load_report


def render() -> None:
    st.title("Currency")
    st.markdown(
        "Amounts are converted to EUR at the ECB daily reference rate of their posting "
        "date, and the rates used are frozen into the run. Spend counts **net**: credit "
        "notes reduce it, because the net figure is what actually flowed and therefore "
        "what one negotiates over."
    )

    run_id = st.session_state.get("run_id")
    if run_id is None or not has_report(run_id):
        st.info("No conversion yet. Apply the data quality rules first, then convert to EUR.")
        return

    report = load_report(run_id)

    left, middle, right = st.columns(3)
    left.metric("Net spend (EUR)", f"{report.spend_net_eur:,.0f}")
    middle.metric("Gross spend (EUR)", f"{report.spend_gross_eur:,.0f}")
    right.metric("Credit volume (EUR)", f"{report.credit_volume_eur:,.0f}")
    st.caption(
        f"{report.converted_rows:,} rows converted with {report.rate_source} rates "
        f"({report.rates_frozen_to}, frozen into the run)."
    )

    if report.group_unconverted_rows:
        st.warning(
            f"The export's own group amounts equal the local amounts on "
            f"{report.group_unconverted_rows:,} non-EUR rows — they were never converted. "
            "Provided figures are used as a cross-check only; the EUR amounts here come "
            "from ECB rates."
        )
    if report.flagged_rows:
        st.info(
            f"{report.flagged_rows:,} rows have an amount but no usable rate (missing "
            "currency, missing date, or a date outside the published range). They are "
            "flagged, not guessed."
        )

    st.subheader("By currency")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Currency": entry.currency,
                    "Rows": entry.rows,
                    "Sum (local)": round(entry.sum_local, 2),
                    "Rate range": (
                        f"{entry.rate_min:,.4f} – {entry.rate_max:,.4f}"
                        if entry.rate_min is not None
                        else "-"
                    ),
                    "Sum (EUR)": round(entry.sum_eur, 2),
                }
                for entry in report.breakdown
            ]
        ),
        width="stretch",
        hide_index=True,
    )

    st.divider()
    if st.button("Normalize suppliers", type="primary"):
        _start_suppliers(run_id)


def _start_suppliers(run_id: str) -> None:
    from suppliers.normalization import has_artifact, run_supplier_normalization

    with st.status("Matching supplier names", expanded=True) as status:
        if not has_artifact(run_id):
            st.write("Scoring name pairs and asking the agent about the unclear ones")
            artifact = run_supplier_normalization(run_id)
        else:
            from suppliers.normalization import load_artifact

            artifact = load_artifact(run_id)
        status.update(
            label=f"{artifact.distinct_names} names -> {len(artifact.groups)} suppliers",
            state="complete",
        )
    st.session_state["switch_to"] = "suppliers"
    st.rerun()
