# Chapter 2: Installation and Hello World

The goal of this chapter is to get you from nothing to a running AIEX
program in about ten minutes. You will install the CLI, compile a
one-line AILang program, and run it. If you do not have an API key
yet, you can still do most of this -- `aiex compile` never calls a
model.

## Prerequisites

- **Python 3.11 or newer.** `python --version` should print 3.11 or
  higher.
- **An Anthropic API key** if you plan to run programs live. Go to
  <https://console.anthropic.com>, create a key, and keep it somewhere
  safe.
- **Git.** To clone the repository.

AIEX itself is a small Python package (about 3,500 lines) with three
direct dependencies -- `anthropic`, `pydantic`, and `rich` -- so
installation is quick and does not require any build tools beyond
`pip`.

## Clone and Install

```bash
git clone https://github.com/pcoz/aiex.git
cd aiex
pip install -e .
```

The `-e` installs the package in editable mode, which means the
`aiex` command on your PATH will follow any local changes you make to
the source. This is how the project is set up for development today;
when a released wheel lands, `pip install aiex` will work without the
`-e`.

## Configure Your API Key

AIEX looks for `ANTHROPIC_API_KEY` in a `.env` file sitting next to
the project's `pyproject.toml`. Create it:

```bash
# From the repo root
echo 'ANTHROPIC_API_KEY=sk-ant-...' > .env
```

The CLI walks up from its own module location to find `pyproject.toml`
and then loads `.env` from that directory, so this works no matter
what your current working directory is when you run `aiex`.

If you want to skip this step for now, that is fine. Every
`aiex compile` invocation runs in dry-run mode and never calls a
model, so you can explore the compiler and budget reports without an
API key. You can also pass `--dry-run` to `aiex run` to exercise the
full pipeline against a mock model that returns deterministic
placeholders -- useful for CI and for getting a feel for the runtime
before spending any credits.

## Verify the Install

```bash
aiex --help
```

You should see two subcommands: `compile` and `run`. If you get
"command not found", check that your `pip install -e .` environment's
bin directory is on your PATH, or run `python -m aiex.cli --help` as a
fallback.

## Hello World

Create a file called `hello.ailang` with exactly this content:

```ailang
#ailang v0.8.2

SET city TO "Jerusalem"

RETRIEVE weather_facts FROM city WITH:
    RETRIEVAL_BUDGET: MINIMAL
    DEPTH: SHALLOW
    MUST_INCLUDE: [city_name, climate_zone]
    OUTPUT_FORMAT: {
        city_name: TEXT,
        climate_zone: TEXT,
        summary: TEXT
    }
END
```

This is a complete AILang program. It sets a single variable, then
issues one `RETRIEVE` operation that must return three structured
fields. The `MUST_INCLUDE` and `OUTPUT_FORMAT` clauses are enforced by
AIEX after the response comes back -- if the model produces something
that is missing `city_name` or cannot be parsed as JSON matching the
schema, the transaction rolls back.

Notice what is *not* there. No prompt string. No model name. No
temperature. No JSON parser. All of that is resolved by the compiler
from the operation type and its modifiers.

## Compile It

```bash
aiex compile hello.ailang
```

You will see output similar to:

```
=== AIEX Execution Budget Report ===
Program: hello
Source: hello.ailang
Transactions: 1
                           Transaction Budget
  TX                Operation  Retrieval  Model              Neurons   Est. Cost  Est. Latency
  tx-1-weather_fa.  RETRIEVE   MINIMAL    all-MiniLM-L6-v2       200   $0.0001    50ms

  Total estimated cost:    $0.0001
  vs. all-Opus baseline:   $0.0900
  Savings: 99.9%
  Estimated latency:       50ms
```

Because `RETRIEVE` routes to the BERT tier by default, and because
`DEPTH: SHALLOW` does not trigger escalation, this tiny program costs
a fraction of a cent and completes in about 50ms of model time. The
all-Opus baseline is calculated for comparison: if every operation
had been dispatched to Opus -- the naive default in most LLM apps --
this program would have cost about nine cents.

You have now compiled an AILang program. No API calls happened. No
credits spent. The compiler read your file, resolved routing, computed
budgets, and printed a plan.

## Run It

```bash
aiex run hello.ailang
```

This prints the same budget report, dispatches the operation to the
configured backend, validates the response against the contract, and
prints an execution summary:

```
=== Execution Summary ===
  Transactions committed:  1
  Transactions rolled back: 0
  Total latency:           ...ms

  Event log:
    [COMMIT] TX tx-1-weather_facts: COMMITTED
```

If you don't have an API key configured, run instead:

```bash
aiex run --dry-run hello.ailang
```

`--dry-run` substitutes mock clients that return structured
placeholder responses. You get the full pipeline -- routing, contract
validation, commit/rollback -- without touching a real model. This is
also how CI pipelines exercise AIEX programs.

## What You Are Seeing

The runtime printed a budget report first, then executed each
transaction in topological order, then printed a summary. Every
committed transaction's result is now in program state. If you had
chained another operation downstream of this one (see
[chapter 6](06_chaining_operations.md)), it would have read
`weather_facts` from state and proceeded.

To persist the structured results to disk:

```bash
aiex run -o results.json hello.ailang
```

This writes a JSON file containing every committed result, every
variable, and the full execution log. It is the audit trail the
architecture chapter referenced -- not a side effect of logging
sprinkled through the code, but a first-class output of the runtime.

## Next

You have installed AIEX, compiled a program, and run it. The program
was deliberately trivial so you could focus on the tooling. In
[chapter 3](03_your_first_program.md) you will write a realistic
program incrementally, adding one contract clause at a time and
watching the budget report and runtime behaviour change with each
addition. Chapter 4 opens up the budget report column by column so you
can read it the way a compiler writer does.
