"""LLM and BERT dispatch layer -- two-phase dispatch for AILang transactions.

This module implements the two-phase dispatch strategy:

  Phase 1: BERT Retrieval
    Before calling the LLM, a BERT-class model performs vector search to
    retrieve domain-relevant context. The RETRIEVAL_BUDGET from the AILang
    source controls this phase:
      - NONE     -> skip retrieval entirely (closed-book reasoning)
      - MINIMAL  -> retrieve up to 3 terminology definitions from a narrow index
      - STANDARD -> retrieve up to 10 results from the full regulatory index
      - FULL     -> same as STANDARD (future: cross-index retrieval)

  Phase 2: LLM Generation
    The retrieved context (if any) is appended to the user message, and the
    composed prompt is sent to the appropriate Claude model. Model selection
    follows the compiler's routing decision:
      - BERT_CLASS          -> colbert-v2 (retrieval only, not used for generation)
      (model IDs come from DEFAULT_MODEL_POOL in models/routing.py)
      - GENERATIVE_LIGHT    -> claude-haiku-4-5 (fast, cheap)
      - GENERATIVE_STANDARD -> claude-sonnet-4-6 (balanced)
      - GENERATIVE_DEEP     -> claude-opus-4-6 (highest quality)

JSON extraction:
  LLMs don't always return clean JSON -- they may wrap it in markdown fences,
  add explanatory prose, or return partial structures. The _extract_json()
  function uses a three-tier fallback strategy to handle this gracefully:
    1. Direct JSON.parse (ideal case)
    2. Extract from ```json ... ``` markdown blocks
    3. Find the outermost { ... } braces (greedy match)
    4. Give up and wrap raw text in an error envelope
"""

from __future__ import annotations

import json
import time
from typing import Any, Protocol

from aiex.models.ast_nodes import RetrievalBudget
from aiex.models.plan import ModelTier, RoutingDecision, TransactionPlan
from aiex.models.routing import DEFAULT_MODEL_POOL
from aiex.models.state import DispatchMeta


# ---------------------------------------------------------------------------
# Client protocols -- these define the interface that any LLM or BERT backend
# must satisfy. Using Protocol (structural subtyping) means concrete classes
# don't need to inherit from a base class; they just need matching signatures.
# ---------------------------------------------------------------------------


class LLMClient(Protocol):
    """Protocol for LLM API clients.

    Any object with a matching create_message() signature will satisfy this
    protocol -- no inheritance required. This allows easy swapping between
    Anthropic, OpenAI, or mock implementations.
    """

    def create_message(
        self,
        model: str,
        system: str,
        user_message: str,
        max_tokens: int,
        temperature: float,
    ) -> dict[str, Any]:
        """Send a message to an LLM and return the parsed response."""
        ...


class BertClient(Protocol):
    """Protocol for BERT-class retrieval clients.

    The BERT client serves Phase 1 of the dispatch pipeline: extracting
    domain terms from input text and searching a vector index for relevant
    definitions or regulatory context to inject into the LLM prompt.
    """

    def extract_terms(self, text: str, domain: str) -> list[str]:
        """Extract domain-specific terms from text."""
        ...

    def search(self, query_terms: list[str], index: str, top_k: int) -> list[dict]:
        """Search a vector index and return results."""
        ...


class AnthropicLLMClient:
    """LLM client using the Anthropic SDK.

    Wraps the anthropic.Anthropic client and automatically parses the
    response text into JSON using the multi-fallback _extract_json() strategy.
    The SDK import is deferred to __init__ so the rest of the codebase can
    load without the anthropic package installed (e.g., in dry-run mode).
    """

    def __init__(self, api_key: str | None = None):
        import anthropic
        self._client = anthropic.Anthropic(api_key=api_key)

    def create_message(
        self,
        model: str,
        system: str,
        user_message: str,
        max_tokens: int,
        temperature: float,
    ) -> dict[str, Any]:
        response = self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": user_message}],
        )
        # The raw response is free-form text; parse it into a JSON dict
        # so the contract validator can inspect individual fields.
        text = response.content[0].text
        return _extract_json(text)


class MockLLMClient:
    """Mock LLM client for dry-run mode.

    Returns a metadata dict describing what *would* have been sent to the
    real API. This lets the budget report and prompt composition be tested
    end-to-end without any API keys or network access.
    """

    def create_message(
        self,
        model: str,
        system: str,
        user_message: str,
        max_tokens: int,
        temperature: float,
    ) -> dict[str, Any]:
        return {
            "_mock": True,
            "_model": model,
            "_system_prompt_length": len(system),
            "_user_message_length": len(user_message),
            "_max_tokens": max_tokens,
            "_temperature": temperature,
        }


class MockBertClient:
    """Mock BERT client that returns placeholder results.

    Uses simple regex heuristics (capitalized phrases, acronyms) instead of
    real BERT embeddings. Useful for development and testing when
    sentence-transformers or a vector DB are not available.
    """

    def extract_terms(self, text: str, domain: str) -> list[str]:
        # Approximate BERT term extraction with regex: capitalized multi-word
        # phrases and uppercase acronyms are likely domain-significant.
        import re
        terms = set()
        for match in re.finditer(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b", text):
            terms.add(match.group())
        for match in re.finditer(r"\b[A-Z]{2,}\b", text):
            terms.add(match.group())
        return list(terms)[:20]

    def search(self, query_terms: list[str], index: str, top_k: int) -> list[dict]:
        return [
            {
                "term": term,
                "definition": f"[MOCK] Definition of {term} from index '{index}'",
                "source": "mock-vector-db",
            }
            for term in query_terms[:top_k]
        ]


# Maps the compiler's abstract model tier to a concrete model identifier.
# The compiler decides the tier based on operation complexity and DEPTH;
# this map resolves it to an actual API model string. BERT_CLASS is used
# only for Phase 1 retrieval (not for generation dispatch).  Derived from
# DEFAULT_MODEL_POOL so dispatch calls exactly the models the budget prices.
MODEL_ID_MAP: dict[ModelTier, str] = {
    tier: cfg.model_id for tier, cfg in DEFAULT_MODEL_POOL.items()
}


def dispatch_transaction(
    tx: TransactionPlan,
    system_prompt: str,
    user_message: str,
    llm_client: LLMClient,
    bert_client: BertClient | None = None,
    input_text: str = "",
) -> tuple[dict[str, Any], DispatchMeta]:
    """Dispatch a transaction to the appropriate model(s).

    Returns:
        Tuple of (parsed response dict, dispatch metadata).
    """
    start_time = time.time()
    retries = 0
    fallbacks = 0

    # ---------------------------------------------------------------
    # Phase 1: BERT retrieval (if applicable)
    # The RETRIEVAL_BUDGET from the AILang source controls whether we
    # perform vector search before calling the LLM. NONE skips this
    # entirely; MINIMAL/STANDARD/FULL trigger retrieval with different
    # scope and result counts.
    # ---------------------------------------------------------------
    retrieval_context = ""
    bert_latency = 0

    if tx.retrieval_budget in (RetrievalBudget.MINIMAL, RetrievalBudget.STANDARD, RetrievalBudget.FULL):
        if bert_client:
            bert_start = time.time()
            terms = bert_client.extract_terms(input_text, domain="general")

            # RETRIEVAL_BUDGET maps to BERT vector search parameters:
            #   MINIMAL  -> top_k=3, narrow index (terminology definitions only)
            #   STANDARD -> top_k=10, broad index (full regulatory knowledge base)
            #   FULL     -> same as STANDARD for now; future: multi-index search
            top_k = 3 if tx.retrieval_budget == RetrievalBudget.MINIMAL else 10
            index = (
                "terminology-definitions"
                if tx.retrieval_budget == RetrievalBudget.MINIMAL
                else "regulatory-knowledge-full"
            )
            results = bert_client.search(terms, index=index, top_k=top_k)
            bert_latency = int((time.time() - bert_start) * 1000)

            # Format retrieved context as labeled sections so the LLM can
            # distinguish between user-provided data and retrieved context.
            if results:
                label = (
                    "RESOLVED TERMINOLOGY"
                    if tx.retrieval_budget == RetrievalBudget.MINIMAL
                    else "REGULATORY CONTEXT"
                )
                retrieval_context = f"\n\n{label}:\n"
                for r in results:
                    retrieval_context += f"- {r.get('term', '')}: {r.get('definition', '')}\n"

    # ---------------------------------------------------------------
    # Phase 2: Compose final prompt with retrieval context
    # Retrieved definitions/context are appended to the user message
    # (not the system prompt) so they appear as grounding data.
    # ---------------------------------------------------------------
    final_user_message = user_message
    if retrieval_context:
        final_user_message = user_message + retrieval_context

    # ---------------------------------------------------------------
    # Phase 3: LLM generation dispatch
    # Resolve the abstract model tier (from the compiler's routing
    # decision) to a concrete model ID, then call the LLM.
    # ---------------------------------------------------------------
    # If the compiler escalated the model (e.g., DEEP operation on a
    # STANDARD base tier), use the escalated tier; otherwise use base.
    model_tier = tx.routing.escalated_to or tx.routing.base_tier
    model_id = MODEL_ID_MAP.get(model_tier, "claude-sonnet-4-6")
    temperature = tx.routing.temperature
    max_tokens = tx.routing.max_output_tokens or 2048

    response = llm_client.create_message(
        model=model_id,
        system=system_prompt,
        user_message=final_user_message,
        max_tokens=max_tokens,
        temperature=temperature,
    )

    elapsed = time.time() - start_time
    latency_ms = int(elapsed * 1000)

    # Rough token estimation using word count -- this is a heuristic since
    # we don't have the actual token counts from all providers. The budget
    # module calculates precise costs separately.
    tokens_in = len(system_prompt.split()) + len(final_user_message.split())
    tokens_out = len(json.dumps(response).split())

    meta = DispatchMeta(
        model_used=model_id,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        latency_ms=latency_ms,
        cost_usd=0.0,  # precise cost is calculated by the budget module
        retries=retries,
        fallbacks=fallbacks,
    )

    return response, meta


def _extract_json(text: str) -> dict[str, Any]:
    """Robustly extract JSON from LLM response text.

    LLMs are notoriously inconsistent about returning clean JSON. Even with
    explicit "respond only in JSON" instructions, models may:
      - Wrap JSON in markdown code fences (```json ... ```)
      - Add explanatory prose before or after the JSON
      - Include trailing commas or comments (invalid JSON)

    This function uses a three-tier fallback strategy, ordered from most
    reliable to most permissive:

      1. Direct parse -- works when the response is pure JSON
      2. Markdown extraction -- strips ```json fences
      3. Brace matching -- finds the outermost { ... } in the text

    If all three fail, the raw text is returned in an error envelope so that
    the contract validator can report the parse failure as a CRITICAL violation.

    Handles: pure JSON, markdown code blocks, JSON embedded in prose,
    and responses where the LLM wraps JSON in explanation.
    """
    import re

    # Strategy 1: Direct parse -- the ideal case where the LLM returned pure JSON
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strategy 2: Markdown code blocks -- common when models see "JSON" in the prompt
    match = re.search(r"```(?:json)?\s*\n(.*?)\n\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Strategy 3: Greedy brace matching -- find first '{' to last '}'.
    # This handles cases where the LLM adds prose like "Here is the result: {...}"
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        candidate = text[first_brace : last_brace + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    # Strategy 4: Give up -- wrap the raw text so downstream code can detect
    # the failure via the "_parse_error" key.
    return {"_raw_text": text, "_parse_error": "Could not extract JSON from response"}
