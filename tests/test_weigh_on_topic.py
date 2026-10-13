"""Regression test: WEIGH ... ON <topic> reaches the prompt.

Before this was wired, the parser read the ``ON`` clause of a WEIGH into
``OperationNode.on_topic`` but the compiler never copied it onto the
``TransactionPlan`` and the engine never rendered it -- so a program like::

    WEIGH soft_factors ON applicant WITH:
        SOURCES: [industry_stability, employment_tenure]
        ...

sent the model a user message containing no applicant data at all (just
the DIMENSIONS line and an empty ``SOURCES:`` header, because SOURCES names
that are not program variables were silently dropped).  Under
``RETRIEVAL_BUDGET: NONE`` the model then correctly answered
``insufficient_info``.  This test pins the fix:

  1. the compiler carries ``on_topic`` onto the transaction;
  2. when the topic names a program variable, its value is in the user message;
  3. when it does not (a plain dimension label), the label itself is shown;
  4. SOURCES names that are not variables are still listed, not dropped.

Runs offline (dry-run mock LLM); no API key required.
"""

from aiex.compiler.compiler import compile_ailang
from aiex.models.state import ProgramState
from aiex.parser import parse_ailang
from aiex.runtime.engine import AiexEngine

PROGRAM = '''#ailang v0.8.2
SET applicant TO {
    name: "Test Applicant",
    industry: "healthcare",
    employment_tenure_months: 96
}

WEIGH soft_factors ON applicant WITH:
    DEPTH: STANDARD
    RETRIEVAL_BUDGET: NONE
    SOURCES: [industry_stability, employment_tenure]
    DIMENSIONS: [forward_payment_capacity]
    OUTPUT_FORMAT: { soft_factor_direction: TEXT IN ["positive", "neutral", "negative"] }
END

WEIGH the_crux ON which_premise_the_clash_turns_on WITH:
    RETRIEVAL_BUDGET: NONE
    OUTPUT_FORMAT: { crux: TEXT }
END
'''


def _setup():
    program = parse_ailang(PROGRAM)
    plan = compile_ailang(program, "test_weigh_on_topic")
    state = ProgramState()
    for var in program.variables:
        state.set_variable(var.name, var.value)
    txs = {t.name: t for t in plan.transactions}
    return AiexEngine(dry_run=True, verbose=False), state, txs


def test_on_topic_carried_onto_transaction():
    _, _, txs = _setup()
    assert txs["soft_factors"].on_topic == "applicant"
    assert txs["the_crux"].on_topic == "which_premise_the_clash_turns_on"


def test_on_topic_variable_value_in_user_message():
    engine, state, txs = _setup()
    msg = engine._compose_user_message(txs["soft_factors"], state)
    assert "applicant" in msg
    assert "Test Applicant" in msg
    assert "healthcare" in msg
    assert "96" in msg


def test_on_topic_label_in_user_message():
    engine, state, txs = _setup()
    msg = engine._compose_user_message(txs["the_crux"], state)
    assert "which_premise_the_clash_turns_on" in msg


def test_unresolved_sources_listed_not_dropped():
    engine, state, txs = _setup()
    msg = engine._compose_user_message(txs["soft_factors"], state)
    assert "industry_stability" in msg
    assert "employment_tenure" in msg


if __name__ == "__main__":
    test_on_topic_carried_onto_transaction()
    test_on_topic_variable_value_in_user_message()
    test_on_topic_label_in_user_message()
    test_unresolved_sources_listed_not_dropped()
    print("ALL WEIGH ON-TOPIC TESTS PASSED")
