"""Regression test: the all-Opus baseline prices the same token estimates
as the routed plan, so the savings figure reflects routing only.

Before this was fixed the routed estimate used per-transaction token
estimates (operation type, depth, inputs, retrieval) while the baseline
assumed a flat 3K input / 1K output per transaction.  An all-Sonnet plan
therefore reported ~63% savings against Opus instead of the 40% that the
Sonnet/Opus price ratio ($3/$15 vs $5/$25) actually gives.
"""

import pytest

from aiex.compiler.compiler import compile_ailang
from aiex.models.plan import ModelTier
from aiex.models.routing import DEFAULT_MODEL_POOL
from aiex.parser import parse_ailang

SONNET_ONLY = '''#ailang v0.8.2
SET facts TO { a: "x" }
SET rules TO { r: "y" }
DEDUCE first_out FROM [facts, rules] WITH:
    RETRIEVAL_BUDGET: NONE
    OUTPUT_FORMAT: { verdict: TEXT }
END
SYNTHESIZE [first_out, facts] INTO second_out WITH:
    RETRIEVAL_BUDGET: NONE
    DEPTH: THOROUGH
    OUTPUT_FORMAT: { verdict: TEXT }
END
'''


def _plan(src):
    return compile_ailang(parse_ailang(src), "test_baseline_consistency")


def test_baseline_uses_routed_token_estimates():
    plan = _plan(SONNET_ONLY)
    opus = DEFAULT_MODEL_POOL[ModelTier.GENERATIVE_DEEP]
    expected = sum(
        b.est_tokens_in / 1000 * opus.cost_per_1k_in
        + b.est_tokens_out / 1000 * opus.cost_per_1k_out
        for b in plan.budget
    )
    assert plan.vs_baseline_cost == pytest.approx(expected, abs=1e-4)


def test_all_sonnet_plan_saves_exactly_the_price_ratio():
    plan = _plan(SONNET_ONLY)
    for tx in plan.transactions:
        assert (tx.routing.escalated_to or tx.routing.base_tier) == ModelTier.GENERATIVE_STANDARD
    # $3/$15 vs $5/$25 per MTok -> 40% at identical token counts.
    assert plan.savings_pct == pytest.approx(40.0, abs=0.2)


if __name__ == "__main__":
    test_baseline_uses_routed_token_estimates()
    test_all_sonnet_plan_saves_exactly_the_price_ratio()
    print("ALL BASELINE CONSISTENCY TESTS PASSED")
