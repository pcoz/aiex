"""Regression test: one model pool drives both pricing and dispatch.

Before this was fixed, four tables disagreed: ``DEFAULT_MODEL_POOL`` (unused)
named legacy models, the budget estimator's ``COST_TABLE`` priced the Opus
tier at $15/$75 per MTok, ``_calculate_baseline_cost`` hard-coded the same,
and the dispatcher's ``MODEL_ID_MAP`` actually called claude-opus-4-6
($5/$25).  The budget report therefore priced models AIEX never calls and
overstated the all-Opus baseline about 3x.  This test pins the fix:

  1. the dispatcher, the budget estimator and the baseline all read
     ``DEFAULT_MODEL_POOL``;
  2. the pool names the model IDs the dispatcher sends, at their published
     per-token prices.

Runs offline; no API key required.
"""

import pytest

from aiex.compiler import budget
from aiex.compiler.compiler import compile_ailang
from aiex.models.plan import ModelTier
from aiex.models.routing import DEFAULT_MODEL_POOL
from aiex.parser import parse_ailang
from aiex.runtime import dispatch

GENERATIVE = [ModelTier.GENERATIVE_LIGHT, ModelTier.GENERATIVE_STANDARD, ModelTier.GENERATIVE_DEEP]

# Published first-party prices, USD per 1K tokens (in, out).
PUBLISHED = {
    "claude-haiku-4-5": (0.001, 0.005),
    "claude-sonnet-4-6": (0.003, 0.015),
    "claude-opus-4-6": (0.005, 0.025),
}


@pytest.mark.parametrize("tier", GENERATIVE)
def test_dispatch_and_budget_read_the_pool(tier):
    cfg = DEFAULT_MODEL_POOL[tier]
    assert dispatch.MODEL_ID_MAP[tier] == cfg.model_id
    assert budget._model_id_for_tier(tier) == cfg.model_id
    assert budget.COST_TABLE[tier] == (cfg.cost_per_1k_in, cfg.cost_per_1k_out)


@pytest.mark.parametrize("tier", GENERATIVE)
def test_pool_prices_match_published(tier):
    cfg = DEFAULT_MODEL_POOL[tier]
    assert cfg.model_id in PUBLISHED
    assert (cfg.cost_per_1k_in, cfg.cost_per_1k_out) == PUBLISHED[cfg.model_id]


def test_baseline_priced_from_pool_opus():
    program = parse_ailang('''#ailang v0.8.2
SET facts TO { a: "x" }
DEDUCE verdict_out FROM facts WITH:
    RETRIEVAL_BUDGET: NONE
    OUTPUT_FORMAT: { verdict: TEXT }
END
''')
    plan = compile_ailang(program, "test_model_pool")
    deep = DEFAULT_MODEL_POOL[ModelTier.GENERATIVE_DEEP]
    # the baseline prices the routed token estimate at the pool's Opus rates
    b = plan.budget[0]
    expected = (b.est_tokens_in * deep.cost_per_1k_in
                + b.est_tokens_out * deep.cost_per_1k_out) / 1000
    assert plan.vs_baseline_cost == pytest.approx(expected, abs=1e-4)


if __name__ == "__main__":
    for t in GENERATIVE:
        test_dispatch_and_budget_read_the_pool(t)
        test_pool_prices_match_published(t)
    test_baseline_priced_from_pool_opus()
    print("ALL MODEL POOL TESTS PASSED")
