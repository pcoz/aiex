# Chapter 8 -- Pipelines: THEN Chains and REASONING_CONTEXT

Most useful AILang programs are not single operations. They are sequences: retrieve some facts, deduce from them, then weigh the result against soft factors, then synthesise a final decision. Composing these steps matters for two reasons. First, the data flow between operations has to be explicit enough that the compiler can detect dependencies and the runtime can route correctly. Second, the *way* information flows from step N to step N+1 has a large cost impact: feeding the entire output of step N into step N+1's prompt is expensive and often wasteful.

AILang offers two composition constructs: **THEN chains** for simple linear flows, and **REASONING_CONTEXT** blocks for multi-step pipelines with shared accumulator state. This chapter walks through both, starting with the simpler one.

## THEN chains

A THEN chain links operations sequentially with implicit data flow. The result of step N is available to step N+1 via the `FROM RESULT` pseudo-source.

**Syntax** (verified against `parse_operation` in [`parser.py`](../../src/aiex/parser/parser.py), lines 484-518):

```ailang
OPERATION_A name_a FROM source WITH:
    ...
THEN OPERATION_B name_b FROM RESULT WITH:
    ...
END
```

The critical parser detail is that a THEN chain uses *one* closing `END` for the entire chain, not one per step. The parser consumes the first operation up to but not including the END, sees the `THEN` keyword, consumes the next operation, and keeps going until it sees END. This is why the `_parse_single_operation` helpers all deliberately stop before END -- the chain logic decides when to close.

**What AIEX does with a THEN chain:**

1. Each operation compiles to its own transaction (see AIEX spec section 7.2).
2. The transactions are linked: step 2 declares `DEPENDS-ON step-1` in the AIEX plan.
3. Step 1 commits its response to the named result variable *and* to `CURRENT-RESULT`.
4. Step 2 sees `FROM RESULT` in its source specification and the runtime wires `CURRENT-RESULT` in as step 2's input.
5. Each step is validated against its own contract independently. If step 1 rolls back, the chain halts; step 2 never runs.

You don't manually pass variables between steps, and you don't write glue code to marshal the JSON of step 1 into the user message of step 2. The runtime does that.

**A three-step THEN chain** (adapted from `test_12` Scenario B):

```ailang
DEDUCE antitrust_risk FROM CONTEXT AND merger_data WITH:
    RETRIEVAL_BUDGET: NONE
    DEPTH: THOROUGH
    EMIT_DIGEST: TRUE
    MUST_INCLUDE: [doj_analysis, eu_dg_comp_analysis, precedent_application]
THEN COMPARE remedy_options AGAINST feasibility FROM RESULT WITH:
    RETRIEVAL_BUDGET: NONE
    DIMENSIONS: [regulatory_acceptance_likelihood, business_impact, timeline]
    OUTPUT_FORMAT: {
        remedies: LIST OF OBJECT {
            remedy: TEXT,
            regulatory_acceptance: TEXT,
            business_impact: TEXT
        },
        recommended_approach: TEXT
    }
END
```

The DEDUCE step produces an antitrust-risk analysis; the COMPARE step takes that analysis as input (`FROM RESULT`) and ranks remedy options against feasibility. One END closes both.

**THEN chain limits.** The AILang spec caps THEN chains at three operations. Beyond that you should use a REASONING_CONTEXT block. The reason is auditability: a five-step THEN chain becomes an unreadable run-on sentence, and the runtime's dependency graph gets hard to reason about.

## REASONING_CONTEXT blocks

A REASONING_CONTEXT is a named scope that groups related operations and gives them a shared digest accumulator. Operations inside the block automatically emit digests (structured summaries) which later operations can read via `FROM CONTEXT`.

**Syntax** (verified against `parse_reasoning_context_block`):

```ailang
BEGIN REASONING_CONTEXT context_name:
    OPERATION_A ...  END
    OPERATION_B ...  END
    OPERATION_C FROM CONTEXT ...  END
END REASONING_CONTEXT
```

Two things distinguish this from a THEN chain. First, the closing delimiter is the two-token sequence `END REASONING_CONTEXT` -- the parser looks ahead one token on seeing `END` to decide whether it closes the context or the enclosed operation. Second, every operation inside the block *implicitly* has `EMIT_DIGEST: TRUE`, per section 8.11 of the AILang spec.

### The digest pattern

Here is the motivation. Suppose step 1 produces 2,000 tokens of structured output and step 4 needs to know the high-level conclusion of step 1. If you pipe the full output of step 1 into step 4's prompt, you pay for those 2,000 tokens as input to step 4. If step 4 reads the output of three prior steps, you pay for 6,000 tokens of input on every run.

The digest pattern solves this. Each operation in a REASONING_CONTEXT emits a short, structured summary of its result (capped at 100 tokens by the default digest contract). The accumulator collects digests; downstream operations read the accumulator, not the full outputs. Step 4 sees three 100-token digests instead of three 2,000-token outputs -- a 20× reduction in input tokens without losing the signal.

**A concrete pipeline** (adapted from AILang spec section 10.5, `migration_decision`):

```ailang
BEGIN REASONING_CONTEXT migration_analysis:

    # Step 1: Retrieve cloud platform capabilities
    # Digest automatically produced and appended to context
    RETRIEVE cloud_platform_capabilities FOR target_platforms WITH:
        DEPTH: STANDARD
        OUTPUT_FORMAT: {
            platforms: LIST OF OBJECT { name: TEXT, capabilities: LIST OF TEXT, pricing: TEXT }
        }
    END

    # Step 2: Compare options -- reads accumulated context (the RETRIEVE digest)
    COMPARE migration_options AGAINST requirements WITH:
        DIMENSIONS: [cost, performance, migration_effort, vendor_lock_in]
        OUTPUT_FORMAT: {
            ranking: LIST OF OBJECT { option: TEXT, rank: NUMBER, rationale: TEXT },
            trade_offs: LIST OF TEXT
        }
    END

    # Step 3: Weigh expert recommendations -- now has two digests in context
    WEIGH architect_recommendations ON migration_strategy WITH:
        SOURCES: [senior_architect_review, vendor_consultants, industry_analysts]
        OUTPUT_FORMAT: {
            recommended_approach: TEXT,
            confidence_level: TEXT IN ["high", "moderate", "low"],
            dissenting_views: LIST OF TEXT
        }
    END

    # Step 4: Synthesize from all accumulated context
    # FROM CONTEXT reads all three prior digests (not the full outputs)
    SYNTHESIZE migration_decision FROM CONTEXT WITH:
        MUST_INCLUDE: [integrated_rationale, risk_assessment, implementation_roadmap]
        OUTPUT_FORMAT: {
            decision: TEXT,
            rationale: TEXT,
            implementation_phases: LIST OF TEXT,
            risk_mitigation: LIST OF TEXT,
            estimated_timeline: TEXT
        }
    END

END REASONING_CONTEXT
```

Four operations, one accumulator. The `SYNTHESIZE migration_decision FROM CONTEXT` step receives three digests -- one each from the RETRIEVE, COMPARE, and WEIGH -- as its input. It never sees the raw 2000-token outputs of those steps; it sees the 100-token structured summaries. The `migration_decision` variable persists after `END REASONING_CONTEXT`; the digest accumulator is scoped and disposed.

### FROM CONTEXT variants

The parser supports three forms of context sourcing (see `_parse_deduce_trace`, lines 606-614):

- `FROM RESULT` -- input is the immediately preceding step's committed result (used inside THEN chains, not REASONING_CONTEXT).
- `FROM CONTEXT` -- input is the current REASONING_CONTEXT's accumulator (all digests to date).
- `FROM CONTEXT AND extra_source` -- accumulator plus one additional named variable.

The `AND extra_source` form is useful when the operation needs the accumulator *plus* a fresh variable that wasn't produced inside the context. The `test_12` merger pipeline uses this: `DEDUCE antitrust_risk FROM CONTEXT AND merger_data` -- the DEDUCE sees the digest of the prior RETRIEVE plus the raw `merger_data` variable.

### THEN chains inside REASONING_CONTEXT

One interaction worth knowing: when a THEN chain appears inside a REASONING_CONTEXT, the *entire chain* produces one digest (from the last step), not one per operation in the chain. This is Appendix D.6 of the AILang spec. Rationale: the chain is semantically one unit of work, and fragmenting it into multiple digests would pollute the accumulator.

### Digest format

The default digest contract (per AIEX spec section 6) validates digests against a small schema:

```aiex
{
    reasoning_type: TEXT,
    confidence: TEXT IN ["high", "moderate", "low"],
    key_finding: TEXT,
    digest: TEXT MAX-TOKENS 100
}
```

A digest that exceeds 100 tokens is truncated and committed (severity MINOR). This bound is what keeps the pattern cheap: downstream operations always see compressed inputs.

## THEN vs REASONING_CONTEXT: which to use

The two constructs overlap but are optimised for different shapes of pipeline.

**Use THEN chains when:**
- the pipeline is linear and short (2-3 operations)
- step N+1's *only* input is step N's output
- you want the syntactic simplicity of one END for the whole unit

**Use REASONING_CONTEXT when:**
- the pipeline has four or more operations
- a later step needs to read summaries of multiple earlier steps, not just the most recent one
- the operations are conceptually related and benefit from a named scope
- you want digest-size control for cost efficiency

The two constructs compose: you can have THEN chains inside a REASONING_CONTEXT (the chain emits one digest for the whole chain). You cannot nest REASONING_CONTEXT blocks arbitrarily inside a THEN chain -- THEN chains are a single-operation construct that doesn't have room for a nested block.

A worked example of the split: the `test_12` Scenario B pipeline uses `REASONING_CONTEXT merger_analysis` as the outer structure (four operations sharing an accumulator), and inside it uses a two-step `DEDUCE ... THEN COMPARE ...` chain for the antitrust-risk analysis. The outer reasoning-context accumulates three digests (one from the RETRIEVE, one from the THEN-chain as a whole, one from the TRACE), and the final WEIGH reads the accumulator via `FROM CONTEXT`. That is the full-strength pattern: THEN chains for tight local sequences, REASONING_CONTEXT for the overall pipeline.

The next chapter covers the last major AILang construct that shapes operation behaviour: [REALITY_CONTEXT for worldview scoping](09_reality_context.md).
