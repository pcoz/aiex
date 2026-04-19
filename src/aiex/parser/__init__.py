"""AILang v0.8.2 parser -- lexer and recursive-descent parser.

This package is the front door of the AIEX parsing pipeline.  It exposes a
single public function, :func:`parse_ailang`, which takes raw AILang source
text and returns a fully typed :class:`~aiex.models.ast_nodes.Program` AST.

Under the hood the pipeline is two phases:

1. **Lexing** (``lexer.py``) -- convert the source string into a flat token
   stream.  Keywords are recognised case-insensitively; dotted identifiers
   (e.g. ``merger_data.key_risks``) are kept as single tokens so downstream
   parsing never has to reassemble them.

2. **Parsing** (``parser.py``) -- a hand-written recursive-descent parser
   consumes the token stream and builds immutable Pydantic AST nodes.  The
   parser handles AILang's mix of top-level statements (SET, DEFINE,
   operations), context blocks (REALITY_CONTEXT, REASONING_CONTEXT), THEN
   chains, and recursive OUTPUT_FORMAT schemas.

Quick usage::

    from aiex.parser import parse_ailang

    program = parse_ailang(source_text)
"""

# Re-export the convenience function so callers only need this package.
from aiex.parser.parser import parse_ailang

__all__ = ["parse_ailang"]
