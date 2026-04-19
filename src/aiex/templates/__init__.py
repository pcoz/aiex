"""AIEX prompt template loader and composer.

Each reasoning type in AILang has a corresponding ``.txt`` template file
in this directory (e.g. ``deduce.txt``, ``synthesis_merge.txt``).  The
template name is stored in ``RoutingDecision.template_name`` and looked up
at dispatch time.

Templates use a simple ``{PLACEHOLDER}`` substitution strategy:
    1. The runtime collects all dynamic values (retrieval modifier, depth
       suffix, style prefix, boundary instructions, input data, output
       schema) into a dict.
    2. ``TemplateLoader.compose()`` loads the template from disk (with
       caching), replaces every ``{PLACEHOLDER}`` with the corresponding
       dict value, and collapses any blank lines left by empty values.
    3. The result is a fully composed system prompt ready to send to the LLM.

Boundary instructions (MUST-INCLUDE, CANNOT-INCLUDE, OUTPUT-FORMAT) are
formatted as explicit natural-language directives via
``get_boundary_instructions()``.  This formatting is deliberate: LLMs
follow clear, imperative instructions more reliably than structured
constraint schemas, so the contract requirements are translated into
"REQUIRED CONTENT" / "PROHIBITED CONTENT" blocks with bullet lists.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional


# Directory containing the .txt template files (same directory as this module).
# Templates are co-located with this __init__.py so they are included in
# the package distribution (referenced in pyproject.toml's package-data).
_TEMPLATES_DIR = Path(__file__).resolve().parent


class TemplateLoader:
    """Loads, caches, and composes AIEX prompt templates.

    Usage::

        loader = TemplateLoader()
        prompt = loader.compose(
            "deduce",
            RETRIEVAL_MODIFIER="Use ONLY the provided data.",
            DEPTH_SUFFIX="",
            STYLE_PREFIX="",
            BOUNDARY_INSTRUCTIONS=loader.get_boundary_instructions(
                must_include=["logical_chain", "final_determination"],
                cannot_include=["speculation_beyond_evidence"],
                output_schema='{"result": "..."}',
            ),
            INPUTS="Premise 1: ..., Premise 2: ...",
            OUTPUT_SCHEMA='{"conclusion": "string", "confidence": "string"}',
        )
    """

    def __init__(self, templates_dir: Optional[Path] = None) -> None:
        self._dir = Path(templates_dir) if templates_dir is not None else _TEMPLATES_DIR
        # In-memory cache: template name -> raw file contents.  Avoids
        # repeated disk I/O when the same template is used across multiple
        # transactions in a single program run.
        self._cache: Dict[str, str] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compose(self, template_name: str, **kwargs: str) -> str:
        """Load a template and substitute all ``{PLACEHOLDER}`` tokens.

        Parameters
        ----------
        template_name:
            Base name of the template file (without the ``.txt`` extension).
            For example ``"deduce"`` loads ``deduce.txt``.
        **kwargs:
            Mapping of placeholder names to their replacement values.
            Placeholder names should be given **without** the surrounding
            braces (e.g. ``RETRIEVAL_MODIFIER="..."``).

        Returns
        -------
        str
            The fully composed system prompt, ready to send to the LLM.

        Raises
        ------
        FileNotFoundError
            If the template file does not exist.
        KeyError
            If the template contains a ``{PLACEHOLDER}`` for which no
            value was supplied.
        """
        raw = self._load(template_name)
        return self._substitute(raw, kwargs)

    @staticmethod
    def get_boundary_instructions(
        must_include: Optional[List[str]] = None,
        cannot_include: Optional[List[str]] = None,
        output_schema: Optional[str] = None,
    ) -> str:
        """Format boundary constraints as clear LLM instructions.

        Translates the CONTRACT DIVISION's MUST-INCLUDE, CANNOT-INCLUDE,
        and OUTPUT-FORMAT sections into natural-language instructions that
        the LLM can follow.

        The output is designed for injection into the ``{BOUNDARY_INSTRUCTIONS}``
        placeholder in any template.  It uses imperative phrasing ("your
        response MUST...") because LLMs are more compliant with direct
        instructions than with abstract constraint schemas.

        When all three parameters are None, returns an empty string, which
        the template substitution collapses cleanly (no orphaned blank lines).

        Parameters
        ----------
        must_include:
            Concepts that MUST appear in the response.  Derived from the
            contract's ``MUST-INCLUDE SECTION``.
        cannot_include:
            Concepts that must NOT appear in the response.  Derived from
            the contract's ``CANNOT-INCLUDE SECTION``.
        output_schema:
            JSON schema string the response must conform to.  Derived
            from the contract's ``OUTPUT-FORMAT SECTION``.

        Returns
        -------
        str
            A block of text suitable for injection into the
            ``{BOUNDARY_INSTRUCTIONS}`` placeholder.
        """
        parts: List[str] = []

        # Each section is formatted as a labelled block with a bullet list.
        # The labels ("REQUIRED CONTENT", "PROHIBITED CONTENT") are chosen
        # to be unambiguous to the LLM -- they read like test assertions.
        if must_include:
            items = "\n".join(f"  - {concept}" for concept in must_include)
            parts.append(
                "REQUIRED CONTENT -- your response MUST address each of "
                "the following. Omitting any is a critical failure:\n" + items
            )

        if cannot_include:
            items = "\n".join(f"  - {concept}" for concept in cannot_include)
            parts.append(
                "PROHIBITED CONTENT -- your response must NOT contain any "
                "of the following. Including any is a critical failure:\n" + items
            )

        if output_schema:
            parts.append(
                "OUTPUT FORMAT -- your response must be valid JSON that "
                "conforms exactly to the schema provided at the end of "
                "this prompt. Do not add extra keys or omit required ones."
            )

        return "\n\n".join(parts)

    def list_templates(self) -> List[str]:
        """Return the base names of all available templates."""
        return sorted(
            p.stem for p in self._dir.glob("*.txt") if p.is_file()
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self, name: str) -> str:
        """Load a template from disk (or cache)."""
        if name in self._cache:
            return self._cache[name]

        path = self._dir / f"{name}.txt"
        if not path.is_file():
            available = ", ".join(self.list_templates()) or "(none)"
            raise FileNotFoundError(
                f"Template '{name}' not found at {path}. "
                f"Available templates: {available}"
            )

        text = path.read_text(encoding="utf-8")
        self._cache[name] = text
        return text

    def _substitute(self, template: str, values: Dict[str, str]) -> str:
        """Replace ``{KEY}`` placeholders with supplied values.

        Empty-string values are accepted (the placeholder is simply
        removed).  Lines that become blank after substitution are
        collapsed so the final prompt has no awkward double-blank-lines.

        The regex only matches ``{UPPER_CASE}`` tokens, so it will not
        interfere with JSON braces or other curly-brace content in the
        template text.
        """
        _placeholder_re = re.compile(r"\{([A-Z_]+)\}")

        def _replacer(match: re.Match) -> str:
            key = match.group(1)
            if key not in values:
                raise KeyError(
                    f"Template placeholder '{{{key}}}' has no supplied value. "
                    f"Supplied keys: {sorted(values.keys())}"
                )
            return values[key]

        result = _placeholder_re.sub(_replacer, template)

        # When a placeholder's value is "", the line it occupied becomes
        # blank.  Multiple consecutive blanks look sloppy, so collapse
        # them down to a single blank line.
        result = re.sub(r"\n{3,}", "\n\n", result)
        return result.strip() + "\n"
