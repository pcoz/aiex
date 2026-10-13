# AIEX Architecture Document

**Project:** AIEX (AI Execution Language)
**Version:** 0.1.0
**Date:** 2026-04-16
**Status:** First live execution completed; core pipeline validated

---

## 1. System Overview

### 1.1 What AIEX Is

AIEX is a domain-specific execution framework that compiles AILang programs into
auditable, transaction-based execution plans and runs them against LLM and BERT
backends. It occupies the layer between the programmer-facing AILang specification
language and the raw LLM API surface:

    AILang (what to reason about)
         |
         v
    AIEX (how to execute the reasoning)
         |
         v
    LLM / BERT APIs (where the reasoning happens)

AIEX is not a general-purpose language runtime. It is a **contract-enforced LLM
orchestration engine** built on the principle that every AI reasoning operation
should be a declared transaction with explicit inputs, explicit outputs, and
enforceable boundary contracts.

### 1.2 The COBOL Lineage

AIEX inherits from COBOL the insight that **program structure should mirror data
processing reality**. Where COBOL's four-division structure mirrors batch
processing on mainframes (identify the program, configure the environment,
define the data, run the procedures), AIEX's five-division structure mirrors
how AI reasoning programs actually execute:

| COBOL Division     | AIEX Division  | Purpose                                    |
|--------------------|----------------|--------------------------------------------|
| IDENTIFICATION     | PROGRAM        | Program metadata, source provenance        |
| ENVIRONMENT        | ENGINE         | LLM configuration, routing tables          |
| DATA               | STATE          | Variables, contexts, accumulators          |
| *(none)*           | CONTRACT       | Boundary validation rules                  |
| PROCEDURE          | EXECUTION      | Transaction pipeline                       |

The fifth division -- CONTRACT -- exists because in AI execution, **boundary
enforcement is the primary value proposition**, not a side effect of procedural
logic. COBOL scatters its validation logic across PROCEDURE DIVISION IF-checks;
AIEX promotes it to a first-class structural concern.

What AIEX does NOT inherit from COBOL: column-based formatting, hardware-coupled
configuration, sequential file I/O, or MOVE/COMPUTE verbosity. AIEX is a modern
Python system with clean abstractions that happens to think like a mainframe
transaction processor.

### 1.3 Core Design Principle: Declarative Intent, Procedural Execution

The AILang programmer writes WHAT reasoning to perform:

    DEDUCE tariff_applicability FROM premise_1, premise_2
        WITH: RETRIEVAL_BUDGET: NONE, DEPTH: STANDARD
        MUST_INCLUDE: [section_232_applicability]

AIEX handles HOW that reasoning is executed: which model to dispatch to, what
system prompt to compose, how to validate the response against the declared
contracts, and whether to commit or roll back the result. The programmer never
touches API parameters, prompt engineering, retry logic, or JSON parsing.

---

## 2. Pipeline Architecture

### 2.1 End-to-End Pipeline

```
 AILang Source (.ailang)
        |
        v
 +-------------+     Token Stream     +-------------+
 |    LEXER     | ------------------> |    PARSER    |
 | (lexer.py)   |                     | (parser.py)  |
 +-------------+                      +-------------+
                                            |
                                       AST (Program)
                                            |
        +-----------------------------------+
        |                                   |
        v                                   v
 +-------------+                     +-------------+
 |  ANALYZER   |                     |  COMPILER   |
 | (analyzer.py)|                    | (compiler.py)|
 +-------------+                     +-------------+
        |                                   |
   DependencyGraph          TransactionPlans + Contracts
        |                                   |
        +-----------------------------------+
                        |
                        v
               +-----------------+
               | BUDGET CALC     |
               | (budget.py)     |
               +-----------------+
                        |
                  ExecutionPlan
                        |
                        v
               +-----------------+       +------------------+
               | RUNTIME ENGINE  | ----> | DISPATCH LAYER   |
               | (engine.py)     |       | (dispatch.py)    |
               +-----------------+       +------------------+
                        |                     |          |
                        |                     v          v
                        |               +---------+  +---------+
                        |               |  BERT   |  |   LLM   |
                        |               | Client  |  |  Client |
                        |               +---------+  +---------+
                        |                     |          |
                        v                     v          v
               +-----------------+      Embeddings  API Responses
               | CONTRACT VALID. | <---------+-----------+
               | (contracts.py)  |
               +-----------------+
                        |
               COMMIT / ROLLBACK
                        |
                        v
               +-----------------+
               | PROGRAM STATE   |
               | (state.py)      |
               +-----------------+
```

### 2.2 Pipeline Stages Summary

| Stage    | Input                  | Output                | Module(s)            |
|----------|------------------------|-----------------------|----------------------|
| LEX      | Raw AILang source text | Token stream          | `parser/lexer.py`    |
| PARSE    | Token stream           | AST (`Program`)       | `parser/parser.py`   |
| ANALYZE  | AST                    | `DependencyGraph`     | `compiler/analyzer.py`|
| ROUTE    | Operation nodes        | `RoutingDecision`s    | `models/routing.py`  |
| CONTRACT | Boundary specs         | `TransactionContract`s| `compiler/compiler.py`|
| BUDGET   | Routing + operations   | `BudgetEstimate`s     | `compiler/budget.py` |
| EMIT     | All above              | `ExecutionPlan`       | `compiler/compiler.py`|
| EXECUTE  | `ExecutionPlan`        | `ProgramState`        | `runtime/engine.py`  |
| DISPATCH | Transaction + prompts  | LLM/BERT responses    | `runtime/dispatch.py`|
| VALIDATE | Response + contract    | `ValidationResult`    | `runtime/contracts.py`|

---

## 3. Five Division Structure

AIEX programs (both the compiled output and the internal representation) are
organized into exactly five divisions, in fixed order. This structure is
reflected in the `ExecutionPlan` and `ProgramState` models.

```
 +================================================================+
 |  PROGRAM DIVISION                                               |
 |  Metadata: program-id, source file, compiler version,          |
 |  target engine, trace level                                     |
 +================================================================+
 |  ENGINE DIVISION                                                |
 |  Connection config, model pool, routing tables,                |
 |  budget tables, depth tables, style tables,                    |
 |  prompt templates                                               |
 +================================================================+
 |  STATE DIVISION                                                 |
 |  Variables (typed), reality contexts, reasoning context         |
 |  accumulators, result slots, dispatch metadata                  |
 +================================================================+
 |  CONTRACT DIVISION                                              |
 |  MUST-INCLUDE checks, CANNOT-INCLUDE checks,                   |
 |  OUTPUT-FORMAT schema validation, RETRIEVAL-COMPLIANCE,         |
 |  SUPPRESS enforcement, CONSTRAIN enforcement,                   |
 |  severity policies                                              |
 +================================================================+
 |  EXECUTION DIVISION                                             |
 |  Transaction pipeline: sequential and parallel groups,          |
 |  THEN chains, CONTEXT-BLOCKs, WITH-CONTEXT scopes,            |
 |  fallback procedures                                            |
 +================================================================+
```

### 3.1 How Each Division Maps to Code

| Division   | Compile-Time Representation        | Runtime Representation           |
|------------|-------------------------------------|----------------------------------|
| PROGRAM    | `ExecutionPlan.program_id`, `.source_file` | Engine metadata logging    |
| ENGINE     | `RoutingDecision`, `ModelConfig`, `DEFAULT_MODEL_POOL` | `AnthropicLLMClient`, `BertClient`, `TemplateLoader` |
| STATE      | `Program.variables`, AST context nodes | `ProgramState` (variables, results, digests, log) |
| CONTRACT   | `TransactionContract`, `ContractCheck` | `validate_contract()` in `contracts.py` |
| EXECUTION  | `ExecutionPlan.transactions`, `.parallel_groups` | `AiexEngine._execute_plan()` loop |

---

## 4. Component Architecture

### 4.1 Parser (`src/aiex/parser/`)

The parser is a two-stage front end that transforms raw AILang source text into
a typed, immutable AST.

**Lexer** (`lexer.py`):
- Regex-based scanner with a master compiled pattern
- Handles two-word keywords joined by underscores (e.g., `REALITY_CONTEXT`,
  `RETRIEVAL_BUDGET`, `MUST_INCLUDE`) by matching them before single keywords
- Produces `Token(type, value, line, col)` objects
- Special handling for the version header (`#ailang v0.8.2`) and string literals
- Emits NEWLINE tokens for line tracking; comments are preserved but filtered
  by the parser

**Parser** (`parser.py`):
- Recursive descent parser consuming the filtered token stream
- Parses the full AILang v0.8.2 grammar: SET variables, all six reasoning
  operations (RETRIEVE, COMPARE, TRACE, DEDUCE, SYNTHESIZE, WEIGH), style
  modifiers, THEN chains, REASONING_CONTEXT blocks, WITH REALITY_CONTEXT scopes,
  nested OUTPUT_FORMAT schemas, and boundary clauses
- Produces frozen Pydantic AST models (`Program`, `OperationNode`, `Variable`,
  `BoundarySpec`, `OutputFormatSchema`, etc.)
- All AST nodes are immutable (`model_config = {"frozen": True}`) -- once
  parsed, the AST is read-only

### 4.2 Compiler (`src/aiex/compiler/`)

The compiler transforms the AST into an executable plan through three sub-stages.

**Dependency Analyzer** (`analyzer.py`):
- Builds a `DependencyGraph` (DAG) from the AST
- Rules: SET variables are immutable inputs (no dependency); an operation
  referencing another operation's `result_name` creates an edge; THEN chains
  are sequential; REASONING_CONTEXT blocks are sequential; operations in
  WITH REALITY_CONTEXT that do not reference each other are independent
- Performs topological sort for execution ordering
- Identifies parallel groups: operations with identical dependency signatures
  and no mutual dependencies are grouped for concurrent dispatch

**Compiler** (`compiler.py`):
- Orchestrates the full compilation pipeline: analyze -> route -> contract ->
  budget -> emit
- For each operation: resolves routing via `resolve_routing()`, generates a
  `TransactionContract` from the operation's `BoundarySpec`, assigns parallel
  group membership, and calculates the budget estimate
- Emits a complete `ExecutionPlan` with pre-computed cost/latency totals and
  savings percentage vs. an all-Opus baseline

**Budget Calculator** (`budget.py`):
- Pre-execution cost/latency estimation using the AILang v0.8.2 neuron hierarchy
- Computes effective neurons per transaction: base neuron count minus retrieval
  savings (NONE saves 865 neurons; MINIMAL saves 665; STANDARD/FULL save 0)
- Estimates input/output tokens from content counts and depth multipliers
- Applies per-tier cost tables derived from `DEFAULT_MODEL_POOL` (BERT:
  ~$0/1K, Haiku: $0.001/$0.005, Sonnet: $0.003/$0.015, Opus: $0.005/$0.025)
- Accounts for BERT retrieval phase cost ($0.00004) and latency (+50ms)
- Calculates parallel execution latency: max within each group, sum across groups

### 4.3 Runtime (`src/aiex/runtime/`)

The runtime executes the compiled plan through a dispatch-validate-commit cycle.

**Engine** (`engine.py`):
- `AiexEngine` class: the top-level runtime coordinator
- Supports dry-run mode (mock clients) and live mode (Anthropic API + optional
  BERT)
- Execution loop: iterates transactions in topological order, respecting
  parallel groups and dependency barriers
- Per-transaction cycle: compose system prompt from template + routing +
  boundaries, compose user message from input references, dispatch, validate,
  commit or rollback
- Retry logic: MAJOR violations trigger prompt reinforcement (violation details
  appended to system prompt) up to `max_retries`; CRITICAL violations cause
  immediate rollback; MINOR violations commit with warnings
- **Code-block branch**: when a transaction has its `code_block_body` field set,
  the engine bypasses the LLM dispatch path entirely and routes to
  `_execute_code_block_transaction`, which delegates to the local code executor.
  The transaction still moves through the COMMIT / ROLLBACK lifecycle, just
  without prompt composition or contract validation -- Python errors are
  deterministic, so there is no retry loop.
- Prints pre-execution budget report and post-execution summary via Rich console

**Code Executor** (`code_executor.py`):
- `execute_code_block(language, body, namespace_in)` -- runs the body via
  in-process `exec()` against a snapshot of AILang variables, captures any new
  bindings, and returns a `CodeBlockResult` with success/failure plus the
  bindings to propagate back into program state.
- `textwrap.dedent` strips AILang block indentation before compilation so the
  body parses as valid Python regardless of how the AILang author indented.
- Returns successfully on any non-exception execution; on exception captures
  the traceback into the result for the engine to log without raising further.
- Trust model: the same author writes the AILang program and the embedded code
  blocks, so no sandbox; deployment as a multi-tenant service would replace
  this with a sandboxed implementation (subprocess, Docker, gVisor) without
  changing the engine's contract with the executor.

**Dispatch Layer** (`dispatch.py`):
- Protocol-based design: `LLMClient` and `BertClient` are Python Protocols,
  enabling mock, test, and real implementations
- `AnthropicLLMClient`: wraps the Anthropic SDK; calls `messages.create()` and
  extracts JSON from the response text
- `MockLLMClient` / `MockBertClient`: return structured placeholder data for
  dry-run and testing
- `dispatch_transaction()`: two-phase dispatch:
  1. BERT retrieval phase (if retrieval budget > NONE): extract terms, search
     vector index, append results as context to user message
  2. LLM dispatch phase: send composed prompt to the routed model tier
- JSON extraction (`_extract_json()`): attempts direct parse, markdown code
  block extraction, and brace-delimited extraction

**Contract Validator** (`contracts.py`):
- `validate_contract()`: evaluates all contract checks against a parsed response
- MUST_INCLUDE: BERT semantic similarity when available; keyword search fallback
- CANNOT_INCLUDE: TERMINOLOGY_DIFF (BERT or keyword-based term set difference),
  KNOWLEDGE_DEPTH_SCAN (regex patterns for citations/statutes/regulations),
  SEMANTIC_SCAN (keyword presence)
- OUTPUT_FORMAT: deterministic JSON schema validation -- field existence, type
  checking, constrained enum validation
- Returns `ValidationResult` with aggregate severity (worst violation wins)

**BERT Validator** (`bert_validator.py`):
- `semantic_contains()`: splits response into chunks, encodes with
  sentence-transformers (all-MiniLM-L6-v2), computes cosine similarity against
  concept embedding; threshold 0.45 for MUST_INCLUDE
- `semantic_absent()`: inverse of `semantic_contains()` with higher threshold
  (0.55) to reduce false positives
- `terminology_diff()`: encodes all input terms and response terms, computes
  pairwise similarity matrix, flags response terms with low max-similarity to
  any input term as "novel"

### 4.4 Models (`src/aiex/models/`)

Pydantic models organized by lifecycle stage:

**AST Nodes** (`ast_nodes.py`):
- Enumerations: `ReasoningType` (7 values), `StyleModifier` (4), `DepthLevel` (5),
  `RetrievalBudget` (4), `Direction` (2)
- Leaf nodes: `Variable`, `ObjectField`
- Schema: `OutputFormatSchema` (recursive -- supports nested objects and lists)
- Boundary: `BoundarySpec` (must_include, cannot_include, output_format)
- Operation: `OperationNode` -- the universal node for all reasoning operations
- Context: `RealityContext`, `ReasoningContextBlock`, `WithContextBlock`, `ThenChain`
- Root: `Program` (variables, operations, reality contexts, reasoning contexts)

**Execution Plan** (`plan.py`):
- `ModelTier` enum (BERT_CLASS / GENERATIVE_LIGHT / GENERATIVE_STANDARD / GENERATIVE_DEEP)
- `ModelConfig`: static per-model configuration (cost, latency, max tokens)
- `RoutingDecision`: compiler's model assignment with optional escalation
- `ContractCheck` / `TransactionContract`: declared validation rules
- `TransactionPlan`: one unit of work with routing, contract, I/O refs, parallelism
- `BudgetEstimate`: pre-execution cost/latency for one transaction
- `ExecutionPlan`: top-level compiled artifact with all transactions, groups, and totals

**Runtime State** (`state.py`):
- `TransactionStatus` enum (PENDING / DISPATCHED / VALIDATING / COMMITTED / ROLLED_BACK / RETRYING)
- `ContractViolation`: one failed check with severity and details
- `ValidationResult`: aggregate pass/fail with violation list
- `DispatchMeta`: telemetry (model used, tokens, latency, cost, retries)
- `TransactionResult`: outcome of one transaction
- `ProgramState`: mutable working storage (variables, results, digests, execution log)

### 4.5 Routing Tables (`models/routing.py`)

Default routing configuration encoding the AIEX specification's heuristics:

- `DEFAULT_MODEL_POOL`: four model tiers with real-world model IDs and pricing
- `DEFAULT_ROUTING_TABLE`: maps each `ReasoningType` to a base `RoutingDecision`
  (tier, template, temperature, neuron budget)
- `DEFAULT_BUDGET_TABLE`: maps each `RetrievalBudget` level to runtime modifiers
  (neurons, prompt modifier, vector search toggle, top-k)
- `DEFAULT_DEPTH_TABLE`: maps each `DepthLevel` to runtime modifiers (max passes,
  token multiplier, prompt suffix)
- `resolve_routing()`: applies escalation rules and returns the final routing decision

### 4.6 Templates (`src/aiex/templates/`)

System prompt templates stored as `.txt` files with `{PLACEHOLDER}` substitution:

- One template per reasoning type: `deduce.txt`, `trace.txt`, `compare.txt`,
  `weigh.txt`, `synthesize.txt`, `retrieve.txt`, `intelligent_general.txt`
- `TemplateLoader` class: loads, caches, and composes templates at runtime
- `get_boundary_instructions()`: converts CONTRACT DIVISION rules into
  natural-language LLM instructions for the `{BOUNDARY_INSTRUCTIONS}` placeholder
- Placeholders: `{RETRIEVAL_MODIFIER}`, `{DEPTH_SUFFIX}`, `{STYLE_PREFIX}`,
  `{BOUNDARY_INSTRUCTIONS}`, `{OUTPUT_SCHEMA}`, `{DIRECTION}`, `{DIMENSIONS}`

---

## 5. Multi-Model Routing

### 5.1 The Neuron Hierarchy

AILang v0.8.2 defines a neuron hierarchy reflecting the computational complexity
of each reasoning type:

```
  RETRIEVE   COMPARE    TRACE     DEDUCE   SYNTHESIZE   WEIGH
   (~865)    (~1,050)  (~1,100)  (~1,200)   (~1,400)   (~1,531)
     |          |         |         |          |           |
  Low-cost  ---------> Mid-cost ----------> High-cost -------->
```

Not every operation needs a frontier generative model. RETRIEVE and COMPARE are
fundamentally classification, retrieval, and ranking tasks -- exactly what
BERT-family models excel at.

### 5.2 Model Pool

```
 +-------------------------------------------------------------------+
 | BERT_CLASS          | all-MiniLM-L6-v2                            |
 | Cost: ~$0           | Capability: retrieval, classification       |
 | Latency: ~50ms      | Max tokens: 512                            |
 +-------------------------------------------------------------------+
 | GENERATIVE_LIGHT    | claude-haiku-4-5                            |
 | Cost: $0.001/$0.005 | Capability: all (lightweight)               |
 | Latency: ~2000ms    | Max tokens: 8192                           |
 +-------------------------------------------------------------------+
 | GENERATIVE_STANDARD | claude-sonnet-4-6                           |
 | Cost: $0.003/$0.015 | Capability: all (balanced)                  |
 | Latency: ~4000ms    | Max tokens: 16384                          |
 +-------------------------------------------------------------------+
 | GENERATIVE_DEEP     | claude-opus-4-6                             |
 | Cost: $0.005/$0.025 | Capability: all (frontier)                  |
 | Latency: ~6000ms    | Max tokens: 32768                          |
 +-------------------------------------------------------------------+
 | LOCAL_CODE          | local Python interpreter (in-process exec)  |
 | Cost: $0            | Capability: deterministic computation only  |
 | Latency: ~100ms     | Max tokens: n/a                             |
 +-------------------------------------------------------------------+
```

The `LOCAL_CODE` tier is the dispatch target for `EXECUTE_CODE` blocks (see
section 6.5). It is not selected by routing logic the way generative tiers are
-- a transaction is in the LOCAL_CODE tier if and only if it originated from
an `EXECUTE_CODE` block in the AILang source. This isolation lets the budget
report and routing log treat code execution as visibly distinct from LLM
dispatch, with $0 contribution to the cost total and the all-Opus baseline.

### 5.3 Default Routing Table

| Reasoning Type | Base Tier            | Template           | Temperature | Neurons |
|----------------|----------------------|--------------------|-------------|---------|
| RETRIEVE       | BERT_CLASS           | retrieval_vector   | 0.0         | 865     |
| COMPARE        | GENERATIVE_LIGHT     | comparison_grid    | 0.2         | 950     |
| TRACE          | GENERATIVE_STANDARD  | trace_chain        | 0.1         | 1100    |
| DEDUCE         | GENERATIVE_STANDARD  | deduction_logic    | 0.15        | 1200    |
| SYNTHESIZE     | GENERATIVE_STANDARD  | synthesis_merge    | 0.4         | 1400    |
| WEIGH          | GENERATIVE_STANDARD  | weigh_tradeoff     | 0.3         | 1300    |
| INTELLIGENTLY  | GENERATIVE_STANDARD  | default            | 0.3         | 1000    |

### 5.4 Escalation Rules

The `resolve_routing()` function applies four escalation rules in order:

```
 Rule 1: DEPTH >= DEEP
         --> push at least to GENERATIVE_STANDARD
 Rule 2: DEPTH == EXHAUSTIVE
         --> push to GENERATIVE_DEEP (Opus)
 Rule 3: RETRIEVAL == FULL and base is BERT_CLASS
         --> escalate to GENERATIVE_LIGHT (Haiku)
 Rule 4: Output requires qualitative text and base is BERT_CLASS
         --> escalate to GENERATIVE_LIGHT (Haiku)
```

Example escalation path for a COMPARE operation:

```
  COMPARE (base: BERT_CLASS)
     |
     +-- DEPTH: STANDARD, OUTPUT: numeric ranking
     |   --> stays BERT_CLASS ($0.00002)
     |
     +-- DEPTH: STANDARD, OUTPUT: has TEXT fields
     |   --> Rule 4 --> GENERATIVE_LIGHT / Haiku ($0.007)
     |
     +-- DEPTH: DEEP, OUTPUT: qualitative
         --> Rule 1 --> GENERATIVE_STANDARD / Sonnet ($0.035)
```

### 5.5 BERT-Generative Handoff

When BERT handles the retrieval phase but a generative model handles the
reasoning phase, dispatch is two-phase:

```
  Phase 1 (BERT):  extract_terms(input_text) --> search(terms, index, top_k)
                                                       |
                                                  [term definitions /
                                                   knowledge items]
                                                       |
  Phase 2 (LLM):  user_message + retrieval_context --> LLM API call
```

The retrieval budget controls the BERT search parameters:

| Budget   | Vector Search | Top-K | Index                      |
|----------|---------------|-------|----------------------------|
| NONE     | No            | 0     | --                         |
| MINIMAL  | Yes           | 3     | terminology-definitions    |
| STANDARD | Yes           | 10    | regulatory-knowledge-full  |
| FULL     | Yes           | 20    | regulatory-knowledge-full  |

---

## 6. Contract Enforcement

### 6.1 The Dispatch-Validate-Commit Cycle

Every transaction follows a strict lifecycle:

```
                     +----------+
                     | COMPOSE  |  System prompt from template + routing
                     | PROMPT   |  + retrieval modifier + boundary instructions
                     +----+-----+
                          |
                          v
                     +----------+
                     | DISPATCH |  Send to LLM (or BERT + LLM)
                     +----+-----+
                          |
                     (raw response)
                          |
                          v
                     +----------+
                     | VALIDATE |  Run all ContractChecks
                     +----+-----+
                          |
            +-------------+-------------+
            |             |             |
          PASS          MAJOR        CRITICAL
            |          FAILURE       FAILURE
            v             |             |
         COMMIT           v             v
            |          RETRY         ROLLBACK
            v        (reinforce        |
       UPDATE STATE   prompt)          v
            |             |        FALLBACK
            v             v        PROCEDURE
         NEXT TX       VALIDATE       |
                       (again)        v
                          |       LOG + HALT
                    pass: COMMIT
                    fail: --> CRITICAL
```

### 6.2 Contract Check Types

| Check Type             | Detection Method      | Implementation                    | LLM Call? |
|------------------------|-----------------------|-----------------------------------|-----------|
| MUST_INCLUDE           | SEMANTIC_SCAN         | BERT cosine similarity (>= 0.45) or keyword search | No (BERT) / No (keyword) |
| CANNOT_INCLUDE         | SEMANTIC_SCAN         | Keyword presence check            | No        |
| CANNOT_INCLUDE         | TERMINOLOGY_DIFF      | Term set difference (BERT or keyword) against input baseline | No |
| CANNOT_INCLUDE         | KNOWLEDGE_DEPTH_SCAN  | Regex patterns for citations, statutes, CFR refs | No |
| OUTPUT_FORMAT          | VALIDATE-SCHEMA       | Deterministic JSON: field exists, type check, enum validation | No |
| RETRIEVAL compliance   | TERMINOLOGY_DIFF      | Novel-term detection vs. input terms | No |
| SUPPRESS enforcement   | SEMANTIC_CLASSIFICATION | (Planned) Classifier model        | Yes       |
| CONSTRAIN enforcement  | SEMANTIC_BOUNDARY_CHECK | (Planned) Focused LLM evaluation  | Yes       |

### 6.3 Severity Levels and Policies

```
 CRITICAL ---------> ROLLBACK transaction immediately
                     State is not modified
                     Fallback procedure runs (if defined)

 MAJOR ------------> RETRY with reinforced prompt
                     Violation details appended to system prompt
                     Max retries (default: 2)
                     After max retries: escalate to CRITICAL

 MINOR ------------> LOG warning and COMMIT
                     Boundary technically violated but within tolerance
                     Response is accepted with annotation
```

The severity assignment is context-dependent. For example:
- Missing a MUST_INCLUDE field: CRITICAL (the response is structurally incomplete)
- CANNOT_INCLUDE violation without BERT: MINOR (keyword false positives are common)
- OUTPUT_FORMAT schema mismatch: CRITICAL (the downstream consumer cannot parse it)
- RETRIEVAL_BUDGET: NONE compliance flag: MINOR (pending BERT for proper enforcement)

### 6.4 Retry Mechanics

When a MAJOR violation triggers a retry:

1. The original system prompt is augmented with a `=== RETRY INSTRUCTION ===`
   block listing every violation message
2. The augmented prompt is re-dispatched to the same model
3. The response is re-validated against the same contract
4. On pass: COMMIT. On fail: if retries remain, repeat. If exhausted, escalate
   to CRITICAL (ROLLBACK)

Known issue (P5 in SESSION_NOTES): retry prompt reinforcement can clutter the
system prompt and degrade LLM output quality. Planned fix: use a separate
retry-instruction user message instead of appending to the system prompt.

### 6.5 EXECUTE_CODE: Code Block Dispatch

`EXECUTE_CODE` blocks are a parallel runtime path. They handle the deterministic
parts of a workflow (amortisation math, JSON formatting, regex extraction) where
an LLM is the wrong tool. AIEX integrates them as first-class transactions
without distorting the LLM-oriented dispatch model.

**Compile-time treatment.** When the parser encounters

    EXECUTE_CODE python:
        ...body...
    END_EXECUTE

it produces a `CodeBlock` AST node holding `language` and `body`. The compiler
emits a `TransactionPlan` with two distinguishing fields set:

- `code_block_body` -- the raw body string
- `code_block_language` -- the language identifier (currently `python` only)
- `routing.base_tier = ModelTier.LOCAL_CODE` -- a sentinel tier signalling
  "no LLM dispatch"
- `est_cost = $0.00` -- code blocks contribute zero to the LLM cost total
- `est_latency_ms = 100` -- a fixed local-execution estimate

The compiler also excludes code blocks from the all-Opus baseline calculation
(they would cost $0 in any plan), so the `Savings %` figure on the budget
report stays honest.

**Dependency analysis.** Code blocks act as ordering barriers. The analyzer
makes each `CodeBlock` depend on every transaction registered before it (the
body may read any AILang variable produced upstream), and registers the block
as a barrier so every subsequent transaction waits for it. This trades
parallelism around code blocks for a strong correctness guarantee on variable
state -- pure-reasoning sections of the program retain full parallelism.

**Runtime dispatch.** When the engine encounters a transaction whose
`code_block_body` is set, it bypasses prompt composition, dispatch, and
validation entirely. It calls `execute_code_block(language, body,
namespace_in)` where `namespace_in` is a snapshot of the current AILang
variable namespace (so the executor cannot mutate state mid-run). On success,
the engine merges the executor's `new_bindings` back into program state via
`state.set_variable`; on failure, the transaction is marked ROLLED_BACK and
no bindings propagate.

**Why this layering is correct.** Variable marshalling and execution ordering
belong in the engine because they are state-management concerns. The actual
code execution is a separate concern (security, isolation, resource limits)
that has nothing to do with LLM orchestration. Today the executor is in-process
`exec()` because the trust model is "the same author writes the program and the
code blocks", which is the same model as `python script.py`. A multi-tenant
deployment would swap the executor implementation without touching the engine's
contract with it -- the engine knows about variable marshalling, not about
sandboxing.

**Trust and security note.** Because the executor runs Python directly in the
AIEX process, anyone who can submit an AILang program to AIEX can run arbitrary
Python under it. This is appropriate for single-tenant developer use and for
deployments where AILang programs come from trusted authors only. For SaaS
deployments accepting untrusted programs, replace the executor with a
subprocess-based, Docker-based, or gVisor-based implementation -- the engine's
interface to the executor is intentionally narrow (one function call, in/out
namespace) precisely so this swap is straightforward.

---

## 7. BERT Integration Points

BERT-class models are used (or planned for use) in three distinct roles within
the AIEX pipeline:

### 7.1 Retrieval Phase (dispatch.py)

```
  Input text ---[BERT encode]---> query embedding
                                       |
                                       v
                              [Vector DB search]
                                       |
                                  top-K results
                                       |
                                       v
                              Appended to user message
                              as retrieval context
```

- Triggered when `RETRIEVAL_BUDGET` is MINIMAL, STANDARD, or FULL
- Uses `BertClient.extract_terms()` for domain-specific term extraction
- Uses `BertClient.search()` for vector index lookup
- Results are formatted as "RESOLVED TERMINOLOGY" (MINIMAL) or
  "REGULATORY CONTEXT" (STANDARD/FULL) and appended to the user message

### 7.2 Contract Validation (bert_validator.py)

- **semantic_contains()**: MUST_INCLUDE enforcement via cosine similarity between
  concept embedding and response chunks (threshold: 0.45)
- **semantic_absent()**: CANNOT_INCLUDE enforcement via inverse similarity
  (threshold: 0.55, higher to reduce false positives)
- **terminology_diff()**: RETRIEVAL_BUDGET: NONE compliance via pairwise
  similarity matrix between response terms and input terms; flags terms with
  low max-similarity as "novel" (external knowledge)

### 7.3 Dependency Analysis (Planned)

- Semantic dependency detection between operations (beyond syntactic reference
  matching)
- Would enable more aggressive parallelization by detecting that two operations
  are semantically independent even if they reference overlapping variable names

### 7.4 Current Status

The BERT integration layer is architecturally complete:
- `BertClient` protocol defined in `dispatch.py`
- `bert_validator.py` module fully implemented
- `MockBertClient` provides term extraction and search stubs for development

**Blocking issue (P2):** `sentence-transformers` v5.4.1 pulls in `torchcodec`
which requires FFmpeg DLLs not present on the current Windows environment. The
`from sentence_transformers import SentenceTransformer` call raises `OSError`.
The system gracefully degrades: `BERT_AVAILABLE` is set to `False` and all
validation falls back to keyword matching. The `MockBertClient` handles the
retrieval phase.

---

## 8. State Management

### 8.1 ProgramState

`ProgramState` is the mutable working storage of a running AIEX program. It is
a plain Python object (not a Pydantic model) that the runtime mutates as
transactions commit.

```
 ProgramState
 +-----------------------------------------------------+
 | variables: dict[str, Any]                            |
 |   Stores SET variables and committed operation       |
 |   results. Variables are set during initialization   |
 |   from Program.variables; results are added on       |
 |   COMMIT.                                            |
 +-----------------------------------------------------+
 | results: dict[str, TransactionResult]                |
 |   Keyed by transaction ID. Stores the full outcome   |
 |   including status, response, dispatch metadata,     |
 |   and validation result.                             |
 +-----------------------------------------------------+
 | digests: dict[str, list[dict]]                       |
 |   Named accumulators for REASONING_CONTEXT digest    |
 |   pipelines. Each digest entry contains reasoning    |
 |   type, confidence, key finding, and summary.        |
 +-----------------------------------------------------+
 | execution_log: list[dict]                            |
 |   Timestamped event log. Every state mutation is     |
 |   logged: set_variable, commit_result,               |
 |   append_digest, COMMIT, ROLLBACK.                   |
 +-----------------------------------------------------+
```

### 8.2 Transaction Atomicity

State is modified ONLY via `COMMIT`:

- On **COMMIT**: the response dict is stored in `state.results[tx.id]` and
  written to `state.variables[tx.output_ref]` (if an output reference is declared)
- On **ROLLBACK**: the response is discarded. `state.results[tx.id]` records the
  failure (status = ROLLED_BACK, response = None) but `state.variables` is NOT
  modified
- On **MINOR warning COMMIT**: the response is committed but violations are
  preserved in `result.validation.violations`

This guarantees that downstream transactions (which read from `state.variables`)
never see partial or invalid data from a failed upstream transaction.

### 8.3 Execution Log

Every state mutation is recorded with a UTC timestamp:

```python
{
    "ts": "2026-04-16T10:30:00.123456+00:00",
    "event": "COMMIT",        # or "ROLLBACK", "set_variable", etc.
    "tx": "tx-1-tariff_none",
    "status": "COMMITTED",
    "message": "TX tx-1-tariff_none: COMMITTED"
}
```

This provides a complete audit trail for every operation in the program.

---

## 9. Data Flow Diagram: Single Transaction

The following diagram traces data flow through one DEDUCE transaction with
RETRIEVAL_BUDGET: MINIMAL from inputs to final state update.

```
 STATE (variables)
 +---------------------------+
 | premise_1: "Under..."     |
 | premise_2: "The USMCA..." |
 | case_facts: {...}         |
 +---------------------------+
        |
        |  (1) Collect inputs
        v
 +---------------------------+
 | _compose_user_message()   |  Formats premises + case_facts
 | _compose_system_prompt()  |  Loads "deduce" template,
 |                           |  injects RETRIEVAL_MODIFIER
 |                           |  ("resolve terminology only"),
 |                           |  DEPTH_SUFFIX (""),
 |                           |  BOUNDARY_INSTRUCTIONS
 |                           |  (MUST_INCLUDE list + schema)
 +---------------------------+
        |
        |  (2) BERT retrieval phase
        v
 +---------------------------+
 | BertClient.extract_terms()|  Extracts: "Section 232",
 |                           |  "USMCA", "anti-dumping"
 | BertClient.search()       |  top_k=3, index=terminology
 |                           |  Returns: term definitions
 +---------------------------+
        |
        |  (3) Append retrieval context to user message
        v
 +---------------------------+
 | "RESOLVED TERMINOLOGY:    |
 |  - Section 232: [defn]    |
 |  - USMCA: [defn]          |
 |  - Anti-dumping: [defn]"  |
 +---------------------------+
        |
        |  (4) LLM dispatch
        v
 +---------------------------+
 | AnthropicLLMClient        |
 |   model: claude-sonnet-4-6|
 |   temperature: 0.15       |
 |   max_tokens: 2048        |
 |   system: [composed]      |
 |   user: [inputs+context]  |
 +---------------------------+
        |
        |  (5) Parse response
        v
 +---------------------------+
 | _extract_json()           |  Try: direct parse
 |                           |  Try: markdown code block
 |                           |  Try: brace extraction
 +---------------------------+
        |
        |  (6) Contract validation
        v
 +---------------------------+
 | validate_contract()       |
 |                           |
 | MUST_INCLUDE checks:      |
 |   "section_232" -> PASS   |
 |   "usmca_qual"  -> PASS   |
 |                           |
 | CANNOT_INCLUDE checks:    |
 |   TERMINOLOGY_DIFF        |
 |     (keyword fallback)    |
 |   -> MINOR warning        |
 |                           |
 | OUTPUT_FORMAT schema:     |
 |   field exists + type     |
 |   + enum validation       |
 |   -> PASS                 |
 +---------------------------+
        |
        |  (7) Commit (worst severity: MINOR)
        v
 +---------------------------+
 | state.commit_result()     |  Store TransactionResult
 | state.set_variable()      |  Write response to output_ref
 | state.log_event()         |  Record COMMIT event
 +---------------------------+
```

---

## 10. Cost Model

### 10.1 Neuron Budget Calculation

Each transaction's effective neuron count is derived from the AILang v0.8.2
specification:

```
  effective_neurons = base_neurons[reasoning_type] - retrieval_savings[budget]
```

| Reasoning Type  | Base Neurons | NONE (-865) | MINIMAL (-665) | STANDARD (0) |
|-----------------|-------------|-------------|----------------|--------------|
| RETRIEVE        | 865         | 0           | 200            | 865          |
| COMPARE         | 1050        | 185         | 385            | 1050         |
| TRACE           | 1100        | 235         | 435            | 1100         |
| DEDUCE          | 1200        | 335         | 535            | 1200         |
| SYNTHESIZE      | 1400        | 535         | 735            | 1400         |
| WEIGH           | 1531        | 666         | 866            | 1531         |

The retrieval savings encode the insight that skipping external knowledge lookup
reduces the computational work the model must perform.

### 10.2 Token Estimation

```
  input_tokens  = 500 (system prompt base)
                + 200 * num_premises
                + 150 * num_knowledge_items (from BERT retrieval)

  output_tokens = base_output[reasoning_type] * depth_multiplier[depth]
```

Depth multipliers: SHALLOW 0.5x, STANDARD 1.0x, THOROUGH 1.5x, DEEP 2.5x,
EXHAUSTIVE 4.0x.

Base output tokens by type: RETRIEVE 400, COMPARE 600, DEDUCE 700, TRACE 800,
WEIGH 800, SYNTHESIZE 900.

### 10.3 Cost Calculation

```
  cost = (input_tokens / 1000 * cost_per_1k_in[tier])
       + (output_tokens / 1000 * cost_per_1k_out[tier])
       + bert_cost  (if retrieval budget > NONE)
```

Tier pricing (USD per 1K tokens):

| Tier                | Input    | Output   |
|---------------------|----------|----------|
| BERT_CLASS          | $0.00002 | $0.00002 |
| GENERATIVE_LIGHT    | $0.001   | $0.005   |
| GENERATIVE_STANDARD | $0.003   | $0.015   |
| GENERATIVE_DEEP     | $0.005   | $0.025   |

### 10.4 Latency Estimation

```
  per_transaction = model_latency[tier] + bert_latency (if applicable)
  total           = sum(max within each parallel group) + sum(sequential)
```

Model latencies: BERT ~50ms, Haiku ~2000ms, Sonnet ~4000ms, Opus ~6000ms.

### 10.5 Parallel Execution Savings

The compiler detects independent operations and groups them for concurrent
execution. For the test_10 program:

```
  Sequential:  TX-1 (4000ms) + TX-2 (4050ms) + TX-3 (4050ms) + TX-4 (2050ms)
               = 14,150ms

  Parallel:    max(TX-1, TX-2, TX-3) (4050ms) + TX-4 (2050ms)
               = 6,100ms

  Speedup:     2.3x
```

### 10.6 Baseline Comparison

Every budget report includes comparison against an all-Opus baseline: each
LLM transaction's own estimate (same input/output token counts, same
retrieval cost) re-priced with the model forced to claude-opus-4-6.
`EXECUTE_CODE` blocks are $0 in both plans, so the savings figure measures
routing alone:

```
  baseline_cost = sum over LLM transactions of
                  calculate_budget(..., routing=<forced GENERATIVE_DEEP>).est_cost

  savings_pct   = (1 - actual_cost / baseline_cost) * 100
```

For test_10 the routing-optimized plan estimates 49.6% savings ($0.058 vs.
$0.116 baseline); for the loan-eligibility example, 40.0% ($0.0447 vs.
$0.0745) -- exactly the Sonnet/Opus price ratio, since all three of its
reasoning steps run on Sonnet.

---

## Appendix A: Source File Inventory

```
src/aiex/
  __init__.py
  cli.py                      CLI entry point
  parser/
    __init__.py
    lexer.py                  Token-based scanner (339 lines)
    parser.py                 Recursive-descent parser
  compiler/
    __init__.py
    analyzer.py               Dependency graph + parallel detection (178 lines)
    budget.py                 Pre-execution cost calculator (130 lines)
    compiler.py               Main compiler pipeline (244 lines)
  models/
    __init__.py
    ast_nodes.py              Frozen AST Pydantic models (221 lines)
    plan.py                   Execution plan models (177 lines)
    routing.py                Default routing tables + resolution (282 lines)
    state.py                  Mutable runtime state (140 lines)
  runtime/
    __init__.py
    engine.py                 Runtime engine + dispatch loop (527 lines)
    dispatch.py               LLM and BERT dispatch layer (238 lines)
    contracts.py              Contract validation (259 lines)
    bert_validator.py         BERT semantic validation (148 lines)
  templates/
    __init__.py               TemplateLoader + composer (182 lines)
    *.txt                     System prompt template files
```

**Total: 20 Python source files, ~3500 lines.**

## Appendix B: Known Issues (as of 2026-04-16)

| ID | Severity | Issue | Impact |
|----|----------|-------|--------|
| P1 | High | JSON extraction fails for complex schemas (11+ fields) | TX-3, TX-4 roll back |
| P2 | High | BERT DLL load failure on Windows (torchcodec/FFmpeg) | Falls back to keyword matching |
| P3 | Medium | CANNOT_INCLUDE keyword checks produce false positives | Severity downgraded to MINOR |
| P4 | Medium | Parallel groups execute sequentially at runtime | 2-3x slower than optimal |
| P5 | Low | Retry prompt reinforcement clutters system prompt | Retry quality may degrade |
| P6 | Low | Relative path issues with .env loader on Windows | CLI startup fragility |

## Appendix C: Key Design Decisions

1. **Pydantic for all models.** AST nodes and plan models are frozen Pydantic
   models; runtime state is mutable plain Python. This gives us validation and
   serialization for free on the immutable side, and unrestricted mutation on the
   runtime side.

2. **Protocol-based dispatch.** `LLMClient` and `BertClient` are Python Protocols
   (structural subtyping), not abstract base classes. This enables drop-in
   replacement of backends without inheritance hierarchies.

3. **Contract severity drives control flow.** Rather than complex error-handling
   logic, the three-level severity system (CRITICAL/MAJOR/MINOR) cleanly maps to
   three actions (ROLLBACK/RETRY/WARN). This keeps the runtime loop simple and
   auditable.

4. **Pre-execution budget reports.** The programmer can review costs before any
   API call is made. This is a direct consequence of making all routing and
   budget decisions at compile time rather than runtime.

5. **Graceful BERT degradation.** The system runs without BERT by falling back to
   keyword matching for validation and mock stubs for retrieval. This allows
   development and testing on machines without GPU/BERT dependencies, at the cost
   of reduced validation accuracy.
