"""Default routing tables and the routing-resolution function.

These tables encode the AIEX specification's heuristics for mapping
reasoning operations to model tiers, neuron budgets, and temperatures.

The routing philosophy:
    Each reasoning type has an inherent complexity level that determines
    the *minimum* model capability needed.  RETRIEVE only needs embedding
    similarity (BERT-class), COMPARE needs light generation (Haiku), while
    TRACE/DEDUCE/SYNTHESIZE/WEIGH need mid-range generation (Sonnet).
    Escalation rules then promote to a higher tier when the operation's
    depth, retrieval budget, or output requirements exceed what the base
    tier can handle.

The neuron budgets (865, 950, 1100, ...) come from the reasoning-router
research that measured the minimum token activation needed for each
reasoning type to produce reliable results.  They are not round numbers
because they were empirically calibrated, not hand-picked.
"""

from __future__ import annotations

from aiex.models.ast_nodes import (
    DepthLevel,
    ReasoningType,
    RetrievalBudget,
)
from aiex.models.plan import (
    ModelConfig,
    ModelTier,
    RoutingDecision,
)


# ---------------------------------------------------------------------------
# Model pool -- the single source of truth for model IDs and prices
# ---------------------------------------------------------------------------
# One concrete model per tier.  The dispatcher sends ``model_id`` to the API
# (``runtime/dispatch.py: MODEL_ID_MAP``), and the budget estimator and the
# all-Opus baseline price tokens with ``cost_per_1k_in`` / ``cost_per_1k_out``
# (``compiler/budget.py: COST_TABLE``).  Change a model or a price here and
# both follow.
#
# Prices are Anthropic's published first-party base rates (no caching):
#   claude-haiku-4-5   $1 / MTok in,  $5 / MTok out
#   claude-sonnet-4-6  $3 / MTok in, $15 / MTok out
#   claude-opus-4-6    $5 / MTok in, $25 / MTok out
# Source: https://platform.claude.com/docs/en/about-claude/pricing
# (checked 2026-10-13).

DEFAULT_MODEL_POOL: dict[ModelTier, ModelConfig] = {
    ModelTier.BERT_CLASS: ModelConfig(
        tier=ModelTier.BERT_CLASS,
        model_id="sentence-transformers/all-MiniLM-L6-v2",
        # Local embedding model: nominal compute cost only.
        cost_per_1k_in=0.00002,
        cost_per_1k_out=0.00002,
        max_tokens=512,
        latency_class="low",
    ),
    ModelTier.GENERATIVE_LIGHT: ModelConfig(
        tier=ModelTier.GENERATIVE_LIGHT,
        model_id="claude-haiku-4-5",
        cost_per_1k_in=0.001,
        cost_per_1k_out=0.005,
        max_tokens=8192,
        latency_class="low",
    ),
    ModelTier.GENERATIVE_STANDARD: ModelConfig(
        tier=ModelTier.GENERATIVE_STANDARD,
        model_id="claude-sonnet-4-6",
        cost_per_1k_in=0.003,
        cost_per_1k_out=0.015,
        max_tokens=16384,
        latency_class="standard",
    ),
    ModelTier.GENERATIVE_DEEP: ModelConfig(
        tier=ModelTier.GENERATIVE_DEEP,
        model_id="claude-opus-4-6",
        cost_per_1k_in=0.005,
        cost_per_1k_out=0.025,
        max_tokens=32768,
        latency_class="high",
    ),
}


# ---------------------------------------------------------------------------
# Default routing table  (ReasoningType -> base RoutingDecision)
# ---------------------------------------------------------------------------
# Each reasoning type maps to exactly one base RoutingDecision.  The base
# tier represents the cheapest model that can reliably handle the reasoning
# pattern.  The escalation rules in resolve_routing() may override this.
#
# Why these specific tiers?
#   RETRIEVE  -> BERT_CLASS          Embedding similarity is sufficient;
#                                    no generation needed.
#   COMPARE   -> GENERATIVE_LIGHT    Structured comparison can be handled
#                                    by a small generative model.
#   TRACE     -> GENERATIVE_STANDARD Causal chains need stronger reasoning.
#   DEDUCE    -> GENERATIVE_STANDARD Logical deduction needs mid-range.
#   SYNTHESIZE-> GENERATIVE_STANDARD Merging multiple inputs coherently
#                                    requires solid generation quality.
#   WEIGH     -> GENERATIVE_STANDARD Trade-off evaluation needs nuance.
#   INTELLIGENTLY -> GENERATIVE_STANDARD  Safe default for untyped ops.
#
# Temperature choices reflect the creativity/precision trade-off:
#   RETRIEVE(0.0) and TRACE(0.1) need precision; SYNTHESIZE(0.4) benefits
#   from more creative merging.

DEFAULT_ROUTING_TABLE: dict[ReasoningType, RoutingDecision] = {
    ReasoningType.RETRIEVE: RoutingDecision(
        operation_type=ReasoningType.RETRIEVE,
        base_tier=ModelTier.BERT_CLASS,
        template_name="retrieval_vector",
        temperature=0.0,          # deterministic retrieval
        neuron_budget=865,         # empirical: minimum for reliable recall
    ),
    ReasoningType.COMPARE: RoutingDecision(
        operation_type=ReasoningType.COMPARE,
        base_tier=ModelTier.GENERATIVE_LIGHT,
        template_name="comparison_grid",
        temperature=0.2,
        neuron_budget=950,
    ),
    ReasoningType.TRACE: RoutingDecision(
        operation_type=ReasoningType.TRACE,
        base_tier=ModelTier.GENERATIVE_STANDARD,
        template_name="trace_chain",
        temperature=0.1,           # low temp for faithful chain-following
        neuron_budget=1100,
    ),
    ReasoningType.DEDUCE: RoutingDecision(
        operation_type=ReasoningType.DEDUCE,
        base_tier=ModelTier.GENERATIVE_STANDARD,
        template_name="deduction_logic",
        temperature=0.15,
        neuron_budget=1200,
    ),
    ReasoningType.SYNTHESIZE: RoutingDecision(
        operation_type=ReasoningType.SYNTHESIZE,
        base_tier=ModelTier.GENERATIVE_STANDARD,
        template_name="synthesis_merge",
        temperature=0.4,           # higher temp allows creative merging
        neuron_budget=1400,        # synthesis needs the most tokens
    ),
    ReasoningType.WEIGH: RoutingDecision(
        operation_type=ReasoningType.WEIGH,
        base_tier=ModelTier.GENERATIVE_STANDARD,
        template_name="weigh_tradeoff",
        temperature=0.3,
        # 1531 matches the AILang v0.8.2 spec figure for WEIGH and the
        # NEURON_TABLE in compiler/budget.py (the source of truth for the
        # budget report).  Earlier this was 1300, which silently disagreed
        # with the budget calculation; harmonised on 2026-04-19.
        neuron_budget=1531,
    ),
    ReasoningType.INTELLIGENTLY: RoutingDecision(
        operation_type=ReasoningType.INTELLIGENTLY,
        base_tier=ModelTier.GENERATIVE_STANDARD,
        template_name="default",
        temperature=0.3,
        neuron_budget=1000,
    ),
}


# ---------------------------------------------------------------------------
# Budget table  (RetrievalBudget -> runtime modifiers)
# ---------------------------------------------------------------------------
# Controls how aggressively the runtime searches external / vector sources.
# ``neurons`` is the additional token budget allocated for retrieved context.
# ``top_k`` sets the number of vector-search results to inject into the prompt.
# ``prompt_modifier`` is a natural-language instruction appended to the system
# prompt telling the LLM how to treat external evidence.

DEFAULT_BUDGET_TABLE: dict[RetrievalBudget, dict] = {
    RetrievalBudget.NONE: {
        "neurons": 0,
        "prompt_modifier": "Do not consult any external sources.",
        "vector_search": False,
    },
    RetrievalBudget.MINIMAL: {
        "neurons": 200,
        "prompt_modifier": "Consult at most 1-2 sources if absolutely necessary.",
        "vector_search": True,
        "top_k": 2,
    },
    RetrievalBudget.STANDARD: {
        "neurons": 500,
        "prompt_modifier": "Consult relevant sources as needed.",
        "vector_search": True,
        "top_k": 5,
    },
    RetrievalBudget.FULL: {
        "neurons": 1000,
        "prompt_modifier": "Exhaustively consult all available sources.",
        "vector_search": True,
        "top_k": 20,
    },
}


# ---------------------------------------------------------------------------
# Depth table  (DepthLevel -> runtime modifiers)
# ---------------------------------------------------------------------------
# Depth controls both the token budget (via ``token_multiplier``) and the
# number of refinement passes (``max_passes``).  At THOROUGH and above the
# runtime may re-prompt the LLM to refine its initial answer.
# ``prompt_suffix`` is appended to the user prompt to steer the LLM's
# verbosity and analytical rigour.

DEFAULT_DEPTH_TABLE: dict[DepthLevel, dict] = {
    DepthLevel.SHALLOW: {
        "max_passes": 1,
        "token_multiplier": 0.5,
        "prompt_suffix": "Provide a brief, high-level answer.",
    },
    DepthLevel.STANDARD: {
        "max_passes": 1,
        "token_multiplier": 1.0,
        "prompt_suffix": "",
    },
    DepthLevel.THOROUGH: {
        "max_passes": 2,
        "token_multiplier": 1.5,
        "prompt_suffix": "Be thorough and cover edge cases.",
    },
    DepthLevel.DEEP: {
        "max_passes": 3,
        "token_multiplier": 2.0,
        "prompt_suffix": "Provide deep analysis with supporting reasoning.",
    },
    DepthLevel.EXHAUSTIVE: {
        "max_passes": 5,
        "token_multiplier": 3.0,
        "prompt_suffix": (
            "Perform an exhaustive analysis. Consider every angle, "
            "edge case, and counter-argument."
        ),
    },
}


# ---------------------------------------------------------------------------
# Routing resolution
# ---------------------------------------------------------------------------
# The resolve_routing() function is the central decision point.  It starts
# with the base tier from DEFAULT_ROUTING_TABLE, then applies four
# escalation rules in priority order.  Each rule can only *promote* the
# tier (never demote), and the highest promotion wins.

# Tiers ordered cheapest -> most expensive for escalation comparisons.
_TIER_ORDER: list[ModelTier] = [
    ModelTier.BERT_CLASS,
    ModelTier.GENERATIVE_LIGHT,
    ModelTier.GENERATIVE_STANDARD,
    ModelTier.GENERATIVE_DEEP,
]


def _tier_index(tier: ModelTier) -> int:
    """Return the ordinal position of a tier (0=cheapest, 3=most capable)."""
    return _TIER_ORDER.index(tier)


def _escalate(
    base: RoutingDecision,
    new_tier: ModelTier,
    reason: str,
) -> RoutingDecision:
    """Return a copy of *base* escalated to *new_tier*.

    Preserves the original ``base_tier`` so the budget report can show
    what would have been used without escalation.  The ``escalation_reason``
    string is logged for observability.
    """
    return RoutingDecision(
        operation_type=base.operation_type,
        base_tier=base.base_tier,
        escalated_to=new_tier,
        escalation_reason=reason,
        template_name=base.template_name,
        temperature=base.temperature,
        neuron_budget=base.neuron_budget,
    )


def resolve_routing(
    op_type: ReasoningType,
    depth: DepthLevel = DepthLevel.STANDARD,
    retrieval: RetrievalBudget = RetrievalBudget.STANDARD,
    output_has_qualitative_text: bool = False,
) -> RoutingDecision:
    """Apply escalation rules and return the final ``RoutingDecision``.

    Escalation triggers
    -------------------
    1. **Depth >= DEEP** -- push at least to ``GENERATIVE_STANDARD``.
    2. **Depth == EXHAUSTIVE** -- push to ``GENERATIVE_DEEP``.
    3. **Retrieval == FULL** and base is ``BERT_CLASS`` -- escalate to
       ``GENERATIVE_LIGHT`` (vector search still runs, but a generative
       model synthesises the answer).
    4. **Qualitative text in output** and base is ``BERT_CLASS`` --
       escalate to ``GENERATIVE_LIGHT`` because BERT cannot generate
       free-form prose.
    """

    # Start with the default routing for this reasoning type.
    # Unknown types fall back to the INTELLIGENTLY (general-purpose) entry.
    base = DEFAULT_ROUTING_TABLE.get(
        op_type,
        DEFAULT_ROUTING_TABLE[ReasoningType.INTELLIGENTLY],
    )

    effective_tier = base.base_tier

    # --- Escalation rules (applied in priority order) ---
    #
    # Rule 1: depth=DEEP requires at least GENERATIVE_STANDARD.
    # Rule 2: depth=EXHAUSTIVE requires GENERATIVE_DEEP (Opus-class).
    #   Rationale: deep/exhaustive analysis exceeds the reasoning capacity
    #   of lighter models and benefits from the larger context window.
    if depth in (DepthLevel.DEEP, DepthLevel.EXHAUSTIVE):
        min_tier = (
            ModelTier.GENERATIVE_DEEP
            if depth == DepthLevel.EXHAUSTIVE
            else ModelTier.GENERATIVE_STANDARD
        )
        if _tier_index(effective_tier) < _tier_index(min_tier):
            effective_tier = min_tier

    # Rule 3: FULL retrieval budget on a BERT tier -> promote to LIGHT.
    #   Rationale: vector search still runs, but a generative model is
    #   needed to synthesise the retrieved passages into a coherent answer.
    if (
        retrieval == RetrievalBudget.FULL
        and effective_tier == ModelTier.BERT_CLASS
    ):
        effective_tier = ModelTier.GENERATIVE_LIGHT

    # Rule 4: qualitative text in the output schema on a BERT tier -> LIGHT.
    #   Rationale: BERT models produce embeddings / classifications, not
    #   free-form prose.  If the output includes descriptive text fields,
    #   a generative model is required.
    if output_has_qualitative_text and effective_tier == ModelTier.BERT_CLASS:
        effective_tier = ModelTier.GENERATIVE_LIGHT

    # --- Build the (possibly escalated) decision ---
    # If any rule promoted the tier, record all applicable reasons so the
    # budget report can explain *why* a more expensive model was chosen.
    if effective_tier != base.base_tier:
        reasons: list[str] = []
        if depth in (DepthLevel.DEEP, DepthLevel.EXHAUSTIVE):
            reasons.append(f"depth={depth.value}")
        if (
            retrieval == RetrievalBudget.FULL
            and base.base_tier == ModelTier.BERT_CLASS
        ):
            reasons.append("full retrieval on BERT tier")
        if output_has_qualitative_text and base.base_tier == ModelTier.BERT_CLASS:
            reasons.append("qualitative output requires generative model")
        return _escalate(base, effective_tier, "; ".join(reasons))

    # No escalation needed -- return the base decision as-is.
    return base
