# AIEX — AI Execution Language

**The execution runtime for [AILang](https://github.com/pcoz/ailang) programs.** AIEX compiles AILang programs into a five-DIVISION COBOL-style execution plan, routes each reasoning operation to the cheapest LLM tier capable of producing the required output, runs `EXECUTE_CODE` blocks deterministically alongside reasoning, validates outputs against contracts with severity-tiered retry/rollback, and shows you the cost before you spend.

```
=== AIEX Execution Budget Report ===
Program: loan_eligibility/scenarios/scenario_01_clear_approve
Transactions: 5

| TX                | Operation     | Model       | Est. Cost |
|-------------------|---------------|-------------|-----------|
| tx-1-codeblock    | CODE_EXECUTION| local-code  |   $0.0000 |
| tx-2-hard_elig... | DEDUCE        | claude-...  |   $0.0144 |
| tx-3-soft_fact... | WEIGH         | claude-...  |   $0.0135 |
| tx-4-final_dec... | SYNTHESIZE    | claude-...  |   $0.0162 |
| tx-5-codeblock    | CODE_EXECUTION| local-code  |   $0.0000 |

  Total estimated cost:    $0.0441
  vs. all-Opus baseline:   $0.3600
  Savings: 87.8%
  Estimated latency:       8200ms
```

## Why AIEX

**For developers building AI reasoning systems where cost, auditability, and reproducibility matter.**

| Need | Without AIEX | With AIEX |
|---|---|---|
| Cost at scale | Pay Opus prices on every call | 80–90% lower via multi-tier routing per reasoning type |
| Predictable budget | Find out at month-end | Pre-execution report shows cost, latency, routing per transaction |
| Audit trail | Bolted on; often incomplete | Built in: every transaction logged with rationale, contract violations, retries |
| Output validation | Post-hoc parsing in your app | Contracts enforced at the runtime boundary with retry-with-reinforcement and rollback |
| Mixing deterministic + reasoning code | Glue code in Python | `EXECUTE_CODE` blocks compile to zero-cost transactions interleaved with reasoning |
| Reproducibility | None — every run drifts | Same program + same inputs → functionally equivalent decisions across runs (qualitative idempotency) |

## Main advantages

- **Pre-execution budget reports.** See the cost, latency, and routing for every transaction *before* you run the program.
- **Multi-tier model routing.** RETRIEVE/COMPARE → BERT/Haiku; TRACE/DEDUCE → Sonnet; WEIGH/SYNTHESIZE → Opus. Escalation rules kick in for `DEPTH: DEEP+` and qualitative output schemas. Routing decisions are logged.
- **Output contracts.** `MUST_INCLUDE`, `CANNOT_INCLUDE`, `OUTPUT_FORMAT` (with constrained `TEXT IN [...]` enums and nested `LIST OF OBJECT { ... }` schemas) become first-class validators with severity escalation: CRITICAL rolls back, MAJOR retries with reinforced prompts, MINOR commits with warning.
- **Transactional atomicity.** Each operation is dispatched, validated, and committed (or rolled back) as a unit. Failed transactions don't leak invalid data into program state; downstream operations see only committed results.
- **Deterministic computation alongside reasoning.** `EXECUTE_CODE python: ... END_EXECUTE` blocks compile to local-execution transactions ($0 cost, ~100ms latency). AILang variables marshal in/out automatically, so you can interleave amortisation math with `WEIGH` operations seamlessly.
- **Auditable execution log.** Every variable mutation, every transaction commit/rollback, every contract violation, every retry — timestamped, in `state.execution_log`.

## Purpose

AIEX is built for **business-rules workloads where qualitative judgment matters and the alternative is fossilising expert reasoning into brittle if/else cascades.** Concretely: insurance underwriting, loan eligibility, claims adjudication, fraud triage, compliance review, regulatory eligibility determination, customer-intent inference. These are the systems that today live in 200,000-line Drools rule sets, IBM ODM modules, or hand-tuned Java decision engines — and that suffer constantly from the impedance mismatch between qualitative policy and quantitative code.

AIEX gives you back the qualitative substrate (LLM reasoning) without the production-grade concerns of cost, audit, and reproducibility being afterthoughts.

It is **not** the right substrate for sub-millisecond decisioning, formally-verified safety-critical systems, or cases where every rule is `(field op constant)` (classical code is cheaper there).

## How AIEX compares to other frameworks

AIEX overlaps in places with several adjacent ecosystems but solves a different shape of problem. The honest comparison:

**vs. LLM orchestration frameworks (LangChain, LangGraph, AutoGen, CrewAI).** These are general-purpose toolkits for chaining LLM calls and coordinating agents. They give you primitives (chains, graphs, agents, tools) but leave routing, cost control, contract enforcement, and audit trails to you. AIEX is narrower and more opinionated: every operation is a typed transaction with a declared contract, routed automatically to the cheapest tier that can produce the required output, with a pre-execution budget and a transactional commit/rollback log built in. If you need a flexible toolkit to assemble agents, those frameworks fit better. If you need a runtime for *the same kind of program* run thousands of times a day with predictable cost and a regulator-grade audit trail, AIEX fits better.

**vs. structured-output libraries (Instructor, Outlines, Guidance, LMQL).** These constrain LLM output to a schema — Pydantic models, regex-bounded grammars, JSON enforced at decode time. AIEX includes structured output (`OUTPUT_FORMAT` with constrained `TEXT IN [...]` enums and nested `LIST OF OBJECT { ... }`) but also enforces *semantic* contracts (`MUST_INCLUDE`, `CANNOT_INCLUDE`) and adds severity-tiered retry-with-reinforcement and rollback. If schema enforcement is all you need, the lighter libraries are sufficient and easier to drop in. If you need "the response must mention X but must not mention Y, and on failure retry with the violation noted, and on repeated failure roll the transaction back", AIEX is built around that workflow.

**vs. prompt optimization frameworks (DSPy).** DSPy treats LLM programs as differentiable modules whose prompts are *learned* by an optimizer against a metric. AIEX treats LLM programs as *declared* — the programmer specifies which reasoning type, which depth, which contracts, and the runtime executes that declaration. The two are complementary in principle (DSPy could optimise prompts within an AIEX template) but philosophically opposite: DSPy automates prompt design; AIEX makes prompt design unnecessary because the right template is selected by the operation type.

**vs. model gateways (LiteLLM, OpenRouter, Portkey).** Gateways route LLM calls across providers based on availability, fallback, latency, or simple cost rules — usually one query at a time, with the routing decision opaque to the caller. AIEX routes *per reasoning type* based on the LLM Reasoning Router research showing different reasoning types activate different neuron populations: RETRIEVE goes to BERT-class models, DEDUCE to Sonnet, WEIGH to Opus. This isn't a fallback or load-balancing decision — it's a quality decision driven by what each operation needs. Gateways and AIEX can compose: AIEX picks the tier, the gateway picks the provider within that tier.

**vs. classical business-rules engines (Drools, IBM ODM, Camunda DMN).** These are mature, enterprise-grade systems for executing rules. They scale, they have tooling, they have decades of operational lineage. They also force qualitative policy ("the customer's industry stability") into brittle quantitative encodings (`if industry in BAD_LIST`) and require a developer for every rule change. AIEX keeps the rules in their natural form (declarative reasoning operations with output contracts that compliance officers can read) and trades sub-millisecond latency and bit-exact reproducibility — neither of which most business-rules systems actually have under load — for orders of magnitude more flexibility. For high-throughput deterministic decisioning the classical engines remain the right answer; for qualitative judgment work currently bottlenecked on human reviewers or rotting if/else cascades, AIEX is the credible alternative.

The short version: AIEX is a *transactional execution runtime* for declarative AI reasoning programs, not a toolkit, not an optimizer, not a gateway, not a rules engine. The closest single comparison is "Drools, but the rules are written in English and the engine reasons rather than evaluates" — but that comparison undersells the cost-routing, contract-enforcement, and code-block integration that make it production-ready in ways Drools never had to be.

## How to use AIEX

The flow is:

1. Write an AILang program (typed reasoning operations + EXECUTE_CODE blocks + output contracts)
2. `aiex compile foo.ailang` — see the budget plan, no API spend
3. `aiex run foo.ailang` — execute end-to-end against the LLM
4. Inspect `state.results` (per-transaction outputs) and `state.variables` (final program state) if you ran from Python

A complete worked example with five live-executed scenarios is in the AILang repo at [`examples/loan_eligibility/`](https://github.com/pcoz/ailang/tree/main/examples/loan_eligibility).

For the language reference (writing AILang programs), see the [AILang specification](https://github.com/pcoz/ailang/blob/main/AILang_Specification.md).

For the in-depth manual, see [`docs/manual/index.md`](docs/manual/index.md) — 18 chapters covering everything from "hello world" through the Python embedding API and the architecture rationale.

## Getting started

### Prerequisites

- Python 3.11 or later
- An [Anthropic API key](https://console.anthropic.com/) (only required for `aiex run`; `aiex compile` works offline)

### Install

```bash
git clone https://github.com/pcoz/aiex.git
cd aiex
pip install -e .
```

Optional BERT support for richer semantic CANNOT_INCLUDE checks:

```bash
pip install -e ".[bert]"
```

### Configure your API key

Create a `.env` file in the AIEX project root:

```bash
# .env
ANTHROPIC_API_KEY=sk-ant-api03-...your-key-here...
```

The runtime auto-loads this when the engine starts. Two notes:

- **Never commit `.env`** — the project's `.gitignore` already excludes it.
- **For `aiex compile` you don't need the key.** Compile mode parses, builds the execution plan, and prints the budget report without ever calling the API. Useful for exploring cost before deciding to run.

You can also export the key into your shell environment instead of using `.env`:

```bash
# bash / zsh
export ANTHROPIC_API_KEY=sk-ant-api03-...

# Windows PowerShell
$env:ANTHROPIC_API_KEY="sk-ant-api03-..."
```

### Verify the install

```bash
aiex --help
```

You should see the available commands (`compile`, `run`).

### Run your first program

Save this as `hello.ailang`:

```ailang
#ailang v0.8.2
SET topic TO "the boiling point of water at sea level"

RETRIEVE answer FOR topic WITH:
    RETRIEVAL_BUDGET: STANDARD
    OUTPUT_FORMAT: { value: TEXT, units: TEXT, source: TEXT }
END
```

Then:

```bash
aiex compile hello.ailang   # shows the budget (no API spend)
aiex run hello.ailang       # executes against the LLM
```

For a step-by-step walkthrough of a more substantial program, see [chapter 3 of the manual](docs/manual/03_your_first_program.md).

## Documentation

- **[User Manual](docs/manual/index.md)** — complete walkthrough, 18 chapters
- **[AIEX Specification](docs/specification.md)** — language design and routing semantics
- **[Architecture](docs/architecture.md)** — five-DIVISION COBOL inheritance and the rationale
- **[Compilation Walkthrough](docs/compilation_walkthrough.md)** — step-by-step example of compiling one AILang program
- **[AILang Specification](https://github.com/pcoz/ailang/blob/main/AILang_Specification.md)** — the language AIEX runs

## License

AIEX is **source-available, free for non-commercial use** (personal projects, academic research, prototyping, evaluation, non-commercial open-source).

**Commercial use requires a separate licence.** Contact:

- Email: [edward@fleetingswallow.com](mailto:edward@fleetingswallow.com)
- LinkedIn: [linkedin.com/in/edward-chalk](https://www.linkedin.com/in/edward-chalk/)

Full terms in [LICENSE.md](LICENSE.md).
