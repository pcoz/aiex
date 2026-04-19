"""Recursive-descent parser for AILang v0.8.2 source files.

Consumes a token stream produced by :class:`aiex.parser.lexer.Lexer` and
builds a typed AST using the Pydantic models in :mod:`aiex.models.ast_nodes`.

Architecture
------------
The parser is a classic hand-written **recursive descent** parser.  Each
grammar production maps to one ``parse_*`` or ``_parse_*`` method:

  Program        -> parse()               top-level loop
  Statement      -> _parse_statement()    dispatch on leading token
  SET            -> parse_set()           variable declaration
  Operation      -> parse_operation()     any reasoning verb, with THEN chains
  WITH block     -> _parse_with_section() key-value property block
  OUTPUT_FORMAT  -> parse_output_format() recursive schema description
  REALITY_CONTEXT  -> parse_reality_context_def()
  REASONING_CONTEXT -> parse_reasoning_context_block()

THEN chains are handled at the ``parse_operation`` level: after parsing the
first operation, the parser checks for a THEN token and, if found, continues
consuming operations into a :class:`ThenChain` list.  The trailing ``END``
is consumed once -- at the end of the entire chain, not after each step.
This means individual ``_parse_single_operation`` methods must stop *before*
END so the chain logic can decide what to do next.

OUTPUT_FORMAT schemas are parsed recursively: ``parse_output_format`` calls
``_parse_type_expr`` for each field, and ``_parse_type_expr`` may recurse
back into ``parse_output_format`` for nested OBJECT types, or into
``_parse_list_element_type`` for LIST OF ... constructions (which can also
nest arbitrarily).

The parser pre-filters the token stream to remove COMMENT and NEWLINE
tokens, so grammar methods never need to worry about whitespace or
comments between meaningful tokens.

Usage::

    from aiex.parser.lexer import Lexer
    from aiex.parser.parser import Parser

    tokens = Lexer(source).tokenize()
    program = Parser(tokens).parse()
"""

from __future__ import annotations

from typing import Any

from aiex.models.ast_nodes import (
    BoundarySpec,
    CodeBlock,
    OperationNode,
    OutputFormatSchema,
    ParseError,
    Program,
    ReasoningContextBlock,
    ReasoningProfile,
    ReasoningType,
    RealityContext,
    StyleModifier,
    ThenChain,
    Variable,
    WithContextBlock,
)
from aiex.parser.lexer import Token


# These sets drive the parser's lookahead decisions.  They are defined at
# module level to keep the grammar logic in the methods readable.

# Tokens that can start a reasoning operation.  INTELLIGENTLY is included
# because it can act as both a style modifier AND the operation verb
# (e.g. "INTELLIGENTLY create_person FROM ...").
_OPERATION_TYPES = frozenset({
    "DEDUCE", "TRACE", "WEIGH", "COMPARE", "RETRIEVE", "SYNTHESIZE",
    "INTELLIGENTLY",
})

# Adverb-style modifiers that may prefix any operation verb.  When the
# parser sees one of these, it records the style and then looks for the
# actual operation keyword that follows.
_STYLE_MODIFIERS = frozenset({
    "CREATIVELY", "ADAPTIVELY", "CONTEXTUALLY",
})

# Property keys recognised inside WITH: blocks.  This set is used for
# documentation / reference -- the actual parsing dispatches on each
# token type individually within _parse_with_section.
_PROPERTY_KEYS = frozenset({
    "RETRIEVAL_BUDGET", "DEPTH", "DIRECTION", "APPLY_TO",
    "SOURCES", "DIMENSIONS", "EMIT_DIGEST",
    "MUST_INCLUDE", "CANNOT_INCLUDE", "OUTPUT_FORMAT",
    "WITHOUT_RETRIEVAL",
    "OUTPUT", "HINTS",
})


class Parser:
    """Recursive-descent parser for AILang v0.8.2.

    Parameters
    ----------
    tokens : list[Token]
        Token stream produced by :class:`Lexer.tokenize`.
    """

    def __init__(self, tokens: list[Token]) -> None:
        # Pre-filter the token stream to drop COMMENT and NEWLINE tokens.
        # AILang's grammar is newline-insensitive (statements are delimited
        # by keywords like END, not by line breaks), so removing these up
        # front means every grammar method can assume the next token is
        # always semantically meaningful.  Comments are preserved in the raw
        # lexer output for tooling that needs them (e.g. formatters).
        self._tokens: list[Token] = [
            t for t in tokens if t.type not in ("COMMENT", "NEWLINE")
        ]
        self._pos: int = 0  # cursor into the filtered token stream

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------
    # These five methods form the "cursor" API that all grammar methods
    # use to navigate the token stream.  They are intentionally minimal:
    #   _cur / _peek  -- lookahead without consuming
    #   _advance      -- unconditional consume
    #   _expect       -- consume-or-die (used when the grammar demands a token)
    #   _match        -- optional consume (used for optional syntax like commas)
    #   _at           -- pure lookahead predicate (no side effects)
    # ------------------------------------------------------------------

    def _cur(self) -> Token:
        """Return current token (never past EOF)."""
        if self._pos < len(self._tokens):
            return self._tokens[self._pos]
        # Synthetic EOF so callers never need to bounds-check.
        return Token("EOF", "", 0, 0)

    def _peek(self, offset: int = 0) -> Token:
        """Peek ahead *offset* positions from current without consuming."""
        idx = self._pos + offset
        if idx < len(self._tokens):
            return self._tokens[idx]
        return Token("EOF", "", 0, 0)

    def _advance(self) -> Token:
        """Consume and return the current token."""
        tok = self._cur()
        self._pos += 1
        return tok

    def _expect(self, *types: str) -> Token:
        """Consume and return a token whose type is one of *types*.

        Raises :class:`ParseError` if the current token doesn't match.
        This is the primary mechanism for enforcing mandatory grammar
        elements -- if the expected token is missing, parsing stops with
        a clear error message including the source location.
        """
        tok = self._cur()
        if tok.type not in types:
            expected = " or ".join(types)
            raise ParseError(
                f"expected {expected}, got {tok.type} ({tok.value!r})",
                tok.line,
                tok.col,
            )
        return self._advance()

    def _match(self, *types: str) -> Token | None:
        """If current token matches one of *types*, consume and return it.

        Returns None (without advancing) if there is no match.  Useful for
        optional syntax elements like trailing commas in lists.
        """
        if self._cur().type in types:
            return self._advance()
        return None

    def _at(self, *types: str) -> bool:
        """True if the current token is one of *types* (pure lookahead)."""
        return self._cur().type in types

    def _error(self, msg: str) -> ParseError:
        """Create a ParseError anchored at the current token's location."""
        tok = self._cur()
        return ParseError(msg, tok.line, tok.col)

    # ------------------------------------------------------------------
    # Top level
    # ------------------------------------------------------------------

    def parse(self) -> Program:
        """Parse the full token stream into a :class:`Program` AST.

        This is the entry point for the grammar.  It loops over top-level
        statements until EOF and sorts each parsed node into the appropriate
        bucket on the Program AST (variables, operations, contexts, etc.).
        The ``statements`` list preserves the original source order across
        all node types, which is important for execution.
        """
        version = "0.8.2"
        variables: list[Variable] = []
        operations: list[Any] = []
        reality_contexts: list[RealityContext] = []
        reasoning_contexts: list[ReasoningContextBlock] = []
        statements: list[Any] = []

        # The version header (#ailang v0.8.2) is optional -- if omitted we
        # default to 0.8.2.
        if self._at("VERSION"):
            tok = self._advance()
            version = tok.value

        # Main parsing loop: consume one statement per iteration and
        # classify it into the correct Program field.
        while not self._at("EOF"):
            stmt = self._parse_statement()
            if stmt is None:
                continue
            statements.append(stmt)
            if isinstance(stmt, Variable):
                variables.append(stmt)
            elif isinstance(stmt, RealityContext):
                reality_contexts.append(stmt)
            elif isinstance(stmt, ReasoningContextBlock):
                reasoning_contexts.append(stmt)
            elif isinstance(stmt, WithContextBlock):
                # WITH blocks can contain nested REASONING_CONTEXTs -- we
                # lift those into the top-level list so they are accessible
                # for cross-referencing, while also keeping the WITH block
                # itself in the operations list.
                for op in stmt.operations:
                    if isinstance(op, ReasoningContextBlock):
                        reasoning_contexts.append(op)
                operations.append(stmt)
            else:
                operations.append(stmt)

        return Program(
            version=version,
            variables=variables,
            operations=operations,
            reality_contexts=reality_contexts,
            reasoning_contexts=reasoning_contexts,
            statements=statements,
        )

    # ------------------------------------------------------------------
    # Statement dispatch
    # ------------------------------------------------------------------

    def _parse_statement(self) -> Any:
        """Dispatch to the correct sub-parser based on the leading token.

        This is the central routing table for AILang's top-level grammar.
        Each branch corresponds to one production rule:

          SET         -> variable declaration
          DEFINE      -> REALITY_CONTEXT definition
          WITH        -> scoped context block (needs 1-token lookahead to
                         confirm REALITY_CONTEXT follows)
          BEGIN       -> REASONING_CONTEXT block
          operation   -> any reasoning verb or style-modified operation
          dotted IDENT -> method-call syntax (obj.method(...))
        """
        tok = self._cur()

        if tok.type == "SET":
            return self.parse_set()

        if tok.type == "DEFINE":
            return self.parse_reality_context_def()

        if tok.type == "WITH":
            # WITH is overloaded: it starts both "WITH REALITY_CONTEXT ..."
            # blocks and "WITH:" property sections inside operations.  At
            # the statement level we only expect the context-block form,
            # so we peek ahead to confirm REALITY_CONTEXT follows.
            if self._peek(1).type == "REALITY_CONTEXT":
                return self.parse_with_context_block()
            raise self._error("unexpected WITH without REALITY_CONTEXT")

        if tok.type == "BEGIN":
            return self.parse_reasoning_context_block()

        if tok.type == "EXECUTE_CODE":
            return self.parse_execute_code()

        if tok.type in _OPERATION_TYPES or tok.type in _STYLE_MODIFIERS:
            return self.parse_operation()

        # Dotted identifiers at statement level represent method-call syntax
        # (e.g. "incident_commander.think(situation)").  We route these
        # through parse_operation, which delegates to _parse_method_call.
        if tok.type == "IDENT" and "." in tok.value:
            return self.parse_operation()

        raise self._error(
            f"unexpected token {tok.type} ({tok.value!r}) at top level"
        )

    # ------------------------------------------------------------------
    # SET name TO <value>
    # ------------------------------------------------------------------

    def parse_set(self) -> Variable:
        """Parse ``SET name TO <value>``.

        The variable type is inferred from the parsed value's Python type
        rather than declared explicitly in the source.  This keeps the
        AILang syntax minimal while still giving the AST enough type info
        for downstream code generation.
        """
        tok = self._expect("SET")
        line, col = tok.line, tok.col
        # The name can be a bare identifier or a quoted string (useful for
        # names that would collide with keywords).
        name_tok = self._expect("IDENT", "STRING")
        name = name_tok.value
        self._expect("TO")
        value = self.parse_value()

        # Infer the variable type from the runtime Python type of the
        # parsed value.  Default is STRING for bare identifiers.
        vtype = "STRING"
        if isinstance(value, dict):
            vtype = "OBJECT"
        elif isinstance(value, list):
            vtype = "LIST"
        elif isinstance(value, int):
            vtype = "INTEGER"
        elif isinstance(value, float):
            vtype = "FLOAT"

        return Variable(name=name, type=vtype, value=value)

    # ------------------------------------------------------------------
    # Value parsing (used by SET and inline values)
    # ------------------------------------------------------------------

    def parse_value(self) -> str | int | float | bool | list | dict:
        """Parse a value: string, number, boolean, list, or object."""
        tok = self._cur()

        if tok.type == "STRING":
            self._advance()
            return tok.value

        if tok.type == "INTEGER":
            self._advance()
            return int(tok.value)

        if tok.type == "FLOAT":
            self._advance()
            return float(tok.value)

        if tok.type == "TRUE":
            self._advance()
            return True

        if tok.type == "FALSE":
            self._advance()
            return False

        if tok.type == "LBRACKET":
            return self.parse_list()

        if tok.type == "LBRACE":
            return self.parse_object()

        # Bare identifier as value (e.g. a variable name reference)
        if tok.type == "IDENT":
            self._advance()
            return tok.value

        raise self._error(
            f"expected a value (string, number, list, or object), got {tok.type}"
        )

    def parse_list(self) -> list:
        """Parse ``[ item1, item2, ... ]``."""
        self._expect("LBRACKET")
        items: list[Any] = []
        while not self._at("RBRACKET", "EOF"):
            items.append(self._parse_list_item())
            self._match("COMMA")
        self._expect("RBRACKET")
        return items

    def _parse_list_item(self) -> Any:
        """Parse one item inside a list -- can be a value or bare identifier/keyword."""
        tok = self._cur()
        if tok.type in ("STRING", "INTEGER", "FLOAT", "TRUE", "FALSE",
                        "LBRACKET", "LBRACE"):
            return self.parse_value()
        # Bare identifiers (and keywords used as identifiers in list context)
        if tok.type == "IDENT" or self._is_identifier_like(tok):
            self._advance()
            return tok.value
        raise self._error(
            f"expected list item, got {tok.type} ({tok.value!r})"
        )

    def parse_object(self) -> dict:
        """Parse ``{ key: value, ... }``.

        Keys can be bare identifiers, keywords-as-identifiers, or quoted
        strings.  This flexibility is needed because AILang object literals
        often use keys like "primary" or "standard" that are also keywords
        in other contexts.
        """
        self._expect("LBRACE")
        obj: dict[str, Any] = {}
        while not self._at("RBRACE", "EOF"):
            key_tok = self._cur()
            if key_tok.type == "IDENT" or self._is_identifier_like(key_tok):
                self._advance()
                key = key_tok.value
            else:
                key_tok = self._expect("STRING")
                key = key_tok.value
            self._expect("COLON")
            value = self._parse_object_value()
            obj[key] = value
            # Commas between entries are optional (AILang is lenient here).
            self._match("COMMA")
        self._expect("RBRACE")
        return obj

    def _parse_object_value(self) -> Any:
        """Parse a value on the right side of a key: inside an object literal.

        This handles nested objects, lists, strings, numbers, booleans, and
        also bare identifiers (used for enum-like values in case_facts).
        """
        tok = self._cur()
        if tok.type in ("STRING", "INTEGER", "FLOAT", "TRUE", "FALSE",
                        "LBRACKET", "LBRACE"):
            return self.parse_value()
        # Bare identifiers or keywords used as values
        if tok.type == "IDENT" or self._is_identifier_like(tok):
            self._advance()
            return tok.value
        raise self._error(
            f"expected object value, got {tok.type} ({tok.value!r})"
        )

    @staticmethod
    def _is_identifier_like(tok: Token) -> bool:
        """True if the token is a keyword that can also serve as a bare name.

        Many AILang keywords (NONE, STANDARD, DEDUCE, etc.) appear as
        bare identifiers in certain positions (list items, object values,
        reasoning profile values, etc.).  Without this helper, the parser
        would reject valid source like ``DEPTH: STANDARD`` because
        "STANDARD" is tokenised as a keyword, not an IDENT.  This method
        defines the set of keywords that are allowed in value positions.
        """
        return tok.type in (
            # Depth / budget values
            "NONE", "MINIMAL", "STANDARD", "FULL",
            "SHALLOW", "THOROUGH", "DEEP", "EXHAUSTIVE",
            # Direction
            "FORWARD", "BACKWARD",
            # Operations used as values in reasoning profiles
            "DEDUCE", "TRACE", "WEIGH", "RETRIEVE", "SYNTHESIZE", "COMPARE",
            "INTELLIGENTLY",
            # Style modifiers
            "CREATIVELY", "ADAPTIVELY", "CONTEXTUALLY",
            # Type names when used as values
            "TEXT", "BOOLEAN", "NUMBER", "OBJECT", "LIST", "QUALITATIVE",
            # Boolean-ish
            "TRUE", "FALSE",
            # Misc
            "PRIMARY", "SECONDARY",
            "RESULT", "CONTEXT",
        )

    # ------------------------------------------------------------------
    # Operations
    # ------------------------------------------------------------------

    def parse_operation(self) -> OperationNode | ThenChain:
        """Parse a reasoning operation, possibly with THEN chaining.

        Handles:
          [STYLE] OPERATION result_name FROM [...] WITH: ... END
          COMPARE [...] AGAINST criteria WITH: ... END
          THEN chains
          INTELLIGENTLY create_person FROM ... WITH: ... END
          dotted_name.method(args) WITH: ... END

        THEN chain strategy: sub-parsers (_parse_single_operation and its
        delegates) parse everything up to but NOT including END or THEN.
        This method then checks the next token:
          - If THEN, it collects more operations into a ThenChain list.
          - If END, it consumes it and returns.
        This way a single END closes the entire chain, matching AILang's
        syntax where THEN-chained operations share one END delimiter.
        """
        first_op = self._parse_single_operation()

        # THEN chaining: collect subsequent operations until we hit END.
        # Each THEN keyword is consumed here, then the next operation is
        # parsed.  The final END is consumed once to close the whole chain.
        if self._at("THEN"):
            chain_ops = [first_op]
            while self._at("THEN"):
                self._advance()  # consume THEN
                next_op = self._parse_single_operation()
                chain_ops.append(next_op)
            self._expect("END")
            return ThenChain(operations=chain_ops)

        # Single operation (no THEN chain) -- consume the closing END.
        self._expect("END")
        return first_op

    def _parse_single_operation(self) -> OperationNode:
        """Parse one operation up to (but NOT including) END or THEN.

        This is the second-level dispatch: after handling an optional style
        modifier prefix, it reads the operation verb and delegates to the
        verb-specific parser.  Each verb has different syntax for its
        arguments (FROM, AGAINST, INTO, FOR, ON, etc.), so they each get
        their own method.
        """
        tok = self._cur()
        line, col = tok.line, tok.col
        style: str | None = None
        reasoning_type: str = "INTELLIGENTLY"

        # An optional style modifier (CREATIVELY, ADAPTIVELY, CONTEXTUALLY)
        # can prefix any operation verb to influence prompt generation.
        if tok.type in _STYLE_MODIFIERS:
            style = tok.value.upper()
            self._advance()
            tok = self._cur()

        # Dotted identifiers at this position are method-call syntax, not
        # standard AILang verbs -- route them to the method-call parser.
        if tok.type == "IDENT" and "." in tok.value:
            return self._parse_method_call(style, line, col)

        # Read the operation verb (DEDUCE, TRACE, WEIGH, etc.).
        if tok.type in _OPERATION_TYPES:
            reasoning_type = tok.value.upper()
            self._advance()
        else:
            raise self._error(
                f"expected reasoning operation, got {tok.type} ({tok.value!r})"
            )

        # Dispatch to verb-specific parsers.  Each verb has its own argument
        # syntax (COMPARE uses AGAINST, RETRIEVE uses FOR, SYNTHESIZE uses
        # INTO, etc.), so they are handled separately.  DEDUCE and TRACE
        # share the same "result FROM [sources]" syntax.
        if reasoning_type == "COMPARE":
            return self._parse_compare(style, reasoning_type, line, col)
        elif reasoning_type == "RETRIEVE":
            return self._parse_retrieve(style, reasoning_type, line, col)
        elif reasoning_type == "SYNTHESIZE":
            return self._parse_synthesize(style, reasoning_type, line, col)
        elif reasoning_type == "WEIGH":
            return self._parse_weigh(style, reasoning_type, line, col)
        elif reasoning_type == "INTELLIGENTLY":
            return self._parse_intelligently(style, reasoning_type, line, col)
        else:
            return self._parse_deduce_trace(
                style, reasoning_type, line, col,
            )

    def _parse_deduce_trace(
        self,
        style: str | None,
        reasoning_type: str,
        line: int,
        col: int,
    ) -> OperationNode:
        """Parse DEDUCE/TRACE: result FROM [...] WITH: ... (no END consumed).

        Grammar: ``DEDUCE result_name FROM source_spec WITH: ... ``

        The FROM clause supports several forms to handle data flow between
        operations:
          - FROM [source1, source2]   -- explicit source list
          - FROM single_source        -- shorthand for one source
          - FROM RESULT               -- implicit input from previous THEN step
          - FROM CONTEXT              -- use the enclosing reasoning context
          - FROM CONTEXT AND extra    -- context plus an additional source
        """
        result_name = self._expect("IDENT").value
        self._expect("FROM")

        # Determine which FROM variant we have by peeking at the next token.
        from_result = False
        from_context = False
        from_context_and: str | None = None
        from_sources: list[str] = []

        if self._at("RESULT"):
            # FROM RESULT: the operation receives its input from the
            # preceding step in a THEN chain.
            self._advance()
            from_result = True
        elif self._at("CONTEXT"):
            # FROM CONTEXT: the operation uses the enclosing reasoning
            # context as its input.  "AND ident" adds a supplementary source.
            self._advance()
            from_context = True
            if self._at("AND"):
                self._advance()
                from_context_and = self._expect("IDENT").value
        elif self._at("LBRACKET"):
            from_sources = self._parse_ident_list()
        else:
            from_sources = [self._expect("IDENT").value]

        properties, boundary, output_format, extra = self._parse_with_section()

        return self._build_operation(
            result_name=result_name,
            reasoning_type=reasoning_type,
            style=style,
            from_sources=from_sources,
            from_result=from_result,
            from_context=from_context,
            from_context_and=from_context_and,
            properties=properties,
            boundary=boundary,
            output_format=output_format,
            line=line,
            col=col,
            extra=extra,
        )

    def _parse_compare(
        self, style: str | None, reasoning_type: str, line: int, col: int,
    ) -> OperationNode:
        """Parse COMPARE [...] AGAINST criteria WITH: ... END.

        COMPARE has two syntactic forms:
          1. ``COMPARE [item1, item2] AGAINST criteria``  -- list form
          2. ``COMPARE result_name AGAINST criteria FROM ...`` -- named form

        In the list form, the result_name is auto-generated from the
        criteria name (e.g. "compare_risk_tolerance").  The optional FROM
        clause works the same way as in DEDUCE/TRACE.
        """
        from_result = False
        from_context = False
        from_sources: list[str] = []
        result_name: str = ""
        against: str | None = None

        # Disambiguate the two forms: list starts with '[', named starts
        # with an identifier.
        if self._at("LBRACKET"):
            from_sources = self._parse_ident_list()
        else:
            result_name = self._expect("IDENT").value

        self._expect("AGAINST")
        against_tok = self._cur()
        if against_tok.type == "IDENT" or self._is_identifier_like(against_tok):
            against = against_tok.value
            self._advance()
        else:
            raise self._error(
                f"expected criteria name after AGAINST, got {against_tok.type}"
            )

        # FROM clause is optional for COMPARE -- it allows piping data
        # from a previous THEN step or context.
        if self._at("FROM"):
            self._advance()
            if self._at("RESULT"):
                self._advance()
                from_result = True
            elif self._at("CONTEXT"):
                self._advance()
                from_context = True
            elif self._at("LBRACKET"):
                from_sources = self._parse_ident_list()
            else:
                from_sources = [self._expect("IDENT").value]

        # When using the list form, no explicit result_name was given, so
        # we synthesize one from the criteria to keep the AST consistent.
        if not result_name:
            result_name = f"compare_{against}" if against else "compare_result"

        properties, boundary, output_format, extra = self._parse_with_section()

        return self._build_operation(
            result_name=result_name,
            reasoning_type=reasoning_type,
            style=style,
            from_sources=from_sources,
            from_result=from_result,
            from_context=from_context,
            against=against,
            properties=properties,
            boundary=boundary,
            output_format=output_format,
            line=line,
            col=col,
            extra=extra,
        )

    def _parse_retrieve(
        self, style: str | None, reasoning_type: str, line: int, col: int,
    ) -> OperationNode:
        """Parse RETRIEVE result FOR query WITH: ... (no END consumed)."""
        result_name = self._expect("IDENT").value
        self._expect("FOR")
        for_query = self._expect("IDENT", "STRING").value

        properties, boundary, output_format, extra = self._parse_with_section()

        return self._build_operation(
            result_name=result_name,
            reasoning_type=reasoning_type,
            style=style,
            for_query=for_query,
            properties=properties,
            boundary=boundary,
            output_format=output_format,
            line=line,
            col=col,
            extra=extra,
        )

    def _parse_synthesize(
        self, style: str | None, reasoning_type: str, line: int, col: int,
    ) -> OperationNode:
        """Parse SYNTHESIZE items INTO result WITH: ... END."""
        from_sources: list[str] = []
        if self._at("LBRACKET"):
            from_sources = self._parse_ident_list()
        else:
            from_sources = [self._expect("IDENT").value]
        self._expect("INTO")
        result_name = self._expect("IDENT").value

        properties, boundary, output_format, extra = self._parse_with_section()

        return self._build_operation(
            result_name=result_name,
            reasoning_type=reasoning_type,
            style=style,
            from_sources=from_sources,
            into_target=result_name,
            properties=properties,
            boundary=boundary,
            output_format=output_format,
            line=line,
            col=col,
            extra=extra,
        )

    def _parse_weigh(
        self, style: str | None, reasoning_type: str, line: int, col: int,
    ) -> OperationNode:
        """Parse WEIGH result ON topic FROM source WITH: ... END."""
        result_name = self._expect("IDENT").value

        on_topic: str | None = None
        if self._at("ON"):
            self._advance()
            on_topic = self._expect("IDENT").value

        from_sources: list[str] = []
        from_context = False
        from_context_and: str | None = None
        if self._at("FROM"):
            self._advance()
            if self._at("CONTEXT"):
                self._advance()
                from_context = True
                if self._at("AND"):
                    self._advance()
                    from_context_and = self._expect("IDENT").value
            elif self._at("LBRACKET"):
                from_sources = self._parse_ident_list()
            else:
                from_sources = [self._expect("IDENT").value]

        properties, boundary, output_format, extra = self._parse_with_section()

        return self._build_operation(
            result_name=result_name,
            reasoning_type=reasoning_type,
            style=style,
            on_topic=on_topic,
            from_sources=from_sources,
            from_context=from_context,
            from_context_and=from_context_and,
            properties=properties,
            boundary=boundary,
            output_format=output_format,
            line=line,
            col=col,
            extra=extra,
        )

    def _parse_intelligently(
        self, style: str | None, reasoning_type: str, line: int, col: int,
    ) -> OperationNode:
        """Parse INTELLIGENTLY verb FROM source WITH: ... END.

        INTELLIGENTLY is a "meta-operation" that lets the user specify an
        arbitrary action verb (e.g. ``INTELLIGENTLY create_person``).  The
        runtime decides which reasoning strategy to apply based on the
        verb and context.  The action identifier doubles as the result_name
        unless an explicit OUTPUT: is given in the WITH block.
        """
        action = self._expect("IDENT").value
        result_name = action

        from_sources: list[str] = []
        from_result = False
        from_context = False
        for_query: str | None = None

        if self._at("FROM"):
            self._advance()
            if self._at("RESULT"):
                self._advance()
                from_result = True
            elif self._at("CONTEXT"):
                self._advance()
                from_context = True
            elif self._at("LBRACKET"):
                from_sources = self._parse_ident_list()
            elif self._at("STRING"):
                from_sources = [self._advance().value]
            else:
                from_sources = [self._expect("IDENT").value]

        properties, boundary, output_format, extra = self._parse_with_section()

        # An explicit OUTPUT: in the WITH block overrides the default
        # result_name (which was the action verb).
        if "OUTPUT" in extra:
            result_name = extra.pop("OUTPUT")

        return self._build_operation(
            result_name=result_name,
            reasoning_type=reasoning_type,
            style=style,
            from_sources=from_sources,
            from_result=from_result,
            from_context=from_context,
            for_query=for_query,
            properties=properties,
            boundary=boundary,
            output_format=output_format,
            line=line,
            col=col,
            extra=extra,
        )

    def _parse_method_call(
        self, style: str | None, line: int, col: int,
    ) -> OperationNode:
        """Parse a dotted method call: ``obj.method(args) WITH: ... END``.

        This handles the OOP-style syntax extension where a dotted name
        like ``incident_commander.think(situation)`` invokes a method on an
        object.  The lexer already delivers the dotted name as a single
        IDENT token, so we just consume it and look for optional
        parenthesised arguments.  The reasoning_type defaults to
        INTELLIGENTLY since method calls are implicitly auto-classified.
        """
        full_name = self._advance().value  # e.g. "incident_commander.think"
        result_name = full_name

        # Optional parenthesised arguments: (arg) or (arg1, arg2)
        from_sources: list[str] = []
        if self._at("LPAREN"):
            self._advance()
            while not self._at("RPAREN", "EOF"):
                tok = self._cur()
                if tok.type == "IDENT" or self._is_identifier_like(tok):
                    from_sources.append(tok.value)
                    self._advance()
                elif tok.type == "STRING":
                    from_sources.append(tok.value)
                    self._advance()
                else:
                    break
                self._match("COMMA")
            self._expect("RPAREN")

        properties, boundary, output_format, extra = self._parse_with_section()

        return self._build_operation(
            result_name=result_name,
            reasoning_type="INTELLIGENTLY",
            style=style,
            from_sources=from_sources,
            properties=properties,
            boundary=boundary,
            output_format=output_format,
            line=line,
            col=col,
            extra=extra,
        )

    # ------------------------------------------------------------------
    # WITH: block parsing
    # ------------------------------------------------------------------

    def parse_with_block(self) -> dict[str, Any]:
        """Parse a WITH: block and return the raw key-value dict.

        Public API for callers that just want the dict.
        """
        props, _, _, _ = self._parse_with_section()
        return props

    def _parse_with_section(
        self,
    ) -> tuple[dict[str, Any], BoundarySpec | None, OutputFormatSchema | None, dict[str, Any]]:
        """Parse ``WITH:`` followed by key-value pairs.

        Returns a 4-tuple ``(properties, boundary, output_format, extra)``:
          - ``properties`` -- flat dict of all recognised key-value pairs
          - ``boundary``   -- structured MUST_INCLUDE / CANNOT_INCLUDE spec
          - ``output_format`` -- parsed OUTPUT_FORMAT schema (if present)
          - ``extra`` -- non-standard keys (OUTPUT, HINTS) that need special
                         handling by the calling operation parser

        The loop terminates when it sees END, THEN, END_WITH, END_CONTEXT,
        or EOF.  This is how the WITH block "knows" it has ended -- there is
        no explicit END_WITH for operation-level WITH blocks; the operation's
        own END delimiter or a THEN keyword serves as the closer.
        """
        self._expect("WITH")
        self._expect("COLON")

        properties: dict[str, Any] = {}
        boundary = BoundarySpec()
        output_format: OutputFormatSchema | None = None
        must_include: list[str] = []
        cannot_include: list[str] = []
        extra: dict[str, Any] = {}

        # Each iteration consumes one key-value property.  The dispatch is
        # on the token type of the key -- known property keys get type-
        # specific validation, while unknown IDENT keys are accepted
        # generically to allow forward-compatible extensions.
        while not self._at("END", "THEN", "END_WITH", "END_CONTEXT", "EOF"):
            tok = self._cur()

            # --- Boundary constraints ---
            # MUST_INCLUDE and CANNOT_INCLUDE define compile-time output
            # contracts: the generated output must/must-not mention the
            # listed concepts.
            if tok.type == "MUST_INCLUDE":
                self._advance()
                self._expect("COLON")
                must_include = self._parse_ident_list()
                properties["MUST_INCLUDE"] = must_include
                continue

            if tok.type == "CANNOT_INCLUDE":
                self._advance()
                self._expect("COLON")
                cannot_include = self._parse_ident_list()
                properties["CANNOT_INCLUDE"] = cannot_include
                continue

            # --- Schema ---
            # OUTPUT_FORMAT triggers recursive schema parsing (see
            # parse_output_format) for structured output specifications.
            if tok.type == "OUTPUT_FORMAT":
                self._advance()
                self._expect("COLON")
                output_format = self.parse_output_format()
                properties["OUTPUT_FORMAT"] = output_format
                continue

            # --- Enum-valued properties ---
            # These properties accept a fixed set of keyword values.  We
            # also accept IDENT to be forward-compatible with new levels.
            if tok.type == "RETRIEVAL_BUDGET":
                self._advance()
                self._expect("COLON")
                val_tok = self._cur()
                if val_tok.type in ("NONE", "MINIMAL", "STANDARD", "FULL",
                                    "IDENT"):
                    self._advance()
                    properties["RETRIEVAL_BUDGET"] = val_tok.value.upper()
                else:
                    raise self._error(
                        f"expected NONE/MINIMAL/STANDARD/FULL after "
                        f"RETRIEVAL_BUDGET:, got {val_tok.type}"
                    )
                continue

            if tok.type == "DEPTH":
                self._advance()
                self._expect("COLON")
                val_tok = self._cur()
                if val_tok.type in ("SHALLOW", "STANDARD", "THOROUGH",
                                    "DEEP", "EXHAUSTIVE", "IDENT"):
                    self._advance()
                    properties["DEPTH"] = val_tok.value.upper()
                else:
                    raise self._error(
                        f"expected depth level after DEPTH:, got {val_tok.type}"
                    )
                continue

            if tok.type == "DIRECTION":
                self._advance()
                self._expect("COLON")
                val_tok = self._cur()
                if val_tok.type in ("FORWARD", "BACKWARD", "IDENT"):
                    self._advance()
                    properties["DIRECTION"] = val_tok.value.upper()
                else:
                    raise self._error(
                        f"expected FORWARD/BACKWARD after DIRECTION:, "
                        f"got {val_tok.type}"
                    )
                continue

            if tok.type == "APPLY_TO":
                self._advance()
                self._expect("COLON")
                val_tok = self._expect("IDENT")
                properties["APPLY_TO"] = val_tok.value
                continue

            # SOURCES accepts either a list or a single identifier (auto-
            # wrapped into a one-element list for consistency).
            if tok.type == "SOURCES":
                self._advance()
                self._expect("COLON")
                if self._at("LBRACKET"):
                    properties["SOURCES"] = self._parse_ident_list()
                else:
                    val_tok = self._cur()
                    if val_tok.type == "IDENT" or self._is_identifier_like(val_tok):
                        self._advance()
                        properties["SOURCES"] = [val_tok.value]
                    else:
                        properties["SOURCES"] = [self._expect("IDENT").value]
                continue

            if tok.type == "DIMENSIONS":
                self._advance()
                self._expect("COLON")
                properties["DIMENSIONS"] = self._parse_ident_list()
                continue

            if tok.type == "EMIT_DIGEST":
                self._advance()
                self._expect("COLON")
                val_tok = self._cur()
                if val_tok.type in ("TRUE", "FALSE"):
                    self._advance()
                    properties["EMIT_DIGEST"] = val_tok.type == "TRUE"
                else:
                    raise self._error(
                        f"expected TRUE/FALSE after EMIT_DIGEST:, "
                        f"got {val_tok.type}"
                    )
                continue

            # WITHOUT_RETRIEVAL is syntactic sugar: it has no colon or
            # value -- it simply sets RETRIEVAL_BUDGET to NONE.
            if tok.type == "WITHOUT_RETRIEVAL":
                self._advance()
                properties["RETRIEVAL_BUDGET"] = "NONE"
                continue

            # --- Extra keys ---
            # OUTPUT and HINTS go into the 'extra' dict because they are
            # handled specially by calling operation parsers (e.g.
            # INTELLIGENTLY uses OUTPUT to override result_name).
            if tok.type == "OUTPUT":
                self._advance()
                self._expect("COLON")
                val_tok = self._expect("IDENT")
                extra["OUTPUT"] = val_tok.value
                continue

            if tok.type == "HINTS":
                self._advance()
                self._expect("COLON")
                hints_val = self.parse_value()
                extra["HINTS"] = hints_val
                properties["HINTS"] = hints_val
                continue

            # --- Generic / future-proof fallback ---
            # Any IDENT not matching a known property key is accepted as a
            # custom key-value pair.  This lets new properties be added to
            # AILang without changing the parser.
            if tok.type == "IDENT":
                key = tok.value
                self._advance()
                self._expect("COLON")
                val = self._parse_property_value()
                properties[key] = val
                continue

            # Unknown token -- stop consuming.  The caller (parse_operation)
            # will expect END or THEN next.
            break

        # Build the BoundarySpec from the accumulated must/cannot lists and
        # the output format schema (if any).
        boundary = BoundarySpec(
            must_include=must_include,
            cannot_include=cannot_include,
            output_format=output_format,
        )

        return properties, boundary, output_format, extra

    def _parse_property_value(self) -> Any:
        """Parse the value side of a key: value pair inside a WITH block."""
        tok = self._cur()
        if tok.type == "LBRACKET":
            return self._parse_ident_list()
        if tok.type == "LBRACE":
            return self.parse_object()
        if tok.type == "STRING":
            self._advance()
            return tok.value
        if tok.type in ("TRUE", "FALSE"):
            self._advance()
            return tok.type == "TRUE"
        if tok.type in ("INTEGER", "FLOAT"):
            self._advance()
            return int(tok.value) if tok.type == "INTEGER" else float(tok.value)
        if tok.type == "IDENT" or self._is_identifier_like(tok):
            self._advance()
            return tok.value
        raise self._error(
            f"expected property value, got {tok.type} ({tok.value!r})"
        )

    # ------------------------------------------------------------------
    # OUTPUT_FORMAT parsing
    # ------------------------------------------------------------------

    def parse_output_format(self) -> OutputFormatSchema:
        """Parse ``{ field: TYPE, field2: TYPE, ... }`` format schema.

        OUTPUT_FORMAT is parsed recursively: each field's type is handled
        by ``_parse_type_expr``, which may call back into this method for
        nested ``OBJECT { ... }`` types.  The result is a tree of
        ``OutputFormatSchema`` nodes mirroring the nesting of the source.

        The root schema node is named "root" by convention; each child
        carries the actual field name from the source.
        """
        self._expect("LBRACE")
        fields: list[OutputFormatSchema] = []

        while not self._at("RBRACE", "EOF"):
            field_name_tok = self._cur()
            if field_name_tok.type == "IDENT" or self._is_identifier_like(field_name_tok):
                field_name = field_name_tok.value
                self._advance()
            else:
                raise self._error(
                    f"expected field name in OUTPUT_FORMAT, got "
                    f"{field_name_tok.type} ({field_name_tok.value!r})"
                )

            self._expect("COLON")
            # Recursion point: _parse_type_expr may call back here for
            # nested OBJECT schemas, or into _parse_list_element_type for
            # LIST OF ... constructions.
            field_schema = self._parse_type_expr(field_name)
            fields.append(field_schema)
            self._match("COMMA")

        self._expect("RBRACE")
        return OutputFormatSchema(name="root", fields=fields)

    def _parse_type_expr(self, field_name: str) -> OutputFormatSchema:
        """Parse a type expression for one OUTPUT_FORMAT field.

        This is the main type-expression dispatcher.  It handles both leaf
        types (TEXT, BOOLEAN, NUMBER, QUALITATIVE) and composite types
        (LIST, OBJECT) that recurse back into the schema parser:

          TEXT                          -> leaf with optional IN constraint
          TEXT IN ["a", "b"]            -> leaf with enum constraint
          BOOLEAN                       -> leaf
          NUMBER                        -> leaf
          QUALITATIVE IN ["high","low"] -> leaf with enum constraint
          LIST OF <type_expr>           -> recurses via _parse_list_element_type
          OBJECT { ... }                -> recurses via parse_output_format
          <unknown_ident>               -> treated as a custom type name (TEXT-like)
        """
        tok = self._cur()

        if tok.type == "TEXT":
            self._advance()
            # TEXT can be followed by an optional IN [...] constraint that
            # limits the value to a fixed set of allowed strings.
            constraint = self._try_parse_in_constraint()
            return OutputFormatSchema(
                name=field_name,
                field_type="TEXT",
                constraint=constraint,
            )

        if tok.type == "BOOLEAN":
            self._advance()
            return OutputFormatSchema(name=field_name, field_type="BOOLEAN")

        if tok.type == "NUMBER":
            self._advance()
            return OutputFormatSchema(name=field_name, field_type="NUMBER")

        if tok.type == "QUALITATIVE":
            self._advance()
            # Like TEXT, QUALITATIVE supports IN [...] enum constraints.
            constraint = self._try_parse_in_constraint()
            return OutputFormatSchema(
                name=field_name,
                field_type="QUALITATIVE",
                constraint=constraint,
            )

        if tok.type == "LIST":
            # LIST OF <element_type> -- delegates to _parse_list_element_type
            # which handles nesting (LIST OF LIST OF OBJECT {...}).
            self._advance()
            self._expect("OF")
            element = self._parse_list_element_type()
            return OutputFormatSchema(
                name=field_name,
                field_type="LIST",
                is_list=True,
                element_schema=element,
            )

        if tok.type == "OBJECT":
            # OBJECT { ... } -- recurse into parse_output_format to read
            # the nested field definitions.
            self._advance()
            inner = self.parse_output_format()
            return OutputFormatSchema(
                name=field_name,
                field_type="OBJECT",
                fields=inner.fields,
            )

        # Fallback: unrecognised type names are accepted as-is (uppercased)
        # so that future AILang versions can introduce new types without
        # needing a parser change.
        if tok.type == "IDENT" or self._is_identifier_like(tok):
            self._advance()
            return OutputFormatSchema(name=field_name, field_type=tok.value.upper())

        raise self._error(
            f"expected type (TEXT, BOOLEAN, NUMBER, LIST, OBJECT, QUALITATIVE) "
            f"in OUTPUT_FORMAT, got {tok.type} ({tok.value!r})"
        )

    def _parse_list_element_type(self) -> OutputFormatSchema:
        """Parse the element type after ``LIST OF``.

        This is structurally similar to ``_parse_type_expr`` but always
        names the result "element" (since list elements are anonymous).
        It also supports self-recursion for nested lists:
        ``LIST OF LIST OF OBJECT { ... }`` recurses here twice before
        hitting the OBJECT branch which recurses into parse_output_format.
        """
        tok = self._cur()

        if tok.type == "TEXT":
            self._advance()
            constraint = self._try_parse_in_constraint()
            return OutputFormatSchema(
                name="element", field_type="TEXT", constraint=constraint,
            )

        if tok.type == "NUMBER":
            self._advance()
            return OutputFormatSchema(name="element", field_type="NUMBER")

        if tok.type == "BOOLEAN":
            self._advance()
            return OutputFormatSchema(name="element", field_type="BOOLEAN")

        if tok.type == "QUALITATIVE":
            self._advance()
            constraint = self._try_parse_in_constraint()
            return OutputFormatSchema(
                name="element", field_type="QUALITATIVE", constraint=constraint,
            )

        if tok.type == "OBJECT":
            self._advance()
            inner = self.parse_output_format()
            return OutputFormatSchema(
                name="element", field_type="OBJECT", fields=inner.fields,
            )

        if tok.type == "LIST":
            # Nested lists: LIST OF LIST OF ...  The inner _parse_list_element_type
            # call handles the next level of nesting, allowing arbitrary depth.
            self._advance()
            self._expect("OF")
            inner_element = self._parse_list_element_type()
            return OutputFormatSchema(
                name="element",
                field_type="LIST",
                is_list=True,
                element_schema=inner_element,
            )

        raise self._error(
            f"expected element type after LIST OF, got {tok.type} ({tok.value!r})"
        )

    def _try_parse_in_constraint(self) -> list[str] | None:
        """Optionally parse ``IN ["a", "b", ...]`` after a type name."""
        if not self._at("IN"):
            return None
        self._advance()  # consume IN
        self._expect("LBRACKET")
        values: list[str] = []
        while not self._at("RBRACKET", "EOF"):
            tok = self._expect("STRING")
            values.append(tok.value)
            self._match("COMMA")
        self._expect("RBRACKET")
        return values

    # ------------------------------------------------------------------
    # Boundary spec (standalone, if needed)
    # ------------------------------------------------------------------

    def parse_boundary(self) -> BoundarySpec:
        """Parse MUST_INCLUDE / CANNOT_INCLUDE as a standalone block.

        Usually these are parsed as part of a WITH: block, but this method
        is provided for direct use.
        """
        must: list[str] = []
        cannot: list[str] = []
        while self._at("MUST_INCLUDE", "CANNOT_INCLUDE"):
            if self._at("MUST_INCLUDE"):
                self._advance()
                self._expect("COLON")
                must = self._parse_ident_list()
            elif self._at("CANNOT_INCLUDE"):
                self._advance()
                self._expect("COLON")
                cannot = self._parse_ident_list()
        return BoundarySpec(must_include=must, cannot_include=cannot)

    # ------------------------------------------------------------------
    # REALITY_CONTEXT definitions
    # ------------------------------------------------------------------

    def parse_reality_context_def(self) -> RealityContext:
        """Parse ``DEFINE REALITY_CONTEXT name: ... END_CONTEXT``.

        A REALITY_CONTEXT is a named configuration block that controls how
        reasoning operations behave when scoped under it (via WITH
        REALITY_CONTEXT).  It contains a worldview description, a reasoning
        profile (which operations to prefer/suppress), assumptions, and a
        value system -- all of which influence prompt generation at runtime.
        """
        self._expect("DEFINE")
        self._expect("REALITY_CONTEXT")
        name = self._expect("IDENT").value
        self._expect("COLON")

        worldview: str | None = None
        profile: ReasoningProfile | None = None
        assumptions: list[str] = []
        value_system: list[str] = []

        # The body is a set of optional, order-independent properties
        # delimited by END_CONTEXT.
        while not self._at("END_CONTEXT", "EOF"):
            tok = self._cur()

            if tok.type == "WORLDVIEW":
                self._advance()
                self._expect("COLON")
                worldview = self._expect("STRING").value
                continue

            if tok.type == "REASONING_PROFILE":
                self._advance()
                self._expect("COLON")
                # Nested block: REASONING_PROFILE: ... END_PROFILE
                profile = self.parse_reasoning_profile()
                continue

            if tok.type == "ASSUMPTIONS":
                self._advance()
                self._expect("COLON")
                assumptions = self._parse_ident_list()
                continue

            if tok.type == "VALUE_SYSTEM":
                self._advance()
                self._expect("COLON")
                value_system = self._parse_ident_list()
                continue

            raise self._error(
                f"unexpected token in REALITY_CONTEXT: {tok.type} ({tok.value!r})"
            )

        self._expect("END_CONTEXT")

        return RealityContext(
            name=name,
            worldview=worldview,
            reasoning_profile=profile,
            assumptions=assumptions,
            value_system=value_system,
        )

    def parse_reasoning_profile(self) -> ReasoningProfile:
        """Parse a REASONING_PROFILE block (inside a REALITY_CONTEXT).

        The reasoning profile tells the runtime which operation types to
        prefer (PRIMARY/SECONDARY), which to limit (CONSTRAIN: op TO "limit"),
        and which to disable entirely (SUPPRESS).  This is how reality
        contexts shape the reasoning strategy without changing the
        operations themselves.
        """
        primary: str | None = None
        secondary: str | None = None
        constrain: dict[str, str] = {}
        suppress: list[str] = []

        while not self._at("END_PROFILE", "EOF"):
            tok = self._cur()

            if tok.type == "PRIMARY":
                self._advance()
                self._expect("COLON")
                # The value is a reasoning operation name (DEDUCE, TRACE, etc.)
                # which may be tokenised as a keyword or identifier.
                val_tok = self._cur()
                if val_tok.type in _OPERATION_TYPES or self._is_identifier_like(val_tok):
                    primary = val_tok.value.upper()
                    self._advance()
                else:
                    raise self._error(
                        f"expected reasoning type after PRIMARY:, got {val_tok.type}"
                    )
                continue

            if tok.type == "SECONDARY":
                self._advance()
                self._expect("COLON")
                val_tok = self._cur()
                if val_tok.type in _OPERATION_TYPES or self._is_identifier_like(val_tok):
                    secondary = val_tok.value.upper()
                    self._advance()
                else:
                    raise self._error(
                        f"expected reasoning type after SECONDARY:, got {val_tok.type}"
                    )
                continue

            if tok.type == "CONSTRAIN":
                self._advance()
                self._expect("COLON")
                # CONSTRAIN has a two-part value: operation name TO "limit string"
                op_tok = self._cur()
                if op_tok.type in _OPERATION_TYPES or self._is_identifier_like(op_tok):
                    op_name = op_tok.value.upper()
                    self._advance()
                else:
                    raise self._error(
                        f"expected reasoning type after CONSTRAIN:, got {op_tok.type}"
                    )
                self._expect("TO")
                limit = self._expect("STRING").value
                constrain[op_name] = limit
                continue

            if tok.type == "SUPPRESS":
                self._advance()
                self._expect("COLON")
                suppress = self._parse_ident_list()
                continue

            raise self._error(
                f"unexpected token in REASONING_PROFILE: {tok.type} ({tok.value!r})"
            )

        self._expect("END_PROFILE")
        return ReasoningProfile(
            primary=primary,
            secondary=secondary,
            constrain=constrain,
            suppress=suppress,
        )

    # ------------------------------------------------------------------
    # WITH REALITY_CONTEXT / BEGIN REASONING_CONTEXT
    # ------------------------------------------------------------------

    def parse_with_context_block(self) -> WithContextBlock:
        """Parse ``WITH REALITY_CONTEXT name: ... END_WITH``.

        This scopes a sequence of operations (and possibly nested
        REASONING_CONTEXTs) under a named reality context.  All operations
        inside the block inherit the context's worldview, reasoning profile,
        and constraints.
        """
        self._expect("WITH")
        self._expect("REALITY_CONTEXT")
        name = self._expect("IDENT").value
        self._expect("COLON")

        body: list[Any] = []
        while not self._at("END_WITH", "EOF"):
            stmt = self._parse_context_body_statement()
            if stmt is not None:
                body.append(stmt)

        self._expect("END_WITH")
        return WithContextBlock(context_name=name, operations=body)

    def parse_execute_code(self) -> CodeBlock:
        """Parse ``EXECUTE_CODE LANG: ... END_EXECUTE``.

        The lexer's pre-extraction step has already replaced the raw body
        text with a single CODE_BODY token, so by the time we reach the
        parser the grammar is regular::

            EXECUTE_CODE  IDENT(language)  COLON  CODE_BODY  END_EXECUTE

        This keeps the parser's grammar uniform: it never has to think
        about indentation-sensitive Python syntax inside an AILang program.
        The CodeBlock node stores the language and raw body for the runtime
        executor to interpret.
        """
        tok = self._expect("EXECUTE_CODE")
        # Language is parsed as a plain IDENT (e.g. "python").  The lexer
        # would never reclassify "python" as a keyword because it is not in
        # the keyword maps.
        lang_tok = self._expect("IDENT")
        self._expect("COLON")
        # The CODE_BODY token holds the entire raw block body, with original
        # indentation preserved.  This is the substrate the runtime executes.
        body_tok = self._expect("CODE_BODY")
        self._expect("END_EXECUTE")
        return CodeBlock(
            language=lang_tok.value,
            body=body_tok.value,
            line=tok.line,
            col=tok.col,
        )

    def parse_reasoning_context_block(self) -> ReasoningContextBlock:
        """Parse ``BEGIN REASONING_CONTEXT name: ... END REASONING_CONTEXT``.

        REASONING_CONTEXTs group related operations under a named scope.
        They can nest (a reasoning context can contain another BEGIN
        REASONING_CONTEXT block), and they can appear at top level or
        inside a WITH REALITY_CONTEXT block.

        The closing delimiter is the two-token sequence ``END REASONING_CONTEXT``.
        This requires 1-token lookahead: when we see END, we peek ahead to
        check whether REASONING_CONTEXT follows.  If it does, this block is
        done; if not, the END belongs to an inner operation and we let the
        inner parser consume it.
        """
        self._expect("BEGIN")
        self._expect("REASONING_CONTEXT")
        name = self._expect("IDENT").value
        self._expect("COLON")

        body: list[Any] = []
        while not self._at("EOF"):
            # Two-token lookahead to detect the closing delimiter:
            # END followed by REASONING_CONTEXT means this block is done.
            if self._at("END"):
                if self._peek(1).type == "REASONING_CONTEXT":
                    self._advance()  # consume END
                    self._advance()  # consume REASONING_CONTEXT
                    break
                # An END without REASONING_CONTEXT following it belongs to
                # an inner operation (e.g. closing a DEDUCE ... END inside
                # this block).  Fall through so the inner parser handles it.
            stmt = self._parse_context_body_statement()
            if stmt is not None:
                body.append(stmt)

        return ReasoningContextBlock(name=name, operations=body)

    def _parse_context_body_statement(self) -> Any:
        """Parse a statement inside a context block.

        This is a restricted version of _parse_statement: it accepts SET
        declarations, nested REASONING_CONTEXT blocks, reasoning operations,
        and method calls -- but NOT DEFINE or WITH REALITY_CONTEXT, since
        those are only valid at the top level.
        """
        tok = self._cur()

        if tok.type == "SET":
            return self.parse_set()

        if tok.type == "BEGIN":
            return self.parse_reasoning_context_block()

        if tok.type == "EXECUTE_CODE":
            return self.parse_execute_code()

        if tok.type in _OPERATION_TYPES or tok.type in _STYLE_MODIFIERS:
            return self.parse_operation()

        if tok.type == "IDENT" and "." in tok.value:
            return self.parse_operation()

        raise self._error(
            f"unexpected token in context body: {tok.type} ({tok.value!r})"
        )

    # ------------------------------------------------------------------
    # Identifier list helper
    # ------------------------------------------------------------------

    def _parse_ident_list(self) -> list[str]:
        """Parse ``[ident1, ident2, ...]`` returning a list of strings.

        Items may be bare identifiers, keywords-as-identifiers, or dotted names.
        Also handles ``CONTEXT`` and ``RESULT`` as list items.
        """
        self._expect("LBRACKET")
        items: list[str] = []
        while not self._at("RBRACKET", "EOF"):
            tok = self._cur()
            if tok.type == "IDENT" or self._is_identifier_like(tok):
                items.append(tok.value)
                self._advance()
            elif tok.type == "STRING":
                items.append(tok.value)
                self._advance()
            else:
                raise self._error(
                    f"expected identifier in list, got {tok.type} ({tok.value!r})"
                )
            self._match("COMMA")
        self._expect("RBRACKET")
        return items

    # ------------------------------------------------------------------
    # OperationNode builder
    # ------------------------------------------------------------------

    def _build_operation(
        self,
        result_name: str,
        reasoning_type: str,
        style: str | None,
        from_sources: list[str] | None = None,
        from_result: bool = False,
        from_context: bool = False,
        from_context_and: str | None = None,
        against: str | None = None,
        on_topic: str | None = None,
        for_query: str | None = None,
        into_target: str | None = None,
        properties: dict[str, Any] | None = None,
        boundary: BoundarySpec | None = None,
        output_format: OutputFormatSchema | None = None,
        line: int = 0,
        col: int = 0,
        extra: dict[str, Any] | None = None,
    ) -> OperationNode:
        """Construct an OperationNode from parsed components.

        This centralised builder is called by every verb-specific parser.
        It converts raw string values (from the token stream) into their
        typed enum equivalents (ReasoningType, StyleModifier, DepthLevel,
        etc.) and extracts well-known properties from the flat dict into
        dedicated AST fields.  Unknown enum values fall back to sensible
        defaults rather than raising -- this keeps the parser lenient for
        forward compatibility.
        """
        props = properties or {}
        from_sources = from_sources or []

        # Convert the string-based reasoning type to the ReasoningType enum.
        # Unknown types default to INTELLIGENTLY (the "let the runtime decide"
        # fallback).
        try:
            rt = ReasoningType(reasoning_type)
        except ValueError:
            rt = ReasoningType.INTELLIGENTLY

        # Convert the optional style modifier string to its enum.
        style_enum: StyleModifier | None = None
        if style:
            try:
                style_enum = StyleModifier(style)
            except ValueError:
                pass  # unknown style modifiers are silently ignored

        # Promote well-known WITH-block properties from the flat dict into
        # typed OperationNode fields.  This gives downstream consumers
        # direct attribute access (e.g. node.depth) instead of dict lookups.
        from aiex.models.ast_nodes import (
            DepthLevel,
            Direction,
            RetrievalBudget,
        )

        depth_str = props.get("DEPTH", "STANDARD")
        try:
            depth = DepthLevel(depth_str)
        except ValueError:
            depth = DepthLevel.STANDARD

        budget_str = props.get("RETRIEVAL_BUDGET", "STANDARD")
        try:
            budget = RetrievalBudget(budget_str)
        except ValueError:
            budget = RetrievalBudget.STANDARD

        direction_str = props.get("DIRECTION")
        direction = None
        if direction_str:
            try:
                direction = Direction(direction_str)
            except ValueError:
                pass

        apply_to = props.get("APPLY_TO")
        sources = props.get("SOURCES", [])
        dimensions = props.get("DIMENSIONS", [])
        emit_digest = props.get("EMIT_DIGEST")

        return OperationNode(
            result_name=result_name,
            reasoning_type=rt,
            style=style_enum,
            depth=depth,
            retrieval_budget=budget,
            direction=direction,
            from_sources=from_sources,
            from_result=from_result,
            from_context=from_context,
            from_context_and=from_context_and,
            apply_to=apply_to,
            sources=sources,
            dimensions=dimensions,
            emit_digest=emit_digest,
            boundary=boundary,
            against=against,
            on_topic=on_topic,
            for_query=for_query,
            into_target=into_target,
            output_format=output_format,
            properties=props,
            line=line,
            col=col,
        )


# ------------------------------------------------------------------
# Convenience function
# ------------------------------------------------------------------

def parse_ailang(source: str) -> Program:
    """Lex and parse an AILang source string into a :class:`Program` AST.

    Parameters
    ----------
    source : str
        Complete AILang source code.

    Returns
    -------
    Program
        The root AST node.

    Raises
    ------
    ParseError
        On syntax errors.
    """
    from aiex.parser.lexer import Lexer

    tokens = Lexer(source).tokenize()
    return Parser(tokens).parse()
