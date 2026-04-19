# Chapter 3: Your First Program

This chapter builds one realistic AILang program from scratch, in four
passes. Each pass adds one idea -- first the reasoning operation, then
an output format, then `MUST_INCLUDE`, then `CANNOT_INCLUDE`. After
every pass you compile, note how the budget report changes, and run
the program. By the end you will have written and debugged the same
kind of program that ships in the AIEX examples directory.

The task is small but not toy: classify an inbound customer support
ticket into one of four categories -- billing, technical, account, or
other -- and return a short rationale. This is a task that real teams
solve with a fine-tuned classifier or a handful of regexes today, and
which AIEX can solve with a single typed operation and a contract.

## Pass 1: A Bare `DEDUCE`

Save the following as `triage.ailang`:

```ailang
#ailang v0.8.2

SET ticket TO {
    subject: "Double charged on my April invoice",
    body: "I see two identical charges of $49.00 on April 3. I only placed one order. Please refund one of them.",
    submitted_by: "jane@example.com"
}

DEDUCE ticket_category FROM ticket WITH:
    DEPTH: STANDARD
    RETRIEVAL_BUDGET: NONE
END
```

We use `DEDUCE` because we are applying a fixed rule -- the four
categories -- to an input and asking which category the ticket falls
into. There is no retrieval required (the ticket text is
self-contained), so `RETRIEVAL_BUDGET: NONE` is correct and saves the
compiler ~865 neurons of budget.

Compile:

```bash
aiex compile triage.ailang
```

```
=== AIEX Execution Budget Report ===
Program: triage
Transactions: 1
  TX               Operation  Retrieval  Model                  Neurons  Est. Cost  Est. Latency
  tx-1-ticket_cat  DEDUCE     NONE       claude-sonnet-4-6          335  $0.0140    4000ms

  Total estimated cost:    $0.0140
  vs. all-Opus baseline:   $0.0900
  Savings: 84.4%
  Estimated latency:       4000ms
```

A bit under one and a half cents. `DEDUCE` routed to Sonnet, not Opus
(Sonnet is the default for logical deduction at `DEPTH: STANDARD`),
and `RETRIEVAL_BUDGET: NONE` brought the effective neuron count down
from 1200 to 335.

Run it:

```bash
aiex run triage.ailang
```

The response will be a free-form JSON or prose blob. It probably
contains the right answer ("billing"), but its shape is unconstrained.
That is a problem. Downstream code that reads `ticket_category` from
program state has no guarantee about what fields exist, what values
are permitted, or whether the category is one of your four
categories or something the model invented on the spot ("refund
request", "duplicate charge inquiry", etc.).

The fix is an `OUTPUT_FORMAT`.

## Pass 2: Constrain the Shape

Replace the `DEDUCE` block with this version:

```ailang
DEDUCE ticket_category FROM ticket WITH:
    DEPTH: STANDARD
    RETRIEVAL_BUDGET: NONE
    OUTPUT_FORMAT: {
        category: TEXT IN ["billing", "technical", "account", "other"],
        rationale: TEXT,
        urgency: TEXT IN ["high", "normal", "low"]
    }
END
```

The schema nails down three things. `category` must be one of the
four declared strings -- AIEX validates this as an exact enum match
on the parsed JSON, no fuzzy matching. `rationale` is free-form text,
which is appropriate for a justification. `urgency` is another
enum-constrained field.

Compile again:

```bash
aiex compile triage.ailang
```

```
  TX               Operation  Retrieval  Model                  Neurons  Est. Cost  Est. Latency
  tx-1-ticket_cat  DEDUCE     NONE       claude-sonnet-4-6          335  $0.0148    4000ms

  Total estimated cost:    $0.0148
  vs. all-Opus baseline:   $0.0900
  Savings: 83.5%
```

The cost moved by a fraction of a cent -- the schema grew the system
prompt slightly. Nothing escalated tiers; the output is still
text-heavy but not so voluminous that Sonnet wouldn't handle it.

Run it again. Now the response is a JSON object with exactly three
fields, `category` is one of the four permitted strings, and
`urgency` is one of three. If the model returns `category: "refund"`
-- a value not in the enum -- the contract fails with a CRITICAL
severity, the transaction rolls back, and `ticket_category` is not
committed to state. No garbage-in-garbage-out downstream.

This is already more structured than most hand-rolled LLM
classifiers. But there is one thing the schema does not enforce: *the
response actually has to justify its answer*. A model might return
`rationale: "billing"` -- technically valid text, useless as a
rationale. `MUST_INCLUDE` fixes that.

## Pass 3: Tighten with `MUST_INCLUDE`

Add a `MUST_INCLUDE` clause:

```ailang
DEDUCE ticket_category FROM ticket WITH:
    DEPTH: STANDARD
    RETRIEVAL_BUDGET: NONE
    MUST_INCLUDE: [
        evidence_from_ticket_body,
        category_justification
    ]
    OUTPUT_FORMAT: {
        category: TEXT IN ["billing", "technical", "account", "other"],
        rationale: TEXT,
        urgency: TEXT IN ["high", "normal", "low"]
    }
END
```

`MUST_INCLUDE` declares concepts that must appear in the response.
Unlike `OUTPUT_FORMAT` (which is a deterministic JSON check),
`MUST_INCLUDE` is a semantic check: after the response comes back,
AIEX uses BERT cosine similarity -- falling back to keyword matching
if BERT is not available -- to verify that each concept is actually
present somewhere in the response. A response whose `rationale` is
"billing" will fail `evidence_from_ticket_body` because that concept
has no semantic match in a one-word string.

Compile:

```
  TX               Operation  Retrieval  Model                  Neurons  Est. Cost  Est. Latency
  tx-1-ticket_cat  DEDUCE     NONE       claude-sonnet-4-6          335  $0.0156    4000ms
```

The cost drifted up by another small amount -- the two `MUST_INCLUDE`
concepts are compiled into `{BOUNDARY_INSTRUCTIONS}` in the system
prompt so the model knows what to include, plus they become post-hoc
validators. The neuron count and model tier are unchanged.

Run it. If `MUST_INCLUDE` fails it is a CRITICAL severity violation
by default: the transaction rolls back, the missing concept is
logged, and no state is committed. There is no silent retry loop --
a CRITICAL failure is a signal to the caller that the operation
needs re-issuing (with stronger premises or tighter wording). In
practice Sonnet produces a grounded rationale on the first try, but
the contract means you notice immediately on the rare run where it
does not.

## Pass 4: Forbid Drift with `CANNOT_INCLUDE`

The final iteration adds a negative constraint:

```ailang
DEDUCE ticket_category FROM ticket WITH:
    DEPTH: STANDARD
    RETRIEVAL_BUDGET: NONE
    MUST_INCLUDE: [
        evidence_from_ticket_body,
        category_justification
    ]
    CANNOT_INCLUDE: [
        suggested_remedy,
        policy_citation
    ]
    OUTPUT_FORMAT: {
        category: TEXT IN ["billing", "technical", "account", "other"],
        rationale: TEXT,
        urgency: TEXT IN ["high", "normal", "low"]
    }
END
```

We want the classifier to classify, not to propose a remedy and not
to cite a policy document that may or may not exist. `CANNOT_INCLUDE`
is enforced primarily via `TERMINOLOGY_DIFF` and
`KNOWLEDGE_DEPTH_SCAN`: AIEX compares the terminology in the response
to the terminology in the inputs, and flags novel terminology (like
"Section 4.2.1 of our refund policy") that was not present anywhere
in the ticket. By default these checks run at MINOR severity -- they
log a warning and commit -- because keyword-based detection without
BERT is noisy. With BERT installed, they can be elevated to MAJOR
safely.

Compile one final time:

```
  Total estimated cost:    $0.0160
  vs. all-Opus baseline:   $0.0900
  Savings: 82.2%
```

We have added two `MUST_INCLUDE` concepts, two `CANNOT_INCLUDE`
concepts, an `OUTPUT_FORMAT` with three constrained fields, and the
program has moved from one and a half cents to one and six-tenths
cents per run. That is the price of precision: a few extra tokens in
the system prompt to spell out each constraint, post-hoc validation
work that runs locally and costs nothing, and a guarantee that
everything downstream sees is structurally correct or does not reach
state at all.

Run it and inspect the result with `-o`:

```bash
aiex run -o out.json triage.ailang
cat out.json
```

You will see a `results` object containing the committed response for
`tx-1-ticket_category`, a `variables` object with the same response
stored under `ticket_category`, and an `execution_log` that records
every state mutation with a timestamp.

## Where to Go Next

You have just written a classifier whose behaviour is auditable,
whose cost is known before it runs, and whose contract fails
predictably rather than quietly. The same template scales: replace
`DEDUCE` with `WEIGH` for judgment tasks, chain operations with
`THEN` to build multi-step reasoning (see
[chapter 8](08_pipelines.md)), or interleave
`EXECUTE_CODE python:` blocks for deterministic math as the
loan-eligibility example does (see
[chapter 10](10_execute_code.md)).

The next chapter unpacks the budget report in detail -- every column,
every number -- so that when you compile something more complicated,
you can read the plan before you run it and tune the cost.
