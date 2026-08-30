"""Supplier normalization stage: many raw names, one canonical supplier each.

Deterministic similarity finds and auto-merges the unambiguous, the matching
agent judges the grey zone, and the user confirms the result. Original names are
never overwritten (SYSTEMCONCEPT section 11): the canonical name, id and country
land in new columns beside `supplier`.

The submission's supplier master joins the pool as ordinary names. A cluster
that catches a master name inherits its id, country and contract status -- master
matching is the same fuzzy problem as name matching, so it uses the same
machinery.

Contract status is carried through because it identifies a lever directly: spend
concentrated on a supplier with no contract on file is what section 12 calls
contract optimization. It is three-valued on purpose. A supplier absent from the
master is *unknown*, which is not the same claim as *no contract*.
"""

from collections import Counter
from pathlib import Path

import pandas as pd

from agents.base import run_agent
from agents.supplier_matching import build_input, definition
from core.canonical import bookings
from core.models import (
    LlmCall,
    RejectedPair,
    SupplierGroup,
    SupplierNormalizationArtifact,
)
from core.run import get_logger, record_step, step_path
from core.table import load_table, write_table
from suppliers.candidates import CandidatePair, build_candidates
from suppliers.intercompany import detect_intercompany

STEP = "supplier_normalization"
ARTIFACT_NAME = "supplier_normalization.json"
CONFIRMED_ARTIFACT_NAME = "supplier_normalization_confirmed.json"

AI_SURE = 0.8

# A blank flag in the master means the supplier is listed but has no contract on
# file. Absence from the master entirely is a different thing, and stays unknown.
CONTRACT_LABELS = {True: "yes", False: "no", None: "unknown"}
CONTRACT_TRUE = frozenset({"Y", "YES", "TRUE", "X", "1", "J", "JA"})


def run_supplier_normalization(run_id: str, *, client=None) -> SupplierNormalizationArtifact:
    logger = get_logger(run_id)
    table = load_table(run_id)

    # Bookings only. A subtotal row carries its own marker in the supplier column,
    # so left in it becomes a name to group -- carrying no spend, but inflating the
    # supplier count and putting a row in front of the user that means nothing.
    counts = Counter(
        name for name in bookings(table)["supplier"].astype(str).str.strip() if name
    )
    master = _load_master(run_id)
    pool = sorted(set(counts) | set(master))

    # One clustering pass over every name. Intercompany is a label on the result,
    # not a separate run, so moving a group between the two review blocks costs
    # nothing to recompute.
    intercompany = {c.supplier: c for c in detect_intercompany(table, sorted(counts))}
    auto, grey, _ = build_candidates(pool)
    logger.info(
        "supplier matching: %d name(s), %d auto pair(s), %d for the agent",
        len(pool),
        len(auto),
        len(grey),
    )

    verdicts, llm_call = _judge(grey, table, master, client, run_id, logger)

    edges: list[tuple[str, str, str, float, str]] = [
        (pair.left, pair.right, "deterministic", pair.similarity, "Same name after cleanup.")
        for pair in auto
    ]
    rejected = []
    for pair, verdict in zip(grey, verdicts):
        if verdict.same:
            source = "ai" if verdict.confidence >= AI_SURE else "ai_unsure"
            edges.append((pair.left, pair.right, source, verdict.confidence, verdict.comment))
        else:
            rejected.append(
                RejectedPair(
                    left=pair.left,
                    right=pair.right,
                    similarity=pair.similarity,
                    comment=verdict.comment,
                )
            )

    groups = _build_groups(pool, edges, counts, master, intercompany)
    artifact = SupplierNormalizationArtifact(
        distinct_names=len(counts),
        groups=groups,
        rejected=rejected,
        llm_call=llm_call,
    )

    target = step_path(run_id, STEP)
    (target / ARTIFACT_NAME).write_bytes(artifact.model_dump_json(indent=2).encode("utf-8"))
    record_step(run_id, STEP, [target / ARTIFACT_NAME])
    logger.info(
        "supplier normalization proposed: %d name(s) -> %d canonical supplier(s)",
        len(counts),
        len(groups),
    )
    return artifact


def confirm_suppliers(
    run_id: str,
    approvals: dict[int, bool] | None = None,
    names: dict[int, str] | None = None,
    intercompany: dict[int, bool] | None = None,
    *,
    assignments: dict[str, str] | None = None,
) -> SupplierNormalizationArtifact:
    """Apply the user's decisions and write the canonical columns.

    A group the user does not approve falls apart: every member keeps its own
    identity. Approving is the only way names merge.

    `assignments` maps a raw name to the group it belongs in, which is how the
    user moves a name between groups, invents a group or splits one -- decisions
    approving and renaming cannot express. It is applied after the group-level
    decisions, so intercompany marks made above carry into the rebuilt groups.
    Without it nothing about the path below changes.
    """
    approvals, names, intercompany = approvals or {}, names or {}, intercompany or {}
    artifact = _load(step_path(run_id, STEP) / ARTIFACT_NAME)

    groups = []
    for group in artifact.groups:
        approved = approvals.get(group.group_id, group.approved)
        renamed = names.get(group.group_id, "").strip()
        updates: dict = {"approved": approved}
        marked = intercompany.get(group.group_id, group.is_intercompany)
        if marked != group.is_intercompany:
            updates.update(
                is_intercompany=marked,
                intercompany_reason="Marked by the user." if marked else "",
                source="user",
            )
        if renamed and renamed != group.canonical_name:
            updates.update(canonical_name=renamed, source="user")
        if approved != group.approved:
            updates["source"] = "user"
        groups.append(group.model_copy(update=updates))

    if assignments is not None:
        groups = _regroup(_name_counts(run_id), groups, assignments)

    confirmed = artifact.model_copy(update={"groups": groups})
    target = step_path(run_id, STEP)
    (target / CONFIRMED_ARTIFACT_NAME).write_bytes(
        confirmed.model_dump_json(indent=2).encode("utf-8")
    )

    _write_columns(run_id, confirmed)
    record_step(run_id, STEP, [target / ARTIFACT_NAME, target / CONFIRMED_ARTIFACT_NAME])
    merged = sum(1 for g in confirmed.groups if g.approved and len(g.members) > 1)
    get_logger(run_id).info(
        "supplier normalization confirmed: %d merge group(s) applied", merged
    )
    return confirmed


def load_artifact(run_id: str) -> SupplierNormalizationArtifact:
    return _load(step_path(run_id, STEP) / ARTIFACT_NAME)


def load_confirmed(run_id: str) -> SupplierNormalizationArtifact:
    return _load(step_path(run_id, STEP) / CONFIRMED_ARTIFACT_NAME)


def has_artifact(run_id: str) -> bool:
    return (step_path(run_id, STEP) / ARTIFACT_NAME).is_file()


def has_confirmed(run_id: str) -> bool:
    return (step_path(run_id, STEP) / CONFIRMED_ARTIFACT_NAME).is_file()


def name_volumes(run_id: str) -> pd.DataFrame:
    """Rows and spend per raw supplier name, so a name can be moved knowingly."""
    table = load_table(run_id)
    amount = pd.to_numeric(table.get("amount_eur"), errors="coerce")
    if amount is None:
        amount = pd.Series(float("nan"), index=table.index)
    eligible = table.get("include_spend_analysis")
    if eligible is not None:
        amount = amount.where(eligible.fillna(False).astype(bool))

    frame = pd.DataFrame(
        {"name": table["supplier"].astype(str).str.strip(), "spend": amount}
    )
    frame = frame[frame["name"] != ""]
    return frame.groupby("name").agg(rows=("name", "size"), spend=("spend", "sum"))


def _name_counts(run_id: str) -> dict[str, int]:
    volumes = name_volumes(run_id)
    return {str(name): int(entry["rows"]) for name, entry in volumes.iterrows()}


def _regroup(
    counts: dict[str, int], groups: list[SupplierGroup], assignments: dict[str, str]
) -> list[SupplierGroup]:
    """Rebuild the groups from the name-to-group map the user wrote.

    A group built by hand is approved by definition -- the user is the decision.
    Two things are inherited rather than invented: the master entry, because
    country and contract status may only come from there, and the intercompany
    mark, which follows whichever original group contributes the most rows.
    """
    origin = {member: group for group in groups for member in group.members}

    clusters: dict[str, list[str]] = {}
    for member in origin:
        label = assignments.get(member, "").strip() or member
        clusters.setdefault(label, []).append(member)

    def weight(names) -> int:
        return sum(counts.get(name, 0) for name in names)

    rebuilt = []
    ordered = sorted(clusters.items(), key=lambda item: -weight(item[1]))
    for sequence, (label, members) in enumerate(ordered, start=1):
        sources = [origin[name] for name in members]
        # Only where the group still holds the name that matched the master. Split
        # a group and it is no longer known which half the master entry described,
        # so country and contract status go back to unknown rather than follow the
        # wrong half.
        master = next(
            (
                group
                for group in sources
                if group.master_id and group.canonical_name in members
            ),
            None,
        )
        dominant = max(sources, key=lambda group: weight(group.members))
        rebuilt.append(
            SupplierGroup(
                group_id=sequence,
                canonical_name=label,
                canonical_id=(master.master_id if master else f"PLI-{sequence:03d}"),
                members=sorted(members),
                row_count=weight(members),
                source="user",
                confidence=1.0,
                comment="Grouped by hand.",
                master_id=master.master_id if master else None,
                country=master.country if master else None,
                contract_on_file=master.contract_on_file if master else None,
                is_intercompany=dominant.is_intercompany,
                intercompany_reason=dominant.intercompany_reason,
                approved=True,
            )
        )
    return rebuilt


def _judge(grey, table, master, client, run_id, logger):
    if not grey:
        return [], None

    context = _context(table, master)
    payload = [
        {
            "pair_id": index,
            "left": pair.left,
            "right": pair.right,
            "left_context": context.get(pair.left, {}),
            "right_context": context.get(pair.right, {}),
        }
        for index, pair in enumerate(grey)
    ]
    result = run_agent(definition(), build_input(payload), client=client, logger=logger)

    by_id = {verdict.pair_id: verdict for verdict in result.output.verdicts}
    verdicts = []
    for index, pair in enumerate(grey):
        verdict = by_id.get(index)
        if verdict is None:
            # No answer is treated as no merge -- the safe direction.
            from agents.supplier_matching import PairVerdict

            verdict = PairVerdict(
                pair_id=index,
                same=False,
                confidence=0.0,
                comment="The agent returned no verdict for this pair.",
            )
        verdicts.append(verdict)
    llm_call = LlmCall(
        model=result.model,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        duration_seconds=result.duration_seconds,
    )
    return verdicts, llm_call


def _build_groups(pool, edges, counts, master, intercompany=None) -> list[SupplierGroup]:
    parent = {name: name for name in pool}

    def find(name):
        while parent[name] != name:
            parent[name] = parent[parent[name]]
            name = parent[name]
        return name

    edge_info: dict[str, list] = {}
    for left, right, source, confidence, comment in edges:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[root_right] = root_left
        edge_info.setdefault(left, []).append((source, confidence, comment))
        edge_info.setdefault(right, []).append((source, confidence, comment))

    clusters: dict[str, list[str]] = {}
    for name in pool:
        clusters.setdefault(find(name), []).append(name)

    groups, sequence = [], 0
    for members in sorted(clusters.values(), key=lambda m: -sum(counts.get(n, 0) for n in m)):
        data_members = [name for name in members if name in counts]
        if not data_members:
            continue  # a master entry nothing matched

        joined = [info for name in members for info in edge_info.get(name, [])]
        sources = {source for source, _, _ in joined}
        if "ai_unsure" in sources:
            source, approved = "ai_unsure", False
        elif "ai" in sources:
            source, approved = "ai", True
        else:
            source, approved = "deterministic", True
        confidence = min((c for _, c, _ in joined), default=1.0)
        comment = next(
            (c for s, _, c in joined if s in ("ai", "ai_unsure")),
            joined[0][2] if joined else "No other name comes close.",
        )

        master_hit = next((name for name in members if name in master), None)
        entry = master.get(master_hit, {}) if master_hit else {}
        hits = [(intercompany or {}).get(name) for name in data_members]
        hits = [hit for hit in hits if hit]
        sequence += 1
        groups.append(
            SupplierGroup(
                group_id=sequence,
                canonical_name=master_hit or max(data_members, key=lambda n: (counts[n], len(n))),
                canonical_id=entry.get("id") or f"PLI-{sequence:03d}",
                members=sorted(data_members),
                row_count=sum(counts[name] for name in data_members),
                source=source,
                confidence=round(confidence, 3),
                comment=comment,
                master_id=entry.get("id") if master_hit else None,
                country=entry.get("country") if master_hit else None,
                contract_on_file=entry.get("contract") if master_hit else None,
                is_intercompany=bool(hits),
                intercompany_reason="; ".join(hits[0].reasons) if hits else "",
                approved=approved,
            )
        )
    return groups


def _write_columns(run_id: str, artifact: SupplierNormalizationArtifact) -> None:
    blank = {"name": "", "id": "", "country": "", "contract": "unknown", "intercompany": "no"}
    lookup: dict[str, dict[str, str]] = {}
    for group in artifact.groups:
        contract = CONTRACT_LABELS[group.contract_on_file]
        intercompany = "yes" if group.is_intercompany else "no"
        if group.approved:
            for member in group.members:
                lookup[member] = {
                    "name": group.canonical_name,
                    "id": group.canonical_id,
                    "country": group.country or "",
                    "contract": contract,
                    "intercompany": intercompany,
                }
        else:
            # A rejected group falls apart, so its members keep their own identity
            # and inherit nothing from the master hit the group had.
            for index, member in enumerate(group.members, start=1):
                lookup[member] = {
                    "name": member,
                    "id": f"{group.canonical_id}-{index}",
                    "country": "",
                    "contract": "unknown",
                    "intercompany": intercompany,
                }

    table = load_table(run_id)
    raw = table["supplier"].astype(str).str.strip()

    def column(key, default=""):
        return raw.map(lambda name: lookup.get(name, blank).get(key, default))

    table["supplier_normalized"] = raw.map(lambda name: lookup.get(name, blank)["name"] or name)
    table["supplier_canonical_id"] = column("id")
    table["supplier_country"] = column("country")
    # Rows with no supplier at all say nothing about contracts either.
    table["supplier_contract_status"] = column("contract").where(raw != "", "")
    table["flag_intercompany"] = column("intercompany", "no") == "yes"
    write_table(
        run_id,
        table,
        STEP,
        note="canonical supplier names, ids, countries, contract status and intercompany",
    )


def _context(table: pd.DataFrame, master) -> dict[str, dict]:
    """What the agent may see per name: categories from the data, country from the master."""
    context: dict[str, dict] = {}
    supplier = table["supplier"].astype(str).str.strip()
    gl = table["gl_description"].astype(str).str.strip()
    for name, group in gl.groupby(supplier):
        if name:
            top = [value for value, _ in Counter(v for v in group if v).most_common(3)]
            if top:
                context[name] = {"purchase_context": top}
    for name, info in master.items():
        entry = context.setdefault(name, {})
        entry["from_supplier_master"] = True
        if info["country"]:
            entry["country"] = info["country"]
    return context


def _load_master(run_id: str) -> dict[str, dict]:
    from ingestion.storage import load_dataframe
    from triage.workbook_triage import load_datasets

    try:
        datasets = load_datasets(run_id)
    except FileNotFoundError:
        # A run without a triage artifact simply has no master to offer.
        return {}
    dataset = next((d for d in datasets if d.role == "supplier_master"), None)
    if dataset is None:
        return {}

    frame = load_dataframe(run_id, dataset)
    name_col = next((c for c in frame.columns if "name" in c.lower()), None)
    id_col = next((c for c in frame.columns if "id" in c.lower()), None)
    country_col = next((c for c in frame.columns if "country" in c.lower()), None)
    contract_col = next((c for c in frame.columns if "contract" in c.lower()), None)

    # Column detection is by header wording, so say what was recognised. A master
    # that is present but unreadable would otherwise look like no master at all.
    get_logger(run_id).info(
        "supplier master: %d row(s), columns name=%r id=%r country=%r contract=%r",
        len(frame),
        name_col,
        id_col,
        country_col,
        contract_col,
    )
    if name_col is None:
        return {}

    master = {}
    for _, row in frame.iterrows():
        name = str(row[name_col]).strip()
        if name:
            master[name] = {
                "id": str(row[id_col]).strip() if id_col else "",
                "country": str(row[country_col]).strip() if country_col else "",
                # Listed but blank means no contract; a missing column means we
                # genuinely do not know.
                "contract": (
                    str(row[contract_col]).strip().upper() in CONTRACT_TRUE
                    if contract_col
                    else None
                ),
            }
    return master


def _load(path: Path) -> SupplierNormalizationArtifact:
    return SupplierNormalizationArtifact.model_validate_json(path.read_bytes())
