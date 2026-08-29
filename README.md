# Procurement Lever Intelligence

Transforms heterogeneous ERP exports from private equity portfolio companies into a
standardized procurement data model, and identifies procurement value creation levers on top
of it. See [SYSTEMCONCEPT.md](SYSTEMCONCEPT.md) for the architecture and functional concept.

## Status

Step 1 of the pipeline is implemented: the Streamlit app shell and the ingestion stage that
uploads ERP exports and stores them with their parse metadata. Schema mapping, profiling, the
rule engine and the spend cube follow.

## Setup

Requires [uv](https://docs.astral.sh/uv/). Python 3.12 is installed and managed by uv.

```bash
uv sync
```

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
    run.json              # which step completed when, and what it wrote
    logs/run.log          # every step, chronologically
    01_ingestion/
        01_sap_export.csv # the original bytes, unmodified
        02_oracle_export.csv
        ingestion.json    # per-file manifest: hash, parse options, shape
```

Later pipeline stages add their own numbered directory, so the processing order is readable off the
filesystem and a finished run carries its full audit trail: the source exports, every intermediate
artifact, and the log of how they were produced.

The base directory is `./runs` and can be redirected with the `PLI_RUNS_DIR` environment variable.
Runs are gitignored -- client data never enters the repository.

Values are read as text throughout ingestion. Type inference belongs to the deterministic rule
engine; applying it earlier would strip leading zeros from supplier IDs and misread German decimal
formats.

## Deployment

Deployed on Streamlit Community Cloud from `app.py`, with dependencies from `requirements.txt`
(regenerate with `uv export --no-hashes --no-dev --no-emit-project -o requirements.txt`).

Note that the Community Cloud filesystem is ephemeral: run workspaces do not survive a reboot or
redeploy. Set `PLI_RUNS_DIR` to a persistent volume for durable storage.
