# Chapter 16: Architecture

## Why COBOL?

COBOL was the language built for transactional business systems, and it remained durable in banking, insurance, and government for sixty years because it got the abstractions right -- divisions to separate concerns, contracts as first-class artefacts, atomic transactions with rollback, declarative data definitions sitting beside procedural logic. The industry spent a long time sneering at it and then quietly discovered that nothing that replaced it offered the same structural guarantees. AIEX inherits those abstractions on purpose. It solves the same *shape* of problem -- qualitative business rules applied consistently at scale -- for a substrate (LLMs) that is probabilistic, non-deterministic, and more in need of those guarantees than a mainframe batch job ever was. The familiar five-division structure is not nostalgia; it is the cheapest way to make AI reasoning auditable.

## The five divisions in AIEX

Every AILang program compiles to an `ExecutionPlan` organised into exactly five divisions, in fixed order. The names echo COBOL; the content is AIEX-specific.

### PROGRAM DIVISION

Program metadata: identifier, source file path, compiler version, target engine, trace level. This is the IDENTIFICATION DIVISION equivalent. At compile time the compiler populates `ExecutionPlan.program_id` and `.source_file` from the AILang source and the caller's arguments. At runtime these fields are logged once at the top of the budget report and then carried into the execution log's events so every trace line can be correlated back to the program that produced it.

The PROGRAM DIVISION is small and boring. That is exactly right -- its value is in being unambiguous, not interesting.

### ENGINE DIVISION

Configuration of the execution substrate: which reasoning type routes to which model tier, what the model pool looks like (ids, costs, latencies, token limits), the retrieval budget table, the depth table, the style table, and the prompt templates. This is the ENVIRONMENT DIVISION's lineal descendant.

At compile time the compiler consults `DEFAULT_MODEL_POOL` and `DEFAULT_ROUTING_TABLE` (in `aiex/models/routing.py`) and produces one `RoutingDecision` per operation, recording the base tier, any escalation, the template name, the temperature, and the neuron budget. At runtime those decisions are immutable -- the dispatcher reads them and forwards to the chosen model. The ENGINE DIVISION is the one place a future operator could override routing (e.g., pin every DEDUCE to Opus during a high-stakes batch) without touching the program itself.

### STATE DIVISION

The WORKING-STORAGE analog. Contains two kinds of data: *variables* (immutable inputs from `SET` statements, plus committed outputs from prior transactions that declared an `output_ref`) and *results* (the full `TransactionResult` record for every transaction that has run -- whether committed or rolled back).

At compile time the STATE DIVISION is a type sketch: the compiler records which variables are referenced and what their declared types are. At runtime it becomes a live `ProgramState` object (see `aiex/models/state.py`) that the engine mutates as transactions commit. Rolled-back transactions leave their result in `state.results` with `status = ROLLED_BACK` and `response = None`, but never write to `state.variables`. This is how AIEX's atomicity promise is kept: downstream operations reading state cannot see partial or invalid upstream output.

### CONTRACT DIVISION

The boundaries attached to every operation: `MUST_INCLUDE`, `CANNOT_INCLUDE`, `OUTPUT_FORMAT`, `RETRIEVAL_BUDGET`. Each AILang `BoundarySpec` compiles to a `TransactionContract` with a list of `ContractCheck` records, a schema, and a severity policy mapping CRITICAL/MAJOR/MINOR to ROLLBACK/RETRY/WARN.

COBOL scattered its validation logic across the PROCEDURE DIVISION as IF checks -- "if age < 18 then reject" buried fifty lines into the business flow. AIEX promotes validation to a top-level division because in LLM orchestration, boundary enforcement *is* the program. A DEDUCE operation that returns valid JSON but violates a `CANNOT_INCLUDE` rule has failed just as completely as one that never responded. Making the contract a structural concern means the compiler can report on it, the runtime can enforce it uniformly, and a reviewer can audit it without reading every operation's body.

### EXECUTION DIVISION

The ordered list of transactions, the parallel-group map, and the dependency edges. This is COBOL's PROCEDURE DIVISION. The compiler builds a DAG from the AST (in `aiex/compiler/analyzer.py`), performs a topological sort, and identifies parallel groups -- sets of operations with identical dependency signatures and no mutual dependency. The runtime walks the result in order, dispatching groups as units and sequential transactions one by one.

The EXECUTION DIVISION is the only division whose shape is meaningfully determined by the program's content. PROGRAM is metadata; ENGINE is configuration; STATE is storage; CONTRACT is declarative boundaries. Only EXECUTION describes *what happens*. That restriction is the point: AIEX pushes as much as possible out of the execution pipeline into the surrounding divisions, leaving the runtime loop small and auditable.

## Compile pipeline

The compiler is a sequence of transformations, each taking the previous stage's output and adding one concern:

```
 PARSE     -- source text becomes the AST (Program node with operations,
              variables, context blocks)
 ANALYZE   -- AST becomes a DependencyGraph (edges from data references,
              plus parallel-group detection)
 ROUTE     -- each operation gets a RoutingDecision (tier, template,
              temperature, neuron budget) with escalation rules applied
 CONTRACT  -- each BoundarySpec becomes a TransactionContract
              (must-include checks, cannot-include checks, schema,
              severity policy)
 SEQUENCE  -- topological sort produces the transaction execution order
              consistent with the dependency graph
 BUDGET    -- each transaction gets a pre-execution cost / latency estimate
              against the routing tier's pricing
 EMIT      -- everything above is assembled into one frozen ExecutionPlan
```

By the time EMIT completes, every decision the runtime will need is fixed and serialisable. Pricing of a run is a read-only operation against the plan. Re-running the same program against the same inputs produces the same plan. This is the deliberate consequence of doing all routing and validation work at compile time rather than in the hot path.

## Runtime cycle

The runtime does exactly one thing, over and over, for each transaction in the plan: dispatch -> validate -> commit-or-rollback. The code lives in `aiex/runtime/engine.py`; the important properties are:

- **Dispatch** composes the system prompt from template + retrieval modifier + depth suffix + boundary instructions, composes the user message from the declared input references, optionally performs BERT retrieval to inject domain context, and sends everything to the LLM tier the compiler chose.
- **Validate** parses the JSON response, checks it against every `ContractCheck`, and aggregates violations. The worst severity wins: CRITICAL > MAJOR > MINOR.
- **Commit** writes the response to `state.results` and, if the transaction declares an `output_ref`, to `state.variables`. **Rollback** records the failure in `state.results` with `response = None` and leaves `state.variables` untouched. A MINOR violation commits the response but attaches the violation list; a MAJOR violation triggers a bounded retry loop with the violation details appended to the system prompt; a CRITICAL violation rolls back immediately.

No other mutation path exists. The entire runtime effect on `state.variables` happens through `commit_result` and `set_variable`, both of which emit events to the execution log. The audit trail is a consequence of the architecture, not a feature added on top.

## Data flow, end to end

```
 AILang source (.ailang)
        |
        | Lexer.tokenize()
        v
 Token stream
        |
        | Parser.parse()
        v
 AST (frozen Program, OperationNode, BoundarySpec, ...)
        |
        | compile_ailang()
        v
 ExecutionPlan (transactions, contracts, routing, budget)
        |
        | AiexEngine._execute_plan()
        v
 TransactionResult stream (one per transaction, in topological order)
        |
        | state.commit_result()  /  state.set_variable()
        v
 ProgramState (variables, results, digests, execution_log)
```

Each arrow is the output of one clearly defined module and the input of the next. The AST and ExecutionPlan are immutable; everything upstream of the engine is a pure transformation. Mutability appears only once, at the very end, and only through `ProgramState` -- which is itself a narrow interface with logged mutations. For more detail on each individual transformation, see the [compilation walkthrough](../compilation_walkthrough.md) and [chapter 12](12_dispatch_validate_commit.md) on the runtime cycle.

## The design principle

The thread that ties everything together is best stated once and then held to everywhere else: **declarative intent, procedural execution**. The AILang programmer declares *what* reasoning to perform and *what* boundaries must hold. AIEX figures out *how*: which model tier handles this reasoning type, what prompt scaffolds it best, what retries to run on a failed boundary, which transactions can run in parallel, how much the whole run will cost before the first token is spent.

This is an inversion of classical programming. In classical programming the programmer specifies the procedure and the runtime executes literally; the burden of getting it right -- including all the edge cases, retries, and validations -- falls on the code. In AIEX the programmer specifies the intent and the runtime is responsible for the procedural detail; the substrate takes care of routing, dispatch, contract validation, rollback, and budget. The COBOL division structure is what makes that division of labour legible. PROGRAM and ENGINE are the runtime's territory; STATE and EXECUTION are the shared surface; CONTRACT belongs to the programmer but is enforced by the runtime. The reason AIEX can promise an audit trail and a pre-execution cost estimate is that it reserved the space for both in the architecture before the first line of runtime code was written.
