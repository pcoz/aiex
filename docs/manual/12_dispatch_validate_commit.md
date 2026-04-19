# Chapter 12: Dispatch, Validate, Commit

COBOL's transaction processing model had a simple rule: a
transaction either completes atomically, or it leaves the system as
if it never happened. You do not end up with a half-updated ledger
because someone's socket timed out in the middle of a write.

AIEX uses the same frame for LLM calls. Every operation in an AILang
program is a transaction. It goes through three phases -- dispatch,
validate, commit -- and either lands cleanly in program state or
rolls back with no trace except an audit log entry. Downstream
operations cannot read data from a rolled-back transaction because
the response is never written to state.

This chapter is about how that lifecycle actually works.

## The Four States

A transaction passes through up to four states during its lifetime:

- **DISPATCHED** -- the prompt has been sent; the engine is waiting
  for the response.
- **VALIDATING** -- the response came back; contract checks are
  running against it.
- **COMMITTED** -- validation passed, or only MINOR violations
  occurred. The response is now part of program state and readable by
  downstream transactions.
- **ROLLED_BACK** -- a CRITICAL violation fired, or a MAJOR violation
  survived all retry attempts. The response is discarded. Any
  transaction that was waiting on this one as a dependency is
  skipped with an `ERROR` event logged.

COMMITTED and ROLLED_BACK are terminal. The engine makes the commit
decision inside `_execute_transaction` in `engine.py` after the
validation phase completes.

## Dispatch: Composing and Sending

Dispatch is the easy phase. The engine assembles a system prompt
from four layers:

1. An operation-specific template (DEDUCE, TRACE, COMPARE, and so
   on), loaded from `aiex/templates/`.
2. A retrieval modifier derived from `RETRIEVAL_BUDGET`. For `NONE`
   the modifier is a strict closed-book instruction; for `MINIMAL` it
   allows basic terminology resolution; `STANDARD` and `FULL` add no
   restriction.
3. A depth suffix derived from `DEPTH`. `SHALLOW` asks for a brief
   answer; `EXHAUSTIVE` asks the model to leave no stone unturned.
4. Boundary instructions rendered from the compiled contract --
   `MUST_INCLUDE` list, `CANNOT_INCLUDE` list, and the output schema
   as a concrete JSON example.

The user message carries the input data: variables named in
`FROM`/`APPLY_TO`/`SOURCES`, serialized as JSON. This layering keeps
instructions separate from data, which reduces the chance of the
model confusing the two.

If `RETRIEVAL_BUDGET` is not `NONE`, AIEX runs a BERT retrieval pass
first and appends the results to the user message as a labelled
`RESOLVED TERMINOLOGY` or `REGULATORY CONTEXT` section. Then the
composed prompt goes to the model identified by the routing
decision's tier.

## Validate: Checking the Response

Once the response arrives, the engine parses it as JSON (with a
three-tier fallback strategy that tolerates markdown fences and
prose-wrapped JSON), and runs `validate_contract`. Validation
iterates over the contract's checks in three phases:

### MUST_INCLUDE

Each MUST_INCLUDE check looks for a specific concept in the response.
Two detection methods are supported:

- `FIELD_EXISTS` -- direct key lookup. The field must exist and
  contain a non-empty value.
- `SEMANTIC_SCAN` -- concept-level check. With BERT available, this
  uses cosine similarity between the concept and response chunks,
  with a threshold of 0.45. Without BERT, it falls back to keyword
  matching with concept expansion (e.g. `causal_chain` expands to
  `["causal_chain", "causal chain", "causal", "chain"]`).

A failed MUST_INCLUDE produces a violation at the severity declared
in the AILang source. The compiler maps MUST_INCLUDE items to
`CRITICAL` by default.

### CANNOT_INCLUDE

Three detection methods cover different categories of unwanted
content:

- `TERMINOLOGY_DIFF` -- detects terms in the response that did not
  appear in the input. This is the primary enforcement for
  `RETRIEVAL_BUDGET: NONE`: if the model introduced external
  terminology the input did not contain, that is evidence it reached
  outside the closed-book scope. With BERT, the comparison is
  semantic (cosine similarity < 0.40 means "novel"); without BERT,
  it is a set-difference on extracted domain terms.
- `KNOWLEDGE_DEPTH_SCAN` -- regex patterns for US legal citations
  (USC, CFR, Public Law, proclamation numbers, case-order codes).
  These are high-confidence signals that the model reached into
  memorised external knowledge.
- `SEMANTIC_SCAN` -- general concept search. The concept is expanded
  into keyword variants and matched against the serialized response.

The compiler defaults CANNOT_INCLUDE violations to `MINOR` because
definitive semantic exclusion requires runtime BERT analysis; the
compiler-side pass is intentionally conservative.

### OUTPUT_FORMAT

Schema validation is structural. Missing fields, wrong types, and
enum constraint violations are all `CRITICAL` because they indicate
the model failed to produce the shape downstream steps depend on.
The validator walks the `OutputFormatSchema` field list and checks
each one: BOOLEAN must be a bool, NUMBER must be int or float,
enum-constrained TEXT/QUALITATIVE values must be in the allowed set,
LIST must be a list.

### Combining Violations

If any violation fires, the overall severity is the worst seen. A
single CRITICAL alongside five MINORs still produces a CRITICAL
result. The engine uses this overall severity to drive the next
decision.

## The Retry Loop

A clean pass commits. A MINOR-only result commits with warnings. But
what happens when a MAJOR or CRITICAL fires?

- **CRITICAL: abort immediately.** No amount of prompt reinforcement
  will fix a fundamentally broken structural response. The engine
  breaks out of the retry loop and proceeds to rollback.
- **MAJOR: retry with reinforced prompt.** The engine appends a
  `=== RETRY INSTRUCTION ===` block to the system prompt with the
  specific violation messages, then re-dispatches. This is more
  effective than a blind retry because the model sees exactly which
  boundary it crossed. The default `max_retries` is 2, for three
  total attempts.
- **MINOR: commit with warnings.** The response is accepted; each
  violation is logged as a `WARNING` in the execution trace, and the
  transaction lands in state.

The reinforcement block appended on retry looks like this:

```
=== RETRY INSTRUCTION ===
Your previous response violated these boundaries:
- Missing required content: dti_check
- Prohibited content detected: recommendations_about_apr_changes
You MUST fix these violations.
=== END RETRY ===
```

The model sees this on the next attempt and can self-correct. If all
retries are exhausted and MAJOR violations persist, the transaction
rolls back.

## Commit or Rollback

The final decision is straightforward. `validation.passed` (no
violations) or `severity == MINOR` leads to `COMMITTED`. Anything
else leads to `ROLLED_BACK`.

On commit, three things happen:

1. A `TransactionResult` is built with the response and dispatch
   metadata, and stored in `state.results` keyed by transaction ID.
2. If the transaction declared an output reference (e.g.
   `-> $my_result`), the response is written to that variable via
   `state.set_variable` so downstream transactions can read it.
3. A `COMMIT` event is appended to `state.execution_log` with the
   transaction ID and status.

On rollback, only the first and third happen, and the `response`
field on the stored result is `None`. Downstream transactions that
listed this one in their `depends_on` and try to read its output
will find the variable unset. When the engine walks the next
transaction, it checks `deps_met = all(d in completed for d in tx.depends_on)`; a
rolled-back dependency means the downstream is skipped entirely,
with an `ERROR` event logged naming the unmet dependencies.

This is the atomic commit semantics promised earlier. A failed
reasoning operation does not leave half-valid data for later steps
to consume. Either the response is in state and valid, or it is not
in state at all.

## Parallel Groups

When the AILang source uses a `PARALLEL` block, the compiler
identifies operations that can run together (no data dependencies
between them) and assigns them to a named parallel group.

The engine detects parallel-group membership via a reverse map
(`transaction ID -> group name`). When it walks the topological order
and encounters the first member of a group, it immediately dispatches
**every** member of that group and marks the whole group as
executed, so subsequent appearances of the same group's other
members in the topological order are skipped.

One honest caveat: the current implementation dispatches the group's
members in sequence, not concurrently (known issue P4). The parallel
group still serves as a barrier -- no transaction outside the group
can start until all members have committed or rolled back -- but
true async dispatch is a future enhancement. The group structure
is already in the plan, so when concurrency is wired in no AILang
program will need to change.

## Inspecting a Run After the Fact

Every state transition is captured. After the engine returns the
final `ProgramState`, three attributes are worth knowing:

- `state.variables` -- the final committed values of every AILang
  variable.
- `state.results` -- a dict keyed by transaction ID. Each value has
  `status`, `response`, `dispatch_meta` (model, tokens, latency),
  and `validation` (the list of violations, if any).
- `state.execution_log` -- an ordered list of events. Each entry is
  a dict with `event` (e.g. `COMMIT`, `ROLLBACK`, `ERROR`,
  `WARNING`), a timestamp, and arbitrary structured data.

A simple post-run inspection:

```python
from aiex.runtime.engine import AiexEngine
from aiex.parser import parse_ailang

program = parse_ailang(open("loan.ailang").read())
engine = AiexEngine(verbose=False)
state = engine.run(program, source_file="loan.ailang")

for tx_id, result in state.results.items():
    print(f"{tx_id}: {result.status.value}")
    if result.validation and result.validation.violations:
        for v in result.validation.violations:
            print(f"  [{v.severity.value}] {v.message}")
```

This is the audit trail that regulators ask for. Every decision the
pipeline made, every response it accepted, every response it
discarded, and the specific violation that caused each rollback.
The trace is structured, durable, and reproducible from the same
inputs when run against the mock LLM client.

For the CLI commands that drive this lifecycle, see
[chapter 14](14_cli_reference.md). For the configuration knobs that
control verbosity and the API client, see
[chapter 13](13_configuration.md).
