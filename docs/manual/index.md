# AIEX User Manual

This manual covers the AIEX execution runtime in depth: what it is, how to install it, how to write and run AILang programs against it, what happens at compile time and at runtime, how to embed it in your own application, and how to debug it when something doesn't work.

The chapters go from the simplest possible "save a file and run it" through to the architectural rationale and the Python embedding API. Read in order if you're new; jump to a specific chapter if you have a specific question.

## What is AIEX, in one paragraph

AIEX is the execution runtime for [AILang](https://github.com/pcoz/ailang). AILang lets you write AI reasoning programs in structured English (typed reasoning operations like `DEDUCE`, `RETRIEVE`, `WEIGH`; orthogonal modifiers like `DEPTH`, `RETRIEVAL_BUDGET`; output contracts like `MUST_INCLUDE` and `OUTPUT_FORMAT`). AIEX compiles those programs into a five-DIVISION COBOL-style execution plan, routes each operation to the cheapest model tier (BERT/Haiku/Sonnet/Opus) that can produce the required output, runs `EXECUTE_CODE` blocks deterministically alongside reasoning, validates outputs against contracts with severity-tiered retry and rollback, and produces a pre-execution budget report so you know what each run will cost before you spend a cent. Typical AILang programs run on AIEX at 80-90% lower LLM cost than a naive all-Opus dispatch, with full audit trails for every transaction.

## Reading paths

| Your situation | Read |
|---|---|
| "I want to try it in 10 minutes" | [1](01_what_is_aiex.md), [2](02_installation_and_hello_world.md) |
| "I want to write my first real program" | [3](03_your_first_program.md), [4](04_reading_the_budget_report.md) |
| "Teach me the AILang language model" | [5](05_typed_reasoning_operations.md), [6](06_style_depth_retrieval.md), [7](07_output_contracts.md), [8](08_pipelines.md), [9](09_reality_context.md) |
| "I need deterministic computation alongside reasoning" | [10](10_execute_code.md) |
| "How does AIEX decide which model to use?" | [11](11_multi_model_routing.md) |
| "What happens when a transaction is dispatched?" | [12](12_dispatch_validate_commit.md) |
| "I'm setting up a real environment" | [13](13_configuration.md), [14](14_cli_reference.md) |
| "I'm embedding AIEX in a Python application" | [15](15_python_api.md) |
| "I want the architectural rationale" | [16](16_architecture.md) |
| "Something isn't working" | [17](17_troubleshooting.md) |
| "What's the license?" | [18](18_license_and_roadmap.md) |

## Chapter index

### Part I — Getting started

1. **[What is AIEX](01_what_is_aiex.md)** — the problem AIEX solves and the shape of the solution
2. **[Installation and hello world](02_installation_and_hello_world.md)** — get something running in 10 minutes
3. **[Your first program](03_your_first_program.md)** — build a small but realistic program from scratch, iteration by iteration
4. **[Reading the budget report](04_reading_the_budget_report.md)** — every column explained, with worked examples

### Part II — The AILang language model

5. **[The six typed reasoning operations](05_typed_reasoning_operations.md)** — RETRIEVE, COMPARE, TRACE, DEDUCE, SYNTHESIZE, WEIGH; when to use each
6. **[Style, depth, and retrieval budget](06_style_depth_retrieval.md)** — the orthogonal modifier axes that compose with every operation
7. **[Output contracts](07_output_contracts.md)** — MUST_INCLUDE, CANNOT_INCLUDE, OUTPUT_FORMAT, severity escalation
8. **[Pipelines: THEN chains and REASONING_CONTEXT](08_pipelines.md)** — composing multi-step reasoning programs with structured digests
9. **[REALITY_CONTEXT and worldview scoping](09_reality_context.md)** — running the same program under different lenses

### Part III — The runtime

10. **[EXECUTE_CODE: deterministic computation](10_execute_code.md)** — Python interleaved with reasoning; variable marshalling; cost model
11. **[Multi-model routing](11_multi_model_routing.md)** — how AIEX picks BERT vs Haiku vs Sonnet vs Opus per transaction
12. **[Dispatch, validate, commit](12_dispatch_validate_commit.md)** — the transactional execution lifecycle, retry semantics, rollback

### Part IV — Operations

13. **[Configuration](13_configuration.md)** — `.env`, model overrides, BERT setup, verbosity
14. **[CLI reference](14_cli_reference.md)** — every command and option

### Part V — Embedding and architecture

15. **[Python API: embedding AIEX](15_python_api.md)** — using AIEX from your own code
16. **[Architecture: the five-DIVISION inheritance](16_architecture.md)** — why the COBOL frame fits AI reasoning, what each DIVISION carries
17. **[Troubleshooting and known issues](17_troubleshooting.md)** — P1 through P7, with workarounds
18. **[License and roadmap](18_license_and_roadmap.md)** — terms, contact, what's next

## License (summary)

AIEX is **source-available, free for non-commercial use**. Personal projects, academic research, prototyping, evaluation, and non-commercial open-source work are covered.

**Commercial use requires a separate licence.** Contact:

- Email: [edward@fleetingswallow.com](mailto:edward@fleetingswallow.com)
- LinkedIn: [linkedin.com/in/edward-chalk](https://www.linkedin.com/in/edward-chalk/)

Full terms in [LICENSE.md](../../LICENSE.md). See [chapter 18](18_license_and_roadmap.md) for the exact definitions.

## Versioning

This manual targets AIEX 0.1 and AILang 0.8.2. When AIEX is upgraded, chapters with version-sensitive material (especially routing tables, model pool, known issues) will be revised — check the chapter date at the top of each file.
