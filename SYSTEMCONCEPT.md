# AI Procurement Lever Identification Platform
## Architecture & Development Concept

---

# 1. Problem Statement

Private Equity firms manage multiple portfolio companies that often operate independently with different ERP systems (SAP, Oracle, Dynamics, Infor, Sage, etc.).

As a result:

- ERP schemas differ significantly.
- Column names are inconsistent.
- Procurement categories are often missing or unreliable.
- Supplier names are inconsistent.
- Multiple currencies exist.
- Data quality varies across companies.

A further complication appears before any of this: a data request rarely comes back as a table. It comes back as a **workbook** containing a cover letter, filling instructions, the actual spend data, and small lookup tables for FX rates or suppliers. Which sheet holds what has to be established before a single column can be interpreted.

The objective of this platform is to automatically transform heterogeneous ERP exports into a standardized procurement data model that enables AI-powered identification of procurement value creation opportunities across the portfolio.

---

# 2. Design Principles

The architecture follows one simple principle:

> **Everything that can be solved deterministically should be solved deterministically. AI is only used where semantic understanding or business reasoning is required.**

## Deterministic Tasks

- File storage and parsing
- Sheet shape analysis
- Validation of every model answer
- Schema validation
- Data profiling
- Cleaning
- Normalization
- Currency harmonization
- Duplicate detection
- Aggregations
- Spend calculations
- Ranking
- Spend Cube generation

## AI Tasks

- Sheet role classification
- Schema understanding
- Supplier semantic classification
- Procurement category generation
- Procurement lever identification
- Executive summaries
- Recommendation generation

The LLM **never modifies financial values**.

## 2.1 The Agent Proposes, Code Decides

Wherever a model is used, its answer is treated as a proposal and validated deterministically before it counts. A model may return a column that does not exist, a field outside the schema, or a confidence above 1.0. None of that can enter the pipeline: unknown names are refused, missing entries become explicit gaps, and scores are clamped.

The split runs in both directions. A sheet that is not a table is classified as documentation by shape alone, without consulting the agent — a cover letter is recognisable without semantics.

## 2.2 Values Are Preserved, Not Interpreted

Everything is read and carried as **text** until the rule engine runs. Type inference during ingestion would strip leading zeros from supplier identifiers (`0000123456`) and misread continental decimal formats (`1.250,00`). Typing, normalization and currency conversion are the rule engine's job, where they are deterministic and auditable.

## 2.3 Data Minimization

Client data leaves the machine only where a model genuinely needs it: column names, inferred types and at most **five sample values per column, truncated to 60 characters**. What was sent is recorded in the run artifact, so it can be reviewed after the fact.

---

# 3. Overall Architecture

```text
ERP Export (workbook or flat file)
      │
      ▼
───────────────────────────────
1. Ingestion
(Deterministic)
───────────────────────────────
      │
      ▼
───────────────────────────────
2. Workbook Triage
shape deterministic, role LLM
───────────────────────────────
      │
      ▼
Datasets with roles
transactions / fx_rates / supplier_master
      │
      ▼
───────────────────────────────
3. Schema Mapping (LLM)
───────────────────────────────
      │
      ▼
Canonical Procurement Schema
      │
      ▼
───────────────────────────────
4. Canonical Working Table
(Deterministic)
───────────────────────────────
      │
      ▼
───────────────────────────────
5. Data Profiling
(Deterministic)
───────────────────────────────
      │
      ▼
Profiling Report
      │
      ▼
───────────────────────────────
6. Rule Engine
(Deterministic)
───────────────────────────────
      │
      ▼
Normalized Dataset
      │
      ▼
───────────────────────────────
7. Canonical Spend Cube
───────────────────────────────
      │
      ├───────────────┐
      │               │
      ▼               ▼
Deterministic      AI Reasoning
Analytics
      │
      ▼
Procurement Levers
      │
      ▼
Dashboard
```

## Implementation Status

| Stage | Section | Status |
|---|---|---|
| Ingestion | 5 | built |
| Workbook Triage | 6 | built |
| Schema Mapping | 7 | built |
| Canonical Working Table | 9 | built |
| Data Profiling | 10 | open |
| Rule Engine | 11 | open |
| Currency Harmonization | 13 | open |
| Canonical Spend Cube | 14 | open |
| Analytical Views and AI Reasoning | 15–19 | open |

---

# 4. Run Workspaces

Every execution of the pipeline owns a directory. It holds the source files, the artifact each stage produced, the working table, and the log of what happened — so a result and the evidence for it stay together.

```text
runs/run_<YYYYMMDD_HHMMSS>/
    run.json                    which stage completed when, and what it wrote
    canonical_table.parquet     the working table, carried forward by every later stage
    canonical_table.json        its shape, and what each stage changed about it
    logs/run.log                every stage, chronologically
    01_ingestion/
    02_workbook_triage/
    03_schema_mapping/
    04_canonical_table/
    ...
```

Rules:

- One run equals one analysis. Starting an analysis always creates a new run; runs are never reused.
- Each stage writes its report into its own numbered directory. The numbering makes the processing order readable off the filesystem.
- The working table sits at the run root because it belongs to no single stage — it is the run's state.
- Where a stage both proposes and is confirmed, **both artifacts are kept**. What the model suggested and what a person accepted must stay distinguishable.
- Run directories contain client data and are never committed to version control.

---

# 5. Ingestion

The first stage preserves rather than interprets.

- The uploaded file is stored **byte-identically**, together with a SHA-256 hash. Every figure downstream can be traced back to the exact bytes it came from.
- For CSV, encoding and delimiter are detected (`utf-8-sig`, `utf-8`, `cp1252`, `latin-1`; delimiters `,` `;` `\t` `|`) and **recorded**, so the file is later re-read identically instead of being sniffed again.
- Accepted formats are CSV and XLSX.

**A file is not a dataset.** A workbook can carry several sheets, and which of them hold data is decided by the next stage. Ingestion records the sheet names and stops there.

---

# 6. Workbook Triage

## Goal

Establish what each sheet of a submission is for, before anything is interpreted.

Observed on a real submission — a five-sheet workbook where only one sheet held transactions:

| Sheet | Rows | Role |
|---|---:|---|
| 1. Brief | 18 | documentation |
| 2. How to Submit | 26 | documentation |
| 3. Spend Data | 18,660 | transactions |
| 4. Supplier Master | 14 | supplier_master |
| 5. FX | 3 | fx_rates |

Defaulting to the first sheet would have analysed the cover letter.

## 6.1 Shape Is Deterministic

Whether a sheet is a table at all is a question of shape, measured without a model:

- **Header row** — complete, unique and textual across the full width
- **Fill ratio** — share of populated cells
- **Rectangularity** — share of rows filling the header width
- **Date and numeric columns** — the fingerprint of a transaction table

Measured on the submission above:

| Sheet | Header | Fill | Rect | Table |
|---|---|---:|---:|---|
| 1. Brief | no | 0.89 | 0.85 | no |
| 2. How to Submit | no | 0.63 | 0.28 | no |
| 3. Spend Data | yes | 0.91 | 1.00 | yes |
| 4. Supplier Master | yes | 0.89 | 1.00 | yes |
| 5. FX | yes | 1.00 | 1.00 | yes |

All signals are required together. Rectangularity alone would have kept the cover letter, which reaches 0.85 simply by being narrow. The header check carries the decision, and it is a real signal: a prose sheet opens with a title filling one of two columns, whereas a data table's header fills every column it spans.

## 6.2 Role Is Semantic

Only the *meaning* of a table needs a model. Sheets that are not tables are marked `documentation` by shape and never reach the agent.

| Role | Recognised by |
|---|---|
| `transactions` | Many rows, one booking per row, with date, amount and supplier |
| `fx_rates` | Small lookup pairing currency codes with rates |
| `supplier_master` | Supplier list with attributes, no per-transaction amounts |
| `documentation` | Brief, instructions, glossary, change log |
| `unknown` | A table that cannot be placed with reasonable certainty |

A file containing exactly one table skips the model call entirely and is taken to hold the transactions.

## 6.3 Nothing Useful Is Discarded

Only `transactions` is mapped and analysed. But `fx_rates` and `supplier_master` are **kept and labelled**, because currency harmonization (section 13) and supplier normalization (section 11) need them. Discarding them here would only mean asking for them again.

## 6.4 Review

The user sees every sheet with its detected role, row and column counts, confidence and comment, and can correct any role before the analysis proceeds.

---

# 7. Schema Mapping (LLM)

## Goal

Every ERP export uses different column names.

Examples

| ERP | Supplier Column |
|------|-----------------|
| SAP | Vendor |
| Oracle | Supplier Name |
| Dynamics | Business Partner |

Instead of hardcoding mappings, the LLM translates every transaction dataset into the canonical procurement schema.

---

## Input

- Column names
- Inferred data types
- Null ratio and distinct count
- Up to five sample values per column, truncated

Example

| Column | Sample |
|----------|---------|
| Vendor | ABC Ltd |
| Amount LC | 1250 |
| Currency | EUR |
| Account Description | Consulting |

---

## Output

One entry per canonical field:

```json
{
  "canonical_field": "supplier",
  "source_column": "Vendor",
  "confidence": 0.95,
  "comment": "Header and sample values are company names."
}
```

Every mapping carries a **confidence score** and a **comment** naming the evidence used. `null` is a valid answer: a forced match is worse than an honest gap, because a reviewer checks low confidence but rarely re-checks a confident wrong answer.

## 7.1 The Deterministic Gate

The agent's answer is reconciled before it becomes an artifact:

- Canonical fields outside the schema are dropped.
- Fields the agent did not answer become explicit gaps.
- A source column the file does not have is refused, and the comment records what was proposed.
- A source column claimed twice is flagged on the second claim.
- Confidence is clamped to 0–1.

A hallucinated column therefore becomes an honest gap, never a silent wrong mapping.

## 7.2 Review and Confirmation

Every field is editable **regardless of confidence** — a confident mistake is exactly the case a threshold would hide. Fields below a configurable threshold (default 0.7) are marked for review; required fields with no mapping are marked as missing.

The proposal and the confirmation are stored as separate artifacts, and each field records whether it was decided by `ai` or by `user`.

---

# 8. Canonical Procurement Schema

After schema mapping, every dataset follows exactly the same structure.

| Key | Field | Required | Meaning |
|---|---|---|---|
| `company` | Company | yes | Identifier of the portfolio company, typically a company code |
| `company_name` | Company Name | no | Readable company name, where the export carries one in addition to a code |
| `supplier` | Supplier | yes | Vendor name |
| `supplier_id` | Supplier ID | no | Stable supplier identifier in the source system |
| `amount_local` | Local Amount | yes | Amount in the document's own currency |
| `amount_group` | Group Amount | no | The same amount already converted to group currency |
| `currency` | Currency | yes | Currency of the local amount |
| `posting_date` | Posting Date | yes | Ledger date |
| `document_date` | Document Date | no | Date on the invoice or source document |
| `invoice_number` | Invoice Number | no | Accounting document number |
| `purchase_order` | Purchase Order | no | Purchasing document reference |
| `gl_account` | GL Account | no | General ledger account number |
| `gl_description` | GL Description | no | Text of the GL account, in accounting language |
| `cost_center` | Cost Center | no | Cost center |
| `profit_center` | Profit Center | no | Profit center |
| `category` | Procurement Category | no | What was actually bought |

Two distinctions the schema deliberately makes, because exports routinely carry both and collapsing them loses information:

- **`company` versus `company_name`** — the code (`1101`) and the readable name (`Helios Power Polska Sp. z o.o.`) are separate fields.
- **`category` versus `gl_description`** — a GL description is accounting language for an account (`Freight costs`); a procurement category classifies the purchase (`Logistics`). Mapping one column to both would defeat the semantic check in section 10.3.

From this point onwards, the remaining pipeline is ERP-independent.

---

# 9. Canonical Working Table

The confirmed mapping is applied once and materialised. From here the pipeline reads canonical column names and nothing has to re-derive them.

## Scope

All transaction datasets are stacked into **one portfolio-wide table**. After mapping they share the same columns, and cross-portfolio analysis is the point of the exercise.

## Content

| Group | Columns |
|---|---|
| Provenance | `dataset_id`, `source_file`, `source_sheet`, `source_row` |
| Canonical | the 16 fields of section 8 |
| Retained | every unmapped source column, under an `extra_` prefix |

`source_row` is the row number in the source sheet, so any figure can be traced back to a line a reviewer can open.

## Rules

**Renamed, never converted.** `1.250,00` stays that string. Conversion belongs to the rule engine.

**The schema is complete.** All canonical columns exist even where nothing was mapped, so nothing downstream has to test whether a column is present.

**Nothing is dropped.** Source columns the mapping did not claim are carried along under an `extra_` prefix. They are frequently the ones that explain a discrepancy later, and fetching them from the source file afterwards would defeat the purpose of a working table.

## Carrying It Forward

The table is written **in place**. Later stages add their quality flags to it rather than writing tables of their own, so every stage finds the current state in one place.

This is safe because rows are never removed (section 12): a rewrite only ever adds columns. `canonical_table.json` records, per stage, the row count and which columns it added — which is what in-place writing would otherwise cost in traceability.

Format is Parquet: typed, compact, and free of the quoting and encoding pitfalls that ingestion works to avoid.

---

# 10. Data Profiling

The objective of data profiling is **not cleaning**.

It only evaluates data quality and creates a structured profiling report.

No AI is used during profiling.

---

## 10.1 Completeness Checks

Questions

- Supplier available?
- Amount available?
- Currency available?
- Company available?
- Posting Date available?
- Purchase Order available?

---

## 10.2 Consistency Checks

- Duplicate invoices
- Duplicate document numbers
- Duplicate transactions
- Negative amounts
- Future dates
- Posting Date before Document Date
- Currency consistency

---

## 10.3 Semantic Checks

### Procurement Category available?

If not

→ Category analysis disabled.

---

### Does the category say anything the GL description does not?

Comparing the two as strings is not enough. A category column is frequently the
accounting text under tidier names, and the renaming hides the duplication:

```
ESS - SUBCONTRACTS    ->  Subcontracts
PERSONNEL COSTS       ->  Payroll
SPECIALIZED SERVICES  ->  Other Specialized Services
```

None of these pairs match as strings, yet the category adds nothing. The check is
therefore a **dependency measure**, not a comparison: for each GL description,
what share of its rows carry that description's single most common category. If
knowing the GL description predicts the category, the column renames the
accounting classification.

Above the configured share, category analysis is disabled.

On a real submission this measured **100%** — 23 categories against 23 GL
descriptions, an exact one-to-one relabeling. A string comparison saw 16.6% and
would have left category analysis switched on.

The measure is language-independent and needs no model, which is why profiling
stays deterministic. Deriving usable categories where the export has none is a
separate matter and belongs to AI enrichment in section 18.

---

### Supplier names in the category column

Categories that are in fact one of the dataset's supplier names. On the same
submission, 78 supplier names appeared as categories across 441 rows, inflating
the apparent category count from 23 to 101 and masking the duplication above.

Flagged and excluded from category analysis; the value itself is kept.

---

### Supplier Quality

Examples

```
ABC LTD
ABC Ltd
ABC Limited
```

Potential normalization candidate.

---

### GL Description Quality

Examples

```
Miscellaneous

Other Expenses

General Costs
```

Low procurement value.

---

## 10.4 Embedded Aggregate Rows

ERP exports frequently contain their own subtotals. Observed on a real submission: eight `*** SUBTOTAL ***` rows, one per company, plus one `*** GRAND TOTAL ***`, sitting among 18,650 detail rows.

```text
detail rows      18,650      227,419,026.85
subtotal rows         8      219,667,586.17
grand total row       1      219,667,586.17
                          ─────────────────
naive column sum            666,754,199.19   →  2.93× overstated
```

Summing the amount column without detecting them nearly triples the spend. Aggregate rows are recognisable by a near-empty row with a populated amount, a marker in a text column, or a missing company identifier.

## 10.5 Reconciliation

Where an export carries its own subtotals, they are a free control total. Detail summed per company is compared against the subtotal the export states for that company.

On the submission above every company's detail exceeded its stated subtotal, by 7,751,440.68 in total (3.4%) — a systematic difference, not rounding. A discrepancy of this kind is a finding for the report, not something to silently correct.

## 10.6 Analytics Readiness

Checks

- Supplier overlap
- Spend concentration
- Currency complexity
- PO coverage
- Category usability
- Supplier identifier availability — where `supplier_id` is absent entirely, supplier normalization has no stable key and must work on names alone

---

## Output

Example

| Check | Result | Severity |
|---------|----------|----------|
| Missing Supplier | 0.5% | High |
| Duplicate Documents | 21 | Medium |
| Category predicted by GL | 100% | High |
| Supplier names as categories | 78 values | Medium |
| Supplier Variants | High | Medium |
| Aggregate rows detected | 9 | High |
| Detail vs subtotal gap | 3.4% | High |

---

# 11. Rule Engine

Every profiling result triggers deterministic rules.

No AI decisions.

---

## Missing Supplier

Action

- Flag row
- Exclude from supplier-based analyses

Reason

Supplier levers cannot be calculated.

Financial reporting remains unaffected.

---

## Missing Amount

Action

- Flag
- Exclude from spend analyses

---

## Missing Currency

If Group Currency exists

→ use Group Currency

Otherwise

- Flag
- Exclude

---

## Missing Company

Action

Flag.

Exclude from cross-company analyses.

---

## Aggregate Rows

Action

- Flag
- Exclude from spend analyses

Reason

They restate figures already present in the detail. Removing them instead would break reconciliation against the source export.

---

## Supplier Formatting

Normalize

- Trim whitespace
- Remove duplicate spaces
- Normalize capitalization
- Remove legal suffixes

Examples

```
Ltd
Limited
Inc
Corp
GmbH
BV
SA
```

Original values remain stored.

---

## Supplier Duplicate Detection

Similarity >95%

→ merge automatically

Similarity 85–95%

→ review queue

Similarity <85%

→ no merge

---

## Duplicate Transactions

Flag first.

Only remove if deterministic rules confirm an exact duplicate.

---

## Negative Amounts

Flag only.

Possible reasons

- Credit Memo
- Reversal
- Refund

Remain inside dataset.

---

## Date Inconsistencies

Flag.

Never automatically modify dates.

---

## Category equals GL Description

Disable category analysis.

---

## Missing Purchase Order

Flag.

PO analyses only run on available records.

---

# 12. Data Preservation Principle

Rows should almost never be deleted.

Instead, every analysis decides whether a row is eligible.

Example

| Supplier | Include Supplier Analysis | Include Spend Analysis |
|-----------|--------------------------|------------------------|
| ABC | ✅ | ✅ |
| NULL | ❌ | ✅ |
| XYZ | ✅ | ❌ |

This guarantees

- auditability
- reproducibility
- financial reconciliation

In practice this means the working table's row count never changes after section 9. Stages add flag columns; they do not remove rows. Even an obviously unusable row — an embedded subtotal, a blank line — is flagged rather than dropped, so the table can always be reconciled against the source export.

---

# 13. Currency Harmonization

Preferred order

1. Existing Group Currency
2. FX Conversion
3. Flag if impossible

Currency conversion is always deterministic.

An FX table shipped with the submission is identified during triage (section 6) and is the preferred rate source, because it is the rate the company itself used.

---

# 14. Canonical Spend Cube

The **Canonical Spend Cube** is the central semantic data model of the platform.

Everything before the Spend Cube prepares the data.

Everything afterwards consumes the Spend Cube.

It represents the single source of truth.

```text
ERP Export
      │
      ▼
Ingestion
      │
      ▼
Workbook Triage
      │
      ▼
Schema Mapping
      │
      ▼
Canonical Working Table
      │
      ▼
Data Profiling
      │
      ▼
Rule Engine
      │
      ▼
Supplier Normalization
      │
      ▼
Currency Harmonization
      │
      ▼
Quality Flags
      │
      ▼
═══════════════════════════
 CANONICAL SPEND CUBE
═══════════════════════════
      │
      ├───────────────┐
      │               │
      ▼               ▼
Analytics      AI Reasoning
```

---

## Spend Cube Structure

| Field | Description |
|----------|------------|
| Company | Portfolio Company |
| Company Name | Readable company name |
| Supplier | Normalized Supplier |
| Supplier ID | Canonical Supplier ID |
| AI Category | Procurement Category |
| Amount EUR | Harmonized Spend |
| Local Amount | Original Spend |
| Currency | Original Currency |
| Posting Date | Transaction Date |
| GL Account | Finance Account |
| Cost Center | Cost Center |
| Profit Center | Profit Center |
| Include Supplier Analysis | Boolean |
| Include Spend Analysis | Boolean |
| Data Quality Flags | Quality Findings |
| Provenance | Source file, sheet and row |

The Spend Cube is **not a reporting artifact**.

It is the semantic foundation for all analytics.

---

# 15. Analytical Views

Instead of building multiple physical cubes, the platform exposes multiple analytical views on top of one single Spend Cube.

Each view targets one procurement lever.

---

## View 1 — Supplier Consolidation

Dimensions

- Supplier
- Company

Measures

- Total Spend
- Companies Served
- Invoice Count

Identifies

- Global Framework Agreements
- Volume Bundling
- Preferred Supplier Opportunities

---

## View 2 — Category Consolidation

Requires

Reliable Procurement Categories

Dimensions

- Category
- Company

Measures

- Spend
- Supplier Count

Identifies

- Category Bundling
- Standardization
- Cross-Portfolio Procurement

---

## View 3 — Tail Spend

Dimensions

- Supplier

Measures

- Spend
- Spend Share
- Invoice Count

Identifies

- Supplier Rationalization
- Process Automation
- Transaction Cost Reduction

---

## View 4 — Contract & Negotiation

Dimensions

- Supplier

Measures

- Spend
- Portfolio Coverage
- Average Invoice
- Spend Growth

Identifies

- Contract Renegotiation
- Payment Term Optimization
- Volume Discounts

---

## View 5 — Portfolio Benchmark

Dimensions

- Company

Measures

- Spend
- Supplier Count
- Tail Spend Ratio
- Top Supplier Share
- Category Mix

Identifies

- Procurement Maturity
- Best Practice Transfer
- Portfolio Synergies

---

# 16. Classical Procurement Levers

The platform focuses on the most common value creation levers in Private Equity.

---

## Supplier Consolidation

Several portfolio companies purchase independently from the same supplier.

Potential

- Better negotiation power
- Framework agreements
- Volume discounts

---

## Category Consolidation

Several suppliers provide similar goods or services.

Potential

- Standardization
- Cross-company sourcing
- Reduced supplier complexity

---

## Tail Spend Reduction

Large number of low-value suppliers.

Potential

- Supplier reduction
- Automation
- Lower transaction costs

---

## Contract Optimization

Existing strategic suppliers with fragmented contracts.

Potential

- Better commercial conditions
- Payment term optimization
- Harmonized contracts

---

## Portfolio Benchmarking

Compare procurement maturity across portfolio companies.

Potential

- Best practice transfer
- Synergy realization
- Faster value creation

---

# 17. Deterministic Analytics

Examples

- Supplier Spend
- Cross Portfolio Spend
- Spend by Company
- Supplier Concentration
- Pareto Analysis
- Tail Spend
- Invoice Count
- Average Invoice Value
- Supplier Coverage
- Spend Growth

No AI required.

---

# 18. AI Enrichment

Only executed after deterministic processing.

---

## Supplier Classification

Example

Microsoft

↓

Software

Atlas Freight

↓

Logistics

---

## Spend Classification

Example

SAP S/4 Migration

↓

IT Consulting

---

## Procurement Category Generation

If procurement categories do not exist.

Always returned with confidence scores.

---

# 19. AI Procurement Reasoning

The LLM never performs calculations.

Instead it receives aggregated KPIs from the Spend Cube.

Example

```json
{
    "supplier":"ABC Logistics",
    "total_spend":5400000,
    "companies":8,
    "supplier_share":0.18,
    "invoice_count":421,
    "category":"Logistics"
}
```

The LLM generates

- Procurement opportunities
- Lever prioritization
- Executive summaries
- Recommended next actions

The deterministic pipeline answers

> **What happened?**

The LLM answers

> **What should we do next?**

---

# 20. Agent Architecture

Every model call in the platform goes through one place. An agent is defined by two things:

1. An **instruction file** in Markdown, holding the stable guidance — role, rules, how to score confidence, what a comment should say.
2. An **output model** whose structure is enforced by the API, so a malformed answer fails loudly instead of being parsed out of prose.

Adding an agent means adding an instruction file, an output model and a thin module. The calling layer does not change.

Two properties follow from this shape:

**No second copy of the schema.** The canonical field list is injected into the instructions at runtime from the schema definition, so instructions and code cannot drift apart.

**Every call is auditable.** Model, input and output tokens and duration are recorded in the stage artifact alongside the answer itself, together with the exact input the model was shown.

---

# 21. Technology and Deployment

| Concern | Choice |
|---|---|
| Language | Python 3.12, managed with uv |
| UI | Streamlit |
| Data | pandas, Parquet via pyarrow |
| Validation | pydantic |
| Model | OpenAI, default `gpt-5-mini`, overridable per environment |

Credentials come from `.env` locally and from `st.secrets` on Streamlit Community Cloud; the layers below the UI read only the environment and stay free of both.

The run directory defaults to `./runs` and is redirectable, because the Community Cloud filesystem is ephemeral and does not survive a redeploy.

## User Flow

```text
Start                 upload exports
      │
      ▼
Workbook Review       confirm what each sheet is
      │
      ▼
Schema Mapping        confirm the column mapping
      │
      ▼
Canonical Table       the working table, ready for profiling
```

Each screen shows what was decided, why, and lets the user override it. No stage proceeds on a model's judgement alone.

---

# 22. Why This Architecture?

The solution is

- ERP agnostic
- Deterministic
- Explainable
- Auditable
- Reproducible
- AI-enabled
- Scalable across portfolio companies

By separating deterministic engineering from AI reasoning, the platform combines trustworthy financial analytics with modern AI capabilities while maintaining full transparency and auditability.

Every AI decision is proposed, validated, recorded and reviewable. Every figure traces back to a row in a source file that is still on disk, byte for byte as it arrived.
