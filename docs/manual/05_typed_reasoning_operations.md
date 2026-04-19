# Chapter 5 -- Typed Reasoning Operations

AILang has exactly six typed reasoning verbs. Every one of them is an AI operation you could have written as "call the LLM and hope," but by declaring *which kind* of reasoning you want, you tell AIEX which model tier to dispatch to, which system-prompt template to compose, what temperature to use, and what a well-formed output looks like. That declaration is the entire value proposition: a typed operation routes to the cheapest model capable of the work, a wrong-type operation wastes money.

This chapter walks through each verb in order of computational weight -- RETRIEVE first (cheapest), WEIGH last (most expensive) -- with the syntax AIEX actually parses, the default tier it dispatches to, and a small example drawn from real test programs. At the end of the chapter you will find a decision tree for picking the right verb.

## RETRIEVE -- factual lookup

**Purpose:** direct knowledge lookup with minimal inference. You have a question whose answer is a fact, not a judgement, and you want it as structured data.

**When to use it:** looking up regulatory texts, fetching product specifications, retrieving the list of prior cases matching a pattern. Anything where the right output is "here is what exists," not "here is what I conclude."

**When *not* to use it:** if the answer requires weighing multiple sources against each other, you want WEIGH. If it requires chaining cause to effect, you want TRACE. RETRIEVE is the wrong tool when the LLM would need to *reason* to produce the answer.

**Syntax AIEX accepts (verified against the parser in [`parser.py`](../../src/aiex/parser/parser.py), `_parse_retrieve`):**

```ailang
RETRIEVE result_name FOR query_ident WITH:
    DEPTH: STANDARD
    RETRIEVAL_BUDGET: STANDARD
    OUTPUT_FORMAT: { ... }
END
```

The `FOR` keyword is mandatory and takes one identifier or string -- this is the query handle. The operation does not accept `FROM` sources; RETRIEVE *is* the retrieval layer.

**Default routing:** `BERT_CLASS` (`sentence-transformers/all-MiniLM-L6-v2` in the default pool). Cost is effectively zero -- embedding similarity runs on a local model, not an LLM API call. The escalation rules in [`routing.py`](../../src/aiex/models/routing.py) promote RETRIEVE to `GENERATIVE_LIGHT` (Haiku) when the output schema contains free-form qualitative text, or when `RETRIEVAL_BUDGET: FULL` is requested (because 20 top-k results need a generative model to synthesise into a coherent answer).

**Small example** (from `test_12` Scenario B):

```ailang
RETRIEVE antitrust_precedents FOR "technology_sector_mergers_52_percent_market_share" WITH:
    DEPTH: STANDARD
    EMIT_DIGEST: TRUE
    OUTPUT_FORMAT: {
        precedents: LIST OF OBJECT { case: TEXT, outcome: TEXT, market_share: TEXT },
        regulatory_framework: TEXT
    }
END
```

Because `regulatory_framework: TEXT` is qualitative free-form, the router escalates this to Haiku at compile time.

## COMPARE -- multi-axis evaluation

**Purpose:** evaluate a set of candidates against a set of dimensions, produce a ranking with trade-offs.

**When to use it:** choosing between migration options, ranking suppliers, scoring resumes on multiple criteria. The shape of the answer is "here are the candidates sorted by X, with notes on each."

**When *not* to use it:** if you only have one candidate, COMPARE has nothing to compare; use DEDUCE or WEIGH. If the dimensions require authority assessment (whose opinion counts more?), use WEIGH.

**Syntax** (two forms, both in `_parse_compare`):

```ailang
COMPARE [item1, item2, item3] AGAINST criteria WITH:
    DIMENSIONS: [cost, performance, risk]
    OUTPUT_FORMAT: { ... }
END
```

or

```ailang
COMPARE result_name AGAINST criteria FROM source WITH:
    DIMENSIONS: [...]
END
```

**Default routing:** `GENERATIVE_LIGHT` (Haiku). A Haiku-class model handles structured comparison with DIMENSIONS cleanly; Sonnet is overkill. At `DEPTH: DEEP` or above, the routing rule escalates to Sonnet.

In the test_10 live run, the COMPARE transaction cost $0.006 at 1050 neurons on Haiku, versus the per-DEDUCE cost of $0.016-$0.020 on Sonnet.

## TRACE -- causal chain analysis

**Purpose:** follow a cause-effect chain either forward (given this event, what happens next) or backward (given this outcome, what led here).

**When to use it:** root cause analysis, forward-projection of a decision, mapping a failure mode. Anything whose shape is "A caused B caused C because of D."

**When *not* to use it:** if you are applying named premises to a case, that is DEDUCE, not TRACE. The difference is: TRACE discovers links, DEDUCE applies them.

**Syntax** (from `_parse_deduce_trace`):

```ailang
TRACE result_name FROM source WITH:
    DIRECTION: BACKWARD
    DEPTH: THOROUGH
    OUTPUT_FORMAT: { ... }
END
```

`DIRECTION` takes `FORWARD` or `BACKWARD`. When omitted, the runtime infers from the output schema.

**Default routing:** `GENERATIVE_STANDARD` (Sonnet), temperature 0.1 for faithful chain-following. Escalates to Opus at `DEPTH: DEEP` or above.

**Example** (from `test_12` Scenario A, pharmaceutical recall):

```ailang
ADAPTIVELY TRACE recall_root_cause FROM pharmaceutical_incident WITH:
    DEPTH: THOROUGH
    RETRIEVAL_BUDGET: MINIMAL
    DIRECTION: BACKWARD
    MUST_INCLUDE: [primary_root_cause, contributing_factors, evidence_at_each_step]
    OUTPUT_FORMAT: { ... }
END
```

## DEDUCE -- logical inference from premises

**Purpose:** apply a set of premises to a target fact pattern and return per-premise conclusions plus an overall verdict.

**When to use it:** business-rule eligibility, compliance determination, premise-driven legal analysis. The shape of the problem is "here are the rules, here is the case, tell me which rules apply."

**When *not* to use it:** if no formal premises exist, DEDUCE has nothing to apply -- use INTELLIGENTLY or WEIGH. If you want to find causes (not apply rules), use TRACE.

**Syntax** (same parser branch as TRACE):

```ailang
DEDUCE result_name FROM [premise_1, premise_2, ...] WITH:
    APPLY_TO: case_facts
    RETRIEVAL_BUDGET: NONE
    MUST_INCLUDE: [...]
    OUTPUT_FORMAT: { ... }
END
```

`APPLY_TO` is the target; the `FROM` list is the premise set. `FROM RESULT` pipes input from a preceding THEN step; `FROM CONTEXT` pulls from an enclosing REASONING_CONTEXT block (see [chapter 8](08_pipelines.md)).

**Default routing:** `GENERATIVE_STANDARD` (Sonnet), temperature 0.15. DEEP/EXHAUSTIVE escalates to Opus. This is typically the sweet-spot operation: expensive enough to get right reasoning, cheap enough that you can run three of them in parallel.

**Example** (from `loan_eligibility.ailang`):

```ailang
DEDUCE hard_eligibility FROM [eligibility_rules, product, affordability_metrics] WITH:
    APPLY_TO: applicant
    DEPTH: STANDARD
    RETRIEVAL_BUDGET: NONE
    MUST_INCLUDE: [credit_score_check, dti_check, bankruptcy_disqualifier_check, ...]
    OUTPUT_FORMAT: {
        credit_score_check: TEXT IN ["pass", "fail"],
        dti_check: TEXT IN ["pass", "fail"],
        hard_outcome: TEXT IN ["all_pass", "marginal", "hard_fail"],
        ...
    }
END
```

## SYNTHESIZE -- integrate multiple inputs

**Purpose:** take several structured outputs and integrate them into a single unified view. The inverse of COMPARE: COMPARE ranks options, SYNTHESIZE merges analyses.

**When to use it:** combining a hard-rule result with soft-factor judgement into a final decision, fusing three independent analyses into one report, reconciling views from different analyst personas.

**When *not* to use it:** if you just want to concatenate strings, use EXECUTE_CODE. SYNTHESIZE earns its cost by finding agreement and naming unresolved tensions across inputs.

**Syntax** (from `_parse_synthesize`):

```ailang
SYNTHESIZE [input_a, input_b, input_c] INTO result_name WITH:
    DEPTH: STANDARD
    MUST_INCLUDE: [...]
    OUTPUT_FORMAT: { ... }
END
```

The `INTO` target is the result name. The AIEX spec [section 4 routing](../../AIEX_Specification_v0.1.md) lists SYNTHESIZE with the largest default neuron budget (1400) because merging is token-hungry.

**Default routing:** `GENERATIVE_STANDARD` (Sonnet) in the current router, with temperature 0.4 for slightly creative merging. The AIEX spec notes SYNTHESIZE is one of the operations most likely to benefit from escalation to Opus -- in practice, `DEPTH: DEEP` on SYNTHESIZE is the common pattern for final-decision steps.

**Example** (from `loan_eligibility.ailang`):

```ailang
SYNTHESIZE [hard_eligibility, soft_factors] INTO final_decision WITH:
    DEPTH: STANDARD
    RETRIEVAL_BUDGET: NONE
    MUST_INCLUDE: [decision, primary_rationale, conditions_if_any, confidence]
    OUTPUT_FORMAT: {
        decision: TEXT IN ["APPROVE", "APPROVE_WITH_CONDITIONS", "REFER_TO_UNDERWRITER", "DECLINE"],
        ...
    }
END
```

## WEIGH -- evaluate competing authorities and evidence

**Purpose:** assess a topic by weighing competing sources along declared dimensions. This is the operation for questions of the form "who is right, and by how much?"

**When to use it:** decisions where source credibility matters more than logical derivation -- medical guidelines from conflicting clinical trials, investment recommendations from rival analysts, policy advice from competing think tanks. Also the natural verb for final go/no-go decisions where multiple prior analyses must be weighted.

**When *not* to use it:** if your "sources" are really premises, use DEDUCE. WEIGH implies the sources have different authority levels that matter to the answer.

**Syntax** (from `_parse_weigh`):

```ailang
WEIGH result_name ON topic FROM source WITH:
    SOURCES: [src1, src2, src3]
    DIMENSIONS: [dim1, dim2]
    OUTPUT_FORMAT: { ... }
END
```

Both `ON topic` and the `FROM` clause are optional individually but at least one anchoring input is expected in practice. The `SOURCES` list inside `WITH:` enumerates the authorities being weighed; `DIMENSIONS` names the axes along which they are compared.

**Default routing:** `GENERATIVE_STANDARD` (Sonnet) in the current router with a 1300-neuron budget; the AIEX specification describes WEIGH as the most demanding operation in the hierarchy (1531 neurons in the spec's example routing table) and recommends Opus for serious authority-weighting workloads. `DEPTH: DEEP` or `DEPTH: EXHAUSTIVE` will escalate to Opus.

**Example** (from `loan_eligibility.ailang`):

```ailang
WEIGH soft_factors ON applicant WITH:
    DEPTH: STANDARD
    RETRIEVAL_BUDGET: NONE
    SOURCES: [industry_stability, employment_tenure, relationship_tenure_with_lender]
    DIMENSIONS: [forward_payment_capacity, relationship_value, risk_trajectory]
    OUTPUT_FORMAT: {
        industry_assessment: TEXT IN ["stable", "neutral", "volatile"],
        soft_factor_direction: TEXT IN ["positive", "neutral", "negative"],
        ...
    }
END
```

## INTELLIGENTLY -- the untyped fallback

`INTELLIGENTLY` is the escape hatch: use it when you genuinely don't know which of the six typed verbs fits, or when you want the runtime to auto-classify from the prompt. Section 8.5 of the AILang spec cites 93.8% auto-classification accuracy in internal testing.

**Syntax** (from `_parse_intelligently`):

```ailang
INTELLIGENTLY verb_name FROM source WITH:
    OUTPUT: result_name
    HINTS: { ... }
END
```

`INTELLIGENTLY` differs from the typed verbs in that the action identifier right after `INTELLIGENTLY` is an arbitrary verb (`create_person`, `analyze_situation`, etc.) that the runtime inspects to guess a reasoning type. The `OUTPUT:` and `HINTS:` keys inside the `WITH:` block are specific to INTELLIGENTLY.

**Default routing:** `GENERATIVE_STANDARD` (Sonnet) -- a safe middle tier. This is more expensive than a typed operation would usually be; using `INTELLIGENTLY` routinely throws away the whole point of typed reasoning. Reserve it for genuinely ambiguous cases.

## Which operation should I use?

```
Is the answer a fact that just needs to be looked up?
    yes -> RETRIEVE

Do you have multiple candidates that need ranking?
    yes -> COMPARE

Do you need to follow cause and effect through a chain?
    yes -> TRACE   (pick DIRECTION: FORWARD or BACKWARD)

Do you have explicit premises to apply to a case?
    yes -> DEDUCE  (premises FROM [...]  APPLY_TO: target)

Are you combining multiple structured outputs into one?
    yes -> SYNTHESIZE

Does the answer depend on weighing competing authorities or sources?
    yes -> WEIGH

None of the above, or genuinely ambiguous?
    -> INTELLIGENTLY   (pay the Sonnet tax, let the runtime guess)
```

Every choice above has cost implications that [chapter 6](06_style_depth_retrieval.md) builds on and that [chapter 7](07_output_contracts.md) makes safe through output contracts.
