# Chapter 14: CLI Reference

The `aiex` command has two subcommands: `compile` and `run`. Both
take an AILang source file as their single positional argument.
Everything else is optional flags.

This chapter is the complete reference. For conceptual discussion
see [chapter 10](10_execute_code.md) through
[chapter 13](13_configuration.md); for the Python API see chapter 15.

## `aiex compile`

Parses an AILang file, compiles the execution plan, and prints the
budget report. No LLM calls are made. No API key is required.

```
aiex compile <file> [--json]
```

### Arguments

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `file`   | path | required | AILang source file to compile |
| `--json` | flag | false   | After the budget report, also print the full `ExecutionPlan` as JSON |

### Behaviour

1. Reads `file` as UTF-8.
2. Parses it with `aiex.parser.parse_ailang`. On parse error, prints
   a red `Parse error: ...` message and exits with a non-zero code.
3. Constructs an `AiexEngine` with `dry_run=True` (which swaps
   `MockLLMClient` in, though no LLM call happens in compile mode
   anyway) and `verbose=True`.
4. Calls `engine.compile_only(program, source_file=...)`.
5. The engine prints the budget report.
6. If `--json` is present, prints the `model_dump_json(indent=2)` of
   the plan.
7. Exits with code 0.

### Exit Codes

| Code | Meaning |
|------|---------|
| 0    | Parse and compile succeeded |
| 1    | File not found, or parse error, or no subcommand given |

### Example

```bash
aiex compile loan_eligibility.ailang
```

Output (abridged):

```
=== AIEX Execution Budget Report ===
Program: loan_eligibility
Source: loan_eligibility.ailang
Transactions: 5

                        Transaction Budget
TX                  Operation   Retrieval  Model              Est. Cost  Est. Latency
tx-1-affordability  CODE        NONE       local-code         $0.0000    100ms
tx-2-hard-elig      DEDUCE      NONE       claude-sonnet-4-6  $0.0144    4000ms
tx-3-soft-factors   WEIGH       NONE       claude-sonnet-4-6  $0.0141    4000ms
tx-4-synth-decision SYNTHESIZE  NONE       claude-sonnet-4-6  $0.0162    4000ms
tx-5-audit          CODE        NONE       local-code         $0.0000    100ms

  Total estimated cost:    $0.0447
  vs. all-Opus baseline:   $0.0745
  Savings: 40.0%
  Estimated latency:       8200ms
```

### JSON Output

```bash
aiex compile loan_eligibility.ailang --json
```

The JSON emitted is a serialised `ExecutionPlan` including every
transaction, its routing decision (base tier, escalation tier,
escalation reason, template, temperature, neuron budget), its
contract (every MUST_INCLUDE check, CANNOT_INCLUDE check, output
schema), and its budget estimate. This is useful for piping into
other tools:

```bash
aiex compile loan.ailang --json | jq '.transactions[] | {id, tier: .routing.escalated_to // .routing.base_tier}'
```

## `aiex run`

Parses, compiles, and executes. By default makes real API calls
against Anthropic.

```
aiex run <file> [--dry-run] [--verbose | --quiet] [--output FILE]
```

### Arguments

| Argument            | Type | Default | Description |
|---------------------|------|---------|-------------|
| `file`              | path | required | AILang source file to execute |
| `--dry-run`         | flag | false   | Use `MockLLMClient`: no API calls, no cost |
| `--verbose`, `-v`   | flag | true    | Print per-transaction trace (on by default) |
| `--quiet`, `-q`     | flag | false   | Suppress the trace and summary; overrides verbose |
| `--output`, `-o`    | path | (none)  | Write the final state as JSON to this path |

The `--verbose` flag is on by default. `--quiet` wins if both are
passed. In the code this is `verbose = not args.quiet`.

### Behaviour

1. Reads and parses the source file (identical to compile).
2. Prints a one-line header:
   ```
   AIEX v0.1.0
   Source: <path>
   Mode: LIVE  |  Mode: DRY RUN (mock LLM)
   ```
3. Constructs `AiexEngine(dry_run=args.dry_run, verbose=verbose)`.
4. Calls `engine.run(program, source_file=...)`.
5. The engine compiles, prints the budget report, executes each
   transaction in dependency order, and returns the final
   `ProgramState`.
6. If `--output` is set, writes a JSON document to that path
   containing `variables`, `results` (with per-transaction status
   and response), and the full `execution_log`. Variables whose
   names start with `_` are excluded from this output.

### Exit Codes

| Code | Meaning |
|------|---------|
| 0    | Program completed (transactions may still have rolled back individually; check the exit summary) |
| 1    | File not found, or parse error, or no subcommand given |

Note that individual transaction rollbacks do not set a non-zero
exit code. They are expected outcomes in many programs -- a contract
violation leading to a rollback is a correct runtime behaviour. To
detect rollbacks programmatically, inspect `state.results` via the
Python API.

### Requirements

`aiex run` in LIVE mode requires:

- `ANTHROPIC_API_KEY` set in a `.env` file readable by the loader
  (see [chapter 13](13_configuration.md)).
- Network access to `api.anthropic.com`.
- At least one non-code transaction in the program (a program
  consisting only of `EXECUTE_CODE` blocks technically does not need
  API access, but the CLI does not special-case this today).

`aiex run --dry-run` relaxes all of these.

### Examples

Live run:

```bash
aiex run loan_eligibility.ailang
```

Dry run, quiet (useful in CI):

```bash
aiex run --dry-run --quiet loan_eligibility.ailang
```

Live run with results written to JSON:

```bash
aiex run loan_eligibility.ailang -o results.json
```

The resulting `results.json` shape:

```json
{
  "variables": {
    "applicant": { ... },
    "product": { ... },
    "affordability_metrics": { ... },
    "hard_eligibility": { ... },
    "soft_factors": { ... },
    "final_decision": { ... },
    "audit_log_entry": { ... },
    "letter_excerpt": "..."
  },
  "results": {
    "tx-1-affordability": { "status": "COMMITTED", "response": {...} },
    "tx-2-hard-elig":     { "status": "COMMITTED", "response": {...} },
    ...
  },
  "log": [
    { "event": "COMMIT", "data": {"tx": "tx-1-affordability", "status": "COMMITTED", ...}, "timestamp": "..." },
    ...
  ]
}
```

## `aiex --help`

Runs argparse's built-in help. Shows the top-level subcommand list
and exits cleanly.

Subcommand help is available with the standard pattern:

```bash
aiex compile --help
aiex run --help
```

## Common Patterns

### Dry-run: budget exploration

```bash
aiex compile program.ailang
```

No API key. No network. No cost. Use this every time you change a
`DEPTH`, `RETRIEVAL_BUDGET`, or output schema and want to see the
new plan. The fastest feedback loop in AIEX.

### Full run with persisted results

```bash
aiex run program.ailang --output results.json
```

Standard production invocation. The JSON file is durable; the
results stay after the process exits, unlike the in-memory state
object. This is how regulatory evidence gets produced.

### CI / integration tests

```bash
aiex run program.ailang --dry-run --quiet
```

`--dry-run` swaps the LLM client for a mock. `--quiet` keeps the
log tidy. This runs the full dispatch-validate-commit pipeline
against mock responses so structural bugs (missing dependencies,
malformed schemas, unreachable transactions) surface without
spending API credits.

### Piping plans to a tool

```bash
aiex compile program.ailang --json > plan.json
```

The plan JSON is a full `ExecutionPlan` including every routing
decision and contract detail. Useful for producing dashboards, for
diffing plans across source changes, and for ingest into cost
tracking.

### Programmatic access

For workflows that need the final `ProgramState` object in process,
embedding programs inside a Python application, or running AIEX
inside a service, use the Python API described in chapter 15.
`aiex run` is the appropriate tool when the integration point is a
shell, and a pure CLI invocation produces enough of a record. When
you need the committed response objects as Python values rather
than JSON, the Python API is the cleaner path.

## Summary of the Full Command Surface

```
aiex compile <file> [--json]
aiex run     <file> [--dry-run] [--verbose | --quiet] [--output FILE]
```

Two subcommands, six flags between them, one positional argument
each. That is the entire CLI.
