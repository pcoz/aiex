"""Pre-execution budget calculator.

This module estimates the cost, latency, and neuron usage for each transaction
**before** it actually runs.  The estimates let the compiler produce a
cost-savings comparison (AIEX plan vs. naive all-Opus baseline) and give the
runtime a latency budget for scheduling and timeout decisions.

The core model works in three steps:

1. **Neuron count** -- Each reasoning type has a fixed neuron count from the
   AILang v0.8.2 specification (see ``NEURON_TABLE``).  RETRIEVAL_BUDGET
   can reduce that count: when retrieval is limited, the retrieval-layer
   neurons (865 base) are partially or fully subtracted.

2. **Token estimation** -- Input tokens are estimated from the number of
   premises and knowledge items.  Output tokens start from a per-type
   baseline (``BASE_OUTPUT_TOKENS``) and are scaled by the DEPTH multiplier
   (``DEPTH_TOKEN_MULTIPLIER``).  Deeper analysis = more output tokens.

3. **Dollar cost** -- Input and output token counts are priced using the
   per-tier cost table (``COST_TABLE``).  If a retrieval phase is active,
   a small fixed BERT embedding + search cost is added on top.
"""

from __future__ import annotations

from aiex.models.ast_nodes import DepthLevel, ReasoningType, RetrievalBudget
from aiex.models.plan import BudgetEstimate, ModelTier, RoutingDecision
from aiex.models.routing import DEFAULT_MODEL_POOL

# ---------------------------------------------------------------------------
# Neuron counts from the AILang v0.8.2 specification
# ---------------------------------------------------------------------------
# Each reasoning type activates a different number of "neurons" (an abstract
# unit from the spec that correlates with model complexity and cost).
# RETRIEVE is the lightest (865) because it primarily does vector search;
# WEIGH and INTELLIGENTLY are the heaviest (1531) because they require
# multi-factor trade-off analysis.
NEURON_TABLE: dict[ReasoningType, int] = {
    ReasoningType.RETRIEVE: 865,
    ReasoningType.COMPARE: 1050,
    ReasoningType.TRACE: 1100,
    ReasoningType.DEDUCE: 1200,
    ReasoningType.SYNTHESIZE: 1400,
    ReasoningType.WEIGH: 1531,
    ReasoningType.INTELLIGENTLY: 1531,
}

# ---------------------------------------------------------------------------
# RETRIEVAL_BUDGET savings
# ---------------------------------------------------------------------------
# The retrieval layer uses 865 neurons at baseline (the RETRIEVE neuron count).
# When the user constrains retrieval, we subtract from the full neuron count
# to reflect the reduced work:
#   NONE    -> subtract all 865 (no retrieval at all)
#   MINIMAL -> subtract 665 (keep only 200 for a quick lookup)
#   STANDARD / FULL -> no savings (full retrieval layer active)
RETRIEVAL_BASE = 865

RETRIEVAL_SAVINGS: dict[RetrievalBudget, int] = {
    RetrievalBudget.NONE: RETRIEVAL_BASE,       # skip all 865
    RetrievalBudget.MINIMAL: 665,                # skip 665, keep 200
    RetrievalBudget.STANDARD: 0,                 # no savings
    RetrievalBudget.FULL: 0,
}

# ---------------------------------------------------------------------------
# DEPTH -> output token multiplier
# ---------------------------------------------------------------------------
# DEPTH controls how much output the model produces.  SHALLOW halves the
# baseline output; EXHAUSTIVE quadruples it.  These multipliers are applied
# to BASE_OUTPUT_TOKENS to get the estimated output token count.
DEPTH_TOKEN_MULTIPLIER: dict[DepthLevel, float] = {
    DepthLevel.SHALLOW: 0.5,
    DepthLevel.STANDARD: 1.0,
    DepthLevel.THOROUGH: 1.5,
    DepthLevel.DEEP: 2.5,
    DepthLevel.EXHAUSTIVE: 4.0,
}

# ---------------------------------------------------------------------------
# Per-tier pricing (USD per 1K tokens): (input, output)
# ---------------------------------------------------------------------------
# Derived from DEFAULT_MODEL_POOL (models/routing.py) so the budget report
# prices exactly the models the dispatcher calls.  BERT_CLASS is near-zero
# because embedding models are orders of magnitude cheaper than generative
# models.
COST_TABLE: dict[ModelTier, tuple[float, float]] = {
    tier: (cfg.cost_per_1k_in, cfg.cost_per_1k_out)
    for tier, cfg in DEFAULT_MODEL_POOL.items()
}

# ---------------------------------------------------------------------------
# Latency estimates per tier (milliseconds, single request)
# ---------------------------------------------------------------------------
LATENCY_TABLE: dict[ModelTier, int] = {
    ModelTier.BERT_CLASS: 50,
    ModelTier.GENERATIVE_LIGHT: 2000,
    ModelTier.GENERATIVE_STANDARD: 4000,
    ModelTier.GENERATIVE_DEEP: 6000,
}

# ---------------------------------------------------------------------------
# Base output tokens by reasoning type (at STANDARD depth)
# ---------------------------------------------------------------------------
# These are empirical averages for each operation kind.  RETRIEVE produces
# the fewest tokens (just the retrieved snippet); SYNTHESIZE the most
# (merging multiple inputs into a cohesive narrative).
BASE_OUTPUT_TOKENS: dict[ReasoningType, int] = {
    ReasoningType.RETRIEVE: 400,
    ReasoningType.COMPARE: 600,
    ReasoningType.TRACE: 800,
    ReasoningType.DEDUCE: 700,
    ReasoningType.SYNTHESIZE: 900,
    ReasoningType.WEIGH: 800,
    ReasoningType.INTELLIGENTLY: 800,
}


def estimate_input_tokens(num_premises: int, num_knowledge_items: int) -> int:
    """Rough estimate of input tokens based on content count.

    Assumes a fixed overhead for the system prompt (~500 tokens), then
    adds ~200 tokens per premise (FROM source) and ~150 tokens per
    knowledge item injected by the retrieval phase.  These are conservative
    averages -- real prompts vary, but overestimating is safer for budgeting.
    """
    base = 500  # system prompt
    per_premise = 200
    per_knowledge = 150
    return base + (num_premises * per_premise) + (num_knowledge_items * per_knowledge)


def calculate_budget(
    tx_id: str,
    reasoning_type: ReasoningType,
    depth: DepthLevel,
    retrieval: RetrievalBudget,
    routing: RoutingDecision,
    num_input_sources: int = 5,
) -> BudgetEstimate:
    """Calculate the budget estimate for a single transaction.

    The calculation proceeds as follows:

    1. Look up the full neuron count for the reasoning type.
    2. Subtract RETRIEVAL_BUDGET savings to get effective neurons.
    3. Estimate input tokens from the number of sources + knowledge items.
    4. Estimate output tokens = BASE_OUTPUT_TOKENS * DEPTH multiplier.
    5. Price both token counts using the routed model tier's cost rates.
    6. If retrieval is active, add a small fixed BERT cost for embedding
       and vector search.
    7. Estimate latency as the tier's base latency + BERT latency if applicable.
    """
    # --- Step 1-2: Neuron budget with retrieval savings ---
    full_neurons = NEURON_TABLE.get(reasoning_type, 1531)
    savings = RETRIEVAL_SAVINGS.get(retrieval, 0)
    effective_neurons = max(full_neurons - savings, 0)

    # --- Step 3: Estimate how many knowledge items the retrieval phase injects ---
    # MINIMAL retrieval fetches ~3 items; STANDARD and FULL fetch ~10.
    # These expand the input prompt, so they factor into token estimation.
    knowledge_items = 0
    if retrieval == RetrievalBudget.MINIMAL:
        knowledge_items = 3
    elif retrieval in (RetrievalBudget.STANDARD, RetrievalBudget.FULL):
        knowledge_items = 10

    tokens_in = estimate_input_tokens(num_input_sources, knowledge_items)

    # --- Step 4: Output tokens scaled by DEPTH ---
    base_out = BASE_OUTPUT_TOKENS.get(reasoning_type, 800)
    depth_mult = DEPTH_TOKEN_MULTIPLIER.get(depth, 1.0)
    tokens_out = int(base_out * depth_mult)

    # --- Step 5: Price using the actual tier (may be escalated from base) ---
    model_tier = routing.escalated_to or routing.base_tier
    cost_in, cost_out = COST_TABLE.get(model_tier, (0.003, 0.015))

    cost = (tokens_in / 1000 * cost_in) + (tokens_out / 1000 * cost_out)

    # --- Step 6: Add BERT embedding/search cost if retrieval is active ---
    # Any non-NONE retrieval budget triggers a vector search pass through
    # a BERT-class model, which adds a small fixed cost and latency.
    bert_cost = 0.0
    bert_latency = 0
    if retrieval in (RetrievalBudget.MINIMAL, RetrievalBudget.STANDARD, RetrievalBudget.FULL):
        bert_cost = 0.00004  # embedding + search
        bert_latency = 50
        cost += bert_cost

    # --- Step 7: Latency = generative model time + optional BERT time ---
    latency = LATENCY_TABLE.get(model_tier, 4000) + bert_latency

    return BudgetEstimate(
        transaction_id=tx_id,
        model=_model_id_for_tier(model_tier),
        neurons=effective_neurons,
        est_tokens_in=tokens_in,
        est_tokens_out=tokens_out,
        est_cost=round(cost, 5),
        est_latency_ms=latency,
    )


def _model_id_for_tier(tier: ModelTier) -> str:
    """Map a model tier enum to its concrete model identifier string."""
    cfg = DEFAULT_MODEL_POOL.get(tier)
    return cfg.model_id if cfg else "unknown"
