"""Runtime state models.

Unlike the AST and plan models (which are frozen / immutable), these are
**mutable** -- they represent the live working storage of a running AIEX
program.  This is the execution-time counterpart to the compile-time
models in :mod:`aiex.models.ast_nodes` and :mod:`aiex.models.plan`.

The central class is :class:`ProgramState`, which plays the same role as
COBOL's ``WORKING-STORAGE SECTION``: it holds all variables, accumulated
results, and digests that operations read from and write to during execution.

The transaction lifecycle tracked here follows a dispatch-validate-commit
pattern:
    1. PENDING     -- transaction is queued
    2. DISPATCHED  -- LLM call is in flight
    3. VALIDATING  -- response received, running contract checks
    4. COMMITTED   -- all checks passed, result stored in ProgramState
    (or ROLLED_BACK / RETRYING on contract violation)
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from aiex.models.plan import Severity


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class TransactionStatus(str, Enum):
    """Lifecycle states for a single transaction.

    State machine::

        PENDING -> DISPATCHED -> VALIDATING -> COMMITTED
                                     |
                                     +-> RETRYING -> DISPATCHED  (on MAJOR violation)
                                     +-> ROLLED_BACK             (on CRITICAL violation)
    """
    PENDING = "PENDING"
    DISPATCHED = "DISPATCHED"
    VALIDATING = "VALIDATING"
    COMMITTED = "COMMITTED"
    ROLLED_BACK = "ROLLED_BACK"
    RETRYING = "RETRYING"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
# These models capture the output of the contract-validation step.  The
# runtime runs every ContractCheck in the TransactionContract against the
# LLM's response and collects any violations here.

class ContractViolation(BaseModel):
    """One failed check inside a transaction contract.

    ``details`` carries diagnostic info (e.g. the actual cosine-similarity
    score for a SEMANTIC_SCAN check) to help developers debug failures.
    """
    check_name: str
    severity: Severity
    message: str = ""
    details: Optional[str] = None


class ValidationResult(BaseModel):
    """Aggregate result of running all contract checks.

    ``passed`` is True only when there are zero violations.
    ``severity`` is set to the highest severity among all violations
    (CRITICAL > MAJOR > MINOR), which determines the severity_policy
    action the runtime takes (ROLLBACK, RETRY_ONCE, or WARN).
    """
    passed: bool
    violations: list[ContractViolation] = Field(default_factory=list)
    severity: Optional[Severity] = Field(default=None)


# ---------------------------------------------------------------------------
# Dispatch metadata
# ---------------------------------------------------------------------------

class DispatchMeta(BaseModel):
    """Telemetry captured during a single LLM / BERT dispatch.

    Recorded after the API call completes.  ``retries`` counts how many
    times the runtime re-dispatched due to MAJOR contract violations.
    ``fallbacks`` counts tier-escalation fallbacks (e.g. Haiku failed
    so the runtime fell back to Sonnet).  These metrics feed into the
    post-execution cost report.
    """
    model_used: str
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0
    retries: int = 0
    fallbacks: int = 0


# ---------------------------------------------------------------------------
# Transaction result
# ---------------------------------------------------------------------------

class TransactionResult(BaseModel):
    """The outcome of executing one ``TransactionPlan``.

    Tracks the full dispatch-validate-commit lifecycle:
        ``status``        -- current lifecycle state (see TransactionStatus)
        ``response``      -- the parsed LLM response (JSON dict)
        ``dispatch_meta`` -- API telemetry (tokens, cost, latency)
        ``validation``    -- contract check results (pass/fail + violations)

    The runtime creates a ``TransactionResult`` in PENDING state, updates
    it through DISPATCHED -> VALIDATING, and finally either COMMITTED
    (stored in ProgramState) or ROLLED_BACK.
    """
    id: str
    name: str
    status: TransactionStatus = Field(default=TransactionStatus.PENDING)
    response: Optional[dict[str, Any]] = Field(default=None)
    dispatch_meta: Optional[DispatchMeta] = Field(default=None)
    validation: Optional[ValidationResult] = Field(default=None)


# ---------------------------------------------------------------------------
# Program state (mutable working storage)
# ---------------------------------------------------------------------------
# This is the COBOL WORKING-STORAGE SECTION equivalent.  It is intentionally
# *not* a Pydantic model -- mutability is the whole point.  The runtime
# creates one ProgramState at the start of execution and passes it through
# every transaction.  Each committed transaction writes its result here,
# making it available as input to subsequent operations.

class ProgramState:
    """Live state of a running AIEX programme.

    This is *not* a Pydantic model -- it is a plain mutable object that
    the runtime mutates as transactions commit.

    Data stores:
        ``variables``     -- key-value pairs from SET statements and
                             transaction outputs (the WORKING-STORAGE).
        ``results``       -- completed TransactionResults keyed by ID,
                             preserving the full dispatch/validation metadata.
        ``digests``       -- named accumulator lists populated by operations
                             with ``emit_digest=True`` (used for building
                             summary tables across THEN-chains).
        ``execution_log`` -- timestamped event log for post-run auditing.
    """

    def __init__(self) -> None:
        self.variables: dict[str, Any] = {}
        self.results: dict[str, TransactionResult] = {}
        self.digests: dict[str, list[dict[str, Any]]] = {}
        self.execution_log: list[dict[str, Any]] = []

    # -- variable helpers ---------------------------------------------------

    def set_variable(self, name: str, value: Any) -> None:
        """Set or update a variable in working storage.

        Every mutation is logged so the execution trace is fully auditable.
        """
        self.variables[name] = value
        self.log_event("set_variable", {"name": name, "value_type": type(value).__name__})

    def get_variable(self, name: str, default: Any = None) -> Any:
        """Retrieve a variable, returning *default* if absent."""
        return self.variables.get(name, default)

    # -- result helpers -----------------------------------------------------

    def commit_result(self, result: TransactionResult) -> None:
        """Store a completed transaction result, keyed by its id.

        This is the final step of the dispatch-validate-commit lifecycle.
        Once committed, the result's response data becomes available to
        downstream transactions via ``input_refs``.
        """
        self.results[result.id] = result
        self.log_event(
            "commit_result",
            {"id": result.id, "name": result.name, "status": result.status.value},
        )

    # -- digest helpers -----------------------------------------------------

    def append_digest(self, accumulator_name: str, digest: dict[str, Any]) -> None:
        """Append a digest entry to a named accumulator.

        Digests are mini-summaries emitted by operations in a THEN-chain.
        The final SYNTHESIZE step can consume the accumulated digests to
        produce a consolidated report.
        """
        if accumulator_name not in self.digests:
            self.digests[accumulator_name] = []
        self.digests[accumulator_name].append(digest)
        self.log_event(
            "append_digest",
            {"accumulator": accumulator_name, "keys": list(digest.keys())},
        )

    # -- logging ------------------------------------------------------------

    def log_event(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        """Append a timestamped event to the execution log.

        Every state mutation and lifecycle transition is recorded here.
        The log is included in the ``--output`` JSON file for post-run
        auditing and debugging.
        """
        self.execution_log.append(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": event_type,
                **(data or {}),
            }
        )
