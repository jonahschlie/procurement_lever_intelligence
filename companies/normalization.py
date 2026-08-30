"""Company normalization: one legal entity per name, however each export spells it.

A single workbook spells its own companies consistently, so with one submission
this stage changes nothing. The case it exists for is the intended one: a
submission per portfolio company. Then `Helios Power Polska Sp. z o.o.` and
`HELIOS POWER POLSKA` arrive from different exports and the analysis counts two
companies -- which quietly distorts the portfolio benchmark, the contract
coverage per company and the "supplies several companies" test behind supplier
consolidation.

The company code carries a signal no supplier name has, and it cuts both ways.
Within one export it is authoritative. Across exports it is not: two ERPs both
numbering their entities from 1000 would merge two unrelated companies. That case
is kept apart and surfaced rather than resolved silently.

Deterministic throughout, and no agent. There are a handful of companies against
eighty supplier names, the user sees every group, and the code makes most of the
decisions on its own. Nothing is overwritten either: `company` and `company_name`
stay as the export wrote them, and the canonical values land beside them
(SYSTEMCONCEPT section 11).
"""

from pathlib import Path

import pandas as pd

from core.config import SUPPLIER_AUTO_MERGE
from core.models import CompanyGroup, CompanyMember, CompanyNormalizationArtifact
from core.run import get_logger, record_step, step_path
from core.table import load_table, write_table
from suppliers.candidates import normalize_name, similarity

STEP = "company_normalization"
ARTIFACT_NAME = "company_normalization.json"
CONFIRMED_ARTIFACT_NAME = "company_normalization_confirmed.json"


def run_company_normalization(run_id: str) -> CompanyNormalizationArtifact:
    logger = get_logger(run_id)
    table = load_table(run_id)

    members = _members(table)
    groups = _build_groups(members)

    artifact = CompanyNormalizationArtifact(distinct_names=len(members), groups=groups)
    target = step_path(run_id, STEP)
    (target / ARTIFACT_NAME).write_bytes(artifact.model_dump_json(indent=2).encode("utf-8"))
    record_step(run_id, STEP, [target / ARTIFACT_NAME])

    collisions = sum(1 for group in groups if group.code_collision)
    logger.info(
        "company normalization proposed: %d spelling(s) -> %d compan(ies)%s",
        len(members),
        len(groups),
        f", {collisions} code collision(s)" if collisions else "",
    )
    return artifact


def confirm_companies(
    run_id: str,
    approvals: dict[int, bool] | None = None,
    names: dict[int, str] | None = None,
) -> CompanyNormalizationArtifact:
    """Apply the user's decisions and write the canonical columns.

    A group the user does not approve falls apart: every spelling keeps its own
    identity, exactly as supplier groups behave.
    """
    approvals, names = approvals or {}, names or {}
    artifact = _load(step_path(run_id, STEP) / ARTIFACT_NAME)

    groups = []
    for group in artifact.groups:
        approved = approvals.get(group.group_id, group.approved)
        renamed = names.get(group.group_id, "").strip()
        updates: dict = {"approved": approved}
        if renamed and renamed != group.canonical_name:
            updates.update(canonical_name=renamed, source="user")
        if approved != group.approved:
            updates["source"] = "user"
        groups.append(group.model_copy(update=updates))

    confirmed = artifact.model_copy(update={"groups": groups})
    target = step_path(run_id, STEP)
    (target / CONFIRMED_ARTIFACT_NAME).write_bytes(
        confirmed.model_dump_json(indent=2).encode("utf-8")
    )
    _write_columns(run_id, confirmed)
    record_step(run_id, STEP, [target / ARTIFACT_NAME, target / CONFIRMED_ARTIFACT_NAME])

    merged = sum(1 for group in confirmed.groups if group.approved and len(group.members) > 1)
    get_logger(run_id).info(
        "company normalization confirmed: %d compan(ies) with more than one spelling", merged
    )
    return confirmed


def load_artifact(run_id: str) -> CompanyNormalizationArtifact:
    return _load(step_path(run_id, STEP) / ARTIFACT_NAME)


def load_confirmed(run_id: str) -> CompanyNormalizationArtifact:
    return _load(step_path(run_id, STEP) / CONFIRMED_ARTIFACT_NAME)


def has_artifact(run_id: str) -> bool:
    return (step_path(run_id, STEP) / ARTIFACT_NAME).is_file()


def has_confirmed(run_id: str) -> bool:
    return (step_path(run_id, STEP) / CONFIRMED_ARTIFACT_NAME).is_file()


# --- the grouping ----------------------------------------------------------


def _members(table: pd.DataFrame) -> list[CompanyMember]:
    """Every distinct (dataset, code, name) the bookings use, with its weight.

    Aggregate rows are left out. A grand total row carries the group's name in
    its company column and would otherwise nominate itself as a company.
    """
    rows = table
    if "flag_aggregate_row" in rows.columns:
        rows = rows[~rows["flag_aggregate_row"].fillna(False).astype(bool)]

    frame = pd.DataFrame(
        {
            "dataset_id": rows["dataset_id"].astype(str).str.strip(),
            "code": rows["company"].astype(str).str.strip(),
            "name": rows["company_name"].astype(str).str.strip(),
        }
    )
    frame = frame[(frame["code"] != "") | (frame["name"] != "")]
    counted = frame.groupby(["dataset_id", "code", "name"], sort=False).size()
    return [
        CompanyMember(dataset_id=dataset, code=code, name=name, row_count=int(count))
        for (dataset, code, name), count in counted.items()
    ]


def _build_groups(members: list[CompanyMember]) -> list[CompanyGroup]:
    parent = {index: index for index in range(len(members))}

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    reasons: dict[int, str] = {}
    collisions: set[int] = set()

    for left in range(len(members)):
        for right in range(left + 1, len(members)):
            one, other = members[left], members[right]
            same_code = one.code != "" and one.code == other.code
            score = similarity(one.name, other.name) if one.name and other.name else 0.0

            if same_code and one.dataset_id == other.dataset_id:
                # Within one export a code is authoritative, whatever the spelling.
                union(left, right)
                reasons[find(left)] = "same company code in the same export"
            elif same_code and score >= SUPPLIER_AUTO_MERGE:
                union(left, right)
                reasons[find(left)] = "same company code and the same name"
            elif same_code:
                # Same number, unrelated names: two ERPs counting from 1000.
                collisions.update({left, right})
            elif score >= SUPPLIER_AUTO_MERGE:
                union(left, right)
                reasons[find(left)] = "the same name in another export"

    clusters: dict[int, list[int]] = {}
    for index in range(len(members)):
        clusters.setdefault(find(index), []).append(index)

    groups, sequence = [], 0
    ordered = sorted(
        clusters.items(),
        key=lambda item: -sum(members[index].row_count for index in item[1]),
    )
    for root, indexes in ordered:
        entries = [members[index] for index in indexes]
        sequence += 1
        named = [entry for entry in entries if entry.name] or entries
        canonical = max(named, key=lambda entry: (entry.row_count, len(entry.name)))
        collided = bool(collisions & set(indexes))
        groups.append(
            CompanyGroup(
                group_id=sequence,
                canonical_name=canonical.name or canonical.code,
                canonical_id=f"PLI-C-{sequence:03d}",
                members=entries,
                row_count=sum(entry.row_count for entry in entries),
                source=(
                    "code"
                    if "code" in reasons.get(root, "")
                    else "name"
                    if root in reasons
                    else "single"
                ),
                comment=reasons.get(
                    root,
                    "Shares its code with a different company in another export."
                    if collided
                    else "No other spelling comes close.",
                ),
                code_collision=collided,
                # A collision is the one case worth a decision rather than a glance.
                approved=not collided,
            )
        )
    return groups


def _write_columns(run_id: str, artifact: CompanyNormalizationArtifact) -> None:
    lookup: dict[tuple[str, str, str], tuple[str, str]] = {}
    for group in artifact.groups:
        for index, member in enumerate(group.members, start=1):
            key = (member.dataset_id, member.code, member.name)
            if group.approved:
                lookup[key] = (group.canonical_name, group.canonical_id)
            else:
                # A rejected group falls apart, so every spelling stands alone.
                lookup[key] = (member.name or member.code, f"{group.canonical_id}-{index}")

    table = load_table(run_id)
    keys = list(
        zip(
            table["dataset_id"].astype(str).str.strip(),
            table["company"].astype(str).str.strip(),
            table["company_name"].astype(str).str.strip(),
        )
    )
    table["company_normalized"] = [
        lookup.get(key, (key[2] or key[1], ""))[0] for key in keys
    ]
    table["company_canonical_id"] = [lookup.get(key, ("", ""))[1] for key in keys]
    write_table(run_id, table, STEP, note="canonical company names and ids")


def _load(path: Path) -> CompanyNormalizationArtifact:
    return CompanyNormalizationArtifact.model_validate_json(path.read_bytes())
