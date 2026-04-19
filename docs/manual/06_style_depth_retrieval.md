# Chapter 6 -- Style, Depth, and Retrieval Budget

Every reasoning operation you saw in [chapter 5](05_typed_reasoning_operations.md) has a *shape*: what verb, what inputs, what output schema. But shape alone is not enough. Two DEDUCE operations can differ by a factor of six in cost depending on how much analysis you asked for, how much external knowledge you let the model draw on, and what thinking style you specified. AILang gives you three orthogonal modifier axes for these choices -- **style**, **depth**, and **retrieval budget** -- and they compose cleanly: any operation can take any combination of the three, and AIEX handles the arithmetic.

This chapter goes through the axes from lightest to heaviest. We start with style (a prompt-prefix tweak), then depth (a token-budget multiplier that can also promote you to a bigger model), then retrieval budget (which controls how much outside knowledge the model may pull in). At the end is the "60-cell matrix" -- the combinatorial space all three axes open up.

## Style: INTELLIGENTLY, CREATIVELY, ADAPTIVELY, CONTEXTUALLY

Style is the cheapest axis. It costs you nothing at dispatch time -- it only changes the prompt prefix composed into the system message. But it does change *how* the model thinks, which shows up in output quality.

**INTELLIGENTLY** is the default; it adds no prefix. Use it (or omit style entirely) when you want the model's standard analytical processing.

**CREATIVELY** adds a prefix telling the model to consider novel hypotheses and unconventional connections, and nudges the dispatch temperature upward by +0.15 (per the AIEX spec's style table). Use it on DEDUCE when you want the model to notice loopholes, or on TRACE when you want it to surface non-obvious causal paths. Avoid it on anything that needs to be precise -- don't use CREATIVELY on a compliance check.

```ailang
CREATIVELY DEDUCE loopholes FROM [tax_code, filing] WITH:
    DEPTH: THOROUGH
END
```

**ADAPTIVELY** is the interesting one. It does two things at once. First, it adds a prompt prefix telling the model to adjust its depth to the strength of evidence at each step. Second -- and this is what makes it special -- it sets a runtime flag (`DYNAMIC-DEPTH: TRUE` in the AIEX routing table) that allows the engine to adjust depth *per step* within declared bounds. Appendix D.2 of the AILang spec formalises the bounds: ADAPTIVELY combined with `DEPTH: THOROUGH` means the engine may use anywhere from STANDARD (on steps with thin evidence) to DEEP (on steps with rich evidence), but never below STANDARD or above DEEP.

ADAPTIVELY has a matching interaction with RETRIEVAL_BUDGET (Appendix D.8): when `ADAPTIVELY + RETRIEVAL_BUDGET: MINIMAL`, the engine may temporarily expand retrieval to STANDARD for a single step if that step genuinely needs it, logging a warning for auditability.

```ailang
ADAPTIVELY TRACE recall_root_cause FROM pharmaceutical_incident WITH:
    DEPTH: THOROUGH
    RETRIEVAL_BUDGET: MINIMAL
    DIRECTION: BACKWARD
END
```

**CONTEXTUALLY** adds a prefix telling the model to use all available program state (prior variables, prior results, any active REALITY_CONTEXT). This matters more inside REASONING_CONTEXT blocks where the runtime has accumulated digests to inject -- CONTEXTUALLY tells the model to actually *use* them rather than solve in isolation.

The parser (see `_parse_single_operation` in [`parser.py`](../../src/aiex/parser/parser.py)) accepts any of CREATIVELY, ADAPTIVELY, CONTEXTUALLY as a prefix before any of the six reasoning verbs. INTELLIGENTLY is handled specially because it can act both as a style modifier and as the verb itself.

## Depth: SHALLOW, STANDARD, THOROUGH, DEEP, EXHAUSTIVE

Depth controls three runtime dimensions at once: how many tokens the model is allowed to produce, how many refinement passes the engine will run, and -- critically -- **which model tier the operation actually dispatches to**.

Here is the depth table from [`routing.py`](../../src/aiex/models/routing.py):

| Depth | Token multiplier | Max passes | Prompt suffix |
|---|---|---|---|
| SHALLOW | 0.5× | 1 | "Provide a brief, high-level answer." |
| STANDARD | 1.0× | 1 | (none) |
| THOROUGH | 1.5× | 2 | "Be thorough and cover edge cases." |
| DEEP | 2.0× | 3 | "Provide deep analysis with supporting reasoning." |
| EXHAUSTIVE | 3.0× | 5 | "Perform an exhaustive analysis. Consider every angle..." |

SHALLOW halves your token cost at the price of quality. Use it when you genuinely just want a sanity check. STANDARD is the default and what most production code should use. THOROUGH buys you a refinement pass -- the runtime prompts the model to refine its first answer.

The escalation rule in `resolve_routing()` is where depth gets expensive: **DEEP promotes at least to `GENERATIVE_STANDARD` (Sonnet), EXHAUSTIVE promotes to `GENERATIVE_DEEP` (Opus), regardless of what the reasoning type's base tier was.** This means a `COMPARE ... WITH DEPTH: EXHAUSTIVE` doesn't run on Haiku -- it runs on Opus. Use EXHAUSTIVE only when the analysis justifies the bill.

The token multiplier compounds with the operation's base `MAX-OUTPUT-TOKENS`. A DEDUCE at `DEPTH: THOROUGH` gets 1536 × 1.5 = 2304 output tokens; at `DEPTH: DEEP` it gets 3072 tokens on Sonnet; at `DEPTH: EXHAUSTIVE` it gets 6144 tokens on Opus, and Opus tokens cost roughly 25× what Haiku tokens cost. The difference between "this is a Sonnet DEDUCE at STANDARD depth" and "this is an Opus DEDUCE at EXHAUSTIVE depth" is the difference between pennies and dollars per transaction.

## Retrieval budget: NONE, MINIMAL, STANDARD, FULL

The retrieval budget controls how much external knowledge the model may bring in beyond the data you explicitly passed. It is the most programmer-hostile-sounding axis, but it is what makes AILang auditable: you declare up front whether the model is allowed to "just know things" about the world, or whether it must work from the premises you gave it.

Here is the budget table from [`routing.py`](../../src/aiex/models/routing.py):

| Budget | Retrieval neurons | top-k | Vector search | Prompt modifier |
|---|---|---|---|---|
| NONE | 0 | -- | off | "Do not consult any external sources." |
| MINIMAL | 200 | 2 | on, 2 results | "Consult at most 1-2 sources if absolutely necessary." |
| STANDARD | 500 | 5 | on, 5 results | "Consult relevant sources as needed." |
| FULL | 1000 | 20 | on, 20 results | "Exhaustively consult all available sources." |

**NONE** is closed-book mode. The AIEX runtime enforces it in two places: the prompt modifier tells the LLM not to recall, and the CONTRACT DIVISION's `RETRIEVAL-COMPLIANCE SECTION` scans the response for external knowledge leakage using a `TERMINOLOGY-DIFF` detection method -- it diffs the response's terminology against the input baseline. Use NONE when the whole point of the operation is to test "can this reasoning be done from these premises alone?" The loan eligibility example in [chapter 5](05_typed_reasoning_operations.md) sets every DEDUCE/WEIGH/SYNTHESIZE to `RETRIEVAL_BUDGET: NONE` because eligibility policy must be applied from the stated rules, not from whatever the model thinks about loan underwriting.

**MINIMAL** activates roughly 200 retrieval neurons -- enough to let the model resolve terminology and basic definitions, but not enough to inject detailed external context. This is the default for operations that need the LLM to understand jargon (e.g. "what exactly does 'material injury' mean in US trade law?") without actually researching the problem from scratch.

**STANDARD** activates the full ~865 retrieval base neurons; roughly 5 vector-search results are injected. This is the right default when you *want* the model to draw on its training. A RETRIEVE operation at STANDARD is roughly "give me what you know about this."

**FULL** is STANDARD plus breadth: 20 top-k results, generative synthesis of retrieved passages. FULL on a BERT-tier operation triggers the escalation rule in `resolve_routing()` -- you cannot do FULL on bare BERT because you need a generative model to synthesise 20 passages.

**Syntactic shortcut:** `WITHOUT_RETRIEVAL` is recognised by the parser (see line 1077 of [`parser.py`](../../src/aiex/parser/parser.py)) and sets `RETRIEVAL_BUDGET` to `NONE`. It is literally sugar for `RETRIEVAL_BUDGET: NONE`.

Here is all three RETRIEVAL_BUDGET levels side by side, from `test_10_retrieval_budget.ailang`:

```ailang
DEDUCE tariff_applicability_none FROM [premise_1, ..., premise_5] WITH:
    RETRIEVAL_BUDGET: NONE
    APPLY_TO: case_facts
    DEPTH: STANDARD
    ...
END

DEDUCE tariff_applicability_minimal FROM [premise_1, ..., premise_5] WITH:
    RETRIEVAL_BUDGET: MINIMAL
    APPLY_TO: case_facts
    DEPTH: STANDARD
    ...
END

DEDUCE tariff_applicability_standard FROM [premise_1, ..., premise_5] WITH:
    RETRIEVAL_BUDGET: STANDARD
    APPLY_TO: case_facts
    DEPTH: STANDARD
    ...
END
```

These three operations are identical except for the retrieval budget. The compiler assigns them neuron budgets of 335, 535, and 1200 respectively (base 1200 for DEDUCE scaled by how many retrieval neurons activate). Across the four-transaction `test_10` program (three DEDUCE + one COMPARE), the budget report showed:

```
TX-1 (DEDUCE, NONE)     -> Sonnet  335 neurons  $0.016
TX-2 (DEDUCE, MINIMAL)  -> Sonnet  535 neurons  $0.017
TX-3 (DEDUCE, STANDARD) -> Sonnet  1200 neurons $0.020
TX-4 (COMPARE)          -> Haiku   1050 neurons $0.006

Total:    $0.058
Baseline: $0.480 (all-Opus)
Savings:  87.8%
```

The 87.8% figure is the payoff: routing three DEDUCEs to Sonnet and one COMPARE to Haiku, with graduated retrieval budgets, costs $0.058 for a program that would cost $0.480 run as generic INTELLIGENTLY operations on Opus.

## The 60-cell matrix

Style, depth, and retrieval budget are orthogonal. Any combination is legal. Multiply the axes out:

- **6 reasoning operations** (RETRIEVE / COMPARE / TRACE / DEDUCE / SYNTHESIZE / WEIGH)
- **4 styles** (INTELLIGENTLY / CREATIVELY / ADAPTIVELY / CONTEXTUALLY)
- **5 depths** (SHALLOW / STANDARD / THOROUGH / DEEP / EXHAUSTIVE)
- **4 retrieval budgets** (NONE / MINIMAL / STANDARD / FULL)

= **480 distinct combinations**, each with its own compiled routing decision, its own cost, and its own prompt template.

That does not mean 480 wildly different operations. Most programs use a handful of combinations -- `DEDUCE STANDARD NONE`, `WEIGH STANDARD NONE`, `SYNTHESIZE STANDARD NONE` make up the bulk of a typical business pipeline. But the matrix exists, and every cell in it is addressable by one line of AILang source. That is the point of keeping the axes orthogonal: you compose a routing decision the way you compose a function call, not by hand-writing middleware for each combination.

Three worked permutations to make the point concrete:

```ailang
# Cell: COMPARE + INTELLIGENTLY + SHALLOW + MINIMAL
# Fastest and cheapest useful comparison; Haiku, 0.5x tokens, 200 retrieval neurons.
COMPARE [product_a, product_b] AGAINST price_features WITH:
    DEPTH: SHALLOW
    RETRIEVAL_BUDGET: MINIMAL
END

# Cell: TRACE + ADAPTIVELY + THOROUGH + MINIMAL
# The pharmaceutical-recall pattern: Sonnet, dynamic depth STANDARD-to-DEEP,
# retrieval may expand to STANDARD for evidence-thin steps.
ADAPTIVELY TRACE recall_root_cause FROM incident WITH:
    DEPTH: THOROUGH
    RETRIEVAL_BUDGET: MINIMAL
    DIRECTION: BACKWARD
END

# Cell: WEIGH + CONTEXTUALLY + DEEP + NONE
# Final-decision pattern: Opus (DEEP escalates WEIGH from Sonnet to Opus),
# uses all accumulated program state, closed-book reasoning.
CONTEXTUALLY WEIGH merger_recommendation ON go_no_go FROM CONTEXT WITH:
    DEPTH: DEEP
    RETRIEVAL_BUDGET: NONE
    SOURCES: [CONTEXT, merger_data.financial_data]
END
```

Three operations, three different cells of the matrix, three different model tiers, three different cost profiles -- all expressed without any imperative dispatch code. The AIEX compiler does the routing arithmetic; you just declare what you want.

The next chapter covers the fourth axis that makes all of this safe in production: [output contracts](07_output_contracts.md).
