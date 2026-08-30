"""Supplier normalization stage: many raw names, one canonical supplier each.

Deterministic similarity finds and auto-merges the unambiguous, the matching
agent judges the grey zone, and the user confirms the result. Original names are
never overwritten (SYSTEMCONCEPT section 11): the canonical name, id and country
land in new columns beside `supplier`.

The submission's supplier master joins the pool as ordinary names. A cluster
that catches a master name inherits its id and country -- master matching is the
same fuzzy problem as name matching, so it uses the same machinery.
"""

from collections import Counter
from pathlib import Path

import pandas as pd

from agents.base import run_agent
from agents.supplier_matching import build_input, definition
from core.models import (
    LlmCall,
    RejectedPair,
    SupplierGroup,
    SupplierNormalizationArtifact,
)
from core.run import get_logger, record_step, step_path
from core.table import load_table, write_table
from suppliers.candidates import CandidatePair, build_candidates

STEP = "supplier_normalization"
ARTIFACT_NAME = "supplier_normalization.json"
CONFIRMED_ARTIFACT_NAME = "supplier_normalization_confirmed.json"

AI_SURE = 0.8


def run_supplier_normalization(run_id: str, *, client=None) -> SupplierNormalizationArtifact:
    logger = get_logger(run_id)
    table = load_table(run_id)

    counts = Counter(name for name in table["supplier"].astype(str).str.strip() if name)
    master = _load_master(run_id)
    pool = sorted(set(counts) | set(master))

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

    groups = _build_groups(pool, edges, counts, master)
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
) -> SupplierNormalizationArtifact:
    """Apply the user's decisions and write the canonical columns.

    A group the user does not approve falls apart: every member keeps its own
    identity. Approving is the only way names merge.
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


def _build_groups(pool, edges, counts, master) -> list[SupplierGroup]:
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
        sequence += 1
        groups.append(
            SupplierGroup(
                group_id=sequence,
                canonical_name=master_hit or max(data_members, key=lambda n: (counts[n], len(n))),
                canonical_id=master[master_hit]["id"] if master_hit else f"PLI-{sequence:03d}",
                members=sorted(data_members),
                row_count=sum(counts[name] for name in data_members),
                source=source,
                confidence=round(confidence, 3),
                comment=comment,
                master_id=master[master_hit]["id"] if master_hit else None,
                country=master[master_hit]["country"] if master_hit else None,
                approved=approved,
            )
        )
    return groups


def _write_columns(run_id: str, artifact: SupplierNormalizationArtifact) -> None:
    lookup: dict[str, tuple[str, str, str]] = {}
    for group in artifact.groups:
        if group.approved:
            for member in group.members:
                lookup[member] = (group.canonical_name, group.canonical_id, group.country or "")
        else:
            for index, member in enumerate(group.members, start=1):
                lookup[member] = (member, f"{group.canonical_id}-{index}", "")

    table = load_table(run_id)
    raw = table["supplier"].astype(str).str.strip()
    table["supplier_normalized"] = raw.map(lambda name: lookup.get(name, (name, "", ""))[0])
    table["supplier_canonical_id"] = raw.map(lambda name: lookup.get(name, ("", "", ""))[1])
    table["supplier_country"] = raw.map(lambda name: lookup.get(name, ("", "", ""))[2])
    write_table(run_id, table, STEP, note="canonical supplier names, ids and countries")


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
    if name_col is None:
        return {}

    master = {}
    for _, row in frame.iterrows():
        name = str(row[name_col]).strip()
        if name:
            master[name] = {
                "id": str(row[id_col]).strip() if id_col else "",
                "country": str(row[country_col]).strip() if country_col else "",
            }
    return master


def _load(path: Path) -> SupplierNormalizationArtifact:
    return SupplierNormalizationArtifact.model_validate_json(path.read_bytes())
