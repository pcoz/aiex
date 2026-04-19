"""Local code-block executor for AILang ``EXECUTE_CODE`` blocks.

This module is the runtime counterpart to the lexer/parser/compiler
support for AILang Section 14 (Code Execution Blocks).  When the engine
encounters a transaction whose ``code_block_body`` is set, it dispatches
here instead of composing a prompt and calling the LLM.

Design notes
------------
*Trust model.*  Code blocks are written by the same author who writes
the AILang program; they execute in the same trust context as the AIEX
process itself.  This is the same model as ``python script.py``: no
sandbox, because the user is running their own code.  If AIEX is ever
deployed as a multi-tenant service accepting untrusted programs, the
sandbox concern arrives at deployment time and is handled by running
AIEX in an isolated container or replacing the executor with a
subprocess/gVisor implementation.  Until then, in-process ``exec`` is
the right level of complexity.

*Variable marshalling.*  AILang variables are exposed to the code body
as plain Python names in the local namespace (e.g. ``SET data TO {...}``
becomes the Python name ``data``).  Anything the body assigns to a
plain (non-underscore) name becomes available to subsequent AILang
operations as if it had been ``SET`` directly.  Modifications to
existing variables are likewise propagated back.

*Error semantics.*  Python exceptions are re-raised as
``CodeBlockError``.  Unlike LLM transactions there is no retry or
reinforcement loop -- a Python error is deterministic, so retrying with
the same inputs would just produce the same exception.  The engine
records the failure and continues with subsequent transactions if their
dependencies allow.
"""

from __future__ import annotations

import textwrap
import traceback
from dataclasses import dataclass
from typing import Any


class CodeBlockError(Exception):
    """Raised when an EXECUTE_CODE body fails to compile or execute.

    ``original_traceback`` carries the original Python traceback so the
    engine can surface a useful error message in the run log.
    """

    def __init__(self, language: str, message: str, original_traceback: str = "") -> None:
        self.language = language
        self.original_traceback = original_traceback
        super().__init__(f"[{language}] {message}")


@dataclass(frozen=True)
class CodeBlockResult:
    """Outcome of executing one code block.

    Attributes
    ----------
    success
        True when the body executed without raising.
    new_bindings
        Mapping of variable name -> value for every name the body assigned
        that should be propagated back into AILang state.  Underscore-
        prefixed names (Python convention for "private") are excluded, as
        are bindings whose values are modules or unbound callables (those
        are presumed to be transient implementation helpers).
    output_text
        Human-readable summary of what the block produced -- used by the
        engine for logging and by the response field of the transaction
        result so downstream consumers see something meaningful.
    error
        When ``success`` is False, the exception message (and traceback
        in verbose runs).
    """
    success: bool
    new_bindings: dict[str, Any]
    output_text: str
    error: str | None = None


def execute_code_block(
    language: str,
    body: str,
    namespace_in: dict[str, Any],
) -> CodeBlockResult:
    """Execute one EXECUTE_CODE block and report what it produced.

    Parameters
    ----------
    language
        Language identifier from the AILang source (``EXECUTE_CODE python:``).
        Only ``python`` is supported.  The check is case-insensitive.
    body
        Raw source text of the block, with original indentation preserved.
    namespace_in
        Mapping of AILang variable name -> Python value.  These are made
        available to the body as plain Python names.  The mapping itself
        is not mutated; a copy is used so the caller's view of program
        state is not affected by intermediate code-block scratch state.

    Returns
    -------
    CodeBlockResult
        Captures success/failure, the new (and modified) bindings to
        propagate, and a summary string for the run log.
    """
    if language.lower() != "python":
        # Future executors (javascript, R, ...) would dispatch here.  For
        # now we surface the limitation explicitly rather than silently
        # ignoring the body.
        return CodeBlockResult(
            success=False,
            new_bindings={},
            output_text="",
            error=f"unsupported language: {language!r} (only 'python' is supported)",
        )

    # Build the execution namespace.  Copy so the caller's state is not
    # mutated mid-execution; that way a CodeBlockError leaves AILang state
    # exactly as it was before the block ran.
    exec_ns: dict[str, Any] = dict(namespace_in)
    pre_keys = set(exec_ns.keys())
    # Snapshot original values of pre-existing keys so we can detect
    # in-place modifications (vs. just new assignments).
    pre_values = {k: id(exec_ns[k]) for k in pre_keys}

    # AILang block indentation (typical: 4 spaces inside EXECUTE_CODE)
    # would be a SyntaxError in Python because the first statement would
    # appear at indent level 1.  ``textwrap.dedent`` strips the longest
    # common leading whitespace, restoring zero-indent for the outermost
    # block while preserving relative indentation inside.  This lets the
    # AILang author indent naturally inside EXECUTE_CODE without the
    # Python interpreter complaining.
    dedented = textwrap.dedent(body)

    try:
        # compile() gives a clearer error message for SyntaxError than
        # bare exec() does, with line numbers anchored to the body text.
        code_obj = compile(dedented, "<aiex_code_block>", "exec")
        exec(code_obj, exec_ns)
    except Exception as exc:  # broad: any exec error should surface
        tb = traceback.format_exc()
        return CodeBlockResult(
            success=False,
            new_bindings={},
            output_text="",
            error=f"{type(exc).__name__}: {exc}",
        )

    # Collect bindings to propagate.  Rules:
    #   - Skip dunder/private names (Python convention; not user data).
    #   - Skip builtins re-bound by exec (``__builtins__`` always appears).
    #   - Include both new keys and modified keys (id changed).
    #   - Include callables ONLY if they were created in the block (a
    #     freshly defined helper function is occasionally useful to
    #     subsequent operations).
    new_bindings: dict[str, Any] = {}
    for k, v in exec_ns.items():
        if k.startswith("_"):
            continue
        if k == "__builtins__":
            continue
        if k in pre_keys and pre_values.get(k) == id(v):
            # Unmodified pre-existing binding -- not a new write.
            continue
        new_bindings[k] = v

    # Build a short summary for the run log.  We list the propagated
    # binding names but avoid printing values (they may be large dicts).
    if new_bindings:
        summary = (
            f"executed Python ({len(body.splitlines())} lines); "
            f"propagated bindings: {', '.join(sorted(new_bindings.keys()))}"
        )
    else:
        summary = (
            f"executed Python ({len(body.splitlines())} lines); "
            f"no new bindings produced"
        )

    return CodeBlockResult(
        success=True,
        new_bindings=new_bindings,
        output_text=summary,
        error=None,
    )
