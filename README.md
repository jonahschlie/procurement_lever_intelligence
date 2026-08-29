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

## Storage

Uploads are written to `data/uploads/<upload_id>/`, one directory per dataset:

| File | Content |
|---|---|
| `source.csv` / `source.xlsx` | the original bytes, unmodified |
| `manifest.json` | file metadata plus the exact options used to parse it |

The base directory is `./data` and can be redirected with the `PLI_DATA_DIR` environment
variable. Uploaded exports are gitignored — client data never enters the repository.

Values are read as text throughout ingestion. Type inference belongs to the deterministic rule
engine; applying it earlier would strip leading zeros from supplier IDs and misread German
decimal formats.

## Deployment

Deployed on Streamlit Community Cloud from `app.py`, with dependencies from `requirements.txt`
(regenerate with `uv export --no-hashes --no-dev --no-emit-project -o requirements.txt`).

Note that the Community Cloud filesystem is ephemeral: stored uploads do not survive a reboot
or redeploy. Set `PLI_DATA_DIR` to a persistent volume for durable storage.
