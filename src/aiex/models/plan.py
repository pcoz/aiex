"""Execution-plan models.

An ``ExecutionPlan`` is the compiled artefact that sits between parsing
(AST) and runtime dispatch.  Every ``TransactionPlan`` maps 1-to-1 with
an ``OperationNode`` but carries routing, contract, and budget metadata.

The naming deliberately echoes COBOL's division structure:

    COBOL                   AIEX plan model
    ----                    ---------------
    EXECUTION DIVISION      ExecutionPlan (one per program)
    TRANSACTION             TransactionPlan (one per operation)
    CONTRACT DIVISION       TransactionContract + ContractCheck
    DATA DIVISION budget    BudgetEstimate (pre-execution cost analysis)

Like the AST models, plan models are frozen (immutable) -- they are the
compiler's output and must not be mutated during execution.  Mutable
runtime state lives in :mod:`aiex.models.state`.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from aiex.models.ast_nodes import (
    DepthLevel,
    ReasoningType,
    RetrievalBudget,
    StyleModifier,
)


# ---------------------------------------------------------------------------
# Frozen base
# ---------------------------------------------------------------------------

class _Frozen(BaseModel):
    """Immutable base for all plan models -- same rationale as in ast_nodes."""
    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# Model tier & config
# ---------------------------------------------------------------------------
# The tier system is the heart of AIEX's cost-optimization strategy.
# Instead of sending every request to the most capable (and expensive)
# model, the router assigns each operation to the cheapest tier that can
# handle its reasoning complexity.  Escalation rules promote to a higher
# tier only when depth, retrieval, or output requirements demand it.

class ModelTier(str, Enum):
    """Tiered model pool -- cheapest to most capable.

    BERT_CLASS            -- Embedding / classification models (free, fast,
                             but cannot generate free-form text).
    GENERATIVE_LIGHT      -- Small generative model (Haiku-class).
    GENERATIVE_STANDARD   -- Mid-range generative model (Sonnet-class).
    GENERATIVE_DEEP       -- Most capable model (Opus-class); used only
                             when depth=EXHAUSTIVE or complex synthesis.
    """
    BERT_CLASS = "BERT_CLASS"
    GENERATIVE_LIGHT = "GENERATIVE_LIGHT"
    GENERATIVE_STANDARD = "GENERATIVE_STANDARD"
    GENERATIVE_DEEP = "GENERATIVE_DEEP"
    # Sentinel tier for EXECUTE_CODE blocks: signals "no LLM dispatch,
    # run via local code executor".  No cost, no token budget; only used
    # so the budget table and routing logs can label code transactions
    # distinctly from LLM ones.
    LOCAL_CODE = "LOCAL_CODE"


class ModelConfig(_Frozen):
    """Static configuration for a single model in the pool.

    One ``ModelConfig`` exists per tier in ``DEFAULT_MODEL_POOL``
    (see :mod:`aiex.models.routing`).  The budget estimator uses
    ``cost_per_1k_in`` / ``cost_per_1k_out`` to compute the
    pre-execution cost report.
    """
    tier: ModelTier
    model_id: str
    cost_per_1k_in: float = Field(description="USD per 1 000 input tokens")
    cost_per_1k_out: float = Field(description="USD per 1 000 output tokens")
    max_tokens: int
    latency_class: str = Field(
        default="standard",
        description="low / standard / high -- rough latency bucket",
    )


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

class RoutingDecision(_Frozen):
    """The compiler's decision on *how* to execute an operation.

    Captures the full multi-model routing choice for a single transaction:
    which tier was originally selected (``base_tier``), whether escalation
    occurred (``escalated_to``), and the prompt-construction parameters
    (template, temperature, neuron budget).

    When ``escalated_to`` is not None, the runtime dispatches to
    ``escalated_to`` instead of ``base_tier``.  The ``escalation_reason``
    string is logged for observability / debugging.

    ``neuron_budget`` is the target total token count (prompt + completion).
    The term "neuron" comes from the reasoning-router research that
    calibrated these budgets to the minimum activation needed per
    reasoning type.
    """
    operation_type: ReasoningType
    # The tier the routing table originally assigned.
    base_tier: ModelTier
    # Set when an escalation rule overrode the base tier.
    escalated_to: Optional[ModelTier] = Field(default=None)
    escalation_reason: Optional[str] = Field(default=None)
    # Which prompt template to load from templates/<name>.txt.
    template_name: str = Field(default="default")
    temperature: float = Field(default=0.3)
    neuron_budget: int = Field(
        default=1000,
        description="Target token budget (prompt + completion)",
    )
    max_output_tokens: int = Field(default=2048)


# ---------------------------------------------------------------------------
# Transaction contracts
# ---------------------------------------------------------------------------
# Contracts are the compiled form of the AILang CONTRACT DIVISION.  They
# define the acceptance criteria that a transaction's LLM response must
# satisfy.  The runtime validates every response against its contract
# before committing the result to ProgramState.

class Severity(str, Enum):
    """How serious a contract violation is.

    The severity drives the ``severity_policy`` action:
        CRITICAL  -- ROLLBACK the transaction (hard failure)
        MAJOR     -- RETRY_ONCE then fail if still violated
        MINOR     -- log a warning but commit anyway
    """
    CRITICAL = "CRITICAL"
    MAJOR = "MAJOR"
    MINOR = "MINOR"


class ContractCheck(_Frozen):
    """A single check in a transaction contract.

    Maps to one entry in the AILang CONTRACT DIVISION's MUST-INCLUDE or
    CANNOT-INCLUDE section.  ``detection_method`` selects the validation
    strategy:
        SEMANTIC_SCAN       -- cosine-similarity search for the concept
        FIELD_EXISTS        -- JSON key presence check
        TERMINOLOGY_DIFF    -- exact-match keyword scan
        KNOWLEDGE_DEPTH_SCAN -- checks that the response reaches a
                                minimum level of analytical depth
    """
    name: str
    detection_method: str = Field(
        default="SEMANTIC_SCAN",
        description="SEMANTIC_SCAN | FIELD_EXISTS | TERMINOLOGY_DIFF | KNOWLEDGE_DEPTH_SCAN",
    )
    target_field: str = Field(default="response")
    expected_content: str = Field(default="")
    severity: Severity = Field(default=Severity.MAJOR)
    message: str = Field(default="")


class TransactionContract(_Frozen):
    """The full contract attached to a transaction.

    Aggregates all MUST-INCLUDE checks, CANNOT-INCLUDE checks, and the
    optional OUTPUT-FORMAT schema.  The ``severity_policy`` dict maps
    each severity level to the action the runtime should take on violation.
    """
    must_include_checks: list[ContractCheck] = Field(default_factory=list)
    cannot_include_checks: list[ContractCheck] = Field(default_factory=list)
    output_format_schema: Any = Field(
        default=None,
        description="OutputFormatSchema object or JSON-schema string for validation",
    )
    retrieval_compliance_level: str = Field(default="STANDARD")
    # Default policy: CRITICAL violations roll back, MAJOR gets one retry,
    # MINOR is logged as a warning but the result is still committed.
    severity_policy: dict[str, str] = Field(
        default_factory=lambda: {
            "CRITICAL": "ROLLBACK",
            "MAJOR": "RETRY_ONCE",
            "MINOR": "WARN",
        },
    )
    max_retries: int = Field(default=2)


# ---------------------------------------------------------------------------
# Transaction plan
# ---------------------------------------------------------------------------
# A TransactionPlan is the EXECUTION DIVISION's equivalent of one COBOL
# transaction.  It bundles the AST operation data with the compiler's
# routing decision and any attached contract.  The runtime processes
# transactions in dependency order (respecting ``depends_on`` edges).

class TransactionPlan(_Frozen):
    """One unit of work in the execution plan.

    Each ``TransactionPlan`` maps 1-to-1 with an ``OperationNode`` from
    the AST.  The compiler enriches the raw AST data with:
        ``routing``        -- which model tier, template, and temperature
        ``contract``       -- acceptance criteria derived from BoundarySpec
        ``depends_on``     -- data-flow edges for execution ordering
        ``parallel_group`` -- transactions in the same group can run
                              concurrently (no data dependencies)
    """
    id: str
    name: str
    operation_type: ReasoningType = Field(default=ReasoningType.INTELLIGENTLY)
    style: Optional[StyleModifier] = Field(default=StyleModifier.INTELLIGENTLY)
    depth: DepthLevel = Field(default=DepthLevel.STANDARD)
    retrieval_budget: RetrievalBudget = Field(default=RetrievalBudget.STANDARD)
    # The compiler's routing decision (model tier, template, temperature).
    routing: RoutingDecision
    # The compiled contract; None if the operation has no boundary spec.
    contract: Optional[TransactionContract] = Field(default=None)
    # Variable names this transaction reads from ProgramState.
    input_refs: list[str] = Field(default_factory=list)
    # Variable name this transaction writes its result to.
    output_ref: Optional[str] = Field(default=None)
    # Transactions sharing a parallel_group value may execute concurrently.
    parallel_group: Optional[str] = Field(default=None)
    # Transaction IDs that must complete before this one can start.
    depends_on: list[str] = Field(default_factory=list)
    # Passthrough fields from the AST OperationNode.
    apply_to: Optional[str] = Field(default=None)
    against: Optional[str] = Field(default=None)
    sources: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    direction: Any = Field(default=None)
    emit_digest: bool = Field(default=False)

    # ---- EXECUTE_CODE support (AILang Section 14) ----
    # When ``code_block_body`` is set, this transaction is a deterministic
    # code-execution unit rather than an LLM call.  The runtime engine
    # detects this and dispatches to the local code executor instead of
    # composing a prompt and calling the model.  Cost is $0 (no tokens),
    # latency is a fixed local-execution estimate, and there is no retry
    # loop because Python exceptions are deterministic, not probabilistic.
    code_block_body: Optional[str] = Field(default=None)
    code_block_language: Optional[str] = Field(default=None)

    model_config = {"arbitrary_types_allowed": True}


# ---------------------------------------------------------------------------
# Budget estimate
# ---------------------------------------------------------------------------
# The compiler produces one BudgetEstimate per transaction *before*
# execution begins.  This allows the ``compile`` command to print a full
# cost report so developers can audit spending before committing to a run.

class BudgetEstimate(_Frozen):
    """Pre-execution cost / latency estimate for one transaction.

    ``neurons`` is the token budget from the routing decision.
    ``est_tokens_in`` / ``est_tokens_out`` are the estimated prompt and
    completion sizes, derived from the neuron budget and depth multiplier.
    ``est_cost`` is computed from the model's per-1k-token pricing.
    """
    transaction_id: str
    model: str
    neurons: int
    est_tokens_in: int
    est_tokens_out: int
    est_cost: float = Field(description="Estimated cost in USD")
    est_latency_ms: int


# ---------------------------------------------------------------------------
# Top-level execution plan
# ---------------------------------------------------------------------------

class ExecutionPlan(_Frozen):
    """The complete compiled execution plan for a program.

    This is the final output of the compile step and the sole input to the
    runtime engine.  It contains everything needed to execute the program:
    the ordered list of transactions, the pre-computed budget, and
    parallelism metadata.

    ``vs_baseline_cost`` and ``savings_pct`` compare the optimized plan's
    cost against a naive baseline that sends every operation to the most
    expensive tier (GENERATIVE_DEEP / Opus).  This lets developers see
    how much money the routing optimizer saved.
    """
    program_id: str
    source_file: str
    # Transactions in dependency-safe execution order.
    transactions: list[TransactionPlan] = Field(default_factory=list)
    # Maps group name -> list of transaction IDs that can run in parallel.
    parallel_groups: dict[str, list[str]] = Field(default_factory=dict)
    # One budget estimate per transaction, for the cost report.
    budget: list[BudgetEstimate] = Field(default_factory=list)
    total_cost: float = Field(default=0.0)
    total_latency_ms: int = Field(default=0)
    # What this program would cost if every operation used Opus.
    vs_baseline_cost: float = Field(
        default=0.0,
        description="What a naive all-Opus plan would cost",
    )
    # Percentage saved by intelligent routing vs the all-Opus baseline.
    savings_pct: float = Field(default=0.0)
