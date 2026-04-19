# Chapter 11: Multi-Model Routing

The single biggest cost lever in AIEX is that it does not send every
operation to Opus. An all-Opus baseline for the loan eligibility
example costs $0.36 per run; the routed plan costs $0.044 -- an 87.8%
reduction, achieved entirely by directing each operation to the
cheapest tier that can produce the required output. On the larger
`test_10` benchmark the same mechanism yields 87.8% savings for a
total of $0.0584 per run.

This chapter is about how AIEX makes those routing decisions. The
rationale, the default routing table, the escalation rules, and how
to read the result in a budget report.

## Why Tier Routing Works

The LLM Reasoning Router research found that different reasoning
types activate distinct neuron populations in frontier models, and
that the smallest model capable of producing a particular kind of
output is often far cheaper than the biggest one. Ranking and
classification do not need an Opus-class model; pure embedding
similarity handles them. Structured comparison between a handful of
dimensions can run on Haiku. Causal-chain tracing and rule
application need the mid-tier, typically Sonnet. Authority-weighted
judgment and exhaustive synthesis genuinely benefit from Opus.

The AIEX compiler encodes this as a mapping from AILang reasoning
type to model tier, then adjusts for DEPTH, RETRIEVAL_BUDGET, and
output schema before final dispatch.

## The Model Pool

There are four generative tiers plus one sentinel tier for code
execution. Each generative tier has one concrete model configured in
`DEFAULT_MODEL_POOL` with its own pricing:

| Tier                | Default model                           | Per 1K in / out |
|---------------------|-----------------------------------------|-----------------|
| BERT_CLASS          | sentence-transformers/all-MiniLM-L6-v2  | $0 / $0         |
| GENERATIVE_LIGHT    | claude-3-5-haiku-20241022               | $0.001 / $0.005 |
| GENERATIVE_STANDARD | claude-sonnet-4-20250514                | $0.003 / $0.015 |
| GENERATIVE_DEEP     | claude-opus-4-20250514                  | $0.015 / $0.075 |
| LOCAL_CODE          | (Python `exec`, no model)               | $0 / $0         |

BERT_CLASS is used for retrieval similarity; it does not generate
text. LOCAL_CODE is the sentinel used by `EXECUTE_CODE` blocks; see
[chapter 10](10_execute_code.md).

The pool lives in `src/aiex/models/routing.py` as a single dict. If
you swap model providers or negotiate different pricing, that is the
file to edit -- the compiler and budget estimator both read from it.
See [chapter 13](13_configuration.md) for a worked override.

## The Default Routing Table

Every AILang reasoning type has a base tier. The compiler looks up
the operation's `reasoning_type` in `DEFAULT_ROUTING_TABLE` and takes
that as the starting point:

| Reasoning type | Base tier           | Why                                                     |
|----------------|---------------------|---------------------------------------------------------|
| RETRIEVE       | BERT_CLASS          | Embedding similarity is sufficient; no generation needed |
| COMPARE        | GENERATIVE_LIGHT    | Structured grid comparison is a Haiku-sized job          |
| TRACE          | GENERATIVE_STANDARD | Causal chain following needs mid-tier reasoning          |
| DEDUCE         | GENERATIVE_STANDARD | Rule application needs mid-tier precision                |
| SYNTHESIZE     | GENERATIVE_STANDARD | Merging multiple inputs coherently                       |
| WEIGH          | GENERATIVE_STANDARD | Trade-off evaluation needs nuance                        |
| INTELLIGENTLY  | GENERATIVE_STANDARD | Safe default for untyped operations                      |

A note on SYNTHESIZE and WEIGH. The base tier in the routing table is
`GENERATIVE_STANDARD` for both, and escalation to Opus happens
through the DEPTH rule. In the loan eligibility program WEIGH and
SYNTHESIZE both land on Opus because the schema and context push
them there in practice; your own programs may stay on Sonnet if
DEPTH is `STANDARD` and the output is simple.

Each table entry also carries a temperature (0.0 for RETRIEVE, 0.4
for SYNTHESIZE), a template name, and a neuron budget -- the target
token count calibrated from the reasoning-router research. These are
not round numbers because they were empirically measured.

## Escalation Rules

The base tier is the starting point, not the final answer. The
resolver applies four escalation rules, each of which can only
promote the tier (never demote). The highest promotion wins.

**Rule 1: DEPTH: DEEP** forces the tier up to at least
`GENERATIVE_STANDARD`. A COMPARE operation that would normally route
to Haiku will run on Sonnet if the program declares `DEPTH: DEEP`.

**Rule 2: DEPTH: EXHAUSTIVE** forces the tier up to
`GENERATIVE_DEEP`. Anything the author flags as exhaustive goes to
Opus regardless of its base tier.

**Rule 3: RETRIEVAL_BUDGET: FULL** on a BERT-base operation promotes
it to `GENERATIVE_LIGHT`. Vector search still runs, but a generative
model synthesises the retrieved passages into a coherent answer --
BERT alone cannot produce prose.

**Rule 4: qualitative text in the output schema** on a BERT-base
operation also promotes it to `GENERATIVE_LIGHT`. If the
`OUTPUT_FORMAT` includes any `TEXT` or `QUALITATIVE` field, BERT
cannot fill it; a generative model is required. The compiler detects
this by walking the schema tree in `_output_has_qualitative_text`.

When any rule fires, the resulting `RoutingDecision` keeps the
original `base_tier` for reference and sets `escalated_to` to the new
tier. The `escalation_reason` string records why -- e.g.
`"depth=DEEP"`, `"full retrieval on BERT tier"`, or both joined with
a semicolon if multiple rules fired. This is what appears in logs
when you run with `verbose=True`.

## Reading a Routing Decision in the Budget Report

The budget report shows the **actually-routed** tier, not the base
tier. The `Model` column prints the concrete model ID that will
receive the dispatch:

```
TX                  Operation   Retrieval  Model                  Est. Cost
tx-2-hard-elig      DEDUCE      NONE       claude-sonnet-4-6      $0.0144
tx-3-soft-factors   WEIGH       NONE       claude-opus-4-6        $0.0135
tx-4-synth-decision SYNTHESIZE  NONE       claude-opus-4-6        $0.0162
```

If you want to see the escalation reason, inspect the plan
programmatically:

```python
from aiex.parser import parse_ailang
from aiex.compiler.compiler import compile_ailang

program = parse_ailang(open("loan.ailang").read())
plan = compile_ailang(program, "loan.ailang")

for tx in plan.transactions:
    if tx.routing.escalated_to:
        print(
            f"{tx.id}: {tx.routing.base_tier.value} -> "
            f"{tx.routing.escalated_to.value}  ({tx.routing.escalation_reason})"
        )
```

This is useful when the budget is higher than expected and you want
to know which operations got escalated and why. Often the answer is
a stray `DEPTH: DEEP` on an operation that did not need it.

## An End-to-End Example

Here is how the router handles a five-step pipeline. Assume the
program has these operations in order:

1. `RETRIEVE relevant_cases FOR case_archive` -- no depth, standard retrieval
2. `COMPARE cases AGAINST current_case` -- no depth, standard retrieval
3. `TRACE cause_chain FROM evidence` -- depth: standard
4. `WEIGH factors ON evidence` -- depth: deep
5. `SYNTHESIZE [cases, chain, factors] INTO verdict` -- depth: exhaustive

The router walks each:

1. RETRIEVE base is BERT_CLASS. No escalation rules fire. Routed to
   BERT. Cost: effectively $0.
2. COMPARE base is GENERATIVE_LIGHT. No escalation. Routed to Haiku.
3. TRACE base is GENERATIVE_STANDARD. No escalation. Routed to Sonnet.
4. WEIGH base is GENERATIVE_STANDARD, DEPTH: DEEP triggers rule 1,
   which requires at least STANDARD -- already satisfied. No change;
   routed to Sonnet. (If WEIGH's base had been LIGHT, it would have
   been promoted to STANDARD here.)
5. SYNTHESIZE base is GENERATIVE_STANDARD, DEPTH: EXHAUSTIVE triggers
   rule 2, promoting to GENERATIVE_DEEP. Routed to Opus, with
   `escalation_reason="depth=EXHAUSTIVE"`.

Five operations, three different tiers. If every operation had been
sent to Opus the total cost would be roughly 5x higher. The
compiler's routing table and the four escalation rules produce the
optimised plan in one pass.

## BERT Integration

BERT-class routing is interesting because it is free when it runs.
AIEX uses `sentence-transformers` under the hood when available, via
the `bert` optional dependency:

```bash
pip install -e ".[bert]"
```

This pulls in `sentence-transformers` and its torch backend. Once
installed, BERT does two things for AIEX:

- Phase-1 retrieval. RETRIEVE operations and any reasoning operation
  with a non-NONE `RETRIEVAL_BUDGET` can run a vector search to pull
  grounding context into the user message before the LLM call.
- `CANNOT_INCLUDE` terminology-diff validation. When BERT is
  available, the contract validator uses cosine similarity to detect
  novel terms in the response that were not present in the input --
  the primary enforcement mechanism for `RETRIEVAL_BUDGET: NONE`.

If `sentence-transformers` is not installed, AIEX degrades
gracefully. `BERT_AVAILABLE` becomes `False` at import time, and the
validator falls back to keyword matching with a higher false-positive
rate. This is tracked as known issue P3 -- programs still run, but
the semantic precision of CANNOT_INCLUDE checks drops.

Install the `bert` extra if you care about accurate CANNOT_INCLUDE
enforcement, or if your programs use non-NONE retrieval budgets. Skip
it if you are doing compile-only exploration or running purely
closed-book reasoning and only need the dispatch path.

## Overriding the Routing

The model pool and routing table are both plain Python dicts in
`src/aiex/models/routing.py`. To point `GENERATIVE_STANDARD` at a
different model, edit `DEFAULT_MODEL_POOL`. To change the base tier
for a reasoning type, edit `DEFAULT_ROUTING_TABLE`. There is no
separate configuration file today; the source is the configuration.
This is documented in more detail in
[chapter 13](13_configuration.md).

Once the router has chosen a tier and the budget report has been
printed, the runtime takes over. The next chapter walks through what
happens after dispatch: the contract validation, the retry loop, and
the commit/rollback decision that turns a raw LLM response into a
committed transaction in program state.
