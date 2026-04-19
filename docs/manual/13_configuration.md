# Chapter 13: Configuration

AIEX has a small configuration surface on purpose. The model pool,
the routing table, and the contract defaults all live in source
files under `src/aiex/models/` and `src/aiex/compiler/`. The only
runtime-variable knobs are the API key, the verbosity flag, and the
dry-run switch. This chapter covers all three plus the handful of
compile-time overrides that occasionally come up.

## The .env File

The one thing you must configure before running real programs is an
Anthropic API key. AIEX loads it from a `.env` file using the
`python-dotenv` package. The format is a single line:

```
ANTHROPIC_API_KEY=sk-ant-api03-...
```

The file lives at the project root, alongside `pyproject.toml`. The
CLI's bootstrap code walks upward from the installed module's
directory looking for `pyproject.toml`, and loads `.env` from that
same directory once found. If no `pyproject.toml` is found upstream
(for example when AIEX has been installed as a wheel into a system
Python), the loader falls back to `python-dotenv`'s default
behaviour, which searches from the current working directory upward.

Two consequences worth noting:

- You can run `aiex compile` and `aiex run` from anywhere inside the
  project tree and the right `.env` will be loaded.
- If you clone the repo but do not create a `.env`, or the key is
  wrong, `aiex run` will fail at the first LLM dispatch with an
  authentication error from the Anthropic SDK. `aiex compile`
  continues to work because it runs in dry-run mode and never calls
  the API.

There had been a Windows-specific fragility around path resolution
in an earlier version (tracked as known issue P6). The current
upward-walk logic handles both POSIX and Windows path separators and
resolves correctly on both platforms.

No other environment variables are required. AIEX does not read
model names from the environment; those live in the model pool, as
described below.

## Overriding the Model Pool

The model pool in `src/aiex/models/routing.py` is the source of
truth for which concrete model backs each tier. If you want to swap
Sonnet 4 for a later Sonnet release, or point `GENERATIVE_LIGHT` at
a different Haiku version, edit the dict directly:

```python
DEFAULT_MODEL_POOL: dict[ModelTier, ModelConfig] = {
    ModelTier.BERT_CLASS: ModelConfig(
        tier=ModelTier.BERT_CLASS,
        model_id="sentence-transformers/all-MiniLM-L6-v2",
        cost_per_1k_in=0.0,
        cost_per_1k_out=0.0,
        max_tokens=512,
        latency_class="low",
    ),
    ModelTier.GENERATIVE_LIGHT: ModelConfig(
        tier=ModelTier.GENERATIVE_LIGHT,
        model_id="claude-3-5-haiku-20241022",  # swap this
        cost_per_1k_in=0.001,
        cost_per_1k_out=0.005,
        max_tokens=8192,
        latency_class="low",
    ),
    # ...
}
```

Two things to keep in sync when you edit the pool:

1. `model_id` is the literal string sent to the Anthropic SDK. It
   must match one of the models your API key is authorised for.
2. `cost_per_1k_in` / `cost_per_1k_out` drive the budget report.
   Update them when pricing changes, or the "estimated cost" column
   will drift from reality.

There is a separate layer in `src/aiex/runtime/dispatch.py` called
`MODEL_ID_MAP` that maps tier to a short model-ID string used during
the actual API call. Both layers exist because the routing pool
carries pricing metadata and the dispatch map carries only the
model ID. If you swap a model in one place, check the other; for
production use these should align.

The routing table itself (`DEFAULT_ROUTING_TABLE`) is also in
`routing.py`. Editing the base tier for a reasoning type there --
say, bumping `COMPARE` from `GENERATIVE_LIGHT` to
`GENERATIVE_STANDARD` -- will change the routing decision for every
COMPARE operation across every program. The budget report reflects
the change on the next compile. See
[chapter 11](11_multi_model_routing.md) for the full default table.

## BERT Setup

BERT-class routing and semantic contract validation both depend on
the `sentence-transformers` package, which is an optional extra:

```bash
pip install -e ".[bert]"
```

This pulls in `sentence-transformers` and its torch backend. The
first import of the BERT validator downloads the
`all-MiniLM-L6-v2` model weights (about 80MB) into the Hugging Face
cache.

If the extra is not installed, AIEX still runs. At import time
`BERT_AVAILABLE` is set to `False` (the import is wrapped in a
broad `try/except` that catches both `ImportError` and the Windows
DLL-load failures that occasionally happen with PyTorch). When BERT
is unavailable:

- `CANNOT_INCLUDE` checks with `TERMINOLOGY_DIFF` fall back to a
  keyword-based set difference on extracted domain terms. This has a
  higher false-positive rate because synonyms, morphological
  variants, and paraphrases are not detected (known issue P3).
- `MUST_INCLUDE` `SEMANTIC_SCAN` falls back to keyword substring
  matching with concept expansion.

For production runs that rely on strict `RETRIEVAL_BUDGET: NONE`
enforcement, install the extra. For compile-only exploration or
early development, skipping it keeps install time low.

## Verbosity

The engine has one verbosity flag. `AiexEngine(verbose=True)` is the
default and prints a per-transaction trace to the console:

```
  TX tx-2-hard-elig (DEDUCE)
    Retrieval: NONE
    Depth: STANDARD
    Model: GENERATIVE_STANDARD
    COMMITTED
```

When a contract violation fires, the violation message appears in
the trace before the status line:

```
  TX tx-3-soft-factors (WEIGH)
    Retrieval: NONE
    Depth: STANDARD
    Model: GENERATIVE_DEEP
    Retry 1/2
    WARNING: Prohibited content detected: speculation_about_applicant_motivation
    COMMITTED (with warnings)
```

Setting `verbose=False` suppresses the per-transaction trace, the
budget report, and the execution summary. The engine still builds
the same `ProgramState` object and still writes the same events to
`state.execution_log` -- you just do not see them on the console.

The CLI exposes verbosity through `--quiet`/`-q` on `aiex run`.
`--verbose` is the default (equivalent to `verbose=True`).

The budget report, specifically, is always printed when `verbose` is
`True`, including in dry-run mode. This is deliberate: the budget
report is the main value proposition of `aiex compile`, and no
flag turns it off.

## Compile-Only Mode

`aiex compile <file>` always runs in dry-run mode internally. It
parses the source, compiles the execution plan, prints the budget
report, and exits. No LLM clients are constructed, no API key is
required, no network calls are made.

This is the cheapest feedback loop in AIEX. If you change a
`DEPTH: STANDARD` to `DEPTH: DEEP`, `aiex compile` will show you the
new cost within a second without touching the network. Use it every
time you are tuning costs.

The compile output can also be emitted as JSON:

```bash
aiex compile loan.ailang --json
```

That prints the full `ExecutionPlan` as JSON including all
transaction plans, routing decisions, contracts, and budget
estimates. Useful for scripting or for feeding the plan into a
separate cost-analysis tool.

`aiex run --dry-run` is a different thing. It does exercise the full
engine -- dispatch, validate, commit -- but swaps the real Anthropic
client for `MockLLMClient`, which returns a metadata dict describing
what would have been sent. This is useful for integration testing
the engine itself, prompt composition, contract validation logic,
and state management, without spending API credits.

## Windows Notes

AIEX is developed and tested on Windows, macOS, and Linux. There
are two Windows-specific considerations:

- **BERT DLL loading.** `sentence-transformers` depends on PyTorch,
  which occasionally fails to load native DLLs on Windows (typically
  when Visual C++ runtime files are missing). The contracts module
  catches this in its broad `try/except` and falls back to keyword
  validation rather than crashing at import time.
- **Path separators in source identifiers.** The compiler builds a
  `program_id` by replacing forward slashes in the source file path.
  Windows paths use backslashes, which pass through unchanged. This
  has not caused issues but may look odd in the budget report's
  `Program:` line if your source file is deeply nested.

Both behaviours are handled in the current code; the notes are here
for completeness.

## Pre-Production Checklist

Before you point `aiex run` at a real workload:

- `.env` exists at the project root with a valid `ANTHROPIC_API_KEY`.
- The model IDs in `DEFAULT_MODEL_POOL` match models your account is
  authorised to call.
- The pricing in `DEFAULT_MODEL_POOL` matches your current contract.
  The budget report is only as accurate as the prices it references.
- `pip install -e ".[bert]"` if any program uses non-NONE retrieval
  or relies on strict CANNOT_INCLUDE terminology enforcement.
- A dry-run pass (`aiex compile` or `aiex run --dry-run`) has been
  done first. The budget for one live run is fine; the budget for a
  thousand-run batch is worth checking before launching.
- If you have scripted programmatic access, `AiexEngine(verbose=False)`
  so your log output is yours to control rather than Rich's.

For the full CLI surface and its flags, see the reference in
[chapter 14](14_cli_reference.md).
