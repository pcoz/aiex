"""AIEX Command-Line Interface.

Provides two top-level commands:

``compile``
    Parse an AILang source file, resolve routing, estimate costs, and
    print the budget report.  No LLM calls are made -- this always runs
    in dry-run mode so developers can inspect the execution plan before
    spending any money.

``run``
    Parse, compile, *and* execute the program.  By default this makes
    real API calls.  Pass ``--dry-run`` to substitute mock LLM clients
    that return deterministic placeholder responses -- useful for
    integration tests and CI pipelines.

Usage examples::

    aiex compile program.ailang          # budget-only, no API calls
    aiex compile --json program.ailang   # emit the plan as JSON
    aiex run program.ailang              # live execution
    aiex run --dry-run program.ailang    # mock execution, no API costs
    aiex run -o results.json prog.ailang # write structured results to file
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Environment bootstrap
# ---------------------------------------------------------------------------
# API keys (ANTHROPIC_API_KEY, etc.) are expected in a .env file next to the
# project's pyproject.toml.  We walk up from this module's location until we
# find pyproject.toml, then load .env from that directory.  This means the
# CLI works correctly regardless of the caller's working directory.
# If no pyproject.toml is found (e.g. installed as a wheel), we fall back to
# python-dotenv's default behaviour which searches from cwd upward.
_project_root = Path(__file__).resolve().parent
while _project_root != _project_root.parent:
    if (_project_root / "pyproject.toml").exists():
        load_dotenv(_project_root / ".env")
        break
    _project_root = _project_root.parent
else:
    load_dotenv()  # fallback: search cwd

from rich.console import Console

console = Console()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="aiex",
        description="AIEX: AI Execution Language -- compile and run AILang programs",
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # compile command
    compile_parser = subparsers.add_parser("compile", help="Compile AILang to execution plan")
    compile_parser.add_argument("file", type=str, help="AILang source file")
    compile_parser.add_argument("--json", action="store_true", help="Output plan as JSON")

    # run command
    run_parser = subparsers.add_parser("run", help="Compile and execute AILang program")
    run_parser.add_argument("file", type=str, help="AILang source file")
    run_parser.add_argument("--dry-run", action="store_true", help="Use mock LLM (no API calls)")
    run_parser.add_argument("--verbose", "-v", action="store_true", default=True)
    run_parser.add_argument("--quiet", "-q", action="store_true", help="Minimal output")
    run_parser.add_argument("--output", "-o", type=str, help="Write results to JSON file")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # ---- Source loading & parsing ----
    # Imports are deferred so that `aiex --help` stays fast even if heavy
    # dependencies (torch, anthropic, etc.) are installed.
    source_path = Path(args.file)
    if not source_path.exists():
        console.print(f"[red]Error: File not found: {args.file}[/red]")
        sys.exit(1)

    source_code = source_path.read_text(encoding="utf-8")

    from aiex.parser import parse_ailang

    try:
        program = parse_ailang(source_code)
    except Exception as e:
        console.print(f"[red]Parse error: {e}[/red]")
        sys.exit(1)

    # ---- compile: budget report only, no LLM calls ----
    if args.command == "compile":
        from aiex.runtime.engine import AiexEngine

        # compile always uses dry_run=True -- its purpose is cost analysis
        engine = AiexEngine(dry_run=True, verbose=True)
        plan = engine.compile_only(program, source_file=str(source_path))

        if args.json:
            console.print(plan.model_dump_json(indent=2))

    # ---- run: compile then execute every transaction ----
    elif args.command == "run":
        from aiex.runtime.engine import AiexEngine

        # --dry-run swaps real LLM clients for mock stubs that return
        # deterministic placeholder responses.  This lets developers test
        # the full pipeline (routing, contracts, state) without spending
        # API credits or needing network access.
        verbose = not args.quiet
        engine = AiexEngine(dry_run=args.dry_run, verbose=verbose)

        console.print(f"\n[bold]AIEX v0.1.0[/bold]")
        console.print(f"Source: {source_path}")
        console.print(f"Mode: {'DRY RUN (mock LLM)' if args.dry_run else 'LIVE'}")

        state = engine.run(program, source_file=str(source_path))

        # ---- Optional JSON output ----
        # Variables prefixed with "_" are internal (e.g. _version) and are
        # excluded from the output to keep the results developer-focused.
        if args.output:
            output_data = {
                "variables": {
                    k: v for k, v in state.variables.items()
                    if not k.startswith("_")
                },
                "results": {
                    k: {
                        "status": v.status.value,
                        "response": v.response,
                    }
                    for k, v in state.results.items()
                },
                "log": state.execution_log,
            }
            output_path = Path(args.output)
            output_path.write_text(json.dumps(output_data, indent=2, default=str))
            console.print(f"\nResults written to {output_path}")


if __name__ == "__main__":
    main()
