"""Pydantic models for the AILang abstract syntax tree.

Every AILang source file is parsed into a tree of these models.  All models
inherit from ``_Frozen`` which sets Pydantic's ``frozen=True`` config.  This
guarantees **immutability after parsing** -- the AST is a compile-time
artefact that must never be mutated.  If you need mutable runtime state,
see :mod:`aiex.models.state`.

Design rationale:
    Freezing the AST prevents accidental mutation during the compile step
    (routing resolution, contract generation, budget estimation).  It also
    makes the models hashable, so they can be used as dict keys or placed
    in sets when the compiler needs to deduplicate operations.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------

class ParseError(Exception):
    """Raised when the parser encounters invalid AILang syntax.

    Carries ``line`` and ``col`` so error messages can point the developer
    to the exact location in the ``.ailang`` source file.
    """

    def __init__(self, message: str, line: int = 0, col: int = 0) -> None:
        self.line = line
        self.col = col
        super().__init__(f"line {line}, col {col}: {message}")


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------
# These enums map 1-to-1 with the keyword vocabulary of the AILang grammar.
# The parser produces enum *values*; the compiler and routing tables consume
# them to select templates, model tiers, and token budgets.

class ReasoningType(str, Enum):
    """The six canonical reasoning operations plus the untyped fallback.

    Each type corresponds to a verb in AILang source code:
        RETRIEVE   -- look up facts from sources / vector store
        COMPARE    -- structured multi-dimensional comparison
        TRACE      -- follow a causal or dependency chain
        DEDUCE     -- apply logical rules to reach a conclusion
        SYNTHESIZE -- merge multiple inputs into a cohesive output
        WEIGH      -- evaluate trade-offs and produce a recommendation
        INTELLIGENTLY -- the untyped fallback when no verb is specified;
                         the router picks a sensible default model tier
    """
    RETRIEVE = "RETRIEVE"
    COMPARE = "COMPARE"
    TRACE = "TRACE"
    DEDUCE = "DEDUCE"
    SYNTHESIZE = "SYNTHESIZE"
    WEIGH = "WEIGH"
    INTELLIGENTLY = "INTELLIGENTLY"  # untyped fallback


class StyleModifier(str, Enum):
    """Adverb-style modifiers that colour an operation's prompt.

    These map to the optional adverb that precedes an operation verb in
    AILang (e.g. ``CREATIVELY SYNTHESIZE ...``).  The template composer
    prepends a style-specific instruction prefix to the system prompt so
    the LLM adjusts its tone/approach accordingly.
    """
    INTELLIGENTLY = "INTELLIGENTLY"
    CREATIVELY = "CREATIVELY"
    ADAPTIVELY = "ADAPTIVELY"
    CONTEXTUALLY = "CONTEXTUALLY"


class DepthLevel(str, Enum):
    """How deeply the operation should explore its search space.

    Controls two things at runtime:
      1. The ``token_multiplier`` (more depth = more tokens allocated).
      2. The ``max_passes`` (THOROUGH+ may re-prompt the LLM for refinement).
    DEEP and EXHAUSTIVE also trigger model-tier escalation via the routing
    rules in :mod:`aiex.models.routing`.
    """
    SHALLOW = "SHALLOW"
    STANDARD = "STANDARD"
    THOROUGH = "THOROUGH"
    DEEP = "DEEP"
    EXHAUSTIVE = "EXHAUSTIVE"


class RetrievalBudget(str, Enum):
    """How many external / vector sources the operation may consult.

    Maps to a ``top_k`` value in the retrieval stage and to a
    ``prompt_modifier`` instruction that tells the LLM how aggressively
    to cite external evidence.
    """
    NONE = "NONE"
    MINIMAL = "MINIMAL"
    STANDARD = "STANDARD"
    FULL = "FULL"


class Direction(str, Enum):
    """Trace direction for TRACE operations.

    FORWARD follows cause -> effect; BACKWARD follows effect -> cause.
    Used in the ``trace_chain`` prompt template to set the traversal order.
    """
    FORWARD = "FORWARD"
    BACKWARD = "BACKWARD"


# ---------------------------------------------------------------------------
# Frozen base
# ---------------------------------------------------------------------------

class _Frozen(BaseModel):
    """Common base that locks every subclass.

    ``frozen=True`` makes all fields immutable after construction.  Any
    attempt to assign to a field raises a ``ValidationError``.  This is
    the core invariant that separates compile-time models (this module)
    from mutable runtime state (:mod:`aiex.models.state`).
    """
    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# Leaf nodes
# ---------------------------------------------------------------------------
# These map to the DATA DIVISION of an AILang program, where the developer
# declares the variables and field definitions the program will use.

class Variable(_Frozen):
    """A declared variable (``SET name TO value``).

    Corresponds to the AILang DATA DIVISION statement::

        SET api_key TO "sk-..."
        SET threshold TO 0.85

    ``type`` defaults to ``"STRING"`` but can also be ``"NUMBER"``,
    ``"BOOLEAN"``, etc.  The runtime stores these in
    :attr:`ProgramState.variables`.
    """
    name: str
    type: str = Field(default="STRING")
    value: Any = Field(default=None)


class ObjectField(_Frozen):
    """A single key inside an OUTPUT_FORMAT object.

    When AILang source declares a structured output like::

        OUTPUT_FORMAT {
            summary: TEXT
            score: NUMBER
        }

    each ``summary`` / ``score`` line becomes one ``ObjectField``.
    """
    name: str
    type: str = Field(default="STRING")
    value: Optional[str] = Field(default=None)


# ---------------------------------------------------------------------------
# OUTPUT_FORMAT schema (recursive)
# ---------------------------------------------------------------------------
# AILang supports arbitrarily nested output schemas, including lists of
# objects.  This model mirrors that recursion:
#
#   Scalar:   OutputFormatSchema(name="score", field_type="NUMBER")
#   Object:   OutputFormatSchema(name="result", fields=[...children...])
#   List:     OutputFormatSchema(name="items", is_list=True,
#                 element_schema=OutputFormatSchema(...))
#
# Example AILang source and its AST mapping:
#
#   OUTPUT_FORMAT {
#       findings: LIST OF OBJECT {
#           title: TEXT
#           severity: TEXT IN ["LOW", "MEDIUM", "HIGH"]
#       }
#   }
#
# becomes:
#   OutputFormatSchema(
#       name="root",
#       fields=[
#           OutputFormatSchema(
#               name="findings", is_list=True,
#               element_schema=OutputFormatSchema(
#                   name="findings_item",
#                   fields=[
#                       OutputFormatSchema(name="title", field_type="TEXT"),
#                       OutputFormatSchema(name="severity", field_type="TEXT",
#                                          constraint=["LOW", "MEDIUM", "HIGH"]),
#                   ]
#               )
#           )
#       ]
#   )

class OutputFormatSchema(_Frozen):
    """Recursive structure representing an OUTPUT_FORMAT block.

    A leaf has *field_type* set (e.g. ``"TEXT"``).
    A composite has *fields* populated with child schemas.
    ``constraint`` holds the list from ``IN [...]`` annotations.
    ``element_schema`` holds the sub-schema for ``LIST OF ...`` types.
    """
    # "root" for the top-level schema; the field name otherwise.
    name: str = Field(default="root")
    # Leaf type -- TEXT, NUMBER, BOOLEAN, etc.  None for composite nodes.
    field_type: Optional[str] = Field(default=None)
    # Child fields when this node is an OBJECT (non-leaf composite).
    fields: list[OutputFormatSchema] = Field(default_factory=list)
    # True when the AILang type annotation is "LIST OF ...".
    is_list: bool = Field(default=False)
    # For LIST types: the schema of each element (may itself be an OBJECT).
    element_schema: Optional[OutputFormatSchema] = Field(default=None)
    # Enumeration constraint from ``IN ["a", "b", "c"]`` annotations.
    constraint: Optional[list[str]] = Field(default=None)
    # Optional developer-facing description carried through to JSON schema.
    description: Optional[str] = Field(default=None)


# ---------------------------------------------------------------------------
# Boundary / contract spec (compile-time)
# ---------------------------------------------------------------------------
# Boundaries are the *compile-time* representation of the CONTRACT DIVISION.
# They are attached directly to an operation in the AST and later expanded
# into full ``TransactionContract`` objects during the compile step.

class BoundarySpec(_Frozen):
    """MUST_INCLUDE / CANNOT_INCLUDE / OUTPUT_FORMAT constraints.

    This is the raw contract data as written in AILang source.  The
    compiler inflates it into a :class:`~aiex.models.plan.TransactionContract`
    with detection methods, severity levels, and retry policies.
    """
    must_include: list[str] = Field(default_factory=list)
    cannot_include: list[str] = Field(default_factory=list)
    output_format: Optional[OutputFormatSchema] = Field(default=None)


# ---------------------------------------------------------------------------
# The universal operation node
# ---------------------------------------------------------------------------
# Rather than having a separate AST node per verb (RetrieveNode, CompareNode,
# etc.), AILang uses a single "universal" node.  The ``reasoning_type`` field
# discriminates the verb, and optional fields like ``dimensions``, ``sources``,
# ``direction`` carry the verb-specific clauses.  This keeps the compiler and
# runtime simple -- they only need to handle one node type.

class OperationNode(_Frozen):
    """A single reasoning operation in the program.

    This is the "universal" node -- every AILang verb (RETRIEVE, COMPARE,
    DEDUCE, ...) compiles down to one of these.  The compiler reads
    ``reasoning_type`` to select the prompt template, model tier, and
    temperature from the routing tables.

    Fields that are only meaningful for specific verbs:
        ``direction``   -- TRACE only (FORWARD / BACKWARD)
        ``dimensions``  -- COMPARE only (the comparison axes)
        ``sources``     -- COMPARE (the items being compared)
        ``from_sources`` -- RETRIEVE (knowledge bases / vector stores)
        ``against``     -- DEDUCE (the rule set to apply against)

    ``line`` and ``col`` preserve source locations for error reporting.
    """
    # The variable name that will hold this operation's output.
    result_name: str
    reasoning_type: ReasoningType = Field(default=ReasoningType.INTELLIGENTLY)
    style: Optional[StyleModifier] = Field(default=None)
    depth: DepthLevel = Field(default=DepthLevel.STANDARD)
    retrieval_budget: RetrievalBudget = Field(default=RetrievalBudget.STANDARD)
    # TRACE-specific: which direction to follow the chain.
    direction: Optional[Direction] = Field(default=None)
    # RETRIEVE-specific: explicit source names (vector stores, APIs).
    from_sources: list[str] = Field(default_factory=list)
    # The primary input text/variable this operation acts on.
    apply_to: Optional[str] = Field(default=None)
    # COMPARE-specific: the items being compared.
    sources: list[str] = Field(default_factory=list)
    # COMPARE-specific: the axes / dimensions of comparison.
    dimensions: list[str] = Field(default_factory=list)
    # When True, the operation appends a digest entry to an accumulator
    # (useful for building summary tables across THEN-chains).
    emit_digest: Optional[bool] = Field(default=None)
    # Compile-time contract constraints (MUST_INCLUDE, CANNOT_INCLUDE, etc.).
    boundary: Optional[BoundarySpec] = Field(default=None)
    # DEDUCE-specific: the rule/premise set to reason against.
    against: Optional[str] = Field(default=None)
    # The topic/question string for the operation prompt.
    on_topic: Optional[str] = Field(default=None)
    # The query string for RETRIEVE operations.
    for_query: Optional[str] = Field(default=None)
    # Target variable to write output into (alternative to result_name).
    into_target: Optional[str] = Field(default=None)
    # True when the input comes from a prior operation's result.
    from_result: bool = Field(default=False)
    # True when the input comes from the current REALITY_CONTEXT.
    from_context: bool = Field(default=False)
    # Combines context with an additional source reference.
    from_context_and: Optional[str] = Field(default=None)
    # Inline output format (alternative to boundary.output_format).
    output_format: Optional[OutputFormatSchema] = Field(default=None)
    # Catch-all for verb-specific key-value pairs not modelled above.
    properties: dict[str, Any] = Field(default_factory=dict)
    # Source location for error messages.
    line: int = Field(default=0)
    col: int = Field(default=0)


# ---------------------------------------------------------------------------
# Reasoning profile & reality context
# ---------------------------------------------------------------------------
# REALITY_CONTEXT is AILang's mechanism for scoping operations under a
# particular worldview, set of assumptions, or value system.  It allows
# the same program to produce different outputs when run under different
# contexts (e.g. "optimistic economist" vs "risk-averse regulator").

class ReasoningProfile(_Frozen):
    """Configures which reasoning types are preferred / suppressed.

    Embedded inside a ``RealityContext`` to bias the router.  For example,
    a "conservative analyst" context might set ``primary="DEDUCE"`` and
    ``suppress=["CREATIVELY"]`` to favour logic over creativity.
    """
    primary: Optional[str] = Field(default=None)
    secondary: Optional[str] = Field(default=None)
    constrain: dict[str, str] = Field(default_factory=dict)
    suppress: list[str] = Field(default_factory=list)


class RealityContext(_Frozen):
    """A ``DEFINE REALITY_CONTEXT`` block.

    Provides a named lens through which operations execute.  The runtime
    injects the worldview, assumptions, and value system into the system
    prompt so the LLM reasons within those constraints.
    """
    name: str
    worldview: Optional[str] = Field(default=None)
    reasoning_profile: Optional[ReasoningProfile] = Field(default=None)
    assumptions: list[str] = Field(default_factory=list)
    value_system: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Context blocks
# ---------------------------------------------------------------------------
# These container nodes group operations under a shared scope.  They are
# the AILang equivalent of COBOL's SECTION groupings.

class ReasoningContextBlock(_Frozen):
    """A named ``REASONING_CONTEXT`` containing a sequence of operations.

    Groups related operations so they can be referenced by name elsewhere
    in the program (e.g. from a ``WITH CONTEXT`` block).
    """
    name: str
    operations: list[Any] = Field(default_factory=list)


class WithContextBlock(_Frozen):
    """A ``WITH CONTEXT ...`` block that scopes operations to a reality.

    All operations inside this block inherit the worldview, assumptions,
    and reasoning profile of the named ``RealityContext``.
    """
    context_name: str
    operations: list[Any] = Field(default_factory=list)


class ThenChain(_Frozen):
    """A ``THEN``-chain -- sequential operations with implicit data flow.

    Each operation in the chain receives the previous operation's output
    as its input.  The runtime executes them strictly in order, threading
    the result through.  This is AILang's primary mechanism for building
    multi-step reasoning pipelines.
    """
    operations: list[OperationNode] = Field(default_factory=list)


class CodeBlock(_Frozen):
    """An ``EXECUTE_CODE LANG: ... END_EXECUTE`` block.

    Holds raw source code in a target language (currently ``python``).
    The runtime executes the body with ``exec()`` against a namespace
    populated from current AILang variables; any new bindings produced
    by the code become AILang variables in subsequent operations.
    Code blocks contribute zero LLM cost to the budget but participate
    in execution ordering exactly like reasoning operations.
    """
    language: str
    body: str
    line: int = Field(default=0)
    col: int = Field(default=0)


# ---------------------------------------------------------------------------
# Top-level program
# ---------------------------------------------------------------------------
# Mirrors the top-level structure of an AILang source file, which (like
# COBOL) is organized into divisions:
#
#   IDENTIFICATION DIVISION  -->  version
#   DATA DIVISION            -->  variables
#   EXECUTION DIVISION       -->  operations (+ reality/reasoning contexts)
#   CONTRACT DIVISION         -->  boundary specs on individual operations

class Program(_Frozen):
    """Root AST node for a complete AILang source file.

    The parser produces exactly one ``Program`` per ``.ailang`` file.
    The compiler walks its children to produce an ``ExecutionPlan``.
    """
    version: str = Field(default="0.8.2")
    # DATA DIVISION: user-declared variables (SET ... TO ...).
    variables: list[Variable] = Field(default_factory=list)
    # EXECUTION DIVISION: top-level operations, THEN-chains, and
    # WITH CONTEXT blocks.  Uses ``list[Any]`` because items can be
    # OperationNode, ThenChain, or WithContextBlock.
    operations: list[Any] = Field(default_factory=list)
    # Named REALITY_CONTEXT definitions referenced by WITH CONTEXT blocks.
    reality_contexts: list[RealityContext] = Field(default_factory=list)
    # Named REASONING_CONTEXT groupings.
    reasoning_contexts: list[ReasoningContextBlock] = Field(default_factory=list)
    # Raw statement list (pre-normalization); operations is the canonical
    # source after the parser has resolved statement ordering.
    statements: list[Any] = Field(default_factory=list)
