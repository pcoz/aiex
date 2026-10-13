"""Regression test: MAJOR violations retry with a reinforced prompt.

Before this was fixed the compiler mapped every MUST_INCLUDE item to
CRITICAL, OUTPUT_FORMAT is CRITICAL, and CANNOT_INCLUDE / retrieval checks
are MINOR -- so no standard check was ever MAJOR and the engine's
retry-with-reinforcement loop could not run.  The spec (section 6.2) defines
MAJOR as "retry with a reinforced prompt ... after max retries, escalates to
CRITICAL", and the manual describes MUST_INCLUDE as the retry-on-fail tier.
This test pins the fix:

  1. MUST_INCLUDE checks compile to MAJOR; OUTPUT_FORMAT stays CRITICAL;
  2. a missing MUST_INCLUDE concept is retried with a RETRY INSTRUCTION
     appended to the system prompt, and commits if the retry fixes it;
  3. a MAJOR violation that persists rolls back after 1 + max_retries calls;
  4. a CRITICAL (schema) violation rolls back after a single call.

Runs offline with a scripted LLM client; no API key required.
"""

from aiex.compiler.compiler import compile_ailang
from aiex.models.plan import Severity
from aiex.parser import parse_ailang
from aiex.runtime.engine import AiexEngine

PROGRAM = '''#ailang v0.8.2
SET facts TO { note: "x" }
DEDUCE verdict_out FROM facts WITH:
    RETRIEVAL_BUDGET: NONE
    MUST_INCLUDE: [hazard_assessment]
    OUTPUT_FORMAT: { verdict: TEXT }
END
'''

GOOD = {"verdict": "ok", "hazard_assessment": "low"}
MISSING_CONCEPT = {"verdict": "ok"}
BAD_SCHEMA = {"hazard_assessment": "low"}


class ScriptedClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.systems: list[str] = []

    def create_message(self, model, system, user_message, max_tokens, temperature):
        self.systems.append(system)
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


def _run(responses):
    client = ScriptedClient(responses)
    state = AiexEngine(llm_client=client, verbose=False).run(
        parse_ailang(PROGRAM), "test_retry_policy")
    result = next(iter(state.results.values()))
    return client, result


def test_must_include_compiles_to_major():
    plan = compile_ailang(parse_ailang(PROGRAM), "test_retry_policy")
    contract = plan.transactions[0].contract
    assert contract.must_include_checks
    assert all(c.severity == Severity.MAJOR for c in contract.must_include_checks)


def test_major_retries_with_reinforcement_then_commits():
    client, result = _run([MISSING_CONCEPT, GOOD])
    assert len(client.systems) == 2
    assert "RETRY INSTRUCTION" not in client.systems[0]
    assert "RETRY INSTRUCTION" in client.systems[1]
    assert "hazard_assessment" in client.systems[1]
    assert result.status.value == "COMMITTED"


def test_persistent_major_rolls_back_after_max_retries():
    client, result = _run([MISSING_CONCEPT])
    assert len(client.systems) == 3  # 1 attempt + max_retries (2)
    assert result.status.value == "ROLLED_BACK"


def test_critical_schema_violation_rolls_back_without_retry():
    client, result = _run([BAD_SCHEMA])
    assert len(client.systems) == 1
    assert result.status.value == "ROLLED_BACK"


if __name__ == "__main__":
    test_must_include_compiles_to_major()
    test_major_retries_with_reinforcement_then_commits()
    test_persistent_major_rolls_back_after_max_retries()
    test_critical_schema_violation_rolls_back_without_retry()
    print("ALL RETRY POLICY TESTS PASSED")
