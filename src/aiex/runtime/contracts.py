"""Contract validation: enforces BOUNDARY rules on LLM responses.

This module is the post-hoc enforcement layer for AILang's BOUNDARY blocks.
After the LLM produces a response, this module checks it against three
categories of contract rules:

  MUST_INCLUDE checks:
    Verify that required concepts or fields are present in the response.
    Two detection methods are supported:
      - FIELD_EXISTS: checks that a specific JSON key is present and non-empty.
      - SEMANTIC_SCAN: checks that the *concept* appears in the response text,
        using BERT cosine similarity (or keyword fallback).

  CANNOT_INCLUDE checks:
    Verify that prohibited content is absent. Three detection methods:
      - TERMINOLOGY_DIFF: compares the vocabulary of the response against the
        vocabulary of the input. Terms that appear in the response but NOT in
        the input are flagged as "novel" (potentially hallucinated). BERT
        embeddings make this check semantic rather than lexical.
      - KNOWLEDGE_DEPTH_SCAN: uses regex patterns to detect specific legal
        citations, statute references, and case numbers that would indicate
        the LLM injected external knowledge (important for RETRIEVAL: NONE).
      - SEMANTIC_SCAN: checks for prohibited concepts using keyword matching.

  OUTPUT_FORMAT schema:
    Validates the structure and types of the JSON response against the
    declared OUTPUT_FORMAT from the AILang source. Missing fields, wrong
    types, and enum constraint violations are all CRITICAL severity.

Severity escalation model (spec section 6.2):
    MINOR    -> committed with warnings (CANNOT_INCLUDE, retrieval checks)
    MAJOR    -> retry with reinforced prompting up to max_retries, then
                rollback (MUST_INCLUDE)
    CRITICAL -> structural failures; immediate rollback, no retry
                (OUTPUT_FORMAT schema)

BERT integration:
    When sentence-transformers is available, MUST_INCLUDE and CANNOT_INCLUDE
    checks use cosine similarity instead of keyword matching. This handles
    paraphrasing, synonyms, and concept-level matching that keywords miss.
    When unavailable (e.g., no GPU, missing dependency), the module gracefully
    degrades to keyword-based detection.
"""

from __future__ import annotations

import json
import re
from typing import Any

from aiex.models.plan import ContractCheck, Severity, TransactionContract
from aiex.models.state import ContractViolation, ValidationResult

# Graceful BERT integration: try to load sentence-transformers at import time.
# We test actual availability (not just importability) because on some Windows
# environments the import succeeds but the native DLLs fail to load.
# If anything goes wrong, we fall back to keyword-based validation.
BERT_AVAILABLE = False
try:
    from sentence_transformers import SentenceTransformer as _ST  # test actual availability
    from aiex.runtime.bert_validator import semantic_contains, semantic_absent, terminology_diff
    BERT_AVAILABLE = True
    del _ST
except Exception:
    # Catches ImportError, OSError (DLL issues on Windows), and any other load failure
    BERT_AVAILABLE = False


def validate_contract(
    response: dict[str, Any],
    contract: TransactionContract,
    input_terms: set[str] | None = None,
) -> ValidationResult:
    """Validate an LLM response against a transaction contract.

    Args:
        response: Parsed JSON response from LLM.
        contract: The contract to validate against.
        input_terms: Set of terms from inputs (for TERMINOLOGY_DIFF detection).

    Returns:
        ValidationResult with pass/fail status and any violations.
    """
    violations: list[ContractViolation] = []

    # --- Phase 1: MUST_INCLUDE checks ---
    # Verify that every required concept or field is present in the response.
    for check in contract.must_include_checks:
        violation = _evaluate_must_include(check, response)
        if violation:
            violations.append(violation)

    # --- Phase 2: CANNOT_INCLUDE checks ---
    # Verify that no prohibited content appears in the response.
    for check in contract.cannot_include_checks:
        violation = _evaluate_cannot_include(check, response, input_terms)
        if violation:
            violations.append(violation)

    # --- Phase 3: OUTPUT_FORMAT schema validation ---
    # Structural check: are all declared fields present with correct types?
    if contract.output_format_schema:
        schema_violations = _validate_output_schema(response, contract.output_format_schema)
        violations.extend(schema_violations)

    # --- Determine overall result ---
    # The overall severity is the WORST severity across all violations.
    # This drives the engine's retry/rollback decision.
    if not violations:
        return ValidationResult(passed=True, violations=[], severity=Severity.MINOR)

    worst = max(violations, key=lambda v: _severity_rank(v.severity))
    return ValidationResult(
        passed=False,
        violations=violations,
        severity=worst.severity,
    )


def _evaluate_must_include(check: ContractCheck, response: dict) -> ContractViolation | None:
    """Check that required content is present in the response.

    Two detection strategies:
      FIELD_EXISTS  -- simple key lookup; checks that the field is present and
                       non-empty (not None, not "", not []).
      SEMANTIC_SCAN -- concept-level check; uses BERT cosine similarity when
                       available, otherwise falls back to keyword substring
                       matching with concept-to-term expansion.
    """
    if check.detection_method == "FIELD_EXISTS":
        # Structural check: the field must exist and contain meaningful data.
        if check.target_field and check.target_field in response:
            value = response[check.target_field]
            if value is not None and value != "" and value != []:
                return None
        return ContractViolation(
            check_name=check.name,
            severity=check.severity,
            message=check.message,
            details=f"Field '{check.target_field}' missing or empty",
        )

    # SEMANTIC_SCAN: check whether the concept is represented in the response.
    # BERT approach: encode the concept and each response chunk, then check
    # if any chunk has cosine similarity >= 0.45 to the concept.
    # Keyword fallback: expand the concept into search terms (e.g.,
    # "causal_chain" -> ["causal_chain", "causal chain", "causal", "chain"])
    # and check for substring matches in the serialized response.
    if BERT_AVAILABLE:
        if semantic_contains(response, check.expected_content):
            return None
    else:
        response_text = json.dumps(response, default=str).lower()
        target = check.expected_content.lower()
        search_terms = _concept_to_search_terms(target)
        for term in search_terms:
            if term in response_text:
                return None

    return ContractViolation(
        check_name=check.name,
        severity=check.severity,
        message=check.message,
        details=f"Concept '{check.expected_content}' not found in response",
    )


def _evaluate_cannot_include(
    check: ContractCheck,
    response: dict,
    input_terms: set[str] | None,
) -> ContractViolation | None:
    """Check that prohibited content is absent from the response.

    Three detection methods, each targeting a different class of violation:

      TERMINOLOGY_DIFF:
        Detects when the LLM introduces domain terms not present in the input.
        This is the primary enforcement mechanism for RETRIEVAL_BUDGET: NONE,
        which requires pure closed-book reasoning. With BERT, terms are compared
        semantically (cosine similarity < 0.40 = novel). Without BERT, a simpler
        set-difference on extracted domain terms is used.

      KNOWLEDGE_DEPTH_SCAN:
        Detects specific legal/regulatory citation patterns (USC, CFR, case
        numbers, etc.) that indicate the LLM drew on memorized external
        knowledge. This catches a different class of violation than
        TERMINOLOGY_DIFF: the LLM might use input vocabulary but still cite
        specific statutes from its training data.

      SEMANTIC_SCAN:
        General-purpose check for prohibited concepts using keyword matching.
        Used when the AILang source explicitly names a concept to exclude.
    """
    response_text = json.dumps(response, default=str).lower()

    if check.detection_method == "TERMINOLOGY_DIFF" and input_terms and BERT_AVAILABLE:
        # BERT-powered terminology diff: encode all response terms and all input
        # terms, then flag response terms whose max cosine similarity to any
        # input term falls below the novelty threshold (0.40).
        novel = terminology_diff(response, input_terms)
        if novel:
            return ContractViolation(
                check_name=check.name,
                severity=check.severity,
                message=check.message,
                details=f"Novel terms (BERT): {', '.join(novel[:5])}",
            )
        return None
    elif check.detection_method == "TERMINOLOGY_DIFF" and input_terms:
        # Keyword fallback: extract domain terms from both input and response,
        # then flag any terms in the response that aren't in the input set.
        # Less accurate than BERT (misses synonyms, catches morphological variants)
        # but works without any ML dependencies.
        response_terms = _extract_domain_terms(response_text)
        novel_terms = response_terms - input_terms
        if novel_terms:
            return ContractViolation(
                check_name=check.name,
                severity=check.severity,
                message=check.message,
                details=f"Novel terms (keyword): {', '.join(list(novel_terms)[:5])}",
            )
        return None

    if check.detection_method == "KNOWLEDGE_DEPTH_SCAN":
        # Heuristic patterns that indicate the LLM injected specific external
        # knowledge (memorized legal citations, statute numbers, etc.).
        # Each pattern targets a common US legal/regulatory citation format.
        deep_knowledge_patterns = [
            r"\b\d+ U\.?S\.?C\.?\b",           # US Code citations (e.g., "19 USC")
            r"\bProclamation \d+\b",             # Presidential proclamations
            r"\b[A-Z]-\d{3}-\d{3}\b",           # Case/order numbers
            r"\b\d{2} CFR \d+\b",               # Code of Federal Regulations
            r"\bPublic Law \d+-\d+\b",           # Public laws (e.g., "Public Law 116-113")
        ]
        for pattern in deep_knowledge_patterns:
            if re.search(pattern, json.dumps(response, default=str)):
                return ContractViolation(
                    check_name=check.name,
                    severity=check.severity,
                    message=check.message,
                    details=f"Deep knowledge pattern detected: {pattern}",
                )
        return None

    # SEMANTIC_SCAN for prohibited content -- expand the concept name into
    # keyword variants and check if any appear in the serialized response.
    target = check.expected_content.lower()
    search_terms = _concept_to_search_terms(target)
    for term in search_terms:
        if term in response_text:
            return ContractViolation(
                check_name=check.name,
                severity=check.severity,
                message=check.message,
                details=f"Prohibited concept '{check.expected_content}' found",
            )
    return None


def _validate_output_schema(response: dict, schema) -> list[ContractViolation]:
    """Validate that the response matches the declared OUTPUT_FORMAT schema.

    Performs structural validation:
      - Missing fields       -> CRITICAL (the LLM omitted a required output field)
      - Wrong type           -> CRITICAL (e.g., string where boolean expected)
      - Enum constraint miss -> CRITICAL (value not in the allowed set)

    All schema violations are CRITICAL because they indicate the LLM failed
    to follow the output structure, which would break downstream processing.
    Unlike MUST_INCLUDE/CANNOT_INCLUDE (which are about content quality),
    schema violations are about structural correctness.
    """
    violations: list[ContractViolation] = []

    if not hasattr(schema, "fields"):
        return violations

    for field_spec in schema.fields:
        name = field_spec.name
        if name not in response:
            violations.append(ContractViolation(
                check_name=f"schema-field-{name}",
                severity=Severity.CRITICAL,
                message=f"Output schema validation failed: missing field '{name}'",
                details=f"Expected field '{name}' of type {field_spec.field_type}",
            ))
            continue

        value = response[name]

        # Type checking
        if field_spec.field_type == "BOOLEAN" and not isinstance(value, bool):
            violations.append(ContractViolation(
                check_name=f"schema-type-{name}",
                severity=Severity.CRITICAL,
                message=f"Field '{name}' should be BOOLEAN, got {type(value).__name__}",
                details=str(value),
            ))
        elif field_spec.field_type == "NUMBER" and not isinstance(value, (int, float)):
            violations.append(ContractViolation(
                check_name=f"schema-type-{name}",
                severity=Severity.CRITICAL,
                message=f"Field '{name}' should be NUMBER, got {type(value).__name__}",
                details=str(value),
            ))
        elif field_spec.field_type in ("TEXT", "QUALITATIVE") and field_spec.constraint:
            if isinstance(value, str) and value not in field_spec.constraint:
                violations.append(ContractViolation(
                    check_name=f"schema-enum-{name}",
                    severity=Severity.CRITICAL,
                    message=f"Field '{name}' value '{value}' not in {field_spec.constraint}",
                    details=str(value),
                ))
        elif field_spec.field_type == "LIST" and not isinstance(value, list):
            violations.append(ContractViolation(
                check_name=f"schema-type-{name}",
                severity=Severity.CRITICAL,
                message=f"Field '{name}' should be LIST, got {type(value).__name__}",
                details=str(value),
            ))

    return violations


def _concept_to_search_terms(concept: str) -> list[str]:
    """Convert a MUST_INCLUDE/CANNOT_INCLUDE concept to search terms.

    AILang concepts use underscored names (e.g., "causal_chain_analysis").
    This expands them into multiple search variants so the keyword fallback
    can match even if the LLM paraphrased the concept:
      "causal_chain" -> ["causal_chain", "causal chain", "causal", "chain"]

    Stop words and short words are filtered out to avoid false positives.
    """
    words = concept.replace("_", " ").split()
    terms = [concept, " ".join(words)]
    # Add individual significant words (skip common words)
    skip = {"a", "an", "the", "of", "in", "to", "for", "and", "or", "with", "each"}
    for word in words:
        if word not in skip and len(word) > 3:
            terms.append(word)
    return terms


def _extract_domain_terms(text: str) -> set[str]:
    """Extract domain-specific terms from text for TERMINOLOGY_DIFF.

    Used by the keyword-based fallback (when BERT is unavailable) to build
    a vocabulary set from the response text. The extracted terms are compared
    against the input vocabulary to find "novel" terms that may indicate
    the LLM injected external knowledge.

    Targets three categories of domain-significant tokens:
      - Acronyms (e.g., USMCA, NAFTA)
      - Quoted terms (often domain-specific definitions or labels)
      - Legal-style references (Section 301, Article 12, etc.)
    """
    terms: set[str] = set()
    # Acronyms
    for match in re.finditer(r"\b[A-Z]{2,}\b", text):
        terms.add(match.group().lower())
    # Quoted terms
    for match in re.finditer(r'"([^"]+)"', text):
        terms.add(match.group(1).lower())
    # Legal-style references
    for match in re.finditer(r"\b(?:section|article|chapter|subheading)\s+\d+[\w.]*", text, re.I):
        terms.add(match.group().lower())
    return terms


def _severity_rank(severity: Severity) -> int:
    """Map severity to a numeric rank for comparison.

    Used to find the worst (highest) severity across all violations,
    which drives the engine's retry/rollback decision.
    """
    return {Severity.MINOR: 1, Severity.MAJOR: 2, Severity.CRITICAL: 3}.get(severity, 0)
