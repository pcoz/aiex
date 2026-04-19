# Chapter 7 -- Output Contracts

An LLM will cheerfully produce whatever text feels plausible. That is its job. The job of a production system, on the other hand, is to fail loudly when the model produces something wrong -- a misspelled enum value, a missing required field, a field containing speculation the business rules forbid. AILang's contract vocabulary (`MUST_INCLUDE`, `CANNOT_INCLUDE`, `OUTPUT_FORMAT`) and AIEX's CONTRACT DIVISION turn those failure modes from "silent data corruption" into "the transaction rolled back and here is why."

This chapter starts with the lightest contract (a semantic check that certain concepts appear), builds up to structured schema enforcement, and ends with the single most important idea in the chapter: constrained enums make LLM output safe for downstream business logic.

## MUST_INCLUDE -- required concepts

`MUST_INCLUDE` lists concept identifiers that must appear in the response. Each identifier is checked against the generated text using a `CHECK-PRESENT` detection method: a combination of keyword match and semantic scan (a lightweight secondary LLM classification when keyword match fails).

Example, from `test_10`:

```ailang
DEDUCE tariff_applicability_none FROM [premise_1, premise_2, premise_3, premise_4, premise_5] WITH:
    RETRIEVAL_BUDGET: NONE
    APPLY_TO: case_facts
    MUST_INCLUDE: [
        each_premise_evaluated,
        section_232_applicability,
        usmca_qualification_analysis,
        anti_dumping_margin_analysis,
        cvd_subsidy_analysis,
        final_tariff_determination
    ]
    OUTPUT_FORMAT: { ... }
END
```

This says: the model's response must contain six named concepts. If one is missing, the contract check fires.

**What happens on failure:** per the AIEX contract spec ([`AIEX_Specification_v0.1.md`](../../AIEX_Specification_v0.1.md) section 6), MUST_INCLUDE violations are assigned severity `MAJOR` by default. Severity MAJOR triggers a **retry with reinforced prompt**: the runtime re-dispatches the same operation with an appended instruction along the lines of "CRITICAL INSTRUCTION: your previous response violated the following boundary: {violation}. You MUST include {concept}." The retry count is bounded (default max 2); if retries are exhausted, the severity escalates to CRITICAL and the transaction rolls back.

This means MUST_INCLUDE is cheap when the model is cooperative (one dispatch), moderately expensive when the model drops a concept (up to three dispatches), and rollback-costly only if the model cannot produce the required content at all -- usually a sign the operation was miscompiled or the premises were insufficient.

## CANNOT_INCLUDE -- prohibited concepts

`CANNOT_INCLUDE` is the dual: a list of concepts that must NOT appear. It is how you tell the model "don't speculate beyond the evidence" or "don't mention competitors by name" or "don't include unsupported claims."

```ailang
DEDUCE hard_eligibility FROM [eligibility_rules, product, affordability_metrics] WITH:
    APPLY_TO: applicant
    RETRIEVAL_BUDGET: NONE
    MUST_INCLUDE: [credit_score_check, dti_check, ...]
    CANNOT_INCLUDE: [
        speculation_about_undisclosed_factors,
        rules_not_present_in_product,
        recommendations_about_apr_changes
    ]
    OUTPUT_FORMAT: { ... }
END
```

The detection method is a `SEMANTIC-SCAN` with a configurable confidence threshold. The spec's default severity for CANNOT_INCLUDE violations is `MAJOR`, but there is a known issue (documented as P3 in the AIEX session notes): without a full BERT validator available, keyword-mode CANNOT_INCLUDE checks produce too many false positives -- the model might use a forbidden phrase incidentally without actually violating the intent. The current mitigation in the shipping runtime is to treat CANNOT_INCLUDE as `MINOR` severity: log a warning, commit the transaction. The full semantic-similarity validator is written (`bert_validator.py`) but not yet wired in. Treat CANNOT_INCLUDE as advisory until that lands; treat MUST_INCLUDE as enforced.

## OUTPUT_FORMAT -- structured schema enforcement

This is the contract that actually makes AILang safe. `OUTPUT_FORMAT` declares the structural schema the LLM's response must match. Violations are severity **CRITICAL** by the default policy, which means a schema-violating response rolls back the transaction -- the operation did not happen, no state is modified, and either a fallback procedure runs or the pipeline halts.

The schema language supports:

- leaf types: `TEXT`, `NUMBER`, `BOOLEAN`, `QUALITATIVE`
- constrained enums: `TEXT IN ["a", "b", "c"]`, `QUALITATIVE IN ["high", "moderate", "low"]`
- `LIST OF <type>` for homogeneous arrays
- `OBJECT { field: type, ... }` for nested records
- arbitrary nesting: `LIST OF OBJECT { sub_list: LIST OF TEXT }`

The parser's `_parse_type_expr` handles all of these recursively (see [`parser.py`](../../src/aiex/parser/parser.py) lines 1190-1271). Detection method for OUTPUT_FORMAT is `VALIDATE-SCHEMA`: a deterministic parser that either accepts or rejects the response against the schema -- no fuzzy matching, no confidence threshold.

Here is a real schema from `loan_eligibility.ailang`:

```ailang
OUTPUT_FORMAT: {
    credit_score_check: TEXT IN ["pass", "fail"],
    dti_check: TEXT IN ["pass", "fail"],
    employment_tenure_check: TEXT IN ["pass", "fail"],
    residual_income_check: TEXT IN ["pass", "fail"],
    bankruptcy_disqualifier_check: TEXT IN ["pass", "fail"],
    fraud_flag_disqualifier_check: TEXT IN ["pass", "fail"],
    loan_amount_within_product_limit_check: TEXT IN ["pass", "fail"],
    term_within_product_limit_check: TEXT IN ["pass", "fail"],
    hard_outcome: TEXT IN ["all_pass", "marginal", "hard_fail"],
    failed_checks: LIST OF TEXT,
    notes: TEXT
}
```

And a nested one from `test_10`:

```ailang
OUTPUT_FORMAT: {
    tariffs_apply: TEXT IN ["yes_tariffs_apply", "no_tariffs_exempt",
                            "partially_applicable", "insufficient_information"],
    section_232_status: TEXT,
    usmca_qualification: BOOLEAN,
    anti_dumping_risk: TEXT IN ["high", "moderate", "low", "insufficient_info"],
    reasoning_chain: LIST OF OBJECT {
        premise_number: NUMBER,
        finding: TEXT,
        applies: BOOLEAN
    },
    confidence_level: QUALITATIVE IN ["high", "moderate", "low"]
}
```

Notice how every decision-critical field is constrained to an enum: the model cannot return `"maybe_tariffs"` because that value is not in the list. The schema validator rejects it; the runtime retries or rolls back.

## The severity policy: what actually happens on failure

The contract outcomes are graduated, not binary. From the AIEX spec:

- **CRITICAL**: transaction rolls back. State is not modified. The operation did not happen. `OUTPUT_FORMAT` schema violations are CRITICAL by default -- a malformed JSON response is worse than no response, because downstream code would crash reading it.

- **MAJOR**: transaction retries with reinforced prompt, up to `max_retries` (default 2). The reinforced prompt prepends or appends an explicit instruction about the violated boundary. After max retries, severity escalates to CRITICAL and the transaction rolls back. `MUST_INCLUDE` violations are MAJOR.

- **MINOR**: transaction commits with a logged warning. The boundary was violated, but the violation is not serious enough to reject the response. `CANNOT_INCLUDE` is currently MINOR in the shipping runtime (see P3 above); the digest-format contract is MINOR (a slightly-over-length digest gets truncated and committed).

This three-tier system means you get a sliding scale of strictness. A programmer writing a contract chooses the severity by choosing which contract slot a check goes into: `OUTPUT_FORMAT` for strict structural requirements, `MUST_INCLUDE` for retry-on-fail concept requirements, `CANNOT_INCLUDE` for log-the-warning advisory requirements.

## Why constrained enums matter more than anything else

The single most valuable feature in this chapter is `TEXT IN [...]`. Here is why.

Consider the final-decision step in the loan example:

```ailang
SYNTHESIZE [hard_eligibility, soft_factors] INTO final_decision WITH:
    ...
    OUTPUT_FORMAT: {
        decision: TEXT IN ["APPROVE", "APPROVE_WITH_CONDITIONS",
                           "REFER_TO_UNDERWRITER", "DECLINE"],
        ...
    }
END
```

Downstream of this operation, a Python block reads `final_decision["decision"]` and branches:

```python
if decision == "APPROVE":
    letter_excerpt = f"Dear {name}, we are pleased to approve ..."
elif decision == "APPROVE_WITH_CONDITIONS":
    ...
elif decision == "REFER_TO_UNDERWRITER":
    ...
else:  # DECLINE
    ...
```

Without the enum constraint, the model could return `"Approved"` (capitalised differently) or `"approve with conditions"` (spaces, lowercase) or `"CONDITIONALLY_APPROVED"` (synonym not in the policy). Each variant is a branch the Python code doesn't handle. You would end up writing defensive normalisation code: `decision.upper().replace(" ", "_")`, and still have edge cases.

With the enum constraint, the schema validator rejects any response whose `decision` field is not exactly one of the four listed strings. Either the model returns one of the four values and the code branches correctly, or the transaction fails at the contract boundary with a clear error. The downstream business logic is safe.

The same pattern runs through every example in the AIEX test suite: `hard_outcome: TEXT IN ["all_pass", "marginal", "hard_fail"]`, `risk_level: TEXT IN ["high", "moderate", "low"]`, `overall_validation: TEXT IN ["pass", "partial_pass", "fail"]`. Every enum is a finite switch statement the Python side can exhaust. Every free-form TEXT field is a place where you are tolerating the model's creativity -- fine for a rationale string, dangerous for anything that drives control flow.

The practical rule is:

> If a downstream piece of code branches on a field, make that field a constrained enum. If the field is displayed to a human or logged, free-form TEXT is fine.

## Putting it together

A complete operation with all three contract kinds:

```ailang
DEDUCE hard_eligibility FROM [eligibility_rules, product, affordability_metrics] WITH:
    APPLY_TO: applicant
    DEPTH: STANDARD
    RETRIEVAL_BUDGET: NONE
    MUST_INCLUDE: [
        credit_score_check, dti_check, employment_tenure_check,
        residual_income_check, bankruptcy_disqualifier_check,
        fraud_flag_disqualifier_check
    ]
    CANNOT_INCLUDE: [
        speculation_about_undisclosed_factors,
        rules_not_present_in_product,
        recommendations_about_apr_changes
    ]
    OUTPUT_FORMAT: {
        credit_score_check: TEXT IN ["pass", "fail"],
        dti_check: TEXT IN ["pass", "fail"],
        employment_tenure_check: TEXT IN ["pass", "fail"],
        residual_income_check: TEXT IN ["pass", "fail"],
        bankruptcy_disqualifier_check: TEXT IN ["pass", "fail"],
        fraud_flag_disqualifier_check: TEXT IN ["pass", "fail"],
        hard_outcome: TEXT IN ["all_pass", "marginal", "hard_fail"],
        failed_checks: LIST OF TEXT,
        notes: TEXT
    }
END
```

A response to this operation goes through three checks in sequence. First, the schema validator confirms the JSON has all eleven fields with the right types, and that every `TEXT IN [...]` field contains an allowed value. If any structural check fails, CRITICAL: rollback. Second, the `CHECK-PRESENT` scan confirms the six required concepts are mentioned somewhere in the response. If one is missing, MAJOR: retry with reinforcement. Third, the CANNOT_INCLUDE scan looks for forbidden speculation. If it finds some, currently MINOR: commit with warning (until BERT-backed validation is enabled).

A response that passes all three commits atomically to `hard_eligibility`, and the pipeline moves on. A response that fails at the schema boundary was never committed; no downstream operation will ever see a broken `hard_eligibility`.

That atomicity -- either the operation committed valid output or it didn't happen -- is what makes AILang programs auditable. Every transaction's response either matched its declared contract or was logged, rolled back, and replaced. The audit trail for a program run is the list of transactions that committed and the contract each committed against. No hidden state, no silent drift.

The next chapter covers what happens when you want a transaction's committed output to feed the next transaction: [pipelines](08_pipelines.md).
