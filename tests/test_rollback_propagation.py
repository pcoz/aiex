"""Regression test: a rolled-back transaction blocks its dependents.

Before this was fixed, ``_execute_plan`` marked every executed transaction
as completed whether it committed or rolled back.  The dependency check
therefore passed for downstream transactions, which then ran with the
rolled-back input silently missing from their prompt (e.g. SYNTHESIZE ran
without one of the two results it was told to integrate).  This test pins
the fix for both paths:

  1. parallel group: one member rolls back -> the SYNTHESIZE that depends on
     the group is skipped (never dispatched) and an ERROR is logged;
  2. sequential: a rolled-back transaction's dependent is skipped too.

Runs offline with a scripted LLM client; no API key required.
"""

from aiex.compiler.compiler import compile_ailang
from aiex.parser import parse_ailang
from aiex.runtime.engine import AiexEngine

PARALLEL = '''#ailang v0.8.2
SET case_a TO { note: "a" }
SET case_b TO { note: "b" }
DEDUCE left_out FROM case_a WITH:
    RETRIEVAL_BUDGET: NONE
    OUTPUT_FORMAT: { verdict: TEXT }
END
DEDUCE right_out FROM case_b WITH:
    RETRIEVAL_BUDGET: NONE
    OUTPUT_FORMAT: { verdict: TEXT }
END
SYNTHESIZE [left_out, right_out] INTO combined WITH:
    RETRIEVAL_BUDGET: NONE
    OUTPUT_FORMAT: { verdict: TEXT }
END
'''

SEQUENTIAL = '''#ailang v0.8.2
SET case_a TO { note: "a" }
DEDUCE left_out FROM case_a WITH:
    RETRIEVAL_BUDGET: NONE
    OUTPUT_FORMAT: { verdict: TEXT }
END
DEDUCE second_out FROM left_out WITH:
    RETRIEVAL_BUDGET: NONE
    OUTPUT_FORMAT: { verdict: TEXT }
END
'''


class FailOn:
    """Returns a schema-violating response (CRITICAL) for prompts that
    mention ``marker``; a valid response otherwise.  Records every call."""

    def __init__(self, marker):
        self.marker = marker
        self.calls: list[str] = []

    def create_message(self, model, system, user_message, max_tokens, temperature):
        self.calls.append(user_message)
        if self.marker in user_message:
            return {"wrong_field": "x"}
        return {"verdict": "ok"}


def _run(source, marker):
    client = FailOn(marker)
    state = AiexEngine(llm_client=client, verbose=False).run(parse_ailang(source), "t")
    return client, state


def _status(state, name):
    for tx_id, r in state.results.items():
        if tx_id.endswith(name):
            return r.status.value
    return None


def test_parallel_group_rollback_skips_dependents():
    plan = compile_ailang(parse_ailang(PARALLEL), "t")
    assert plan.parallel_groups, "expected the two DEDUCEs to form a parallel group"
    client, state = _run(PARALLEL, '"note": "a"')
    assert _status(state, "left_out") == "ROLLED_BACK"
    assert _status(state, "right_out") == "COMMITTED"
    assert _status(state, "combined") is None          # never executed
    assert len(client.calls) == 2                      # SYNTHESIZE never dispatched
    errors = [e for e in state.execution_log if e.get("event") == "ERROR"]
    assert any("combined" in e.get("tx", "") for e in errors)


def test_sequential_rollback_skips_dependents():
    client, state = _run(SEQUENTIAL, '"note": "a"')
    assert _status(state, "left_out") == "ROLLED_BACK"
    assert _status(state, "second_out") is None
    assert len(client.calls) == 1
    errors = [e for e in state.execution_log if e.get("event") == "ERROR"]
    assert any("second_out" in e.get("tx", "") for e in errors)


if __name__ == "__main__":
    test_parallel_group_rollback_skips_dependents()
    test_sequential_rollback_skips_dependents()
    print("ALL ROLLBACK PROPAGATION TESTS PASSED")
