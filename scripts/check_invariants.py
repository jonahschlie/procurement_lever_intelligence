"""Print the figures of a confirmed run that later changes must not move.

Refactors are supposed to change how a number is produced, never the number. This
script reads a run that has been through the whole pipeline and prints its
headline results, so the same run can be re-read after every change and compared
line by line.

    uv run python scripts/check_invariants.py                # newest run
    uv run python scripts/check_invariants.py run_2026...    # a specific one
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analysis.spend_report import spend_chain  # noqa: E402
from core.config import runs_dir  # noqa: E402
from core.table import has_table, load_table  # noqa: E402
from levers.engine import has_artifact as has_levers  # noqa: E402
from levers.engine import load_artifact as load_levers  # noqa: E402
from suppliers.normalization import has_confirmed as has_suppliers  # noqa: E402
from suppliers.normalization import load_confirmed as load_suppliers  # noqa: E402


def latest_run() -> str | None:
    runs = sorted(path.name for path in runs_dir().glob("run_*") if path.is_dir())
    return runs[-1] if runs else None


def report(run_id: str) -> None:
    print(f"run {run_id}")

    if not has_table(run_id):
        print("  no canonical table -- nothing to check")
        return
    table = load_table(run_id)
    print(f"  rows                    {len(table):,}")

    chain = spend_chain(table)
    for step in chain.chain:
        print(f"  {step.label:<23} {step.amount:>18,.0f}")

    for column in ("flag_duplicate_transaction", "flag_duplicate_document"):
        flag = table.get(column)
        if flag is not None:
            print(f"  {column[5:]:<23} {int(flag.fillna(False).astype(bool).sum()):>18,}")

    if has_suppliers(run_id):
        artifact = load_suppliers(run_id)
        approved = [g for g in artifact.groups if g.approved]
        intercompany = sum(1 for g in approved if g.is_intercompany)
        print(
            f"  suppliers               {artifact.distinct_names} raw names -> "
            f"{len(approved)} canonical, {intercompany} intercompany"
        )

    if has_levers(run_id):
        artifact = load_levers(run_id)
        print(f"  analysable spend        {artifact.analysable_spend:>18,.0f}")
        print(
            f"  potential low/base/high {artifact.total_low:>18,.0f} "
            f"{artifact.total_base:>14,.0f} {artifact.total_high:>14,.0f}"
        )
        for lever in artifact.levers:
            if lever.status == "quantified":
                print(
                    f"    {lever.lever_id:<24} base {lever.net_base:>16,.0f} "
                    f"-> {lever.potential_base:>13,.0f}"
                )
        blocked = sum(1 for l in artifact.levers if l.status == "not_assessable")
        print(f"  not assessable          {blocked:>18}")
        print(f"  data requests           {len(artifact.data_requests):>18}")


if __name__ == "__main__":
    run_id = sys.argv[1] if len(sys.argv) > 1 else latest_run()
    if run_id is None:
        sys.exit("no runs found")
    report(run_id)
