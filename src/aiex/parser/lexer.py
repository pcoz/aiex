"""Token-based lexer for AILang v0.8.2 source files.

Produces a stream of ``Token(type, value, line, col)`` objects from raw
source text.  Keywords are matched case-insensitively; strings and
identifiers preserve their original casing.

Strategy
--------
The lexer uses a single compiled master regex built from an ordered list of
patterns.  Each pattern is a named group; the regex engine tries them
left-to-right, so longer/more-specific patterns (comments, strings, floats)
must appear before shorter ones (integers, bare words, dots).

Keyword recognition is a two-step process:
  1. The master regex matches any ``[A-Za-z_][A-Za-z0-9_]*`` run (plus
     optional dotted suffixes) as a generic ``WORD``.
  2. The ``_scan`` method then checks the lowercase form against two
     dictionaries -- ``_TWO_WORD_KEYWORDS`` (underscore-joined compound
     keywords like ``REALITY_CONTEXT``) and ``_SINGLE_KEYWORDS`` -- to
     reclassify the WORD into the correct keyword token type.  Anything
     not found in either dictionary becomes an ``IDENT``.

Dotted identifiers (e.g. ``merger_data.key_risks``) are captured as a
single WORD token by the regex's ``(?:\\.[A-Za-z_]...)`` suffix, and
always emitted as ``IDENT`` -- they are never reclassified as keywords.
This keeps downstream parsing simple: the parser never needs to
reassemble ``IDENT DOT IDENT`` sequences.

Usage::

    from aiex.parser.lexer import Lexer
    tokens = Lexer(source_text).tokenize()
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Token
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Token:
    """A single lexical token."""
    type: str
    value: str
    line: int
    col: int

    def __repr__(self) -> str:  # pragma: no cover
        return f"Token({self.type!r}, {self.value!r}, {self.line}, {self.col})"


# ---------------------------------------------------------------------------
# Token types
# ---------------------------------------------------------------------------

# The full set of valid token type strings.  This is used as a validation
# set -- when the scanner produces a punctuation token whose named regex
# group happens to match one of these, it is accepted directly.  It also
# serves as living documentation of every token the grammar can contain.
# fmt: off
TOKEN_TYPES: set[str] = {
    # Punctuation / structure
    "HASH", "COLON", "COMMA", "LBRACKET", "RBRACKET", "LBRACE", "RBRACE",
    "DOT", "LPAREN", "RPAREN", "NEWLINE", "COMMENT", "EOF",

    # Literals
    "STRING", "IDENT", "INTEGER", "FLOAT",

    # Language header
    "VERSION",

    # Core keywords
    "SET", "TO", "FROM", "WITH", "END",
    "DEFINE", "BEGIN",
    "COMPARE", "AGAINST", "THEN",

    # Reasoning operations
    "DEDUCE", "TRACE", "WEIGH", "RETRIEVE", "SYNTHESIZE",

    # Style modifiers
    "INTELLIGENTLY", "CREATIVELY", "ADAPTIVELY", "CONTEXTUALLY",

    # Context blocks
    "REALITY_CONTEXT", "REASONING_CONTEXT",
    "REASONING_PROFILE", "END_PROFILE", "END_CONTEXT", "END_WITH",
    "WITH_CONTEXT",

    # Property keys
    "MUST_INCLUDE", "CANNOT_INCLUDE", "OUTPUT_FORMAT",
    "RETRIEVAL_BUDGET", "DEPTH", "DIRECTION", "APPLY_TO",
    "SOURCES", "DIMENSIONS", "EMIT_DIGEST",
    "WITHOUT_RETRIEVAL",
    "ON", "FOR", "INTO", "IN", "OF", "AND", "RESULT",
    "OUTPUT",

    # Reality-context internals
    "WORLDVIEW", "PRIMARY", "SECONDARY", "CONSTRAIN", "SUPPRESS",
    "ASSUMPTIONS", "VALUE_SYSTEM",

    # Type names
    "OBJECT", "TEXT", "BOOLEAN", "NUMBER", "LIST", "QUALITATIVE",

    # Boolean literals
    "TRUE", "FALSE",

    # Depth-level values (also usable as identifiers)
    "NONE", "MINIMAL", "STANDARD", "FULL",
    "SHALLOW", "THOROUGH", "DEEP", "EXHAUSTIVE",

    # Direction values
    "FORWARD", "BACKWARD",

    # HINTS keyword for INTELLIGENTLY create_person
    "HINTS",

    # CONTEXT keyword (standalone)
    "CONTEXT",

    # Code execution blocks
    "EXECUTE_CODE", "END_EXECUTE", "CODE_BODY",
}
# fmt: on


# ---------------------------------------------------------------------------
# Keyword map  (lower-case lookup -> token type)
# ---------------------------------------------------------------------------

# AILang has several compound keywords written with underscores
# (e.g. REALITY_CONTEXT, MUST_INCLUDE).  Because the WORD regex captures
# underscored runs as a single match, these are detected first.  If we
# checked single keywords first, "reality" alone would match and the
# trailing "_context" would become a separate (invalid) token.
_TWO_WORD_KEYWORDS: dict[str, str] = {
    "reality_context": "REALITY_CONTEXT",
    "reasoning_context": "REASONING_CONTEXT",
    "reasoning_profile": "REASONING_PROFILE",
    "end_profile": "END_PROFILE",
    "end_context": "END_CONTEXT",
    "end_with": "END_WITH",
    "must_include": "MUST_INCLUDE",
    "cannot_include": "CANNOT_INCLUDE",
    "output_format": "OUTPUT_FORMAT",
    "retrieval_budget": "RETRIEVAL_BUDGET",
    "apply_to": "APPLY_TO",
    "emit_digest": "EMIT_DIGEST",
    "value_system": "VALUE_SYSTEM",
    "without_retrieval": "WITHOUT_RETRIEVAL",
    "execute_code": "EXECUTE_CODE",
    "end_execute": "END_EXECUTE",
}

# Single-word keywords.  The lookup is always lowercase so that AILang
# source is case-insensitive for keywords (e.g. "Set", "SET", "set" all
# produce a SET token).
_SINGLE_KEYWORDS: dict[str, str] = {
    "set": "SET",
    "to": "TO",
    "from": "FROM",
    "with": "WITH",
    "end": "END",
    "define": "DEFINE",
    "begin": "BEGIN",
    "compare": "COMPARE",
    "against": "AGAINST",
    "then": "THEN",
    "deduce": "DEDUCE",
    "trace": "TRACE",
    "weigh": "WEIGH",
    "retrieve": "RETRIEVE",
    "synthesize": "SYNTHESIZE",
    "intelligently": "INTELLIGENTLY",
    "creatively": "CREATIVELY",
    "adaptively": "ADAPTIVELY",
    "contextually": "CONTEXTUALLY",
    "on": "ON",
    "for": "FOR",
    "into": "INTO",
    "in": "IN",
    "of": "OF",
    "and": "AND",
    "result": "RESULT",
    "output": "OUTPUT",
    "worldview": "WORLDVIEW",
    "primary": "PRIMARY",
    "secondary": "SECONDARY",
    "constrain": "CONSTRAIN",
    "suppress": "SUPPRESS",
    "assumptions": "ASSUMPTIONS",
    "depth": "DEPTH",
    "direction": "DIRECTION",
    "sources": "SOURCES",
    "dimensions": "DIMENSIONS",
    "object": "OBJECT",
    "text": "TEXT",
    "boolean": "BOOLEAN",
    "number": "NUMBER",
    "list": "LIST",
    "qualitative": "QUALITATIVE",
    "true": "TRUE",
    "false": "FALSE",
    "none": "NONE",
    "minimal": "MINIMAL",
    "standard": "STANDARD",
    "full": "FULL",
    "shallow": "SHALLOW",
    "thorough": "THOROUGH",
    "deep": "DEEP",
    "exhaustive": "EXHAUSTIVE",
    "forward": "FORWARD",
    "backward": "BACKWARD",
    "context": "CONTEXT",
    "hints": "HINTS",
}


# ---------------------------------------------------------------------------
# Lexer
# ---------------------------------------------------------------------------

class LexError(Exception):
    """Raised on unrecognised input."""

    def __init__(self, message: str, line: int = 0, col: int = 0) -> None:
        self.line = line
        self.col = col
        super().__init__(f"line {line}, col {col}: {message}")


class Lexer:
    """Regex-based scanner for AILang source text.

    Parameters
    ----------
    source : str
        Complete AILang source code.
    """

    def __init__(self, source: str) -> None:
        # EXECUTE_CODE blocks contain raw text in another language (Python,
        # etc.) that must NOT be tokenised as AILang.  We pre-extract every
        # block, replace it with a placeholder string token, and restore the
        # body in tokenize() after the main scan.  This keeps the master
        # regex simple and avoids stateful lexing.
        self._code_bodies: list[tuple[str, str]] = []
        self._source = self._extract_code_blocks(source)
        self._tokens: list[Token] = []

    def _extract_code_blocks(self, source: str) -> str:
        """Find EXECUTE_CODE...END_EXECUTE blocks; replace bodies with placeholders.

        Each block's (language, body) is stashed in ``self._code_bodies`` and
        the source is rewritten so that the body becomes a single string
        literal containing ``__AIEX_CODEBODY_<index>__``.  Line counts are
        preserved by padding the substitute with newlines so subsequent token
        line numbers stay accurate.
        """
        pattern = re.compile(
            r"^([ \t]*)(EXECUTE_CODE)[ \t]+(\w+)[ \t]*:[ \t]*\n(.*?)^[ \t]*END_EXECUTE",
            re.MULTILINE | re.DOTALL | re.IGNORECASE,
        )

        def _replace(m: re.Match) -> str:
            indent = m.group(1)
            lang = m.group(3)
            body = m.group(4).rstrip("\n")
            idx = len(self._code_bodies)
            self._code_bodies.append((lang, body))
            # Match the original block's newline count so subsequent line
            # numbers are unaffected.
            original_newlines = m.group(0).count("\n")
            # Substitute base form: `EXECUTE_CODE LANG: "PLACEHOLDER"\nEND_EXECUTE`
            # has 1 newline; pad to match original.
            pad = "\n" * max(0, original_newlines - 1)
            return (
                f'{indent}EXECUTE_CODE {lang}: "__AIEX_CODEBODY_{idx}__"'
                f"{pad}\nEND_EXECUTE"
            )

        return pattern.sub(_replace, source)

    # ---- public API -------------------------------------------------------

    def tokenize(self) -> list[Token]:
        """Scan the entire source and return a list of tokens.

        Trailing ``EOF`` is always appended.  ``NEWLINE`` tokens are
        emitted for every non-blank line boundary (blank lines are
        collapsed).  ``COMMENT`` tokens are emitted but can be filtered
        by the parser.
        """
        self._tokens = []
        self._scan()
        # Ensure trailing EOF
        if not self._tokens or self._tokens[-1].type != "EOF":
            last_line = self._source.count("\n") + 1
            self._tokens.append(Token("EOF", "", last_line, 1))
        # Restore CODE_BODY tokens: any STRING with the placeholder shape
        # is swapped out for a CODE_BODY token carrying the original body.
        prefix = "__AIEX_CODEBODY_"
        for i, tok in enumerate(self._tokens):
            if tok.type == "STRING" and tok.value.startswith(prefix) and tok.value.endswith("__"):
                idx = int(tok.value[len(prefix):-2])
                _, body = self._code_bodies[idx]
                self._tokens[i] = Token("CODE_BODY", body, tok.line, tok.col)
        return self._tokens

    # ---- internals --------------------------------------------------------

    # The master regex is an alternation of named groups, tried left to right.
    # ORDER IS CRITICAL -- the patterns below are arranged so that:
    #   - COMMENT matches before anything else on a '#' line
    #   - STRING captures the full quoted span (including escaped chars)
    #   - FLOAT matches before INTEGER so "3.14" isn't split into "3" + ".14"
    #   - WORD matches alphanumeric runs (including dotted suffixes like
    #     "obj.method") before individual DOT punctuation
    #   - Punctuation tokens come next
    #   - NEWLINE and whitespace are handled last
    _PATTERNS: list[tuple[str, str]] = [
        # Line comments: everything from '#' to end-of-line.  The version
        # header (#ailang v0.8.2) is a special case handled in _scan().
        ("COMMENT", r"#[^\n]*"),
        # Double-quoted strings.  The inner alternation (?:[^"\\]|\\.)
        # accepts any non-quote/non-backslash char OR an escaped pair,
        # giving us basic escape support (\", \\) without a full escape
        # engine.
        ("STRING", r'"(?:[^"\\]|\\.)*"'),
        # Floats must precede integers so "3.14" is one FLOAT, not INT+DOT+INT.
        ("FLOAT", r"-?\d+\.\d+"),
        ("INTEGER", r"-?\d+"),
        # Words: standard identifier chars, with an optional dotted suffix
        # so "merger_data.key_risks" stays one token.  This avoids the
        # parser having to reassemble IDENT DOT IDENT chains.  The regex
        # allows multiple dot-separated segments (a.b.c).
        ("WORD", r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*"),
        # Punctuation -- single-character tokens.
        ("COLON", r":"),
        ("COMMA", r","),
        ("LBRACKET", r"\["),
        ("RBRACKET", r"\]"),
        ("LBRACE", r"\{"),
        ("RBRACE", r"\}"),
        ("LPAREN", r"\("),
        ("RPAREN", r"\)"),
        # Standalone dot (only reached if not already consumed by WORD's
        # dotted-identifier suffix).
        ("DOT", r"\."),
        ("NEWLINE", r"\n"),
        # Horizontal whitespace is silently skipped.
        ("SKIP", r"[ \t\r]+"),
    ]

    # Combine all patterns into one compiled regex.  Named groups let us
    # identify which pattern won via mo.lastgroup.
    _MASTER_RE = re.compile(
        "|".join(f"(?P<{name}>{pattern})" for name, pattern in _PATTERNS)
    )

    def _scan(self) -> None:
        """Walk through the source using the master regex and emit tokens.

        Each regex match is classified by its named group (``mo.lastgroup``).
        WORDs go through keyword reclassification; COMMENTs are inspected for
        the special ``#ailang vX.Y`` version header; everything else is
        emitted directly or skipped (whitespace).

        Line and column tracking is maintained manually so that every token
        carries accurate source-location metadata for error reporting.
        """
        line = 1
        line_start = 0  # byte offset of the first char in the current line

        for mo in self._MASTER_RE.finditer(self._source):
            kind = mo.lastgroup
            value = mo.group()
            start = mo.start()
            # Column is 1-based: distance from start-of-line to match start.
            col = start - line_start + 1

            if kind == "SKIP":
                continue

            if kind == "NEWLINE":
                self._tokens.append(Token("NEWLINE", "\\n", line, col))
                line += 1
                line_start = mo.end()
                continue

            if kind == "COMMENT":
                # The version header looks like a comment (#ailang v0.8.2)
                # but carries semantic meaning -- it sets the language version
                # for the entire file.  We detect it here with a sub-regex
                # so the parser receives a VERSION token instead of COMMENT.
                ver_match = re.match(
                    r"#\s*ailang\s+v(\d+\.\d+(?:\.\d+)?)", value, re.IGNORECASE
                )
                if ver_match:
                    self._tokens.append(
                        Token("VERSION", ver_match.group(1), line, col)
                    )
                else:
                    self._tokens.append(Token("COMMENT", value, line, col))
                continue

            if kind == "STRING":
                # Strip the surrounding double-quotes and unescape the two
                # sequences we support: \" -> " and \\ -> \.  More exotic
                # escapes (e.g. \n, \t) are passed through literally since
                # AILang strings are typically natural-language prompts, not
                # code.
                inner = value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
                self._tokens.append(Token("STRING", inner, line, col))
                continue

            if kind == "FLOAT":
                self._tokens.append(Token("FLOAT", value, line, col))
                continue

            if kind == "INTEGER":
                self._tokens.append(Token("INTEGER", value, line, col))
                continue

            if kind == "WORD":
                # --- Keyword reclassification ---
                # Dotted identifiers (e.g. "merger_data.key_risks") are never
                # keywords -- they always become IDENT so the parser can
                # treat them as qualified variable/method references.
                if "." in value:
                    self._tokens.append(Token("IDENT", value, line, col))
                    continue
                # Case-insensitive keyword lookup: try compound keywords
                # first (they contain underscores that would also match
                # single-keyword prefixes), then single keywords, then
                # fall back to IDENT.
                low = value.lower()
                if low in _TWO_WORD_KEYWORDS:
                    self._tokens.append(
                        Token(_TWO_WORD_KEYWORDS[low], value, line, col)
                    )
                elif low in _SINGLE_KEYWORDS:
                    self._tokens.append(
                        Token(_SINGLE_KEYWORDS[low], value, line, col)
                    )
                else:
                    self._tokens.append(Token("IDENT", value, line, col))
                continue

            # Punctuation tokens: the named group (COLON, LBRACE, etc.)
            # already matches the desired token type string, so we emit it
            # directly.
            if kind in TOKEN_TYPES:
                self._tokens.append(Token(kind, value, line, col))
                continue

            # If we get here, the regex matched something we don't handle --
            # this should be unreachable for well-formed source, but we raise
            # a clear error rather than silently dropping characters.
            raise LexError(f"unexpected character {value!r}", line, col)
