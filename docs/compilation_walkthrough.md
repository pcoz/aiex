# AILang → AIEX Compilation Walkthrough

**Source:** `ailang-v0.8.2/tests/test_10_retrieval_budget.ailang`
**Output:** `docs/examples/compiled_example_test_10.aiex`
**Date:** 2026-04-16

This document walks through every transformation the compiler performs, showing how each AILang construct becomes AIEX.

---

## Step 1: PARSE — Source to AST

The compiler reads the AILang source and produces an abstract syntax tree. For test_10, the AST contains:

```
Program
├── Header: #ailang v0.8.2
├── Variables (6)
│   ├── SET premise_1 TO "..." → TextVariable("premise_1", "Under Section 232...")
│   ├── SET premise_2 TO "..." → TextVariable("premise_2", "The USMCA...")
│   ├── SET premise_3 TO "..." → TextVariable("premise_3", "Anti-dumping...")
│   ├── SET premise_4 TO "..." → TextVariable("premise_4", "Countervailing...")
│   ├── SET premise_5 TO "..." → TextVariable("premise_5", "A product is...")
│   └── SET case_facts TO {...} → ObjectVariable("case_facts", {...})
├── Operations (4)
│   ├── DEDUCE tariff_applicability_none
│   │   ├── FROM: [premise_1..5]
│   │   ├── RETRIEVAL_BUDGET: NONE
│   │   ├── DEPTH: STANDARD
│   │   ├── MUST_INCLUDE: [6 items]
│   │   ├── CANNOT_INCLUDE: [2 items]
│   │   └── OUTPUT_FORMAT: {schema}
│   ├── DEDUCE tariff_applicability_minimal
│   │   ├── FROM: [premise_1..5]
│   │   ├── RETRIEVAL_BUDGET: MINIMAL
│   │   ├── DEPTH: STANDARD
│   │   ├── MUST_INCLUDE: [7 items]
│   │   ├── CANNOT_INCLUDE: [1 item]
│   │   └── OUTPUT_FORMAT: {schema}
│   ├── DEDUCE tariff_applicability_standard
│   │   ├── FROM: [premise_1..5]
│   │   ├── RETRIEVAL_BUDGET: STANDARD
│   │   ├── DEPTH: STANDARD
│   │   ├── MUST_INCLUDE: [7 items]
│   │   ├── CANNOT_INCLUDE: []
│   │   └── OUTPUT_FORMAT: {schema}
│   └── COMPARE [3 results] AGAINST validation_criteria
│       ├── DIMENSIONS: [6 items]
│       ├── MUST_INCLUDE: [4 items]
│       └── OUTPUT_FORMAT: {schema}
```

## Step 2: ANALYZE — Dependency Graph

The compiler builds a dependency graph from the AST:

```
TX-1 (DEDUCE NONE) ──────┐
                          │
TX-2 (DEDUCE MINIMAL) ───┼──→ TX-4 (COMPARE)
                          │
TX-3 (DEDUCE STANDARD) ──┘
```

**Key compiler decisions:**
- TX-1, TX-2, TX-3 are **independent** — they share inputs (premises, case_facts) but don't write to each other's state. → Marked `PARALLEL-ELIGIBLE`.
- TX-4 **depends on** TX-1, TX-2, TX-3 — it reads their committed results. → `DEPENDS-ON` all three. → Compiler inserts `AWAIT PARALLEL-GROUP group-1` barrier before TX-4.

**In Python**, you'd have to manually manage `asyncio.gather()` or thread pools for parallelism. **In AIEX**, the compiler declares it and the runtime handles it.

## Step 3: ROUTE — Operation to Model Assignment

For each operation, the compiler looks up the routing table and makes model decisions:

| Operation | Type | Depth | Retrieval | Base Route | Escalation Check | Final Model |
|---|---|---|---|---|---|---|
| TX-1 | DEDUCE | STANDARD | NONE | Sonnet | N/A | **Sonnet** |
| TX-2 | DEDUCE | STANDARD | MINIMAL | Sonnet | N/A | **Sonnet** (+ BERT for terminology) |
| TX-3 | DEDUCE | STANDARD | STANDARD | Sonnet | N/A | **Sonnet** (+ BERT for full retrieval) |
| TX-4 | COMPARE | STANDARD | NONE | BERT | Output has qualitative TEXT → YES | **Haiku** (escalated) |

**The BERT routing decision for TX-4 is interesting.** The routing table says COMPARE → BERT-CLASS. But the compiler examines the OUTPUT_FORMAT and finds fields like `findings: LIST OF TEXT` and `terminology_precision_ranking: LIST OF TEXT` — these require natural language generation, not just ranking. So it triggers the `ESCALATE-WHEN OUTPUT-REQUIRES QUALITATIVE-TEXT` rule and promotes TX-4 to Haiku.

A pure ranking COMPARE (e.g., `OUTPUT_FORMAT: { ranking: LIST OF NUMBER }`) would stay on BERT.

## Step 4: CONTRACT — Boundaries to Validation Rules

Each AILang boundary clause compiles to a specific CONTRACT section:

### `MUST_INCLUDE` → `MUST-INCLUDE SECTION`

```
AILang:
    MUST_INCLUDE: [section_232_applicability, ...]

AIEX:
    CHECK-PRESENT "section_232_applicability"
        IN RESPONSE.reasoning_chain
        DETECTION-METHOD SEMANTIC-SCAN
        ON-FAIL SEVERITY CRITICAL
        MESSAGE "Missing: Section 232 applicability analysis"
```

**The compiler must decide:** What DETECTION-METHOD to use? For conceptual checks like "did the response include a Section 232 analysis," it uses `SEMANTIC-SCAN` (a lightweight LLM call to Haiku that asks "does this response contain X?"). For structural checks like "does the field exist," it uses `FIELD-EXISTS` (deterministic JSON check).

### `CANNOT_INCLUDE` → `CANNOT-INCLUDE SECTION`

```
AILang:
    CANNOT_INCLUDE: [knowledge_not_in_premises, external_regulatory_details]

AIEX:
    CHECK-ABSENT "external_regulatory_details"
        IN RESPONSE
        DETECTION-METHOD TERMINOLOGY-DIFF
        BASELINE premise-1, ..., case-facts
        SENSITIVITY HIGH
```

**`TERMINOLOGY-DIFF` is the key method here.** It computes the set of domain-specific terms in the response and diffs them against terms present in the inputs. If the response contains terms like "Proclamation 9705" or "19 USC 1677(34)" that don't appear in any premise or the case facts, it flags a CANNOT_INCLUDE violation. This is a deterministic check — no LLM call needed.

### `OUTPUT_FORMAT` → `OUTPUT-FORMAT SECTION`

```
AILang:
    OUTPUT_FORMAT: {
        tariffs_apply: TEXT IN ["yes_tariffs_apply", ...],
        reasoning_chain: LIST OF OBJECT { premise_number: NUMBER, ... }
    }

AIEX:
    VALIDATE-SCHEMA RESPONSE AGAINST {
        tariffs_apply: TEXT CONSTRAINED-TO ["yes_tariffs_apply", ...],
        reasoning_chain: LIST OF { premise_number: NUMBER, ... }
    }
```

This is a **deterministic JSON schema validation** — no LLM call needed. The response is parsed as JSON and validated against the declared schema. Constrained enumerations (like `TEXT IN [...]`) are checked as exact string matches.

### `RETRIEVAL_BUDGET` compliance → `RETRIEVAL-COMPLIANCE SECTION`

This is the novel contract section that doesn't exist in v0.7.1. Each RETRIEVAL_BUDGET level gets its own compliance check:

| Budget | Detection Method | What It Checks |
|---|---|---|
| NONE | `TERMINOLOGY-DIFF` with `TOLERANCE ZERO` | Response terms must be a subset of input terms |
| MINIMAL | `KNOWLEDGE-DEPTH-SCAN` with `THRESHOLD TERMINOLOGY-ONLY` | Response may have terminology definitions but not deep regulatory knowledge |
| STANDARD | `LOG` only | No restriction, just audit what external knowledge was used |

## Step 5: SEQUENCE — Execution Order

The compiler determines the execution order:

```
1. PARALLEL: TX-1, TX-2, TX-3 (all in PARALLEL-GROUP group-1)
2. BARRIER: AWAIT group-1 (all three must COMMIT)
3. SEQUENTIAL: TX-4 (reads results from TX-1, TX-2, TX-3)
```

## Step 6: BUDGET — Pre-Execution Cost Analysis

The compiler pre-computes the execution budget before any API call is made:

```
TX-1 (DEDUCE, NONE, Sonnet):
    Neurons:        335 (DEDUCE 1200 - RETRIEVAL 865)
    Input tokens:   ~2800 (system prompt + 5 premises + case facts)
    Output tokens:  ~800
    Cost:           $0.020
    Latency:        ~3000ms

TX-2 (DEDUCE, MINIMAL, BERT + Sonnet):
    BERT phase:     ~50ms, $0.00004 (term extraction + top-3 search)
    Neurons:        535 (DEDUCE 1200 - 665 retrieval savings)
    Input tokens:   ~3200 (system prompt + premises + case facts + 3 definitions)
    Output tokens:  ~1000
    Cost:           $0.025
    Latency:        ~3500ms (50ms BERT + 3450ms Sonnet)

TX-3 (DEDUCE, STANDARD, BERT + Sonnet):
    BERT phase:     ~50ms, $0.00004 (term extraction + top-10 search)
    Neurons:        1200 (full DEDUCE, no retrieval savings)
    Input tokens:   ~4500 (system prompt + premises + case facts + 10 knowledge items)
    Output tokens:  ~1400
    Cost:           $0.035
    Latency:        ~4000ms (50ms BERT + 3950ms Sonnet)

TX-4 (COMPARE, Haiku):
    Neurons:        1050
    Input tokens:   ~4000 (system prompt + 3 prior results)
    Output tokens:  ~600
    Cost:           $0.007
    Latency:        ~2000ms

TOTALS:
    Cost:           $0.087
    vs. all-Opus:   $0.340 → 74.4% savings
    Latency:        ~6000ms (parallel TX-1,2,3 = 4000ms + TX-4 = 2000ms)
    vs. sequential: ~24000ms → 4x speedup
```

**This budget report is available before execution begins.** A programmer can review it, adjust RETRIEVAL_BUDGET levels or DEPTH to hit a cost target, and re-compile — all without making a single API call.

## Step 7: EMIT — The Compiled AIEX Program

The compiler emits the five DIVISION structure:

| DIVISION | What It Contains | Lines |
|---|---|---|
| PROGRAM | Metadata, source reference, target engine | ~15 |
| ENGINE | Connection config, model pool, routing table, budget table, depth table, style table, prompt templates | ~150 |
| STATE | 5 premises, case facts, 4 result slots, system registers | ~80 |
| CONTRACT | 4 contracts (one per transaction) with MUST-INCLUDE, CANNOT-INCLUDE, OUTPUT-FORMAT, RETRIEVAL-COMPLIANCE, SEVERITY-POLICY | ~200 |
| EXECUTION | 4 transactions (3 parallel DEDUCE + 1 dependent COMPARE) with DISPATCH, ON-RESPONSE, and FALLBACK | ~300 |

Total: ~745 lines of AIEX from 214 lines of AILang. The expansion factor (~3.5x) comes from making every implicit behavior explicit: prompt composition, model selection, contract validation, fallback procedures, parallel execution barriers, and budget reporting.

---

## What the Programmer Never Has to Think About

| Concern | AILang programmer writes | AIEX handles automatically |
|---|---|---|
| Model selection | `DEDUCE ... WITH: DEPTH: STANDARD` | Routes to Sonnet (not Opus, not Haiku) |
| BERT routing | `RETRIEVAL_BUDGET: MINIMAL` | BERT term extraction → vector search → append to prompt |
| Parallel execution | Three independent DEDUCE operations | Detects independence, runs in parallel, inserts barrier |
| Contract validation | `MUST_INCLUDE: [...]` | Semantic scan post-response, retry on violation |
| Retrieval compliance | `CANNOT_INCLUDE: [external_knowledge]` | Terminology-diff check against input baseline |
| Cost optimization | Nothing — writes the reasoning spec | Budget report before execution, model escalation rules |
| Fallback on failure | Nothing — declares the boundaries | Reinforced prompt retry → escalation → rollback |
| Output parsing | `OUTPUT_FORMAT: { ... }` | JSON schema validation, constrained enum checks |

**This is the COBOL inheritance.** The programmer declares the data structure and the processing intent. The runtime handles the procedural execution, the I/O, the error recovery, and the resource management. Declarative intent, procedural execution.
