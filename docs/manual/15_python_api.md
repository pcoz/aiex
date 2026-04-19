# Chapter 15: The Python API

The `aiex` CLI is convenient for one-off runs and CI pipelines, but most real integrations embed AIEX inside a larger Python application: a web service that runs an AILang program for every incoming request, a batch job that sweeps a program across thousands of input rows, a notebook where an analyst iterates on contracts interactively. The AIEX engine exposes a small, stable Python surface that makes all of those trivial. This chapter walks through that surface from the simplest possible invocation up to a programmatic sweep.

## The simplest case: four lines plus a `run`

Every AIEX program goes through the same three stages: lex, parse, compile-and-run. The engine bundles compile and run together so the caller only sees two objects -- the `Program` AST and the `ProgramState` that comes back.

```python
from aiex.parser.lexer import Lexer
from aiex.parser.parser import Parser
from aiex.runtime.engine import AiexEngine

src = open("my_program.ailang").read()
tokens = Lexer(src).tokenize()
program = Parser(tokens).parse()
engine = AiexEngine(verbose=False)
state = engine.run(program, source_file="my_program")
```

That is the whole API for the common case. `Lexer(source).tokenize()` returns a filtered token stream; `Parser(tokens).parse()` returns an immutable `Program` AST; `engine.run(program, source_file=...)` compiles the program to an `ExecutionPlan`, walks the plan transaction by transaction, and returns a `ProgramState` once every transaction has committed or rolled back.

The `source_file` string is cosmetic -- it is embedded in the budget report header and in the execution log, nothing more. Pass the scenario name or the original file path; whichever will make your logs easier to read.

## The ProgramState object

`ProgramState` is the one object you spend any real time with. It is defined in `aiex.models.state` and it is deliberately a plain mutable Python class, not a Pydantic model, because its whole job is to be written to during execution. Three attributes carry almost everything you will want:

```python
state.variables      # dict[str, Any]  -- SET values plus committed outputs
state.results        # dict[str, TransactionResult] -- one entry per transaction
state.execution_log  # list[dict] -- timestamped event stream
```

`state.variables` holds two kinds of entries. Anything set by a `SET` statement at the top of the AILang program is there from the moment `engine.run` begins. Anything committed by a transaction with an `-> $output_ref` or emitted by an `EXECUTE_CODE` block is added as that transaction commits. If a transaction rolls back, its output never lands here -- this is the atomicity guarantee from [chapter 12](12_dispatch_validate_commit.md).

`state.results` is keyed by the compiler-generated transaction ID (for example, `tx-2-hard_eligibility`). Each value is a `TransactionResult` with a `.status` (COMMITTED or ROLLED_BACK), a `.response` dict (the parsed JSON the LLM returned -- `None` on rollback), a `.dispatch_meta` telemetry record (model used, tokens, latency), and a `.validation` object listing any contract violations found.

`state.execution_log` is a flat, timestamped list. Every `set_variable`, every `commit_result`, every `COMMIT`, every `ROLLBACK` is appended here in UTC order. It is the audit trail. If you are writing a service that has to produce a regulatory-grade record of what happened during a run, this list is what you persist.

## Finding a specific transaction's output

The `loan_eligibility` example in the AILang repo (under [`examples/loan_eligibility/scenarios/run_all.py`](https://github.com/pcoz/ailang/blob/main/examples/loan_eligibility/scenarios/run_all.py)) shows the pattern for pulling one transaction's response out of `state.results` without having to know the exact compiler-generated ID. The ID always contains the operation's result name, so a fragment match is enough:

```python
def _find_response(state, fragment: str):
    """Locate a transaction result by name fragment."""
    for tx_id, res in state.results.items():
        if fragment in tx_id:
            return res.response
    return None

final = _find_response(state, "final_decision")
if final is not None:
    print(final["decision"], final["confidence"])
```

This decouples your calling code from the compiler's ID scheme (which has changed once already and may change again). As long as the AILang `result_name` stays the same, the lookup keeps working.

## Compile-only usage

If you only want the plan -- for auditing, for a cost preview, for a dashboard that shows operators what a run *would* cost -- use `compile_only`:

```python
plan = engine.compile_only(program, source_file="my_program")

for tx in plan.transactions:
    print(tx.id, tx.operation_type.value, tx.routing.base_tier.value)

print(f"Total cost: ${plan.total_cost:.4f}")
print(f"Savings vs all-Opus: {plan.savings_pct}%")
```

`compile_only` does not touch an LLM. It is pure compile-time analysis: dependency graph, routing, contracts, budget. The returned `ExecutionPlan` is a frozen Pydantic model, so it is safe to serialise, compare, or cache. Two runs that parse-and-compile to the same plan will cost the same; diffing two plans is a reliable way to see the effect of changing an operation's `DEPTH` or `RETRIEVAL_BUDGET` before spending any tokens.

## Injecting a custom LLM client

Sometimes you want to run the full pipeline -- routing, dispatch, contract validation, commit -- but not hit a real model. Tests are the obvious case; so is a prototype where you have a stored response you want to replay against an updated contract.

AIEX uses `typing.Protocol` for its client interfaces, which means you do not need to inherit from any base class. Anything with a matching `create_message` signature satisfies `LLMClient`:

```python
from typing import Any

class ReplayClient:
    def __init__(self, responses: dict[str, dict]):
        self._responses = responses  # keyed by anything stable, e.g. operation name

    def create_message(
        self,
        model: str,
        system: str,
        user_message: str,
        max_tokens: int,
        temperature: float,
    ) -> dict[str, Any]:
        # Route by some key you pick -- here, the first line of the system prompt.
        key = system.split("\n", 1)[0]
        return self._responses.get(key, {"_parse_error": "no stored response"})

engine = AiexEngine(llm_client=ReplayClient(my_recorded_responses), verbose=False)
state = engine.run(program)
```

The same trick works for BERT. `BertClient` expects `extract_terms` and `search`; supply your own implementation and it is dropped in. The built-in `MockLLMClient` and `MockBertClient` (in `aiex.runtime.dispatch`) are good references if you want to see the minimum viable shape.

## The verbose flag

`AiexEngine(verbose=True)` (the default when nothing is passed) prints the pre-execution budget report, a per-transaction trace as each dispatch happens, warnings on MINOR violations, and a post-execution summary -- all via Rich so it looks reasonable in a terminal. `verbose=False` silences all of that. When you are embedding AIEX in a service that has its own logging, or when you are sweeping the same program over hundreds of inputs and don't want a wall of text, pass `verbose=False` and read the state yourself afterwards.

A small but useful pattern: run with `verbose=False` in production, but if a run produced any rolled-back transactions, re-run the same inputs with `verbose=True` and pipe the output to a log for human triage.

## Sweeping a program over many inputs

The canonical pattern -- the one the `loan_eligibility` example implements -- is: load one AILang program, run it against N different input variants, aggregate the structured outputs. The lexer and parser are pure so you pay their cost only once; the engine is stateless across runs so a single instance handles every variant.

```python
from pathlib import Path
from aiex.parser.lexer import Lexer
from aiex.parser.parser import Parser
from aiex.runtime.engine import AiexEngine

def run_scenarios(scenario_dir: Path) -> list[dict]:
    engine = AiexEngine(verbose=False)
    results = []
    for src_path in sorted(scenario_dir.glob("scenario_*.ailang")):
        src = src_path.read_text(encoding="utf-8")
        tokens = Lexer(src).tokenize()
        program = Parser(tokens).parse()
        state = engine.run(program, source_file=src_path.stem)

        decision = None
        for tx_id, res in state.results.items():
            if "final_decision" in tx_id and res.response is not None:
                decision = res.response
                break

        results.append({
            "scenario": src_path.stem,
            "applicant": state.variables.get("applicant"),
            "decision": decision,
            "committed": sum(1 for r in state.results.values() if r.status.value == "COMMITTED"),
            "rolled_back": sum(1 for r in state.results.values() if r.status.value == "ROLLED_BACK"),
        })
    return results
```

This is effectively what `run_all.py` does. Each scenario file is an independent AILang program; each run produces its own `ProgramState`; the outer loop aggregates. Because `engine.run` is synchronous, you can wrap the loop in `concurrent.futures.ThreadPoolExecutor` to parallelise across scenarios (one AIEX run per thread). AIEX's own internal parallel groups are a separate concern -- see [P4 in chapter 17](17_troubleshooting.md) for the current status there.

With the API in hand, building a service around AIEX is a matter of deciding where to get the program and inputs from (a database, an HTTP request, a queue) and where the `ProgramState` goes afterwards (a results table, a downstream queue, a webhook). The AIEX half stays exactly as it is in this chapter.
