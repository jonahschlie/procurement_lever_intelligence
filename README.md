# Procurement Lever Intelligence

Transforms heterogeneous ERP exports from private equity portfolio companies into a
standardized procurement data model, and identifies procurement value creation levers on top
of it. See [SYSTEMCONCEPT.md](SYSTEMCONCEPT.md) for the architecture and functional concept.

## Status

Implemented so far:

1. **Ingestion** — upload ERP exports and store them unchanged with their parse metadata.
2. **Workbook triage** — work out what each sheet of a submission is for. Shape decides what is a
   table; an agent decides whether a table holds transactions, FX rates or a supplier list.
3. **Schema mapping** — an agent maps the transaction columns onto the canonical procurement schema
   with a confidence score and a comment; the user reviews and corrects both steps in the UI.
4. **Canonical table** — the confirmed mapping is applied and every dataset is stacked into one
   portfolio-wide working table.
5. **Data quality** — profiling measures completeness, consistency and embedded totals; the rule
   engine turns the findings into flags and decides which rows each analysis may use.
6. **Currency** — amounts are converted to EUR at the ECB daily rate of their posting date. Spend
   counts net, with gross and credit volume reported alongside.
7. **Companies** — one legal entity per name, however each export spells it. Deterministic, and
   deliberate about the company code: authoritative within an export, and a collision rather than a
   merge when two systems both number their entities from 1000.
8. **Suppliers and intercompany** — name variants are matched deterministically, the unclear pairs
   judged by an agent. The review shows one row per raw name with the group as an editable cell, so
   moving a name, inventing a group, splitting one and overruling the agent are all the same
   gesture and the supplier count adds up. Suppliers that are the group's own entities are detected
   from the company names in the data itself and separated out.
9. **Addressability** — cost types procurement cannot influence (payroll, tax, financing,
   provisions) are classified once and excluded from the negotiable figure.

Everything requiring judgement is gathered on one **Review & Confirm** screen; everything automatic
appears afterwards in one **Data Quality Report**, which ends in the chain from gross spend down to
analysable spend — addressable spend less the bookings that name no supplier, which is what the
levers are measured against.

10. **Levers** — a fixed catalogue of fifteen standard procurement levers tested against the data.
    Those the data supports are quantified with a saving range, every euro credited to exactly one
    of them and traceable back to its bookings. Those it does not support say why — either measured
    and empty, or missing a field, which produces the request list for the next data ask.

11. **Executive summary** — six tabs over the artifacts: what was found, the biggest levers, the
    full catalogue, the spend chain and supplier share drawn, the open data and business questions,
    and a chat grounded in this run's figures.

12. **Export** — beside the Executive Summary title, reachable from any tab: the same material
    as a workbook and as a single HTML file. The workbook carries
    native charts bound to their data and the canonical table twice, business fields and all of
    them; amounts are numbers, so it pivots. The HTML file looks and behaves like the summary tabs
    and embeds its chart runtime, so it opens with the network switched off. Both are built on
    request and kept with the run.

## Setup

Requires [uv](https://docs.astral.sh/uv/). Python 3.12 is installed and managed by uv.

```bash
uv sync
cp .env.example .env    # then add your OpenAI API key
```

`OPENAI_API_KEY` is required for the schema mapping agent. `OPENAI_MODEL` is optional and
defaults to `gpt-5-mini`. On Streamlit Cloud the same keys are read from `st.secrets` instead.

## Run

```bash
uv run streamlit run app.py    # http://localhost:8501
uv run pytest
```

## Run workspaces

Each execution of the pipeline gets its own directory under `runs/`. It is created when the first
upload is stored and holds everything that run produced:

```
runs/run_20260829_233045/
    run.json                # which step completed when, and what it wrote
    canonical_table.parquet # the working table, carried forward by every later step
    canonical_table.json    # its shape, and what each step changed about it
    logs/run.log            # every step, chronologically
    01_ingestion/
        01_helios.xlsx    # the original bytes, unmodified
        ingestion.json    # per-file manifest: hash, parse options, sheet names
    02_workbook_triage/
        workbook_triage.json            # sheet shapes and proposed roles
        workbook_triage_confirmed.json  # confirmed roles, and the datasets they define
    03_schema_mapping/
        schema_mapping.json            # what the agent proposed, and what it cost
        schema_mapping_confirmed.json  # what the user confirmed
    04_canonical_table/
        canonicalization.json          # what each dataset contributed
    05_profiling/
        profiling_report.json          # findings, aggregate candidates, reconciliation
        profiling_confirmed.json       # with the user's decisions
    06_rule_engine/
        rule_report.json               # what each rule flagged, spend before and after
    07_currency/
        currency_report.json           # net, gross and credit volume in EUR
        ecb_rates.csv                  # the rates this run used, frozen
    08_company_normalization/
        company_normalization.json             # one entity per spelling, code collisions
        company_normalization_confirmed.json
    09_supplier_normalization/
        supplier_normalization.json            # candidates, agent verdicts, clusters
        supplier_normalization_confirmed.json  # what the user approved
    10_spend_classification/
        spend_classification.json              # addressability per cost type
        spend_classification_confirmed.json
    11_levers/
        levers.json                            # bases, rates, potentials, narrative
    12_executive_summary/
        summary.json                           # the six tabs, and the questions for the business
    13_export/
        report.xlsx                            # one sheet per section and per figure, plus the data
        report.html                            # the same report as one self-contained file
```

Inserting a stage renumbers the ones after it, so runs written earlier keep their old numbers on
disk. They stay readable: an existing directory for a stage wins over the number that stage would
get today.

Pipeline stages and screens are deliberately not the same thing: the run keeps every stage as its
own numbered directory for auditability, while the UI collapses them into one decision screen and
one report.

`ecb_fx_reference_rates.csv` in the repository root holds the ECB daily reference rates.
Conversion reads it rather than the network, so it works offline and a run reproduces against the
same rates later; `fx.ecb.fetch_ecb_history()` refreshes it.

A file is not a dataset. A submission workbook holds a cover letter, instructions, the spend data
and small lookup tables; triage turns the sheets worth keeping into datasets, each with a role.
Only `transactions` is mapped and analysed, but `fx_rates` and `supplier_master` are kept, because
currency harmonization and supplier normalization will need them.

Later pipeline stages add their own numbered directory, so the processing order is readable off the
filesystem and a finished run carries its full audit trail: the source exports, every intermediate
artifact, and the log of how they were produced.

The base directory is `./runs` and can be redirected with the `PLI_RUNS_DIR` environment variable.
Runs are gitignored -- client data never enters the repository.

Values are read as text throughout ingestion and canonicalization. Type inference belongs to the
deterministic rule engine; applying it earlier would strip leading zeros from supplier IDs and
misread German decimal formats.

## The working table

From canonicalization onwards the pipeline works on one table in the canonical schema, at the run
root rather than inside a step directory — it belongs to no single step, it is the run's state.
Later steps add quality flags to it rather than writing tables of their own, so each step finds the
current version in one place.

Rows are never removed from it (SYSTEMCONCEPT section 12): eligibility for an analysis is expressed
through flag columns, which is what keeps the totals reconcilable against the source. A rewrite
therefore only ever adds columns, and `canonical_table.json` records what each step changed.

Alongside the canonical fields the table carries where each row came from — `dataset_id`,
`source_file`, `source_sheet`, `source_row` — so any figure can be traced back to a line in the
original export.

Source columns the mapping did not claim are kept under an `extra_` prefix rather than dropped. They
are often the ones that explain a discrepancy later, and going back to the source file to fetch them
would defeat the point of having a working table.

The rule engine adds typed values (`amount_local_value`, `posting_date_value`), quality flags
(`flag_*`) and eligibility columns (`include_*`) — never rows, and never removing any. On a real
submission that turned a naive spend of 666,754,199 into 227,419,026 by excluding nine embedded
subtotal rows, without deleting a single line.

Amount and date formats are decided per column, not per value. `83,122.08` alongside `12485.57`
means the comma groups thousands; `1.250,00` means it does not. Reading either value on its own
would be wrong by three orders of magnitude.

## Agents

LLM calls live in `agents/`. An agent is an instruction file plus the pydantic model it must
return; `agents/base.py` is the only place that talks to OpenAI, and structure is enforced by the
API rather than parsed out of prose.

```
agents/
    client.py                     # credentials and model, read from the environment
    base.py                       # AgentDefinition + run_agent()
    instructions/
        workbook_triage.md        # what each agent is told to do
        schema_mapping.md
    workbook_triage.py            # each agent's input assembly and output model
    schema_mapping.py
```

Adding an agent means adding an instruction file, an output model and a thin module. The canonical
schema in `core/canonical.py` is injected into the instructions at runtime, so the field list never
exists in two places.

The agent proposes; deterministic code decides. Every answer is checked before it counts: an
invented column or sheet name becomes an honest gap with a note, unknown fields and roles are
refused, and confidence is clamped. A hallucination cannot reach the pipeline.

The split runs the other way too. A sheet that is not a table is marked as documentation by
`ingestion/sheet_profile.py` without asking anything — a cover letter is recognisable by shape, and
the agent is only consulted about tables whose *meaning* is in question.

Only column names, inferred types and at most five truncated sample values per column are sent to
the model. What was sent is recorded in the run artifact.

## Dependencies

`requirements.txt` is generated, not edited:

```bash
uv export --no-hashes --no-dev --no-emit-project -o requirements.txt
```

Streamlit Community Cloud installs from it whenever the file is present, so a
dependency added to `pyproject.toml` without regenerating it is installed locally
and missing in production. `vl-convert-python` is the one that shows: without it
the HTML export has no charts at all, and says so in the file.

## Deployment

Deployed on Streamlit Community Cloud from `app.py`, with dependencies from `requirements.txt`
(regenerate with `uv export --no-hashes --no-dev --no-emit-project -o requirements.txt`).

Note that the Community Cloud filesystem is ephemeral: run workspaces do not survive a reboot or
redeploy. Set `PLI_RUNS_DIR` to a persistent volume for durable storage.
