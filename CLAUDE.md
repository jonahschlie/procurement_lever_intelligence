# CLAUDE.md

## Project

AI Procurement Lever Identification Platform. Transforms heterogeneous ERP exports
(SAP, Oracle, Dynamics, Infor, Sage) from private equity portfolio companies into a
Canonical Spend Cube, then derives procurement value creation levers from it.

## Core Principle

Everything depends on this:

> Everything that can be solved deterministically is solved deterministically.
> AI is used only where semantic understanding or business reasoning is required.
> The LLM never modifies financial values and never performs calculations.

## Language

Everything is written in English: code, identifiers, comments, docstrings, commit
messages, documentation, log output, error messages, and UI text. No German in the
repository.

## Development Approach

Build lean. Ship the smallest thing that genuinely solves the current step, then extend it
when the next step actually demands it.

- Implement what is needed now, not what might be needed later.
- No speculative abstraction layers, plugin systems, or config knobs without a caller.
- Prefer a direct function over a class hierarchy; add structure when duplication or a real
  requirement justifies it.
- Prefer extending existing modules over creating parallel ones.

## Code Quality

High quality, low ceremony. These are not in tension — the goal is code that is short,
obvious, and correct.

- Clear naming and honest, explicit data flow beat clever compression.
- Type hints on public functions and data models. Validate at boundaries, trust internally.
- Deterministic stages must be pure and reproducible: same input, same output, no hidden state.
- Errors surface with actionable context; do not swallow exceptions.
- Every deterministic transformation that touches amounts, dates, or dedup logic gets a test.
  AI-facing prompts and outputs get schema validation rather than exhaustive unit tests.

Avoid:

- Boilerplate for its own sake — no getters/setters wrapping plain attributes, no
  pass-through wrapper functions, no `__init__.py` re-export cascades.
- Comments that restate the code. Comment the *why*, and only when it is not obvious.
- Defensive checks for conditions that cannot occur.
- Docstrings on trivially self-explanatory functions.

## Data Handling

Follow the data preservation principle from the concept: rows are flagged, not deleted.
Eligibility for each analysis is expressed through inclusion flags on the row, so that
auditability, reproducibility, and financial reconciliation stay intact.
