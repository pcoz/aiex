"""Regression test: REALITY_CONTEXT is injected into prompts and logged.

Before this was wired, ``DEFINE`` / ``WITH REALITY_CONTEXT`` parsed but the
declared worldview / assumptions / value system never reached the model or the
audit trail -- the construct was inert at runtime.  This test pins the fix:

  1. the compiler attaches the rendered frame to every transaction that runs
     under a WITH REALITY_CONTEXT block;
  2. the engine prepends that frame to the system prompt;
  3. the engine records a CONTEXT event in the execution log so the audit
     trail shows the lens each result was produced under.

Runs offline (dry-run mock LLM); no API key required.
"""

from aiex.compiler.compiler import compile_ailang
from aiex.models.state import ProgramState
from aiex.parser import parse_ailang
from aiex.runtime.engine import AiexEngine

PROGRAM = '''#ailang v0.8.2
SET will_column TO { premise: "spending does not create net output" }
SET krugman_column TO { premise: "spending creates net output in a slump" }

DEFINE REALITY_CONTEXT crux_lens:
    WORLDVIEW: "An argument analyst who judges the text on the page, not the author."
    ASSUMPTIONS: [opposed_columns_share_most_facts]
    VALUE_SYSTEM: [report_the_crux_honestly]
END_CONTEXT

WITH REALITY_CONTEXT crux_lens:
    WEIGH the_crux ON which_premise_the_clash_turns_on FROM will_column WITH:
        DEPTH: DEEP
        RETRIEVAL_BUDGET: NONE
        SOURCES: [will_column, krugman_column]
        OUTPUT_FORMAT: { crux: TEXT, kind: TEXT IN ["CLOSED", "OPEN"] }
    END
END_WITH
'''


def _plan():
    return compile_ailang(parse_ailang(PROGRAM), "test_reality_context")


def test_frame_attached_to_transaction():
    """The compiler renders the active context onto each LLM transaction."""
    plan = _plan()
    llm_txs = [t for t in plan.transactions if t.code_block_body is None]
    assert llm_txs, "expected at least one LLM transaction"
    for tx in llm_txs:
        assert tx.reality_context_name == "crux_lens"
        assert tx.reality_frame is not None
        assert "REALITY CONTEXT: crux_lens" in tx.reality_frame
        assert "judges the text on the page" in tx.reality_frame      # worldview
        assert "opposed_columns_share_most_facts" in tx.reality_frame  # assumption
        assert "report_the_crux_honestly" in tx.reality_frame          # value system


def test_frame_prepended_to_system_prompt():
    """The engine leads the system prompt with the frame, then the op scaffold."""
    plan = _plan()
    engine = AiexEngine(dry_run=True, verbose=False)
    tx = next(t for t in plan.transactions if t.code_block_body is None)
    prompt = engine._compose_system_prompt(tx, ProgramState())
    assert prompt.startswith("=== REALITY CONTEXT: crux_lens ===")
    assert "=== END REALITY CONTEXT ===" in prompt
    # the operation's own reasoning scaffold still follows the frame
    assert len(prompt) > len(tx.reality_frame) + 10


def test_context_logged_to_audit_trail():
    """Every transaction under the context emits a CONTEXT audit event."""
    engine = AiexEngine(dry_run=True, verbose=False)
    state = engine.run(parse_ailang(PROGRAM), "test_reality_context")
    # log_event spreads the data dict into the event record (no nested "data").
    context_events = [e for e in state.execution_log if e.get("event") == "CONTEXT"]
    assert context_events, "expected a CONTEXT audit event"
    assert any(e.get("reality_context") == "crux_lens" for e in context_events)


if __name__ == "__main__":
    test_frame_attached_to_transaction()
    test_frame_prepended_to_system_prompt()
    test_context_logged_to_audit_trail()
    print("ALL REALITY_CONTEXT TESTS PASSED")
