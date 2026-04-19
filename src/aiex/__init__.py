"""AIEX -- AI Execution Language runtime.

AIEX is the reference compiler and runtime for AILang, a COBOL-inspired
language for orchestrating multi-model AI reasoning.  The architecture
follows a classic compiler pipeline:

    .ailang source  -->  Parser (AST)  -->  Compiler (ExecutionPlan)  -->  Runtime (dispatch + validate)

Key packages:
    aiex.models      -- Immutable AST nodes and plan models (compile-time)
    aiex.models.state -- Mutable runtime state (execution-time)
    aiex.parser      -- Lark-based grammar parser that produces the AST
    aiex.runtime     -- Engine that dispatches transactions to LLM providers
    aiex.templates   -- Prompt templates keyed by reasoning type
"""

__version__ = "0.1.0"
