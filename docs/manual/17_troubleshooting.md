# Chapter 17: Troubleshooting

AIEX shipped its first live execution on 2026-04-16 and has a short, honest list of known issues from that session. This chapter documents them in the same P1-P7 numbering the session notes use, so a bug report that mentions "P2" points at the same thing here, in the codebase, and in the original notes. For each issue you get the symptom (what you will see), the cause (why it happens), the mitigation (what to do today), and where relevant the roadmap (what is planned).

If you hit something not in this list, the general debugging tips at the end of the chapter usually narrow it down quickly.

## P1: JSON extraction for complex schemas

**Symptom.** A transaction rolls back with a validation error that points at a missing field or a parse failure, even though the LLM visibly produced a plausible JSON response. Schemas with roughly eleven or more fields are the common trigger; schemas with eight or fewer almost always pass.

**Cause.** Sonnet occasionally splits a large JSON structure across two markdown code blocks, or interleaves short prose between groups of fields. The `_extract_json()` function in `aiex/runtime/dispatch.py` tries three strategies -- direct parse, single markdown block, outermost brace match -- and none of them handle multi-block or prose-interleaved JSON.

**Mitigation.** Keep `OUTPUT_FORMAT` schemas to ten fields or fewer where you can. Composite responses (one operation producing a ten-field record, a downstream operation augmenting it) often factor more cleanly than monolithic ones anyway. If you must have a wide schema, consider switching to the Anthropic SDK's JSON mode (the boundary is a small change inside `AnthropicLLMClient`) or a lenient parser like `json5` or `demjson3`.

**Roadmap.** The planned fix is to add a multi-block merge step and to switch to SDK-native structured output where available.

## P2: BERT DLL load failure on Windows

**Symptom.** At startup you see an import warning along the lines of `sentence-transformers not loaded`. BERT-backed semantic validation silently falls back to keyword matching; BERT retrieval uses the `MockBertClient`.

**Cause.** `sentence-transformers` 5.x pulls in `torchcodec`, which requires FFmpeg DLLs that are not present in a default Windows Python install. The `from sentence_transformers import SentenceTransformer` call raises `OSError` and AIEX catches it to degrade gracefully.

**Mitigation.** Three options depending on what you need. First, pin `sentence-transformers<5.0` in your environment -- the 4.x line does not pull in torchcodec. Second, install FFmpeg on the machine (add to PATH) and let 5.x load normally. Third, ignore it and run with keyword-fallback validation; for many programs this is fine, because MUST_INCLUDE keyword search is generous and CANNOT_INCLUDE is downgraded to MINOR specifically because of this mode (see P3).

**Roadmap.** A Linux-first GPU path and an API-based embedding backend (Anthropic, Cohere) are both on the list. Neither depends on the Windows FFmpeg dance.

## P3: CANNOT_INCLUDE keyword false positives

**Symptom.** You get `WARNING` lines in the runtime trace flagging prohibited content in a transaction output that visibly does not contain it. The warnings do not block the commit.

**Cause.** Without BERT, `CANNOT_INCLUDE` checks use literal keyword matching, which fires on coincidental substring hits and on domain words that happen to collocate with the prohibited concept. AIEX deliberately assigns these checks MINOR severity when BERT is unavailable, which is why the run continues -- but the warnings are noisy.

**Mitigation.** The MINOR severity is the mitigation: the commit goes through, the warning is recorded in the transaction's `validation.violations` for later review, and nothing else changes. If you want clean validation, install BERT (fix P2). For short, curated CANNOT_INCLUDE lists you can tolerate the noise; for long ones with common English tokens, prefer waiting for BERT.

## P4: Parallel groups execute sequentially

**Symptom.** The budget report shows a parallel group of three transactions. The actual total latency comes in at roughly the sum of their individual latencies, not the maximum.

**Cause.** The compiler correctly identifies parallel groups and the `ExecutionPlan.parallel_groups` field is populated. The runtime, however, walks the members sequentially -- `asyncio.gather` has not yet been wired into `_execute_plan`. The correctness of the execution is unaffected; only the wall-clock time is.

**Mitigation.** None short of patching. If wall time matters and your program has naturally parallel operations, splitting the program into separate runs and parallelising at the Python level with `concurrent.futures.ThreadPoolExecutor` (see [chapter 15](15_python_api.md)) is the pragmatic workaround.

**Roadmap.** The asyncio rewrite of the execution loop is the single largest planned runtime change; state isolation is already correct, so the change is localised.

## P5: Retry prompt corruption

**Symptom.** On a transaction that hits its retry limit, the second and third attempts produce *worse* output than the first, not better -- more off-topic content, looser schema adherence, occasional refusal.

**Cause.** The retry loop appends a `=== RETRY INSTRUCTION ===` block to the system prompt listing the violation details, then re-dispatches. Because the system prompt is mutated in place for subsequent retries, each attempt sees a progressively longer, more cluttered system prompt. Large prompts with repeated "you violated X" clauses degrade output quality, and occasionally cause the model to try to explain *why* it is retrying instead of just retrying.

**Mitigation.** Cap retries by setting `max_retries` to 1 or 2 on contracts where this pattern bites. One retry with the reinforcement is usually better than the first attempt; by the third, quality has typically degraded past the original.

**Roadmap.** The fix is architectural: move retry instructions into a fresh user message on each attempt rather than accumulating them in the system prompt. The system prompt stays pristine; only the most recent violation list is shown to the model.

## P6: .env path resolution

**Symptom.** AIEX fails to find `ANTHROPIC_API_KEY` despite a populated `.env` file, or it finds one `.env` while you expected it to find a different one.

**Cause.** The loader walks up from its own module location looking for `pyproject.toml` and loads `.env` from that directory. Editable installs, symlinked packages, and unusual layouts (particularly on Windows with OneDrive-backed paths) sometimes resolve to a different project root than expected.

**Mitigation.** Two reliable options. Run AIEX from the project root so the walk-up succeeds at the first hop. Or set `ANTHROPIC_API_KEY` directly in the shell environment (`setx` on Windows, `export` on Unix) so the `.env` lookup becomes irrelevant. The [`run_all.py`](https://github.com/pcoz/ailang/blob/main/examples/loan_eligibility/scenarios/run_all.py) example in the AILang repo shows a third approach: load `.env` explicitly into `os.environ` at the top of the script before importing the engine.

## P7: BERT-based dependency analysis

**Status.** Not implemented. This is a planned enhancement to `aiex/compiler/analyzer.py`.

**Context.** Today the dependency analyser detects data dependencies syntactically: one operation depends on another if it references the other's `result_name` (directly or via a variable assignment). Semantic dependencies -- where one DEDUCE produces a "tariff analysis" and a downstream COMPARE references "trade compliance results" without a syntactic link -- are invisible to the analyser. The consequence is occasional over-parallelisation: two operations that share no syntactic reference but that *should* run sequentially end up in the same parallel group.

**Roadmap.** Once BERT is reliably available (fix P2), the analyser can encode operation descriptions and cross-check cosine similarity against input/output names to detect semantic edges. This would tighten parallel-group detection without requiring the AILang programmer to wire up redundant result_name references.

## General debugging tips

A few techniques head off most problems before they become bug reports.

**Turn on verbose mode.** `AiexEngine(verbose=True)` (the default) prints a per-transaction trace as each dispatch happens. You see the model chosen, the retrieval budget, the depth, and the COMMITTED / ROLLED BACK outcome. When something unexpected happens, this trace usually points at the transaction that failed.

**Read `state.execution_log`.** Every state mutation and every lifecycle event is recorded with a UTC timestamp. When a downstream operation behaves oddly, scanning the log for the upstream `COMMIT` / `ROLLBACK` is usually enough to locate where the data (or lack of data) came from.

**Use `compile_only` to audit routing.** Before a live run, `engine.compile_only(program)` returns an `ExecutionPlan` without spending a token. The plan tells you which tier each operation routed to, what it will cost, and what the parallel-group structure looks like. A surprise in the budget report -- an unexpected Opus escalation, for instance -- is almost always easier to diagnose by reading the plan than by reading the runtime output.

**Inspect the system prompt.** `engine._compose_system_prompt(tx, state)` is callable (underscore notwithstanding) and returns the exact string that will be sent to the LLM. When a transaction rolls back and you cannot see why, printing the composed prompt often shows a missing input, a schema placeholder that did not render, or a retrieval modifier that clashes with a boundary instruction.

**Run `--dry-run` first.** The mock clients exercise the full pipeline -- routing, dispatch shape, contract validation wiring -- without touching a real model. If a program fails under `--dry-run`, the problem is in the program or the compiler, not the LLM.

The combination of the budget report, the execution log, and the composed prompt is usually sufficient to diagnose any failure AIEX can exhibit today. When it is not, the [license and roadmap chapter](18_license_and_roadmap.md) has contact information for reporting.
