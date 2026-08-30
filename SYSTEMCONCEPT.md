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

## 2.3 What the User Decides

Not every finding is a question. The line is not "simple versus complex" but
**"is there anything to decide?"**

A missing invoice number has exactly one correct treatment, so nobody is asked:
it is flagged, the affected analyses skip the row, and it appears in the report
afterwards. Whether a supplier belongs to the group, or whether a cost type can be
negotiated, cannot be measured — those go to a person.

Everything requiring judgement is gathered on **one screen**, preselected with the
best available proposal, and confirmed in a single action. Everything automatic
appears afterwards in **one report**. Mixing the two is what makes a pipeline feel
opaque: a user asked to confirm things that were never in doubt stops reading the
ones that were.

## 2.4 Data Minimization

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
| Data Profiling | 10 | built |
| Rule Engine | 11 | built |
| Currency Harmonization | 13 | built |
| Company Normalization | 11 | built |
| Supplier Normalization | 11 | built |
| Intercompany and Addressability | 11 | built |
| Canonical Spend Cube | 14 | open |
| Lever Quantification and Reasoning | 16.1 | built |
| Lever Catalogue and Data Requests | 16.1 | built |
| Executive Summary | 15, 17–19 | built |
| Export (Excel and HTML) | 15 | built |

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

## Field tiers

The schema is the gate: the mapping agent looks only for fields defined here, so a
field absent from it never becomes canonical. It survives as an `extra_` column,
but under whatever name the submission used, untyped, and unusable by any generic
rule. Fields that later levers need must therefore be declared **before** the first
export is read.

| Tier | Meaning | Treatment when missing |
| --- | --- | --- |
| **core** | Nothing works without it | Serious quality finding |
| **standard** | Usually present, carries the main analyses | Ordinary quality finding |
| **extended** | Unlocks a specific lever when present | **No finding.** Reported at the lever it blocks |

The tiers exist for a measured reason. Adding eight extended fields would have
turned 14 completeness findings into 21, the additional ones almost all noise: a
submission without quantities is not a submission with a data quality problem. Their
absence belongs where it can be acted on -- next to the lever it prevents, and in
the request list for the next data ask.

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

The extended tier holds `item_code`, `quantity`, `unit_price`, `unit_of_measure`,
`payment_terms`, `contract_id`, `contract_end_date` and `delivery_location`. None
appear in every export; each is what one or more levers is measured from.

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

Summing the amount column without detecting them nearly triples the spend.

Three independent signals nominate a row, and any one of them is enough:

1. **A total marker in a text column** — `TOTAL`, `SUMME`, `ZWISCHENSUMME` and the like, matched on word boundaries so `TotalEnergies SE` stays a booking. Language-dependent, and therefore the weakest of the three.
2. **An amount with no identifiers** — no posting date, no document number, no GL account. Structural and language-independent: measured against exports in five languages it caught `Totaal`, `Totale`, `Razem` and `Suma` without any of those words being on the marker list.
3. **An amount equal to the sum of its block** — pure arithmetic, within a 0.5% tolerance, over the rows of the same company or the same company and GL account. This is what catches the subtotal that *kept* its posting date, document number and account, which the first two signals miss. A block needs at least four other rows and the candidate must be the largest amount in it, so a handful of similar bookings cannot nominate each other.

Only signal 2 preticks the exclusion. A marker on an otherwise complete booking, or a sum that happens to match, is shown for a decision rather than acted on — a false negative silently overstates spend, a false positive costs a glance.

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

## Company Normalization

A company that appears under several spellings is counted as several companies. One
workbook spells its own entities consistently, so with a single submission this
stage changes nothing — it exists for the intended case, one export per portfolio
company, where `Helios Power Polska Sp. z o.o.` and `HELIOS POWER POLSKA` arrive
from different systems.

The consequences of getting it wrong are quiet rather than loud: the portfolio
benchmark gains a company, the contract coverage per company splits, and supplier
consolidation miscounts how many companies a supplier serves.

Deterministic throughout, with no agent. There are a handful of companies against
eighty supplier names, the user sees every group, and the company code carries a
signal supplier names do not — which cuts both ways:

| Situation | Treatment |
|---|---|
| Same code, same export | One company, whatever the spelling. Within one export a code is authoritative. |
| Same code, matching name across exports | One company. |
| **Same code, unrelated names across exports** | **Kept apart and surfaced.** Two ERPs both numbering their entities from 1000 are not one company. |
| Matching name across exports | One company. |

The result is written to `company_normalized` and `company_canonical_id` beside the
untouched `company` and `company_name`. Everything downstream asks for the company
through a helper that falls back to the raw name where the stage has not run, so a
run written before it existed groups exactly as it always did.

The canonical id is also what duplicate detection keys on, so two exports whose
codes collide cannot make unrelated bookings look like the same payment twice.

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

The proposed grouping is a starting point, not a verdict, and the review shows it
as **one table over every raw name** rather than as a set of group-level decisions.

The reason is arithmetic. Split across a "needs a decision" table, an
"automatically merged" list and a read-only record of rejected pairs, the supplier
count in the heading could not be reconciled with anything on screen: on a real
submission 20 groups covered 69 of 80 names and the remaining 11 appeared nowhere.
One row per name, with the group as an editable cell, makes every correction the
same gesture — move a name, invent a group, split one, or overrule a rejection by
giving both names the same group — and makes the count add up.

Each row carries what decided it (cleanup match, agent, agent unsure, alone) and
why, including the neighbours the agent judged to be a different supplier. A group
the agent was not confident about starts ungrouped, so nothing merges on a guess.

A group built by hand is approved by definition. It inherits two things rather than
reinventing them: the intercompany mark of whichever original group contributes the
most rows, and the supplier master entry — but only where the group still holds the
name that matched the master. Split a group and it is no longer known which half the
master entry described, so country and contract status go back to unknown rather
than following the wrong half.

**The agent's judgement is not stable across runs, and that is visible.** Measured
over seven runs of the same submission: the deterministic half never moved (44
automatic merges, 52 pairs for the agent, every time), while five of those 52 pairs
got a different verdict depending on the run, swinging the supplier count between 25
and 27. They sit at similarity 0.76-0.87, where the question -- is "Brokers" a
different business or the same name written out? -- has no answer in the data.
Nothing is remembered between runs: each run is judged on its own.

---

## Intercompany Spend

A portfolio company billing a sister company is not procurement spend. Nothing
about it is negotiable, and counting it inflates every supplier figure. On a real
submission it was **9.6% of net spend across 1,648 rows** — five of the group's
eight entities appeared as suppliers.

Group membership is stated nowhere, so it is derived from two independent signals,
neither of which hardcodes a name:

**The data names its own companies.** Every row carries the entity that booked it.
A supplier name close to one of those entities is the group buying from itself.

**Entities share a stem.** Tokens appearing in most of the group's company names
are the group's own name. Suppliers carrying that stem are candidates.

On the submission in hand both signals independently returned the same five
suppliers, the first at similarity 1.00, and the stem was derived rather than
configured. Rows flagged as aggregates are excluded from the derivation first — a
grand total row carries the group name in its company column and would otherwise
nominate itself as an entity.

What neither signal sees is a group entity appearing *only* as a supplier and in
no company column — a parent outside the analysed scope. The review screen
therefore lets a supplier be marked intercompany by hand.

Effect: flagged, excluded from supplier analyses, and subtracted in the spend
chain. The spend itself is real and stays in the table.

## Addressable Spend

Payroll, taxes, interest and provisions sit in the same ledger as consulting and
freight, but no sourcing exercise changes them. Reporting them as spend overstates
every lever derived later.

The distinction is drawn over the **distinct cost types**, not the rows: a chart of
accounts holds tens of labels, so one model call classifies all of them. The agent
reads meaning rather than keywords, which is what makes it work in any language and
any chart of accounts — `PERSONNEL COSTS`, `Personalaufwand` and `Coûts de personnel`
are the same thing to it, and a keyword list is precisely the mistake section 10.3
already taught.

Not addressable: payroll, taxes and duties, financing costs, accounting entries
(provisions, accruals, depreciation), intercompany recharges, statutory fees.
Everything bought from a third party under negotiable terms is addressable, utilities
included.

A cost type the agent does not judge stays **addressable**: spend excluded by
nobody's decision would disappear from the analysis unnoticed, whereas spend wrongly
included merely gets examined and dismissed.

**Cost types the agent was unsure about are named above the table, not left in it.**
Observed across two runs of the same submission: `MANAGEMENT FEES` was called
addressable at confidence 0.40 in one and not addressable at 0.85 in the next,
moving addressable spend by 3.06m EUR -- 2.7%. The review table sorts by spend
rather than by confidence, so the one case worth a second look sat in the middle of
twenty-four rows. It is now stated up front with its spend, its verdict and its
confidence, using the same threshold the schema mapping uses for the same purpose.

The hedged answer was the better one, incidentally: intercompany detection found
only 202k of those fees to be internal, and the remaining 3.06m goes to the same
third parties that appear in every other cost type.

Measured: 12.4m EUR of 127.6m third party spend, leaving 115.2m addressable.

## Analysable Spend

Addressable is not the same as actionable. Every lever needs a counterparty: a
booking with no supplier cannot be consolidated, matched to a contract or placed in
a tail, however negotiable its cost type is. So the spend chain runs one step
further than the addressability decision.

```text
Addressable spend            115,217,899
  no supplier name            −4,155,712     5,885 rows
Analysable spend             111,062,187     what every lever is measured against
```

Measured on a real submission: 5,885 of 15,195 addressable rows carry no supplier
at all — 35% of the rows, 3.6% of the money, spread over ordinary accounts
(`ESS - SUBCONTRACTS`, `SPECIALIZED SERVICES`, `CONSULTING`). The supplier column
was simply empty there; nothing was lost in normalization.

This step exists because both figures were previously called *addressable spend* on
two different screens, and the 4.2m difference appeared on neither. Stating it makes
the report end where the levers begin, and turns the gap into what it is: spend that
is one field away from being analysable.

## Supplier Duplicate Detection

The pool is built from **bookings only**. A subtotal row carries its own marker in
the supplier column, so left in it becomes a name to be grouped — carrying no spend,
but inflating the supplier count and putting a row in front of the reviewer that
means nothing. The same filter already guards company normalization and intercompany
detection; all three now share it.

Frequently there is no supplier identifier at all — on a real submission the
column was entirely absent — so matching works on names alone. Three stages,
each doing only what it is suited to:

**Deterministic.** Names are reduced to a form blind to case, punctuation, legal
form and connectives. Identical normal forms merge outright. A similarity score
over the remaining pairs decides which are worth attention.

| Similarity | Treatment |
| --- | --- |
| ≥ 0.95 | merged without asking |
| 0.70 – 0.95 | judged by the matching agent |
| < 0.70 | not a candidate |

**AI for the grey zone.** Whether `Atlas Frght & Log.` and
`Atlas Freight & Logistics` are one company is a semantic question; finding the
pair is not. The agent sees the two names plus country and purchase context, never
amounts. Its instruction weights the errors asymmetrically: a wrong merge silently
misstates every supplier figure, a missed one costs a reviewer a glance, so
uncertainty answers "different".

This is where the judgement pays for itself. On the real submission the agent
merged abbreviations correctly and **kept apart** `Helios Renewables España SL`
and `Helios Renewables Iberia, S.A.` — two national subsidiaries a similarity
score alone would have collapsed into one supplier.

**Review.** Every group is shown with its members, the evidence and who decided;
deterministic and confident AI merges arrive preticked, uncertain ones do not.
Rejected pairs stay visible. Nothing becomes canonical without confirmation.

The result lands in new columns — canonical name, canonical id, country and
contract status from the supplier master where one matched. The original name is
never overwritten.

## Contract Status

Where a submission ships a supplier master, its contract flag is carried into the
table, because it identifies a lever without any further processing: spend
concentrated on a supplier that has no contract on file is what section 12 calls
contract optimization.

The status is three-valued on purpose. A supplier listed in the master with a
blank flag has **no contract**; a supplier absent from the master is **unknown**.
Collapsing the two would invent a finding.

Measured on a real submission: 43.2% of net spend (58.7m EUR) sits with suppliers
that have no contract on file, 31.4% with suppliers that do, and 25.4% with
suppliers the master does not list. Two suppliers alone account for 41.9m EUR of
the uncovered spend.

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

Spend across a portfolio is only comparable in one currency. Conversion is always
deterministic.

## Rate source

**ECB daily reference rates at the posting date of each transaction.** The rate
history ships with the repository rather than being fetched at run time, so
conversion needs no network, works on a locked-down deployment, and a run made
today reproduces against the same rates in a year. The rates a run actually used
are additionally frozen into the run directory.

Rates exist only for trading days; weekends and holidays take the last published
rate, which is the ECB's own convention. The ECB quotes units of currency per one
euro, so conversion is `amount_eur = amount_local / rate`.

Order of preference:

1. ECB daily reference rate for the posting date
2. FX table shipped with the submission, if no rate history is available
3. Flag the row — never guess

## Why provided amounts are a cross-check, not a source

Earlier versions of this concept preferred a group-currency column already
present in the export. Measurement overturned that: on a real submission the
group amount equalled the local amount on **8,168 of 8,182 non-EUR rows** — the
column was never converted. The submission's own FX sheet covered one of three
foreign currencies and was labelled "may be stale".

Provided figures are therefore reconciled against the computed ones and reported
where they disagree, but they do not drive the conversion.

The effect is not cosmetic. Summing the raw amount column across currencies gave
227,419,026; converted at daily rates the same rows are **140,639,488 EUR**. The
unconverted figure overstated spend by 62%, and 13.5 million HUF that read as
13.5 million in a mixed sum are 34,151 EUR.

## Credit Notes

**Spend counts net.** Credit notes, reversals and refunds carry their sign into
every total, because the net figure is what actually flowed and therefore what a
negotiation is about. Gross spend and credit volume are reported alongside, so
the correction is visible rather than hidden.

Matching a credit note to the invoice it corrects is not attempted. It was
tested and found impossible on real data: not one document number appeared twice
with opposing signs. Where a future export does support it, matching becomes a
profiling check, not a change to this rule.

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
Company Normalization
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

## Handing the Analysis On

An analysis that ends in a browser cannot be given to anyone. Two outputs close
that, for two different readers, and both are built on request rather than on
every run — nobody needs a nine megabyte workbook they did not ask for. Both are
kept in the run as the artifacts of a stage, so a report that has been sent
somewhere stays findable next to the evidence for it.

**One assembly, two renderings.** `analysis/report.py` decides what a report
contains, in what order, with which figures and which tables underneath them. The
Executive Summary screen, the workbook and the HTML file each only decide how to
draw that. Assembled separately they would agree today and diverge at the first
new chart, which is why the screen's Visuals tab renders the same list the exports
do rather than a parallel one.

The control sits beside the Executive Summary title rather than inside a tab, so
it is reachable wherever the reader is, and the built files are held for the run:
a download button re-runs the script, and a control that rebuilt itself each time
would hand over the first file and lose the second.

**Excel — for the reader who carries on calculating.** One sheet per section, one
per figure, and the canonical table twice: the business fields, and all of them
with every flag. Charts are native Excel charts bound to the sheet they sit on, so
they can be recoloured, resized and pasted into a deck. Amounts are written as
numbers with a display format, never as pre-formatted text — a workbook whose
figures cannot be pivoted misses the point of being a workbook. Excel has no
waterfall openpyxl can write, so it is a stacked bar with an invisible base series,
the standard construction.

**HTML — for the reader who only wants to read it.** One file, no request leaves
the page when it opens. A report mailed to a portfolio company gets opened from a
download folder, on a train, behind a corporate proxy, so the Vega runtime is
embedded rather than fetched from a CDN. Written once and shared by every chart it
costs about a megabyte; carried per chart it would be nine.

Two things that took measuring rather than reasoning:

- Text from the data — supplier names, GL descriptions, the agent's own sentences
  — reaches both the page and the chart specifications. In the page it is escaped
  as text. Inside the `<script>` element that carries the specifications, escaping
  as HTML is not enough: a browser looks for `</script>` before it looks for JSON,
  so those characters are written as `\u` sequences, which keeps the JSON valid
  and the sequence unwritable.
- A chart in an inactive tab has no width to measure, so every specification
  carries an explicit one rather than sizing to its container.

## Spend Cube Structure

| Field | Description |
|----------|------------|
| Company | Portfolio Company |
| Company Name | Readable company name |
| Company Normalized | Canonical company across submissions |
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

# 16.1 Quantifying the Levers

Naming a lever is not enough; it has to carry a number a decision-maker can weigh,
and that number has to survive being questioned.

## A fixed catalogue, tested against the data

The levers are the standard set that procurement work in private equity turns on.
The catalogue does not change between companies; what changes is which of them the
data can support. Every lever therefore declares the canonical fields it is measured
from, as **alternative combinations** rather than one list -- price harmonisation
works from item code plus quantity plus amount, or from item code plus a stated unit
price, and insisting on one form would report a gap where none exists.

Each lever ends in one of three states:

| State | Meaning |
| --- | --- |
| `quantified` | Measured, and there is something there |
| `not_applicable` | Measurable, measured, and there is nothing there |
| `not_assessable` | Could not be measured: a field is missing, or its content cannot carry the lever |

The last two are opposites in practice. "All suppliers have contracts" is a result;
"no contract data was supplied" is a gap in the request. Before this distinction
existed both produced a base of zero and were indistinguishable.

Levers that cannot be assessed produce the **data request list**: which fields to
ask the portfolio company for, and which levers each would unlock.

## What is derivable, and what is not

From a booking-level export the platform quantifies five levers and reports two
exposures. The limit is the data, not the analysis:

| Lever | Base measured on a real submission |
| --- | --- |
| Supplier Consolidation | Suppliers billing several companies |
| Contract Coverage | Spend with suppliers the master lists without a contract |
| Maverick / Process Compliance | No purchase order **and** supplier absent from the master |
| Tail Spend | Suppliers each below a small share, carrying many small bookings |
| Duplicate Payments | Bookings identical on five fields — recoverable cash rather than negotiation |

Two further levers are reported as **exposures, deliberately outside the savings
total**: supplier dependency and currency exposure. Both are real findings for an
investor; neither is a saving to be booked, and adding them would inflate the
number.

**Duplicate payments count the excess, not the flag.** The duplicate rule marks
every row of a group, but one booking per group is legitimate. On real data 424
flagged rows formed 212 pairs: the recoverable amount is 3,188,054 EUR, exactly half
of the 6,376,108 the flag sums to. Taking the flagged sum would double the lever.

Portfolio Benchmark is reported as a **diagnostic rather than a fifth pot**: the
action it implies — transfer what the best company does — *is* the other levers,
applied where they bite hardest. Giving it a potential of its own would count the
same euros twice.

Category Consolidation waits for generated categories (section 18). Price variance,
payment terms and spend growth are not derivable at all from an export without line
items, without payment conditions and covering a single year. That is stated rather
than quietly omitted.

## No euro is counted twice

On a real submission **64.5% of addressable spend qualified for more than one
lever**. Summing the levers would have counted the same money two and three times.

Each euro is therefore credited to exactly one lever, taking the **most specific
population first**: tail spend, then maverick, then contract coverage, then supplier
consolidation, which applies to everything and so takes the remainder.

Specificity is the criterion because it is a property of the data. Ordering by
assumed saving rate would maximise the total and bias it optimistic.

Two figures are always shown: **gross**, what a lever is worth examined alone, and
**net**, its contribution after assignment. The net bases sum exactly to the
addressable spend.

## Saving rates are assumptions

The percentages are practitioner ranges. They are **not derived from the data** and
are labelled as such wherever they appear, with the rate shown next to every figure
it produced and configurable without touching logic.

What comes from the data is the base each lever applies to, which bookings those
are, and how they were assigned. What comes from assumption is only the rate.

## Priority is computed, not asserted

Three measured quantities, none collapsed into an opaque score:

- **Impact** — the net potential in the base case
- **Effort** — how many suppliers and companies must be coordinated
- **Confidence** — whether the base rests on evidence or on an absence of it

Confidence is the honest one. A base built from a *missing* purchase order is weaker
than one built from a confirmed supplier name, because absence has other
explanations. The rating and its reason travel with the figure.

## Traceability

Every row carries which levers it qualifies for and which one it was credited to, so
any figure can be filtered back to its bookings and from there, through the source
row, into the uploaded file.

## What the agent does

It writes the opportunity and the next steps, and may propose a different order with
an argument. It receives aggregates only — never a booking — and its output model has
no numeric field, so it cannot contradict the arithmetic shown beside it. The
pipeline answers what is there; the agent answers what to do about it.

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

# 21.1 Executive Summary

The analysis is spread over screens that each serve one working step. Someone who
did not click through it needs the result in one place: what was found, the biggest
levers, the spend chain drawn, the questions still open, and somewhere to ask.

Six tabs over the artifacts the stages already wrote. Nothing is recomputed, so this
view cannot disagree with the screens behind it.

## Charts are computed, not styled by eye

Colour follows the visualisation guideline, verified with its validator rather than
chosen by taste. The brand purple failed as a categorical hue -- measured lightness
0.301, outside the permitted band, and three checks failed -- so it stays the
interface accent and the dark end of the sequential ramp, while the series use a
validated six-hue set whose worst adjacent colour-vision separation is dE 9.1.

Two of those hues sit below the contrast floor. The guideline allows that only
against relief, so every chart carries direct labels and a "Show data" expander with
the numbers behind it. The expander doubles as traceability.

A pie is legible at a glance and only up to six segments, so supplier share shows the
largest five plus "Other" and is paired with a ranked bar that shows the tail a pie
cannot. Deductions in the waterfall are neutral, not red: removing intercompany spend
from a total is not a failure.

## The chat is grounded, and says when it is not

The assistant receives one context assembled from the run's artifacts -- aggregates
only, never a booking. It answers from that or says the question is outside this
analysis and names what would be needed. It never calculates, for the same reason it
never does anywhere else: a figure it derived could contradict one on the screen
beside it.

## Questions the data cannot answer

Booking data measures what happened and cannot say why. An agent turns the findings
into questions for the people who know the business -- whether a missing purchase
order reference is a control gap or simply how this company buys, what the stated
subtotals exclude, whether duplicate bookings were already recovered. Alongside them
sits the deterministic list of fields to request, each naming the lever it would
unlock.

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
