"""BERT-powered semantic validation for contract checks.

This module replaces keyword-based detection with embedding-based cosine
similarity, solving a fundamental limitation of substring matching: LLMs
paraphrase, use synonyms, and restructure concepts in ways that keywords
miss entirely.

Approach:
  Uses the sentence-transformers library to encode both the target concept
  and the LLM response into dense vector embeddings, then computes cosine
  similarity to determine semantic overlap. The response is split into
  overlapping chunks (max 500 chars each) so that individual concepts are
  not diluted by long documents.

Model choice:
  all-MiniLM-L6-v2 was chosen for its balance of speed and quality:
    - 384-dimensional embeddings (compact, fast dot products)
    - ~80ms per encoding batch on CPU
    - Competitive with larger models on semantic textual similarity benchmarks
  The model is loaded lazily on first use and cached for the process lifetime.

Threshold rationale:
  MUST_INCLUDE (semantic_contains):  threshold = 0.45
    Lower threshold because we want to FIND the concept -- false positives
    (claiming something is present when it's marginally related) are less
    harmful than false negatives (claiming it's missing when the LLM used
    a synonym). A miss triggers a retry, which wastes tokens.

  CANNOT_INCLUDE (semantic_absent):  threshold = 0.55
    Higher threshold because we want to be confident the concept is truly
    PRESENT before flagging a violation. False positives here (claiming
    something prohibited is present when it's not) would cause unnecessary
    rollbacks. We'd rather let borderline cases pass.

  TERMINOLOGY_DIFF:  threshold = 0.40
    The novelty threshold: a response term with max similarity < 0.40 to
    ANY input term is considered "novel" (not derivable from the inputs).
    This is intentionally low because even loosely related terms should not
    be flagged -- only truly foreign vocabulary indicates hallucinated
    external knowledge.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

import numpy as np


@lru_cache(maxsize=1)
def _get_model():
    """Load the sentence-transformer model lazily and cache it.

    Uses lru_cache(maxsize=1) to ensure we only load the model once per
    process -- the model weights are ~80MB in memory and loading takes ~2s.
    Subsequent calls return the cached instance instantly.
    """
    from sentence_transformers import SentenceTransformer
    # all-MiniLM-L6-v2: 384-dim embeddings, fast on CPU, good at
    # semantic textual similarity. See model card:
    # https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2
    return SentenceTransformer("all-MiniLM-L6-v2")


def semantic_contains(response: dict[str, Any], concept: str, threshold: float = 0.45) -> bool:
    """Check if a concept is semantically present in the response.

    Uses BERT embeddings + cosine similarity. Returns True if any
    chunk of the response text has similarity >= threshold to the concept.

    Used for MUST_INCLUDE checks.

    Why chunking matters:
      Embedding a long document into a single vector dilutes the signal from
      any individual concept. By splitting into ~500-char chunks, we ensure
      that a concept mentioned in one paragraph isn't drowned out by unrelated
      content elsewhere. The max similarity across all chunks is used.

    Why threshold=0.45:
      See module docstring for threshold rationale. In short: a lower bar
      reduces false negatives (missing a paraphrased concept), which is more
      important than avoiding false positives for MUST_INCLUDE checks.
    """
    model = _get_model()
    response_text = json.dumps(response, default=str)

    # Split into chunks so each embedding captures a focused segment
    chunks = _split_into_chunks(response_text, max_chars=500)
    if not chunks:
        return False

    # AILang concepts use underscores (e.g., "causal_chain") -- normalize
    # to natural language so the embedding captures the semantic meaning.
    concept_text = concept.replace("_", " ")

    # Encode concept and all chunks. normalize_embeddings=True ensures unit
    # vectors, so dot product == cosine similarity (no separate normalization needed).
    concept_emb = model.encode([concept_text], normalize_embeddings=True)
    chunk_embs = model.encode(chunks, normalize_embeddings=True)

    # Compute cosine similarity between each chunk and the concept.
    # Result shape: (num_chunks,) -- one similarity score per chunk.
    similarities = np.dot(chunk_embs, concept_emb.T).flatten()
    max_sim = float(np.max(similarities))

    return max_sim >= threshold


def semantic_absent(response: dict[str, Any], concept: str, threshold: float = 0.55) -> bool:
    """Check if a concept is semantically ABSENT from the response.

    Returns True if NO chunk of the response has similarity >= threshold.

    Uses a HIGHER threshold (0.55) than semantic_contains (0.45) to create
    an asymmetric detection bias: we need stronger evidence to claim something
    IS present (and thus violating a CANNOT_INCLUDE rule) than to claim
    something is present for a MUST_INCLUDE check. This reduces false-positive
    violations that would trigger unnecessary rollbacks.

    Used for CANNOT_INCLUDE checks.
    """
    return not semantic_contains(response, concept, threshold=threshold)


def terminology_diff(
    response: dict[str, Any],
    input_terms: set[str],
    threshold: float = 0.40,
) -> list[str]:
    """Find terms in the response that are semantically novel (not in inputs).

    This is the BERT-powered enforcement mechanism for RETRIEVAL_BUDGET: NONE.
    When a transaction is configured for closed-book reasoning, the LLM should
    only use vocabulary and concepts present in the provided inputs. Any "novel"
    terms in the response suggest the LLM drew on its training data.

    Algorithm:
      1. Extract significant terms from the response (acronyms, multi-word
         phrases, legal references).
      2. Encode both input terms and response terms into BERT embeddings.
      3. Compute the (R x I) cosine similarity matrix.
      4. For each response term, find its maximum similarity to any input term.
      5. If that max similarity < threshold (0.40), the term is "novel" --
         it has no semantic counterpart in the inputs.

    Why threshold=0.40:
      This is intentionally low. Even loosely related terms (similarity 0.40-0.60)
      are considered "derivable" from the inputs. Only terms that are truly
      unrelated to anything in the input get flagged. This avoids false positives
      from legitimate inference (e.g., if the input says "tariff" and the LLM
      says "duty", those are similar enough not to be flagged).

    Used for RETRIEVAL_BUDGET: NONE compliance checking.
    """
    model = _get_model()
    response_text = json.dumps(response, default=str)

    # Extract candidate terms from the response -- these are the terms we'll
    # check for novelty against the input vocabulary.
    response_terms = _extract_significant_terms(response_text)
    if not response_terms or not input_terms:
        return []

    # Encode all terms into the same embedding space so cosine similarity
    # captures semantic relatedness (not just lexical overlap).
    input_list = list(input_terms)
    input_embs = model.encode(input_list, normalize_embeddings=True)
    response_embs = model.encode(list(response_terms), normalize_embeddings=True)

    # Build the full similarity matrix: shape (num_response_terms, num_input_terms).
    # Each cell [i, j] is the cosine similarity between response_term[i] and input_term[j].
    similarities = np.dot(response_embs, input_embs.T)
    # For each response term, take its best match across all input terms.
    max_sims = np.max(similarities, axis=1)

    # Terms whose best match is below the threshold are novel -- they have no
    # semantic counterpart anywhere in the input vocabulary.
    novel = []
    for i, term in enumerate(response_terms):
        if max_sims[i] < threshold:
            novel.append(term)

    return novel


def _split_into_chunks(text: str, max_chars: int = 500) -> list[str]:
    """Split text into chunks for embedding, respecting sentence boundaries.

    Sentence-transformer models have a fixed context window (typically 256-512
    tokens). Feeding an entire multi-page response as a single input would
    either truncate it or produce a diluted embedding. Splitting into ~500-char
    chunks ensures each embedding captures a focused segment of the response.

    The chunks are built greedily: sentences are accumulated until the chunk
    would exceed max_chars, then a new chunk is started. This is a rough
    approximation of sentence boundaries (splitting on ".") but works well
    enough for the similarity comparison use case.
    """
    sentences = text.replace("\\n", "\n").split(".")
    chunks = []
    current = ""
    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        if len(current) + len(sent) > max_chars:
            if current:
                chunks.append(current)
            current = sent
        else:
            current = f"{current}. {sent}" if current else sent
    if current:
        chunks.append(current)
    return chunks


def _extract_significant_terms(text: str) -> list[str]:
    """Extract significant domain terms from text for terminology diff.

    Targets four categories of domain-significant tokens that are most
    likely to reveal hallucinated external knowledge:

      1. Multi-word capitalized phrases (e.g., "Trade Representative",
         "National Security") -- these are usually proper nouns or official titles.
      2. Acronyms (e.g., "USMCA", "CFR") -- common in legal/regulatory domains.
         Common English words that happen to be all-caps (IN, OF, etc.) are
         excluded to reduce noise.
      3. Legal/regulatory references (e.g., "Section 301", "Article 12.3").
      4. Statute citations (e.g., "19 USC 2411") -- strong indicators of
         memorized legal knowledge.

    The result is capped at 50 terms to bound the encoding cost. In practice,
    most responses produce 10-30 significant terms.
    """
    import re
    terms = set()

    # Multi-word capitalized phrases
    for match in re.finditer(r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+", text):
        terms.add(match.group())

    # Acronyms (2+ uppercase letters), filtering out common English stop-words
    # that happen to appear in all-caps in serialized JSON
    for match in re.finditer(r"\b[A-Z]{2,}\b", text):
        t = match.group()
        if t not in ("IN", "OF", "TO", "OR", "AND", "THE", "TEXT", "LIST", "END"):
            terms.add(t)

    # Legal/regulatory references
    for match in re.finditer(r"(?:Section|Article|Chapter)\s+\d+[\w.]*", text, re.I):
        terms.add(match.group())

    # Statute citations (US Code format)
    for match in re.finditer(r"\d+\s+U\.?S\.?C\.?\s+[§\d]+[\w.]*", text):
        terms.add(match.group())

    return list(terms)[:50]  # cap to avoid excessive encoding time
