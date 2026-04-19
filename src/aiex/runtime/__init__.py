"""AIEX Runtime Package -- the execution layer for compiled AILang programs.

This package provides the runtime engine that takes a compiled AILang
ExecutionPlan and drives it through the dispatch-validate-commit cycle.

Architecture overview:
    engine.py     -- Orchestrates the full lifecycle: compile -> execute -> report.
                     Manages state, retries, parallel groups, and prompt composition.
    dispatch.py   -- Two-phase dispatch: BERT retrieval followed by LLM generation.
                     Handles model selection, API calls, and JSON extraction.
    contracts.py  -- Post-hoc validation of LLM responses against BOUNDARY contracts.
                     Enforces MUST_INCLUDE, CANNOT_INCLUDE, and OUTPUT_FORMAT rules.
    bert_validator.py -- Semantic validation using sentence-transformers embeddings.
                     Replaces brittle keyword matching with cosine-similarity checks.

The main entry point is AiexEngine, which wires all these components together.
"""

from aiex.runtime.engine import AiexEngine

__all__ = ["AiexEngine"]
