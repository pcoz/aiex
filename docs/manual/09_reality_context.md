# Chapter 9 -- REALITY_CONTEXT: Worldview Scoping

The same loan-eligibility program, run by a conservative compliance officer and by a growth-focused product manager, should not produce identical decisions. Not because either is wrong, but because they reason from different *worldviews*: different priorities, different tolerances for risk, different defaults about what counts as evidence. Hard-coding these differences into prompt strings scattered through the program makes them invisible to reviewers. AILang's `REALITY_CONTEXT` construct makes a worldview a first-class declaration, and AIEX enforces it at routing time.

This chapter covers the problem REALITY_CONTEXT solves, the syntax for defining and applying one, how CONSTRAIN and SUPPRESS differ, and the audit value of declarative worldviews.

## The problem: implicit worldviews in prompts

Consider two operations:

```ailang
# Version A
WEIGH investment_recommendation ON portfolio WITH:
    SOURCES: [analyst_reports, market_data, risk_models]
END

# Version B -- same program, different wording
WEIGH investment_recommendation ON portfolio WITH:
    SOURCES: [analyst_reports, market_data, risk_models, growth_forecasts]
END
```

Version B adds `growth_forecasts` to the source list. That single change reflects a policy position: growth projections should count when weighing an investment. But the reviewer reading version B has to infer the policy from the source list. There is no named, auditable place in the program that says "this organisation weighs growth forecasts." The worldview is implicit, encoded in editorial choices.

REALITY_CONTEXT makes it explicit. A worldview gets a name, a description, a preferred reasoning profile, and a set of assumptions. Operations run *under* a worldview inherit those properties automatically.

## Defining a reality context

**Syntax** (verified against `parse_reality_context_def` in [`parser.py`](../../src/aiex/parser/parser.py), lines 1371-1432):

```ailang
DEFINE REALITY_CONTEXT context_name:
    WORLDVIEW: "short description of the worldview"
    REASONING_PROFILE:
        PRIMARY: TRACE
        SECONDARY: DEDUCE
        CONSTRAIN: WEIGH TO "limit_description"
        SUPPRESS: [SYNTHESIZE]
    END_PROFILE
    ASSUMPTIONS: [list_of_identifiers]
    VALUE_SYSTEM: [list_of_identifiers]
END_CONTEXT
```

The parser accepts `WORLDVIEW`, `REASONING_PROFILE`, `ASSUMPTIONS`, and `VALUE_SYSTEM` as top-level keys; all are optional. The REASONING_PROFILE itself is a nested block with `PRIMARY`, `SECONDARY`, `CONSTRAIN`, and `SUPPRESS` (all optional), closed by `END_PROFILE`.

**A real example**, from `test_12` Scenario A:

```ailang
DEFINE REALITY_CONTEXT pharmaceutical_safety:
    WORLDVIEW: "Patient safety is paramount; every causal link must be evidence-grounded"
    REASONING_PROFILE:
        PRIMARY: TRACE
        SECONDARY: DEDUCE
        CONSTRAIN: WEIGH TO "published_clinical_data_only"
        SUPPRESS: [SYNTHESIZE]
    END_PROFILE
    ASSUMPTIONS: [precautionary_principle, evidence_hierarchy]
    VALUE_SYSTEM: [patient_safety, regulatory_compliance, scientific_rigor]
END_CONTEXT
```

This declares a worldview -- "patient safety is paramount" -- with a reasoning profile that says: prefer causal tracing, fall back to deduction, limit any WEIGH operation to published clinical data only, and never invoke SYNTHESIZE. The assumptions and value system are machine-readable annotations that the runtime injects into the system prompt for every operation scoped under this context.

## Applying a reality context

Defining a context does nothing on its own. To scope operations under it, use a `WITH REALITY_CONTEXT` block:

```ailang
WITH REALITY_CONTEXT pharmaceutical_safety:
    ADAPTIVELY TRACE recall_root_cause FROM pharmaceutical_incident WITH:
        DEPTH: THOROUGH
        RETRIEVAL_BUDGET: MINIMAL
        DIRECTION: BACKWARD
        MUST_INCLUDE: [primary_root_cause, contributing_factors, evidence_at_each_step]
        OUTPUT_FORMAT: { ... }
    END
END_WITH
```

The parser (`parse_with_context_block`) consumes `WITH REALITY_CONTEXT name:`, parses the body of operations, and closes on `END_WITH`. Any operation inside the block inherits the context's profile: the runtime attaches the context's `CONSTRAIN` and `SUPPRESS` rules as implicit contracts on every transaction in the block, and composes the worldview description, assumptions, and value system into the system prompt.

The AIEX runtime represents this at the execution layer as (per the AIEX spec section 7.4):

```aiex
WITH-CONTEXT pharmaceutical-safety.
    APPLY-CONTRACT suppress-enforcement TO ALL-TRANSACTIONS.
    APPLY-CONTRACT constrain-enforcement TO ALL-TRANSACTIONS.
    [... operations ...]
END-WITH-CONTEXT.
```

Every transaction in the block has two additional contracts attached: `suppress-enforcement` (scans the response to confirm no suppressed reasoning type was used) and `constrain-enforcement` (validates that constrained operations stay within their declared boundary).

## CONSTRAIN vs SUPPRESS

This is the distinction that makes REALITY_CONTEXT nuanced rather than blunt. Both keywords restrict what reasoning the engine may do, but they do so with different shapes.

**SUPPRESS is binary.** `SUPPRESS: [SYNTHESIZE]` means: within this context, the engine must never invoke SYNTHESIZE. Not "try not to," not "weigh the alternatives" -- never. The enforcement mechanism is the contract `suppress-enforcement`, which uses `SEMANTIC-CLASSIFICATION` to identify what reasoning type was actually used in the response, and rolls back the transaction (severity CRITICAL) if a suppressed type was detected. The rollback re-dispatches with a reinforced prompt: "CRITICAL: Do NOT use SYNTHESIZE reasoning. This reasoning type is suppressed in the current context."

Use SUPPRESS when a reasoning type is inappropriate for the domain. In the pharmaceutical_safety context, SYNTHESIZE is suppressed because synthesising a recommendation from partial evidence could produce unsafe clinical guidance. Even if a downstream prompt would naturally invite SYNTHESIZE -- "combine these three analyses into a final recommendation" -- the context blocks it.

**CONSTRAIN is graduated.** `CONSTRAIN: WEIGH TO "published_clinical_data_only"` means: WEIGH operations may still run, but they must restrict their source consideration to the named category. The enforcement mechanism is `constrain-enforcement`, which uses `SEMANTIC-BOUNDARY-CHECK` to verify the response's WEIGH reasoning stayed within the declared limit. Violations are severity MAJOR -- retry with reinforcement, up to max retries, then escalate to CRITICAL.

Use CONSTRAIN when a reasoning type is appropriate but its source or scope needs boundaries. WEIGH is the right operation for evaluating clinical evidence; you just want to constrain what counts as evidence.

**Conceptually:** SUPPRESS is a scalpel that removes a whole operation type; CONSTRAIN is a fence that limits where an operation may graze.

## Walking through pharmaceutical_safety

Here is how the `test_12` Scenario A pharmaceutical recall runs under the context. The incident is a drug batch recall: three patients had severe adverse reactions to a cardiovascular drug because the new API supplier's smaller particle size produced faster dissolution, giving a higher effective dose than the QC assay measured.

```ailang
WITH REALITY_CONTEXT pharmaceutical_safety:
    ADAPTIVELY TRACE recall_root_cause FROM pharmaceutical_incident WITH:
        DEPTH: THOROUGH
        RETRIEVAL_BUDGET: MINIMAL
        DIRECTION: BACKWARD
        MUST_INCLUDE: [
            primary_root_cause,
            contributing_factors,
            secondary_causes,
            evidence_at_each_step,
            quality_system_gaps
        ]
        CANNOT_INCLUDE: [speculation_beyond_evidence, unsourced_claims]
        OUTPUT_FORMAT: {
            primary_root_cause: TEXT,
            causal_chain: LIST OF OBJECT { ... },
            synthesize_suppressed: BOOLEAN,
            ...
        }
    END
END_WITH
```

When AIEX compiles this, it does the following. The `TRACE` operation is the primary reasoning type of the profile, so routing prefers it -- no escalation needed. The `CONSTRAIN: WEIGH TO "published_clinical_data_only"` rule applies to any WEIGH that *might* fire (none does here, but if the adaptive engine had attempted to weigh sources, it would have been fenced). The `SUPPRESS: [SYNTHESIZE]` rule means the engine cannot, even in an adaptive-depth refinement pass, decide to synthesise a recommendation -- if it tries, the suppress-enforcement contract rolls back the transaction.

The `synthesize_suppressed: BOOLEAN` field in the output schema is a declarative way of asking the response to confirm that suppression was respected. A `false` value here would trigger a contract failure on its own.

Contrast with the same fact pattern under a hypothetical `growth_focused_product_development` context: no SUPPRESS on SYNTHESIZE, perhaps CONSTRAIN on TRACE instead. The same TRACE-leading code would still run, but a downstream step could synthesise aggressive recommendations for accelerating API-supplier qualification. Same program source, different reasoning profile, different decision shape -- and the reviewer can see exactly which worldview drove each run by reading one `DEFINE REALITY_CONTEXT` block.

## The audit angle

This is the reason REALITY_CONTEXT exists as a language construct rather than as a "put this paragraph at the top of your prompts" convention.

In a conventional LLM pipeline, if you wanted to ask "under what worldview was this decision made?" you would have to reconstruct the answer from evidence: read the prompt strings that were injected into each step, look at the system messages, cross-reference which helper functions called which, and form a hypothesis. The worldview is never written down; it is implicit in the editorial choices of whoever wrote the prompts.

In an AILang program, the question has a one-line answer: the decision was made under `REALITY_CONTEXT pharmaceutical_safety`, which is defined on line 41 of the source, which has these four declared properties, and which attached these two enforcement contracts to every operation in the block. The worldview is a named object. Changing it means editing one definition, not hunting through prompt strings. Comparing two runs of the same program under different worldviews means running the program twice with different `WITH REALITY_CONTEXT` blocks and diffing the outputs.

This matters most in regulated domains. A compliance officer reviewing a loan decision wants to confirm that the decision was made under the organisation's declared underwriting worldview, not under whatever defaults happened to be in effect when the program ran. A `REALITY_CONTEXT conservative_underwriting` definition at the top of the source file, plus a `WITH REALITY_CONTEXT conservative_underwriting: ... END_WITH` block around the decision pipeline, gives the compliance officer exactly the audit artefact they need: a named, declarative, reviewable worldview that shaped every routing decision the runtime made.

The worldview stops being an implicit assumption buried in prompt wording, and becomes a first-class question a reviewer can ask and a programmer can answer by pointing at a block of source.

## What you have now

With the five chapters of part II (this chapter and [chapters 5](05_typed_reasoning_operations.md)-[8](08_pipelines.md)) you have the core of AILang: typed operations that route to the right model tier, orthogonal modifiers that compose freely, output contracts that make the LLM's output safe for downstream code, pipeline constructs that manage data flow between operations cheaply, and reality contexts that scope operations under declared worldviews. The subsequent chapters cover how AIEX executes all of this: the budget report that runs before any LLM call, the routing tables that drive dispatch, and the debugging surface when something goes wrong.
