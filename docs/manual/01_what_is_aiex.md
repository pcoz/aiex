# Chapter 1: What AIEX Is

A developer pastes a 200-line AILang program into AIEX. Before a single
token has been sent to a model, AIEX prints something like this:

```
=== AIEX Execution Budget Report ===
Program: loan-eligibility
Transactions: 5

TX                  Operation   Retrieval  Model                  Neurons  Est. Cost  Est. Latency
tx-1-affordability  CODE        NONE       local-code                  0  $0.0000    10ms
tx-2-hard-elig      DEDUCE      NONE       claude-sonnet-4-6         335  $0.0144    4000ms
tx-3-soft-factors   WEIGH       NONE       claude-opus-4-6           666  $0.0135    6000ms
tx-4-synth-decision SYNTHESIZE  NONE       claude-opus-4-6           535  $0.0162    6000ms
tx-5-audit          CODE        NONE       local-code                  0  $0.0000    10ms

  Total estimated cost:    $0.0441
  vs. all-Opus baseline:   $0.3600
  Savings: 87.8%
  Estimated latency:       16020ms
```

`$0.0441 per run. Sixteen seconds. 88% cheaper than the naive approach.`
The developer sees that number before paying a cent. If it is too high,
they change `DEPTH: DEEP` to `DEPTH: STANDARD`, or drop a
`RETRIEVAL_BUDGET: STANDARD` down to `MINIMAL`, recompile, and watch the
number move. No API calls have happened yet.

This is the problem AIEX exists to solve. The alternative -- which is
what almost every LLM application looks like in 2026 -- is to send every
reasoning step to Opus because the developer cannot tell in advance
which steps need it, pay Opus prices on the 70% of operations that
could have run on Sonnet or Haiku or even a BERT model, and discover
the cost at the end of the month. When one of those operations
produces garbage, there is no audit trail, no contract violation to
point to, no deterministic rollback. There is just a bad response
buried in a chain of function calls.

## AILang and AIEX

AILang is the language. It is a natural-language programming notation
for AI reasoning: typed reasoning operations (`DEDUCE`, `RETRIEVE`,
`COMPARE`, `TRACE`, `SYNTHESIZE`, `WEIGH`), orthogonal modifiers
(`DEPTH`, `RETRIEVAL_BUDGET`, style adverbs like `ADAPTIVELY`), and
output contracts (`MUST_INCLUDE`, `CANNOT_INCLUDE`, `OUTPUT_FORMAT`).
An AILang programmer writes *what reasoning to perform*. They do not
write prompts, they do not pick models, they do not parse JSON.

AIEX is the runtime. It compiles an AILang program into an executable
plan, dispatches each operation to the cheapest model tier that can
produce the required output, validates every response against the
declared contracts, and either commits the result to program state or
rolls it back. It is what turns an AILang specification into a running
program.

The two are separated deliberately. AILang describes intent; AIEX
handles execution. A future runtime could take the same AILang program
and dispatch it to different model providers, or run it against a
private evaluation suite, without the source program changing at all.

## Five-Division Compile-Time Plan

AIEX borrows its organising principle from COBOL. Every compiled
program consists of exactly five divisions, in fixed order:

- `PROGRAM DIVISION` -- identity, source provenance, target engine.
- `ENGINE DIVISION` -- model pool, routing tables, retrieval and depth
  tables, prompt templates.
- `STATE DIVISION` -- typed variables, reality contexts, reasoning
  accumulators, dispatch metadata slots.
- `CONTRACT DIVISION` -- every boundary check the runtime will enforce
  after a response comes back (the division COBOL does not have; it
  exists because boundary enforcement is the whole point).
- `EXECUTION DIVISION` -- the transaction pipeline.

Everything the runtime needs is resolved at compile time and captured
in these divisions. Prompt composition, model selection, contract
validation rules, fallback policies, parallel execution barriers --
none of it is invented at runtime. That is what makes the budget
report possible: once the plan is known, its cost and latency can be
calculated.

See [chapter 5](05_typed_reasoning_operations.md) for the reasoning
operations and [chapter 16](16_architecture.md) for a deeper tour
of the five DIVISIONs.

## Multi-Tier Routing

Not every reasoning step needs a frontier model. AIEX maintains a
four-tier model pool:

| Tier                | Example              | Input / output per 1K |
|---------------------|----------------------|-----------------------|
| BERT_CLASS          | all-MiniLM-L6-v2     | ~$0.00002 / ~$0.00002 |
| GENERATIVE_LIGHT    | claude-haiku-4-5     | $0.001 / $0.005       |
| GENERATIVE_STANDARD | claude-sonnet-4-6    | $0.003 / $0.015       |
| GENERATIVE_DEEP     | claude-opus-4-6      | $0.015 / $0.075       |

`RETRIEVE` and many `COMPARE` operations route to BERT, because what
they actually do -- classification, ranking, lookup -- is what BERT
was designed for. `DEDUCE`, `TRACE`, and `SYNTHESIZE` route to Sonnet
by default. `WEIGH` (authority-weighted judgment) routes to Opus.
`DEPTH: DEEP` and `DEPTH: EXHAUSTIVE` escalate any operation up the
tier ladder. A BERT-eligible operation with qualitative text in its
output schema is also escalated, because BERT cannot generate prose.

The compiler makes every one of these decisions before execution and
records them in the budget report.

## Contracts

Every AILang boundary clause compiles to a validator in the CONTRACT
DIVISION. `MUST_INCLUDE` becomes a semantic or keyword check against
the response. `CANNOT_INCLUDE` becomes a terminology-diff or
knowledge-depth scan. `OUTPUT_FORMAT` becomes a deterministic JSON
schema validation.

Each check has a severity:

- `CRITICAL` -- the transaction rolls back. Downstream operations that
  read its result do not run with a half-valid input.
- `MAJOR` -- the transaction retries with a reinforced prompt that
  names the violation. Up to `max_retries`; then it escalates to
  CRITICAL.
- `MINOR` -- the transaction commits with a logged warning.

This is the "transaction" in AIEX's transaction-based model: the
operation either commits cleanly, or its effect on program state is
erased. There is no "the LLM returned something weird and the
downstream step happened to work anyway." See
[chapter 7](07_output_contracts.md) for the full contract reference
and [chapter 12](12_dispatch_validate_commit.md) for the
dispatch/validate/commit lifecycle.

## Pre-Execution Budgets

Because every routing decision, every token-count heuristic, every
BERT retrieval top-k is resolved at compile time, AIEX can estimate
the cost and latency of a program before running it. The budget report
shows per-transaction cost and latency, the total, and a comparison
against an all-Opus baseline.

This changes how programs get written. Instead of running, getting a
bill, and guessing which operation was expensive, you inspect the
budget, tighten the `DEPTH` or `RETRIEVAL_BUDGET` on the operations
that dominate the cost, and recompile. The feedback loop is seconds,
not days. Chapter 4 is dedicated to reading these reports in detail.

## What's Next

Chapter 2 gets you from `git clone` to a running hello-world in about
ten minutes, including the fallback of compile-only mode when you do
not yet have an API key. From there,
[chapter 3](03_your_first_program.md) walks through writing a realistic
program incrementally, and
[chapter 4](04_reading_the_budget_report.md) opens up the budget
report so you can read every column of it with confidence.
