# Chapter 4: Reading the Budget Report

The budget report is the most useful thing AIEX prints. It tells you,
before any API call is made, exactly which model each operation will
dispatch to, how many neurons of budget it will consume, how many
tokens AIEX expects it to use, how much it will cost, and how long it
will take. This chapter takes the report apart column by column, and
then walks through two real reports from programs that ship with the
AILang repository.

## The Anatomy of a Report

Every report has three parts: a header, a per-transaction table, and a
totals block.

```
=== AIEX Execution Budget Report ===
Program: <program-id>
Source: <path>
Transactions: <count>
Parallel groups: <group-name>(<count>), ...

                        Transaction Budget
  TX   Operation  Retrieval  Model   Neurons  Est. Cost  Est. Latency
  ...

  Total estimated cost:    $...
  vs. all-Opus baseline:   $...
  Savings: ...%
  Estimated latency:       ...ms
```

The per-transaction table has seven columns. Each one is a compile-time
decision made visible.

### TX

The transaction identifier, generated from the AILang operation's
result name (e.g. `DEDUCE hard_eligibility FROM ...` becomes
`tx-2-hard_eligibility`). Identifiers longer than 30 characters are
truncated in the display. This is the same ID used in the execution
log and in the `results` dictionary of the output JSON, so you can
trace any line of the budget report to the operation that produced
it.

### Operation

The reasoning type: `DEDUCE`, `RETRIEVE`, `COMPARE`, `TRACE`,
`SYNTHESIZE`, `WEIGH`, or `CODE` for `EXECUTE_CODE` blocks. This is
the single most important input to routing -- every other column
follows from it and from the modifiers.

### Retrieval

The `RETRIEVAL_BUDGET` declared on the operation, one of `NONE`,
`MINIMAL`, `STANDARD`, `FULL`. This column matters because it controls
both the BERT retrieval phase (top-k vector search before the LLM
call) and the prompt modifier that gets inserted into the system
prompt. It also controls the neuron-savings subtraction in the cost
model: `NONE` saves 865 neurons of compute, `MINIMAL` saves 665, and
`STANDARD`/`FULL` save 0.

### Model

The concrete model ID the transaction will dispatch to. This is the
output of `resolve_routing()`: it starts from the default routing
table (RETRIEVE -> BERT, DEDUCE -> Sonnet, WEIGH -> Opus, etc.) and
applies four escalation rules in order:

1. `DEPTH: DEEP` forces at least Sonnet.
2. `DEPTH: EXHAUSTIVE` forces Opus.
3. `RETRIEVAL_BUDGET: FULL` on a BERT-based operation escalates to
   Haiku.
4. A qualitative-text output schema on a BERT-based operation
   escalates to Haiku.

So a `COMPARE` with `OUTPUT_FORMAT: { ranking: LIST OF NUMBER }`
stays on BERT, but a `COMPARE` that includes a `findings: LIST OF TEXT`
field is promoted to Haiku because BERT cannot generate the prose.

### Neurons

The effective neuron budget for the operation. This is not a marketing
number -- it comes from the "LLM Reasoning Router" research underlying
AILang, which measured the activation overlap between reasoning types
and found that different typed operations recruit 25-75% of each
other's neurons. Untyped prompts have to allocate the full ~1,531-neuron
"general reasoning" budget because the runtime doesn't know which
subset will actually fire. A typed operation tells the runtime which
reasoning pathway to light up, so it can activate only what is needed.

The per-type bases:

| Type       | Base neurons |
|------------|--------------|
| RETRIEVE   | 865          |
| COMPARE    | 1050         |
| TRACE      | 1100         |
| DEDUCE     | 1200         |
| SYNTHESIZE | 1400         |
| WEIGH      | 1531         |

The retrieval savings subtract from this base: a `DEDUCE` with
`RETRIEVAL_BUDGET: NONE` has 1200 - 865 = 335 effective neurons, while
the same operation with `RETRIEVAL_BUDGET: STANDARD` stays at 1200.
That is why choosing `NONE` when the inputs really are self-contained
has an outsized effect on the budget.

### Est. Cost

Dollars-and-cents estimate for this transaction, computed from
estimated input tokens, estimated output tokens, and the per-model
pricing:

| Tier                | Input / 1K | Output / 1K |
|---------------------|-----------:|------------:|
| BERT_CLASS          | $0.00002   | $0.00002    |
| GENERATIVE_LIGHT    | $0.001     | $0.005      |
| GENERATIVE_STANDARD | $0.003     | $0.015      |
| GENERATIVE_DEEP     | $0.015     | $0.075      |

Input tokens are estimated from the system-prompt baseline (~500
tokens), 200 tokens per premise, and 150 tokens per retrieved
knowledge item. Output tokens scale from a per-type base by the depth
multiplier (SHALLOW 0.5, STANDARD 1.0, THOROUGH 1.5, DEEP 2.5,
EXHAUSTIVE 4.0). A BERT retrieval phase adds a small fixed $0.00004
when `RETRIEVAL_BUDGET` is above NONE.

### Est. Latency

Milliseconds per transaction, using these per-model latencies: BERT
~50ms, Haiku ~2,000ms, Sonnet ~4,000ms, Opus ~6,000ms. BERT retrieval
adds 50ms when active. These are estimates; real latency depends on
token counts and network conditions.

## The Totals Block

- **Total estimated cost.** The sum of per-transaction `Est. Cost`.
- **vs. all-Opus baseline.** A comparison cost: what you would have
  paid if every operation had dispatched to Opus with no routing
  optimization. This uses a fixed assumption of 3,000 input + 1,000
  output tokens per call, priced at Opus rates -- roughly $0.12 per
  transaction. This is the number that makes the savings meaningful;
  without it, "$0.044 total" is just a number.
- **Savings %.** `(1 - total / baseline) * 100`.
- **Estimated latency.** The sum of sequential transactions plus the
  `max()` within each parallel group -- *not* the naive sum. See
  below.

## Parallel Groups

When the compiler's dependency analyzer finds operations that share
inputs but do not depend on each other's outputs, it groups them for
concurrent dispatch. The header prints which groups exist:

```
Parallel groups: group-1(3)
```

...meaning three transactions will run concurrently in the same wall-
clock window. For latency, the group counts as `max(per-tx-latency)`
rather than the sum. For the `test_10_retrieval_budget.ailang`
program -- three independent `DEDUCE` operations followed by one
`COMPARE` -- the parallel plan is about 6 seconds wall-clock versus
14 seconds fully sequential.

Note: runtime parallel dispatch is currently limited by a known issue
(P4 in the architecture notes) and the three operations may actually
execute sequentially at runtime today, even though the plan groups
them. The budget report reflects the plan, not the current runtime
limitation.

## When Escalation Happens

Escalation is when the compiler pushes a transaction up the model tier
ladder from its default. The Model column lets you spot this. If you
wrote `COMPARE` and see `claude-haiku-4-5` instead of the expected
BERT model, your output schema triggered rule 4. If you wrote
`DEDUCE` and see `claude-opus-4-6`, you probably declared `DEPTH:
EXHAUSTIVE`, which applies to anything.

Escalation is usually what you want -- it happens when the declared
output cannot actually be produced by the cheaper tier -- but it is
also the most common cause of unexpected cost. When a budget looks
too high, the escalation column is the first place to look.

## Walkthrough: `test_10_retrieval_budget.ailang`

This program (214 lines, ships with AILang) runs the same
international-trade-compliance question three times with progressively
more retrieval budget, then compares the three results.

`aiex compile test_10_retrieval_budget.ailang` produces:

```
Transactions: 4
Parallel groups: group-1(3)

  TX                    Operation  Retrieval  Model                 Neurons  Est. Cost  Est. Latency
  tx-1-tariff_none      DEDUCE     NONE       claude-sonnet-4-6         335  $0.0198    4000ms
  tx-2-tariff_minimal   DEDUCE     MINIMAL    claude-sonnet-4-6         535  $0.0253    4050ms
  tx-3-tariff_standard  DEDUCE     STANDARD   claude-sonnet-4-6        1200  $0.0347    4050ms
  tx-4-compare_runs     COMPARE    NONE       claude-haiku-4-5         1050  $0.0070    2000ms

  Total estimated cost:    $0.0868
  vs. all-Opus baseline:   $0.3400
  Savings: 74.4%
  Estimated latency:       6050ms
```

Reading it line by line:

- **TX-1 through TX-3** are all `DEDUCE` on the same premises. The
  only thing that varies is `RETRIEVAL_BUDGET`. You can see that in
  the Neurons column: 335 -> 535 -> 1200 as `NONE` -> `MINIMAL` ->
  `STANDARD`. That is the retrieval-savings arithmetic made visible.
  Costs follow suit: $0.02 -> $0.025 -> $0.035. All three stay on
  Sonnet because `DEPTH: STANDARD` does not trigger escalation.
- **TX-4** is the `COMPARE`. Its base routing is BERT, but the output
  schema contains `findings: LIST OF TEXT` and
  `terminology_precision_ranking: LIST OF TEXT` -- qualitative text
  fields -- so rule 4 escalates it to Haiku. That is why you see
  `claude-haiku-4-5` rather than `all-MiniLM-L6-v2`. Cost is only
  seven-tenths of a cent; Haiku is cheap.
- **Parallel groups.** The header shows `group-1(3)`, which groups
  TX-1, TX-2, and TX-3. TX-4 depends on all three (it reads their
  committed results) and runs after the group's barrier. Wall-clock
  latency is max(4000, 4050, 4050) + 2000 = 6,050ms.
- **Totals.** Nine cents for a four-transaction trade-compliance
  analysis with three retrieval strategies and a cross-validation.
  Against an all-Opus baseline of 34 cents, that is a 74% saving --
  and the saving comes entirely from routing `COMPARE` to Haiku and
  keeping `DEDUCE` on Sonnet instead of sending everything to Opus.

## Walkthrough: `loan_eligibility.ailang`

The loan-eligibility example is the larger end of what AIEX programs
look like today. It has five transactions: two `EXECUTE_CODE` blocks
(deterministic Python) and three reasoning operations -- a `DEDUCE`
for hard rule checks, a `WEIGH` for borderline judgment, and a
`SYNTHESIZE` for the final decision.

```
Transactions: 5

  TX                         Operation   Retrieval  Model                  Neurons  Est. Cost  Est. Latency
  tx-1-affordability         CODE        NONE       local-code                  0  $0.0000    10ms
  tx-2-hard_eligibility      DEDUCE      NONE       claude-sonnet-4-6         335  $0.0144    4000ms
  tx-3-soft_factors          WEIGH       NONE       claude-opus-4-6           666  $0.0135    6000ms
  tx-4-final_decision        SYNTHESIZE  NONE       claude-opus-4-6           535  $0.0162    6000ms
  tx-5-audit                 CODE        NONE       local-code                  0  $0.0000    10ms

  Total estimated cost:    $0.0441
  vs. all-Opus baseline:   $0.3600
  Savings: 87.8%
  Estimated latency:       16020ms
```

A few things stand out:

- **CODE rows are free and fast.** `EXECUTE_CODE python:` blocks
  dispatch to an in-process Python executor, not to an LLM, so they
  have zero cost and tens-of-milliseconds latency. The loan program
  uses them for the amortisation formula (step 1) and the audit-log
  formatting (step 5) -- exactly the tasks a probabilistic model
  should not be doing. Every dollar of Est. Cost here comes from the
  three reasoning steps.
- **`DEDUCE` chose Sonnet.** The hard-eligibility check is routine
  rule application; Sonnet is the right tier. 335 neurons (1200 minus
  865 retrieval savings) -- `RETRIEVAL_BUDGET: NONE` is appropriate
  because all rules are in the inputs.
- **`WEIGH` chose Opus.** Authority-weighted judgment is where the
  frontier model pays off, and the routing table defaults `WEIGH` to
  Opus. 666 neurons (1531 minus 865 savings).
- **`SYNTHESIZE` chose Opus.** Integration of the hard + soft outputs
  into a single regulated decision is a case where you do not want
  the model cutting corners. 535 neurons (1400 minus 865 savings).
- **Total: $0.044, saving 87.8%.** The baseline -- all-Opus -- would
  be $0.36. The saving comes from sending nothing to an LLM that does
  not need one (the two Python blocks), sending `DEDUCE` to Sonnet,
  and reserving Opus for the two operations that genuinely need it.

That 87.8% saving is not a marketing claim. It is a direct
consequence of routing being a compile-time decision made per
operation, with the accounting visible in the report. When you tune
the program -- change `DEPTH`, change `RETRIEVAL_BUDGET`, split one
operation into two -- the savings number moves in real time and you
can see what the change cost you before you pay for it.

## Reading Practice

When you look at a budget report, the right reading order is:

1. Glance at **Savings %** and total cost. Is this roughly what you
   expected?
2. Scan the **Model** column for surprises. Did anything escalate to
   Opus unexpectedly?
3. Check the **Retrieval** column. Should any of these really need
   `STANDARD`? Dropping to `NONE` or `MINIMAL` is the biggest single
   lever.
4. Look at **Parallel groups** in the header. If three operations
   could run in parallel but don't appear in a group, they have an
   accidental dependency -- usually because one reads another's
   result. That is a source-program issue, not a report issue.
5. Sum the `Est. Latency` of the critical path. If it is too slow,
   the SHALLOW/STANDARD/THOROUGH/DEEP lever on individual operations
   is where to start.

Chapter 5 opens up the typed reasoning operations in full, so you can
make informed choices about which operation to reach for when the
budget report tells you a step is too expensive.
