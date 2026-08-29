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

The objective of this platform is to automatically transform heterogeneous ERP exports into a standardized procurement data model that enables AI-powered identification of procurement value creation opportunities across the portfolio.

---

# 2. Design Principles

The architecture follows one simple principle:

> **Everything that can be solved deterministically should be solved deterministically. AI is only used where semantic understanding or business reasoning is required.**

## Deterministic Tasks

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

- Schema understanding
- Supplier semantic classification
- Procurement category generation
- Procurement lever identification
- Executive summaries
- Recommendation generation

The LLM **never modifies financial values**.

---

# 3. Overall Architecture

```text
ERP Export
      │
      ▼
───────────────────────────────
1. Schema Mapping (LLM)
───────────────────────────────
      │
      ▼
Canonical Procurement Schema
      │
      ▼
───────────────────────────────
2. Data Profiling
(Deterministic)
───────────────────────────────
      │
      ▼
Profiling Report
      │
      ▼
───────────────────────────────
3. Rule Engine
(Deterministic)
───────────────────────────────
      │
      ▼
Normalized Dataset
      │
      ▼
───────────────────────────────
4. Canonical Spend Cube
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

---

# 4. Schema Mapping (LLM)

## Goal

Every ERP export uses different column names.

Examples

| ERP | Supplier Column |
|------|-----------------|
| SAP | Vendor |
| Oracle | Supplier Name |
| Dynamics | Business Partner |

Instead of hardcoding mappings, the LLM translates every dataset into a canonical procurement schema.

---

## Input

- Column names
- Data types
- Sample values

Example

| Column | Sample |
|----------|---------|
| Vendor | ABC Ltd |
| Amount LC | 1250 |
| Currency | EUR |
| Account Description | Consulting |

---

## Output

```json
{
  "supplier":"Vendor",
  "company":"Company Name",
  "amount_local":"Amount LC",
  "currency":"Currency",
  "posting_date":"Posting Date",
  "invoice_number":"Document Number",
  "gl_description":"Account Description",
  "category":null
}
```

Every mapping contains a confidence score.

Mappings below a configurable threshold require manual confirmation.

---

# 5. Canonical Procurement Schema

After schema mapping, every dataset follows exactly the same structure.

Canonical fields include:

- Company
- Supplier
- Supplier ID
- Local Amount
- Group Amount
- Currency
- Posting Date
- Document Date
- Invoice Number
- Purchase Order
- GL Account
- GL Description
- Cost Center
- Profit Center
- Procurement Category (optional)

From this point onwards, the remaining pipeline is ERP-independent.

---

# 6. Data Profiling

The objective of data profiling is **not cleaning**.

It only evaluates data quality and creates a structured profiling report.

No AI is used during profiling.

---

## 6.1 Completeness Checks

Questions

- Supplier available?
- Amount available?
- Currency available?
- Company available?
- Posting Date available?
- Purchase Order available?

---

## 6.2 Consistency Checks

- Duplicate invoices
- Duplicate document numbers
- Duplicate transactions
- Negative amounts
- Future dates
- Posting Date before Document Date
- Currency consistency

---

## 6.3 Semantic Checks

### Procurement Category available?

If not

→ Category analysis disabled.

---

### Category identical to GL Description?

If yes

Category is treated as accounting classification.

Category analysis disabled.

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

## 6.4 Analytics Readiness

Checks

- Supplier overlap
- Spend concentration
- Currency complexity
- PO coverage
- Category usability

---

## Output

Example

| Check | Result | Severity |
|---------|----------|----------|
| Missing Supplier | 0.5% | High |
| Duplicate Documents | 21 | Medium |
| Category = GL | Yes | Warning |
| Supplier Variants | High | Medium |

---

# 7. Rule Engine

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

# 8. Data Preservation Principle

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

---

# 9. Currency Harmonization

Preferred order

1. Existing Group Currency
2. FX Conversion
3. Flag if impossible

Currency conversion is always deterministic.

---

# 10. Canonical Spend Cube

The **Canonical Spend Cube** is the central semantic data model of the platform.

Everything before the Spend Cube prepares the data.

Everything afterwards consumes the Spend Cube.

It represents the single source of truth.

```text
ERP Export
      │
      ▼
Schema Mapping
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

The Spend Cube is **not a reporting artifact**.

It is the semantic foundation for all analytics.

---

# 11. Analytical Views

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

# 12. Classical Procurement Levers

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

# 13. Deterministic Analytics

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

# 14. AI Enrichment

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

# 15. AI Procurement Reasoning

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

# 16. Why This Architecture?

The solution is

- ERP agnostic
- Deterministic
- Explainable
- Auditable
- Reproducible
- AI-enabled
- Scalable across portfolio companies

By separating deterministic engineering from AI reasoning, the platform combines trustworthy financial analytics with modern AI capabilities while maintaining full transparency and auditability.
