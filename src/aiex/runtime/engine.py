"""AIEX Runtime Engine: the dispatch-validate-commit loop.

This module implements the core execution cycle inspired by COBOL's
PERFORM-EVALUATE-COMMIT pattern, adapted for LLM orchestration:

    DISPATCHED --> VALIDATING --> COMMITTED  (happy path)
                              --> ROLLED_BACK (contract violation)

Each AILang transaction goes through three phases:
  1. DISPATCH  -- Send the composed prompt to an LLM (with optional BERT retrieval).
  2. VALIDATE  -- Check the LLM response against BOUNDARY contract rules.
  3. COMMIT/ROLLBACK -- Accept the result into program state, or discard it.

Retry strategy:
  When a MAJOR violation occurs, the engine uses "reinforced prompting" -- it
  appends the violation details to the system prompt so the LLM knows exactly
  what went wrong, then re-dispatches. CRITICAL violations abort immediately
  (no retry). MINOR violations are committed with warnings.

Parallel groups:
  Transactions within a PARALLEL block share no data dependencies, so they
  can be dispatched in sequence within a group (true async is a future
  enhancement). The engine tracks which groups have been executed to avoid
  re-running members when iterating the topological order.

System prompt composition:
  The final system prompt is assembled from four layers:
    1. Template  -- operation-specific reasoning scaffold (DEDUCE, TRACE, etc.)
    2. Routing   -- retrieval modifiers and depth suffixes from the execution plan
    3. Boundaries -- MUST_INCLUDE / CANNOT_INCLUDE / OUTPUT_FORMAT instructions
    4. Retry reinforcement -- appended only on retry attempts
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from rich.console import Console
from rich.table import Table

from aiex.compiler.compiler import compile_ailang
from aiex.models.ast_nodes import Program, RetrievalBudget
from aiex.models.plan import ExecutionPlan, Severity, TransactionPlan
from aiex.models.state import (
    ContractViolation,
    DispatchMeta,
    ProgramState,
    TransactionResult,
    TransactionStatus,
    ValidationResult,
)
from aiex.runtime.code_executor import execute_code_block
from aiex.runtime.contracts import validate_contract
from aiex.runtime.dispatch import (
    AnthropicLLMClient,
    BertClient,
    LLMClient,
    MockBertClient,
    MockLLMClient,
    dispatch_transaction,
)
from aiex.templates import TemplateLoader

console = Console()


class AiexEngine:
    """The AIEX runtime engine.

    Executes an AILang program by:
    1. Compiling it to an ExecutionPlan
    2. Executing each transaction in dependency order
    3. Running the dispatch-validate-commit cycle for each transaction
    4. Managing state, retries, and fallbacks
    """

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        bert_client: BertClient | None = None,
        dry_run: bool = False,
        verbose: bool = True,
    ):
        self.dry_run = dry_run
        self.verbose = verbose
        self.template_loader = TemplateLoader()

        # In dry-run mode, swap in mock clients that return placeholder data
        # without making any API calls. This lets users inspect compilation
        # output, budget reports, and prompt composition without spending tokens.
        if dry_run:
            self.llm_client: LLMClient = MockLLMClient()
            self.bert_client: BertClient | None = MockBertClient()
        else:
            # Default to the Anthropic SDK for LLM calls; BERT client falls
            # back to MockBertClient until a real vector-search backend is wired in.
            self.llm_client = llm_client or AnthropicLLMClient()
            self.bert_client = bert_client or MockBertClient()

    def run(self, program: Program, source_file: str = "<stdin>") -> ProgramState:
        """Compile and execute an AILang program.

        Returns the final ProgramState with all committed results.
        """
        # Step 1: Compile
        if self.verbose:
            console.print("\n[bold blue]=== AIEX Compiler ===[/bold blue]")
        plan = compile_ailang(program, source_file)

        # Step 2: Print budget report
        if self.verbose:
            self._print_budget_report(plan)

        # Step 3: Initialize state
        state = ProgramState()
        for var in program.variables:
            state.set_variable(var.name, var.value)

        # Step 4: Execute transactions
        if self.verbose:
            console.print("\n[bold blue]=== AIEX Runtime ===[/bold blue]")

        self._execute_plan(plan, state)

        # Step 5: Print summary
        if self.verbose:
            self._print_execution_summary(state)

        return state

    def compile_only(self, program: Program, source_file: str = "<stdin>") -> ExecutionPlan:
        """Compile without executing. Returns the execution plan."""
        plan = compile_ailang(program, source_file)
        if self.verbose:
            self._print_budget_report(plan)
        return plan

    def _execute_plan(self, plan: ExecutionPlan, state: ProgramState) -> None:
        """Execute all transactions in dependency order.

        Walks the compiler's topological ordering. When a transaction belongs
        to a parallel group, the entire group is dispatched on first encounter
        (subsequent members are skipped because they're already in `completed`).
        Sequential transactions have their dependency sets checked before dispatch.
        """
        tx_map = {tx.id: tx for tx in plan.transactions}
        completed: set[str] = set()

        # Build a reverse map: transaction_id -> group_name, so we can detect
        # whether a given tx should be dispatched individually or as part of a group.
        in_group: dict[str, str] = {}
        for group_name, members in plan.parallel_groups.items():
            for m in members:
                in_group[m] = group_name

        # Walk transactions in topological order (the compiler guarantees this
        # ordering satisfies all inter-transaction data dependencies).
        executed_groups: set[str] = set()
        for tx in plan.transactions:
            if tx.id in in_group:
                group_name = in_group[tx.id]
                if group_name not in executed_groups:
                    # First time seeing this group -- fire all members now.
                    # Members share no data dependencies with each other, so
                    # execution order within the group is arbitrary.
                    group_members = [
                        tx_map[tid] for tid in plan.parallel_groups[group_name]
                    ]
                    if self.verbose:
                        console.print(
                            f"\n[yellow]>>> Parallel group: {group_name} "
                            f"({len(group_members)} transactions)[/yellow]"
                        )
                    for gtx in group_members:
                        self._execute_transaction(gtx, state, plan)
                        completed.add(gtx.id)
                    executed_groups.add(group_name)
            elif tx.id not in completed:
                # Sequential transaction -- verify all upstream dependencies
                # have committed before we dispatch. If any are missing (e.g.,
                # an upstream rolled back), skip this tx and log an error.
                deps_met = all(d in completed for d in tx.depends_on)
                if not deps_met:
                    unmet = [d for d in tx.depends_on if d not in completed]
                    state.log_event(
                        "ERROR",
                        {"tx": tx.id, "message": f"Unmet dependencies: {unmet}"},
                    )
                    continue
                self._execute_transaction(tx, state, plan)
                completed.add(tx.id)

    def _execute_transaction(
        self,
        tx: TransactionPlan,
        state: ProgramState,
        plan: ExecutionPlan,
    ) -> None:
        """Execute a single transaction through the dispatch-validate-commit cycle.

        State transitions:
            DISPATCHED  -- prompt sent to LLM, awaiting response
            VALIDATING  -- response received, contract checks running
            COMMITTED   -- all checks passed (or only MINOR violations)
            ROLLED_BACK -- CRITICAL violation, or MAJOR after all retries exhausted

        The retry loop uses "reinforced prompting": on each MAJOR failure, the
        violation details are appended to the system prompt so the LLM can
        self-correct. This is more effective than blind retries because the
        model sees exactly which boundary it violated.
        """
        # ============================================================
        # CodeBlock branch: deterministic local execution, no LLM call.
        # ============================================================
        # When ``tx.code_block_body`` is set the compiler has marked this
        # transaction as an EXECUTE_CODE block.  We skip every LLM-shaped
        # step (prompt composition, dispatch, contract validation, retry
        # loop) and run the body directly via the local code executor.
        # The block's new variable bindings are merged back into program
        # state so subsequent transactions see them.
        if tx.code_block_body is not None:
            self._execute_code_block_transaction(tx, state)
            return

        if self.verbose:
            console.print(f"\n  [cyan]TX {tx.id}[/cyan] ({tx.operation_type.value})")
            console.print(f"    Retrieval: {tx.retrieval_budget.value}")
            console.print(f"    Depth: {tx.depth.value}")
            model = tx.routing.escalated_to or tx.routing.base_tier
            console.print(f"    Model: {model.value}")

        # --- Prompt composition ---
        # The system prompt encodes the operation type (DEDUCE, TRACE, etc.),
        # retrieval constraints, depth instructions, and boundary rules.
        # The user message carries the actual input data and references.
        system_prompt = self._compose_system_prompt(tx, state)
        user_message = self._compose_user_message(tx, state)

        # Gather input text so we can extract domain terms for TERMINOLOGY_DIFF
        # validation -- this detects when the LLM hallucinates terms not present
        # in the original inputs (important for RETRIEVAL_BUDGET: NONE).
        input_text = self._collect_input_text(tx, state)
        input_terms = self._extract_input_terms(input_text)

        # --- Retry loop ---
        # Total attempts = 1 initial + max_retries (default 2 if unspecified).
        max_attempts = 1 + (tx.contract.max_retries or 2)
        response = None
        meta = None
        validation = None

        for attempt in range(max_attempts):
            if attempt > 0 and self.verbose:
                console.print(f"    [yellow]Retry {attempt}/{tx.contract.max_retries}[/yellow]")

            response, meta = dispatch_transaction(
                tx=tx,
                system_prompt=system_prompt,
                user_message=user_message,
                llm_client=self.llm_client,
                bert_client=self.bert_client,
                input_text=input_text,
            )

            # --- Validation phase ---
            validation = validate_contract(
                response=response,
                contract=tx.contract,
                input_terms=input_terms,
            )

            if validation.passed:
                break

            # --- Severity-based retry decision ---
            if validation.severity == Severity.CRITICAL:
                # CRITICAL = structural failure (missing schema fields, wrong types).
                # No amount of prompt reinforcement will fix a fundamentally broken
                # response, so we abort immediately.
                if self.verbose:
                    for v in validation.violations:
                        console.print(f"    [red]CRITICAL: {v.message}[/red]")
                break
            elif validation.severity == Severity.MAJOR:
                # MAJOR = the LLM understood the format but violated a boundary
                # (e.g., included prohibited content, missed required concepts).
                # Reinforced prompting: append the violation details so the next
                # attempt can specifically address them.
                violation_details = "\n".join(v.message for v in validation.violations)
                system_prompt += (
                    f"\n\n=== RETRY INSTRUCTION ===\n"
                    f"Your previous response violated these boundaries:\n"
                    f"{violation_details}\n"
                    f"You MUST fix these violations.\n"
                    f"=== END RETRY ===\n"
                )
            else:
                # MINOR = cosmetic issues that don't compromise correctness.
                # Accept the response as-is, with warnings attached.
                break

        # --- Commit or rollback decision ---
        # Passed validation or only minor violations -> COMMITTED.
        # Everything else -> ROLLED_BACK (response discarded from state).
        if validation and validation.passed:
            status = TransactionStatus.COMMITTED
            if self.verbose:
                console.print(f"    [green]COMMITTED[/green]")
        elif validation and validation.severity == Severity.MINOR:
            status = TransactionStatus.COMMITTED
            if self.verbose:
                for v in validation.violations:
                    console.print(f"    [yellow]WARNING: {v.message}[/yellow]")
                console.print(f"    [green]COMMITTED (with warnings)[/green]")
        else:
            status = TransactionStatus.ROLLED_BACK
            if self.verbose:
                console.print(f"    [red]ROLLED BACK[/red]")

        result = TransactionResult(
            id=tx.id,
            name=tx.name,
            status=status,
            # Rolled-back responses are excluded from state to prevent downstream
            # transactions from consuming invalid data.
            response=response if status == TransactionStatus.COMMITTED else None,
            dispatch_meta=meta,
            validation=validation,
        )

        state.commit_result(result)
        # If the transaction declared an output reference (e.g., `-> $result`),
        # store the committed response so downstream transactions can read it.
        if tx.output_ref and status == TransactionStatus.COMMITTED:
            state.set_variable(tx.output_ref, response)

        state.log_event(
            "COMMIT" if status == TransactionStatus.COMMITTED else "ROLLBACK",
            {"tx": tx.id, "status": status.value, "message": f"TX {tx.id}: {status.value}"},
        )

    def _execute_code_block_transaction(
        self,
        tx: TransactionPlan,
        state: ProgramState,
    ) -> None:
        """Execute an EXECUTE_CODE block and propagate its bindings.

        This is the no-LLM dispatch path.  Steps:
          1. Snapshot the current AILang variable namespace as Python locals.
          2. Hand the body off to ``execute_code_block``.
          3. On success, copy every new/modified binding back into state
             via ``state.set_variable`` (which also logs the mutation).
          4. Record a TransactionResult so the execution log shows the
             code block alongside reasoning transactions.

        On failure the block is marked ROLLED_BACK and no bindings are
        propagated -- this preserves AILang state's "all-or-nothing"
        commit semantics across the dispatch-validate-commit lifecycle,
        even though code-block "validation" is just "did it raise".
        """
        if self.verbose:
            console.print(f"\n  [cyan]TX {tx.id}[/cyan] (CODE_EXECUTION / {tx.code_block_language})")
            console.print(f"    Model: local-code")

        # Build the namespace the code body sees: every AILang variable
        # is exposed as a Python local with the same name.  We pass a
        # snapshot dict so the executor cannot mutate state mid-run; only
        # the bindings it returns are committed back.
        namespace_in = dict(state.variables)
        result = execute_code_block(
            language=tx.code_block_language or "python",
            body=tx.code_block_body or "",
            namespace_in=namespace_in,
        )

        if result.success:
            # Propagate every new/modified binding back into AILang state.
            # set_variable logs each mutation so the audit trail captures
            # what the code block produced.
            for name, value in result.new_bindings.items():
                state.set_variable(name, value)
            status = TransactionStatus.COMMITTED
            if self.verbose:
                console.print(f"    [green]COMMITTED[/green] -- {result.output_text}")
        else:
            status = TransactionStatus.ROLLED_BACK
            if self.verbose:
                console.print(f"    [red]ROLLED BACK[/red] -- {result.error}")

        # Record the transaction result.  Code blocks have no "response"
        # in the LLM sense; we synthesise a small dict so downstream
        # consumers (and the audit log) see something coherent.
        synthetic_response: dict[str, Any] = {
            "kind": "code_execution",
            "language": tx.code_block_language,
            "summary": result.output_text,
            "propagated_bindings": list(result.new_bindings.keys()) if result.success else [],
        }
        if not result.success:
            synthetic_response["error"] = result.error

        tx_result = TransactionResult(
            id=tx.id,
            name=tx.name,
            status=status,
            response=synthetic_response if status == TransactionStatus.COMMITTED else None,
            dispatch_meta=None,
            validation=None,
        )
        state.commit_result(tx_result)
        state.log_event(
            "COMMIT" if status == TransactionStatus.COMMITTED else "ROLLBACK",
            {"tx": tx.id, "status": status.value, "kind": "code_execution"},
        )

    def _compose_system_prompt(self, tx: TransactionPlan, state: ProgramState) -> str:
        """Compose the system prompt from template + routing + boundaries.

        The prompt is built in layers:
          1. Template selection -- each AILang operation type (DEDUCE, TRACE,
             COMPARE, etc.) has a corresponding Jinja template that provides
             the reasoning scaffold.
          2. Retrieval modifier -- controls how much external knowledge the LLM
             may use, from NONE (strict closed-book) to FULL (open retrieval).
          3. Depth suffix -- governs analysis depth (SHALLOW through EXHAUSTIVE).
          4. Boundary instructions -- the MUST_INCLUDE / CANNOT_INCLUDE /
             OUTPUT_FORMAT rules rendered as explicit instructions.
          5. Operation-specific params -- direction (TRACE), dimensions (COMPARE).

        If the template is missing, a fallback prompt is constructed inline.
        """
        # Map reasoning type to template name
        template_map = {
            "DEDUCE": "deduce",
            "TRACE": "trace",
            "COMPARE": "compare",
            "WEIGH": "weigh",
            "SYNTHESIZE": "synthesize",
            "RETRIEVE": "retrieve",
            "INTELLIGENTLY": "intelligent_general",
        }
        template_name = template_map.get(tx.operation_type.value, "intelligent_general")

        # Build boundary instructions
        must_include = [c.expected_content for c in tx.contract.must_include_checks]
        cannot_include = [c.expected_content for c in tx.contract.cannot_include_checks]

        schema_str = ""
        if tx.contract.output_format_schema:
            schema_str = _schema_to_json_example(tx.contract.output_format_schema)

        boundary_instructions = self.template_loader.get_boundary_instructions(
            must_include=must_include,
            cannot_include=cannot_include,
            output_schema=schema_str,
        )

        # Build retrieval modifier -- this is the key control for hallucination
        # prevention. NONE injects a strict "closed-book" instruction; MINIMAL
        # allows basic terminology resolution; STANDARD/FULL add no restrictions.
        retrieval_modifiers = {
            RetrievalBudget.NONE: (
                "CRITICAL INSTRUCTION: You MUST reason ONLY from the premises "
                "and data provided below. Do NOT use any external knowledge, "
                "regulatory details, case law, or facts not explicitly stated "
                "in the inputs. If information is insufficient, state "
                "'insufficient_info'."
            ),
            RetrievalBudget.MINIMAL: (
                "You may resolve standard terminology and basic definitions "
                "relevant to the domain. Do NOT inject detailed external "
                "knowledge such as specific case outcomes, current regulatory "
                "orders, or implementation details beyond basic terminology."
            ),
            RetrievalBudget.STANDARD: "",
            RetrievalBudget.FULL: "",
        }
        retrieval_modifier = retrieval_modifiers.get(tx.retrieval_budget, "")

        # Build depth suffix -- controls how thorough the LLM analysis should be.
        # This maps directly to the DEPTH modifier in the AILang source.
        depth_suffixes = {
            "SHALLOW": "Provide a brief, surface-level analysis.",
            "STANDARD": "",
            "THOROUGH": (
                "Include contributing factors and secondary causes "
                "beyond the primary chain."
            ),
            "DEEP": (
                "Perform multi-pass mechanism-level analysis. "
                "Identify systemic vulnerabilities."
            ),
            "EXHAUSTIVE": (
                "Leave no stone unturned. Explore all causal branches, "
                "counterfactuals, and hypotheses."
            ),
        }
        depth_suffix = depth_suffixes.get(tx.depth.value, "")

        # Build direction (for TRACE)
        direction = ""
        if tx.direction:
            direction = tx.direction.value

        # Build dimensions (for COMPARE)
        dimensions = ", ".join(tx.dimensions) if tx.dimensions else ""

        kwargs = {
            "RETRIEVAL_MODIFIER": retrieval_modifier,
            "DEPTH_SUFFIX": depth_suffix,
            "STYLE_PREFIX": "",
            "BOUNDARY_INSTRUCTIONS": boundary_instructions,
            "INPUTS": "",  # inputs go in user message
            "OUTPUT_SCHEMA": schema_str,
            "DIRECTION": direction,
            "DIMENSIONS": dimensions,
        }

        try:
            return self.template_loader.compose(template_name, **kwargs)
        except (KeyError, FileNotFoundError):
            # Fallback: construct a basic prompt
            return (
                f"You are executing a {tx.operation_type.value} operation.\n"
                f"{retrieval_modifier}\n{depth_suffix}\n{boundary_instructions}\n"
                f"Respond in valid JSON matching this schema:\n{schema_str}"
            )

    def _compose_user_message(self, tx: TransactionPlan, state: ProgramState) -> str:
        """Compose the user message from input references.

        Resolves variable references from program state and assembles them
        into a structured message. This keeps data separate from instructions
        (which live in the system prompt), following the Anthropic best practice
        of putting grounding data in the user turn.
        """
        parts = []

        # Add input sources -- these are the primary data the LLM reasons over
        if tx.input_refs:
            parts.append("INPUT DATA:")
            for ref in tx.input_refs:
                value = state.get_variable(ref)
                if value is not None:
                    parts.append(f"\n{ref}:")
                    if isinstance(value, (dict, list)):
                        parts.append(json.dumps(value, indent=2, default=str))
                    else:
                        parts.append(str(value))

        # Add APPLY_TO
        if tx.apply_to:
            value = state.get_variable(tx.apply_to)
            if value is not None:
                parts.append(f"\nAPPLY TO:")
                if isinstance(value, (dict, list)):
                    parts.append(json.dumps(value, indent=2, default=str))
                else:
                    parts.append(str(value))

        # Add AGAINST (for COMPARE)
        if tx.against:
            parts.append(f"\nEVALUATE AGAINST: {tx.against}")

        # Add DIMENSIONS (for COMPARE)
        if tx.dimensions:
            parts.append(f"\nDIMENSIONS: {', '.join(tx.dimensions)}")

        # Add SOURCES
        if tx.sources:
            parts.append("\nSOURCES:")
            for src in tx.sources:
                value = state.get_variable(src)
                if value is not None:
                    parts.append(f"  {src}: {json.dumps(value, default=str)[:500]}")

        return "\n".join(parts) if parts else "Execute the operation as specified."

    def _collect_input_text(self, tx: TransactionPlan, state: ProgramState) -> str:
        """Collect all input text for BERT term extraction.

        Flattens all input references and APPLY_TO data into a single string
        so that BERT can extract domain terms for retrieval and for the
        TERMINOLOGY_DIFF validation (detecting hallucinated terms).
        """
        parts = []
        for ref in tx.input_refs:
            value = state.get_variable(ref)
            if value:
                parts.append(str(value))
        if tx.apply_to:
            value = state.get_variable(tx.apply_to)
            if value:
                parts.append(json.dumps(value, default=str) if isinstance(value, dict) else str(value))
        return " ".join(parts)

    def _extract_input_terms(self, text: str) -> set[str]:
        """Extract domain terms from input text for TERMINOLOGY_DIFF validation.

        This builds the "known vocabulary" set from the original inputs. During
        contract validation, any terms in the LLM response that do NOT appear
        in this set are flagged as potentially hallucinated external knowledge
        (important for RETRIEVAL_BUDGET: NONE enforcement).

        The extraction is deliberately broad: all words > 3 chars plus uppercase
        acronyms. False positives are acceptable because the TERMINOLOGY_DIFF
        check only fires when CANNOT_INCLUDE specifies it.
        """
        import re
        terms: set[str] = set()
        # Lowercase words longer than 3 chars (filters out articles, prepositions)
        for word in text.lower().split():
            if len(word) > 3:
                terms.add(word.strip('",.:;()[]{}'))
        # Uppercase acronyms (e.g., "USMCA", "NAFTA") are domain-significant
        for match in re.finditer(r"\b[A-Z]{2,}\b", text):
            terms.add(match.group().lower())
        return terms

    def _print_budget_report(self, plan: ExecutionPlan) -> None:
        """Print the pre-execution budget report."""
        console.print(f"\n[bold]=== AIEX Execution Budget Report ===[/bold]")
        console.print(f"Program: {plan.program_id}")
        console.print(f"Source: {plan.source_file}")
        console.print(f"Transactions: {len(plan.transactions)}")

        if plan.parallel_groups:
            groups = ", ".join(
                f"{name}({len(members)})"
                for name, members in plan.parallel_groups.items()
            )
            console.print(f"Parallel groups: {groups}")

        table = Table(title="Transaction Budget")
        table.add_column("TX", style="cyan")
        table.add_column("Operation")
        table.add_column("Retrieval")
        table.add_column("Model")
        table.add_column("Neurons", justify="right")
        table.add_column("Est. Cost", justify="right")
        table.add_column("Est. Latency", justify="right")

        for budget in plan.budget:
            tx = next((t for t in plan.transactions if t.id == budget.transaction_id), None)
            if tx:
                table.add_row(
                    tx.id[:30],
                    tx.operation_type.value,
                    tx.retrieval_budget.value,
                    budget.model,
                    str(budget.neurons),
                    f"${budget.est_cost:.4f}",
                    f"{budget.est_latency_ms}ms",
                )

        console.print(table)
        console.print(f"\n  Total estimated cost:    [bold]${plan.total_cost:.4f}[/bold]")
        console.print(f"  vs. all-Opus baseline:   ${plan.vs_baseline_cost:.4f}")
        console.print(f"  [green]Savings: {plan.savings_pct}%[/green]")
        console.print(f"  Estimated latency:       {plan.total_latency_ms}ms")

    def _print_execution_summary(self, state: ProgramState) -> None:
        """Print post-execution summary."""
        console.print(f"\n[bold]=== Execution Summary ===[/bold]")

        committed = sum(
            1 for r in state.results.values()
            if r.status == TransactionStatus.COMMITTED
        )
        rolled_back = sum(
            1 for r in state.results.values()
            if r.status == TransactionStatus.ROLLED_BACK
        )

        console.print(f"  Transactions committed:  [green]{committed}[/green]")
        console.print(f"  Transactions rolled back: [red]{rolled_back}[/red]")

        total_latency = sum(
            r.dispatch_meta.latency_ms
            for r in state.results.values()
            if r.dispatch_meta
        )
        console.print(f"  Total latency:           {total_latency}ms")

        if state.execution_log:
            console.print(f"\n  Event log:")
            for event in state.execution_log[-10:]:
                evt = event.get("event", "?")
                data = event.get("data", {})
                msg = data.get("message", "") if isinstance(data, dict) else str(data)
                console.print(f"    [{evt}] {msg}")


def _schema_to_json_example(schema) -> str:
    """Convert an OutputFormatSchema to a JSON example string.

    Produces a concrete JSON example (not a JSON Schema) that the LLM can
    use as a template for its response. Each field is rendered with a
    placeholder value that communicates both the expected type and any
    constraints (e.g., enum values). This is more effective than abstract
    schema descriptions because LLMs are better at mimicking examples.
    """
    if not hasattr(schema, "fields"):
        return "{}"

    example: dict[str, Any] = {}
    for field_spec in schema.fields:
        if field_spec.field_type == "TEXT":
            if field_spec.constraint:
                example[field_spec.name] = f"<one of: {', '.join(field_spec.constraint)}>"
            else:
                example[field_spec.name] = "<text>"
        elif field_spec.field_type == "BOOLEAN":
            example[field_spec.name] = True
        elif field_spec.field_type == "NUMBER":
            example[field_spec.name] = 0
        elif field_spec.field_type == "QUALITATIVE":
            if field_spec.constraint:
                example[field_spec.name] = f"<one of: {', '.join(field_spec.constraint)}>"
            else:
                example[field_spec.name] = "<qualitative>"
        elif field_spec.field_type == "LIST":
            if field_spec.element_schema:
                inner = _schema_to_json_example(field_spec.element_schema)
                example[field_spec.name] = [f"<{field_spec.field_type or 'item'}>"]
            else:
                example[field_spec.name] = [f"<{field_spec.field_type or 'item'}>"]
        elif field_spec.field_type == "OBJECT":
            if field_spec.element_schema:
                example[field_spec.name] = json.loads(
                    _schema_to_json_example(field_spec.element_schema)
                )
            else:
                example[field_spec.name] = {}

    return json.dumps(example, indent=2)
