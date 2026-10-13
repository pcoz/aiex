# Chapter 10: EXECUTE_CODE Blocks

Not every step in a real workflow is qualitative reasoning. A loan
application has a debt-to-income calculation. An invoice pipeline has
date arithmetic. A case-triage flow builds an audit log entry whose
every field is already known. For any of those, an LLM is the wrong
tool -- it is slow, it costs money, and it introduces rounding
variance into numbers that should be reproducible to the cent.

`EXECUTE_CODE` is AILang's answer. It lets you drop a block of
deterministic Python directly into an AILang program, and AIEX runs
it locally without any API call. The variables flowing around your
reasoning steps are the same variables the code block sees; anything
the block assigns becomes an AILang variable that downstream steps
can read.

## Syntax

A code block opens with `EXECUTE_CODE <language>:` and closes with
`END_EXECUTE`. The body is indented inside:

```ailang
EXECUTE_CODE python:
    principal = applicant["requested_loan_amount_usd"]
    n = applicant["requested_term_months"]
    monthly_rate = product["base_apr_pct"] / 100 / 12
    if monthly_rate == 0:
        monthly_payment = principal / n
    else:
        factor = (1 + monthly_rate) ** n
        monthly_payment = principal * (monthly_rate * factor) / (factor - 1)
END_EXECUTE
```

Only `python` is supported today. The language identifier is
case-insensitive. The body is raw Python, not a DSL -- whatever the
Python interpreter accepts will run here.

One practical note about indentation. AILang authors naturally indent
the body of `EXECUTE_CODE` by four spaces. Python would ordinarily
reject that as an `IndentationError` on the first statement. AIEX
strips the longest common leading whitespace via `textwrap.dedent`
before handing the body to the interpreter, so you can indent however
you like. Relative indentation inside the block is preserved, so
nested `if`/`for`/`def` blocks work exactly as written.

## Variable Marshalling

The important thing about `EXECUTE_CODE` is that it is not a sealed
sandbox. It has one foot in each world.

Before the block runs, the engine takes a snapshot of every AILang
variable in scope and exposes each as a Python local with the same
name. A `SET applicant TO {...}` earlier in the program becomes a
Python dict named `applicant` inside the block. You read it like any
other dict.

When the block finishes, the engine walks the local namespace and
captures every new or modified binding whose name does not start with
an underscore. Each one becomes an AILang variable available to
subsequent operations. In the example above, `monthly_payment`,
`principal`, and the dict `affordability_metrics` all flow out of the
block and become readable by the next `DEDUCE` step:

```ailang
DEDUCE hard_eligibility FROM [eligibility_rules, product, affordability_metrics] WITH:
    APPLY_TO: applicant
    ...
END
```

Names starting with `_` are treated as private scratch (Python
convention). Module imports and unmodified pre-existing bindings are
not propagated -- only values the block actually produced.

## What Happens at Compile Time

When the compiler sees an `EXECUTE_CODE` block it produces a
`TransactionPlan` just like for any reasoning operation, but with a
few fields set differently. `code_block_body` carries the raw source
text. The routing tier is the sentinel `LOCAL_CODE`, which tells the
runtime "do not go to an LLM." The budget entry reports $0 cost, 0
tokens, and a fixed 100 ms latency estimate -- enough to cover
process overhead for typical business-rule computations.

You can see this in any budget report that includes code blocks. From
the loan eligibility example:

```
TX                  Operation   Retrieval  Model             Est. Cost  Est. Latency
tx-1-affordability  CODE        NONE       local-code        $0.0000    100ms
tx-2-hard-elig      DEDUCE      NONE       claude-sonnet-4-6 $0.0144    4000ms
tx-3-soft-factors   WEIGH       NONE       claude-opus-4-6   $0.0135    6000ms
tx-4-synth-decision SYNTHESIZE  NONE       claude-opus-4-6   $0.0162    6000ms
tx-5-audit          CODE        NONE       local-code        $0.0000    100ms
```

Two of the five transactions cost nothing because they are code.
The savings over the all-Opus baseline -- 62.7% on this program --
are partly the routing story told in
[chapter 11](11_multi_model_routing.md) and partly the fact that
deterministic steps were not sent to an LLM at all.

## What Happens at Runtime

The engine's main dispatch function checks `tx.code_block_body` first.
When it is set, the LLM path is skipped entirely -- no prompt
composition, no contract validation, no retry loop -- and control
passes to `_execute_code_block_transaction`. That function:

1. Builds an execution namespace by copying every AILang variable into
   a Python dict.
2. Runs the dedented body via `compile()` and `exec()` against that
   namespace.
3. On success, merges new/modified bindings back into program state
   via `set_variable`, which also writes a log entry for each mutation.
4. On exception, marks the transaction `ROLLED_BACK` and propagates
   nothing back to state.

Commit semantics are atomic in the same sense as LLM transactions.
Either every new binding lands in state, or none of them do. A code
block that raises halfway through leaves the program exactly as it
was before the block started.

There is no retry loop. LLM responses are probabilistic and worth
retrying; a Python `ZeroDivisionError` will raise again on identical
inputs, so asking the engine to re-dispatch would waste wall-clock
time for no benefit. If a block fails, it fails once, and the audit
log records why.

## Worked Example: Amortised Payment

This is the step-1 block from the loan eligibility example:

```ailang
EXECUTE_CODE python:
    principal = applicant["requested_loan_amount_usd"]
    n = applicant["requested_term_months"]
    monthly_rate = product["base_apr_pct"] / 100 / 12
    if monthly_rate == 0:
        monthly_payment = principal / n
    else:
        factor = (1 + monthly_rate) ** n
        monthly_payment = principal * (monthly_rate * factor) / (factor - 1)

    monthly_income = applicant["annual_income_usd"] / 12
    total_monthly_debt = applicant["monthly_debt_obligations_usd"] + monthly_payment
    dti_pct = (total_monthly_debt / monthly_income) * 100
    residual_income = monthly_income - total_monthly_debt

    affordability_metrics = {
        "monthly_payment_usd": round(monthly_payment, 2),
        "monthly_income_usd": round(monthly_income, 2),
        "dti_pct": round(dti_pct, 2),
        "residual_income_usd": round(residual_income, 2),
        "principal_usd": principal,
        "term_months": n,
        "apr_pct": product["base_apr_pct"],
    }
END_EXECUTE
```

Two AILang variables come in: `applicant` and `product`. Seven Python
names are assigned; one of them, `affordability_metrics`, is the dict
the next reasoning step consumes. The rest (`principal`, `n`,
`monthly_rate`, `factor`, etc.) are also propagated, but the
downstream `DEDUCE` only references the metrics dict, so the rest are
effectively scratch.

## Worked Example: Audit Log Formatting

Once the reasoning steps are done, the program has structured dicts
for the hard rules, soft factors, and final decision. Turning those
into an audit log entry and a customer letter is pure string
assembly:

```ailang
EXECUTE_CODE python:
    import datetime

    audit_log_entry = {
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "applicant_name": applicant["name"],
        "computed_metrics": affordability_metrics,
        "hard_eligibility": hard_eligibility,
        "soft_factors": soft_factors,
        "final_decision": final_decision,
    }

    decision = final_decision["decision"]
    rationale = final_decision["primary_rationale"]
    if decision == "APPROVE":
        letter_excerpt = (
            f"Dear {applicant['name']}, we are pleased to approve your "
            f"loan request of ${applicant['requested_loan_amount_usd']:,}. "
            f"{rationale}"
        )
    elif decision == "DECLINE":
        letter_excerpt = (
            f"Dear {applicant['name']}, we are unable to approve your loan "
            f"request at this time. {rationale}"
        )
END_EXECUTE
```

This is the kind of step that would, in a pure-LLM pipeline, be sent
to Sonnet or Opus to "format the decision." Doing it in Python
instead saves a few cents and a few seconds per run, and the wording
is byte-for-byte identical every time -- which matters for regulatory
review.

## Current Limitations

- **Python only.** The lexer accepts other language identifiers but
  the runtime rejects anything other than `python` with an
  `unsupported language` error. JavaScript and R are plausible
  extensions; they would slot in behind the same `execute_code_block`
  interface.
- **In-process execution.** The body runs via `exec()` inside the
  AIEX process. It has full access to the Python environment AIEX
  was launched with.
- **No sandbox.** The trust model is "you wrote the code, you trust
  it" -- the same model as `python script.py`. If AIEX is ever
  deployed as a multi-tenant service accepting untrusted AILang from
  external callers, a subprocess or gVisor-based sandbox would be
  added at the executor boundary. Until then, in-process `exec` is
  the right level of complexity for the single-tenant case.

## When to Reach for EXECUTE_CODE

If the step is deterministic -- math, date handling, regex extraction,
JSON formatting, lookup against a small Python dict, sort-or-filter
against a list -- use a code block. It is cheaper, faster, and
produces identical output on every run.

If the step requires judgment, interpretation, or synthesis -- apply
a rule whose semantics are described in prose, weigh competing
factors, summarise a document -- use a typed reasoning operation.
Those are what the LLM layer is for.

Many programs mix both, as the loan eligibility example does:
deterministic math sandwiches three reasoning operations, and the
final audit log is again deterministic. See
[chapter 12](12_dispatch_validate_commit.md) for how the engine
orchestrates a mixed pipeline through its transaction lifecycle.
