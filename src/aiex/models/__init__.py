"""AIEX data models -- re-exports for convenient access.

This package provides a single flat import surface for all compile-time
data structures.  Downstream code can write::

    from aiex.models import OperationNode, Program, ReasoningType

instead of reaching into the individual submodules.

Submodules
----------
ast_nodes
    Frozen (immutable) Pydantic models that represent the parsed AILang
    abstract syntax tree.  These are the compiler's input.
plan
    Frozen models for the execution plan -- the compiler's output.
    Includes routing decisions, contracts, budget estimates.
state
    *Mutable* runtime models (ProgramState, TransactionResult).  These
    live on the other side of the compile/execute boundary.
routing
    Default routing tables and the escalation-resolution function that
    maps (ReasoningType, depth, retrieval) to a concrete model tier.
"""

# AST nodes
from aiex.models.ast_nodes import (
    BoundarySpec,
    DepthLevel,
    Direction,
    ObjectField,
    OperationNode,
    OutputFormatSchema,
    ParseError,
    Program,
    ReasoningContextBlock,
    ReasoningProfile,
    ReasoningType,
    RealityContext,
    RetrievalBudget,
    StyleModifier,
    ThenChain,
    Variable,
    WithContextBlock,
)

__all__ = [
    "BoundarySpec",
    "DepthLevel",
    "Direction",
    "ObjectField",
    "OperationNode",
    "OutputFormatSchema",
    "ParseError",
    "Program",
    "ReasoningContextBlock",
    "ReasoningProfile",
    "ReasoningType",
    "RealityContext",
    "RetrievalBudget",
    "StyleModifier",
    "ThenChain",
    "Variable",
    "WithContextBlock",
]
