"""Main compiler: AILang AST -> AIEX ExecutionPlan.

This is the orchestrator module that ties together all compilation stages.
Given a parsed AILang ``Program`` (AST), it produces a fully resolved
``ExecutionPlan`` ready for the runtime dispatcher.

The compilation pipeline has five logical steps:

1. **Analyze** -- Build a dependency graph (``analyzer.py``).  The graph
   encodes which operations must run before which others and identifies
   groups that can execute in parallel.

2. **Route** -- For each operation, call ``resolve_routing()`` to determine
   which model tier should handle it.  Routing considers the reasoning type
   (RETRIEVE -> BERT, DEDUCE -> Sonnet, etc.), DEPTH (EXHAUSTIVE -> Opus),
   RETRIEVAL_BUDGET, and whether the output requires free-form text
   (which forces escalation away from BERT).

3. **Contract** -- Generate a ``TransactionContract`` from the operation's
   ``BoundarySpec``.  MUST_INCLUDE items become CRITICAL checks (the
   response is rolled back if they are missing); CANNOT_INCLUDE items
   become MINOR warnings (flagged but not blocking, because full semantic
   enforcement requires a BERT pass at runtime).  Retrieval compliance
   checks are also added when RETRIEVAL_BUDGET is NONE or MINIMAL.

4. **Budget** -- Call ``calculate_budget()`` to estimate tokens, cost, and
   latency for each transaction based on its reasoning type, depth, retrieval
   budget, and routed model tier.

5. **Emit** -- Assemble the ``ExecutionPlan`` with all transactions, their
   parallel group assignments, aggregated cost/latency totals, and a
   savings comparison against a naive all-Opus baseline.
"""

from __future__ import annotations

from aiex.compiler.analyzer import analyze_dependencies
from aiex.compiler.budget import calculate_budget
from aiex.models.ast_nodes import (
    CodeBlock,
    DepthLevel,
    OperationNode,
    Program,
    ReasoningType,
    RetrievalBudget,
    StyleModifier,
    ThenChain,
)
from aiex.models.plan import (
    BudgetEstimate,
    ContractCheck,
    ExecutionPlan,
    ModelTier,
    RoutingDecision,
    Severity,
    TransactionContract,
    TransactionPlan,
)
from aiex.models.routing import resolve_routing


def compile_ailang(program: Program, source_file: str = "<stdin>") -> ExecutionPlan:
    """Compile a parsed AILang program into an AIEX execution plan.

    This is the main entry point for the compiler.  It walks the dependency
    graph in topological order, producing one ``TransactionPlan`` per
    operation, then calculates aggregate cost and latency metrics.
    """
    # ---- Step 1: Analyze dependencies ----
    # Build the DAG of operations and detect parallelism opportunities.
    graph = analyze_dependencies(program)
    sorted_ids = graph.topological_sort()

    # Index the program's REALITY_CONTEXT definitions by name so each
    # operation that runs under a WITH REALITY_CONTEXT block can have its
    # frame (worldview / assumptions / value system) rendered and attached.
    reality_contexts_by_name = {rc.name: rc for rc in program.reality_contexts}

    # ---- Step 2-4: Route, contract, and budget each operation ----
    # We iterate in topological order so that every operation's
    # dependencies have already been processed.
    transactions: list[TransactionPlan] = []
    budgets: list[BudgetEstimate] = []

    for op_id in sorted_ids:
        op = graph.nodes[op_id]

        # -- CodeBlock branch: deterministic local execution, no LLM --
        # Code blocks are compiled into a TransactionPlan with the body
        # carried inline.  No model is selected, no contract is generated
        # (Python errors are deterministic, not probabilistic), and the
        # budget shows $0 cost with a fixed local-execution latency.
        if isinstance(op, CodeBlock):
            tx, budget = _compile_code_block(op, op_id, graph)
            transactions.append(tx)
            budgets.append(budget)
            continue

        # -- Route: decide which model tier handles this operation --
        # Qualitative text in the output schema forces escalation away
        # from BERT (which cannot generate prose).
        output_has_qualitative = _output_has_qualitative_text(op)
        routing = resolve_routing(
            op_type=op.reasoning_type,
            depth=op.depth,
            retrieval=op.retrieval_budget,
            output_has_qualitative_text=output_has_qualitative,
        )

        # -- Contract: translate MUST_INCLUDE / CANNOT_INCLUDE into checks --
        contract = _generate_contract(op)

        # -- Parallel group: find if this operation belongs to one --
        parallel_group = None
        for group_name, members in graph.parallel_groups.items():
            if op_id in members:
                parallel_group = group_name
                break

        # -- Reality context: if this op runs inside a WITH REALITY_CONTEXT
        # block, render its frame so the runtime can inject it into the prompt.
        ctx_name = graph.node_contexts.get(op_id)
        reality_ctx = reality_contexts_by_name.get(ctx_name) if ctx_name else None
        reality_frame = _render_reality_frame(reality_ctx) if reality_ctx else None

        # -- Build the TransactionPlan that maps 1:1 with the OperationNode --
        # This is the compiled representation: it carries the original AST
        # fields plus routing, contract, dependency, and parallelism metadata.
        tx = TransactionPlan(
            id=op_id,
            name=op.result_name or op_id,
            operation_type=op.reasoning_type,
            style=op.style,
            depth=op.depth,
            retrieval_budget=op.retrieval_budget,
            routing=routing,
            contract=contract,
            input_refs=list(op.from_sources),
            output_ref=op.result_name,
            parallel_group=parallel_group,
            depends_on=graph.edges.get(op_id, []),
            apply_to=op.apply_to,
            against=op.against,
            sources=list(op.sources),
            dimensions=list(op.dimensions),
            direction=op.direction,
            emit_digest=bool(op.emit_digest),
            reality_context_name=ctx_name,
            reality_frame=reality_frame,
        )
        transactions.append(tx)

        # -- Budget: estimate tokens, cost, and latency --
        # num_input_sources counts FROM sources + the APPLY TO target (if any),
        # which determines how many premise tokens to estimate.
        budget = calculate_budget(
            tx_id=op_id,
            reasoning_type=op.reasoning_type,
            depth=op.depth,
            retrieval=op.retrieval_budget,
            routing=routing,
            num_input_sources=len(op.from_sources) + (1 if op.apply_to else 0),
        )
        budgets.append(budget)

    # ---- Step 5: Emit -- aggregate totals and produce the final plan ----
    total_cost = sum(b.est_cost for b in budgets)

    # Baseline comparison: what would this program cost if every operation
    # ran on Opus with no routing optimization?  This lets the user see
    # how much the AIEX compiler saves through tier routing and retrieval
    # budget tuning.
    baseline_cost = _calculate_baseline_cost(transactions)

    # Wall-clock latency: parallel groups count as max(group) not sum(group).
    total_latency = _calculate_total_latency(transactions, budgets, graph.parallel_groups)

    savings_pct = round((1 - total_cost / baseline_cost) * 100, 1) if baseline_cost > 0 else 0.0

    program_id = f"{source_file.replace('/', '-').replace('.ailang', '')}"

    return ExecutionPlan(
        program_id=program_id,
        source_file=source_file,
        transactions=transactions,
        parallel_groups=graph.parallel_groups,
        budget=budgets,
        total_cost=round(total_cost, 4),
        total_latency_ms=total_latency,
        vs_baseline_cost=round(baseline_cost, 4),
        savings_pct=savings_pct,
    )


def _render_reality_frame(rc) -> str:
    """Render a REALITY_CONTEXT into an authoritative system-prompt preamble.

    The runtime prepends this to the system prompt of every operation that
    runs inside the corresponding ``WITH REALITY_CONTEXT`` block, so the
    declared worldview, assumptions, and value system actually condition the
    model's reasoning (rather than being parsed and discarded).  Keeping the
    rendering here means it is computed once at compile time and is visible
    in the plan for audit.
    """
    lines = [
        f"=== REALITY CONTEXT: {rc.name} ===",
        "Reason strictly within this frame; treat it as authoritative for this operation.",
    ]
    if rc.worldview:
        lines.append(f"WORLDVIEW: {rc.worldview}")
    if rc.assumptions:
        lines.append("ASSUMPTIONS (hold these as given):")
        lines.extend(f"  - {a}" for a in rc.assumptions)
    if rc.value_system:
        lines.append("VALUE SYSTEM (let these govern judgement):")
        lines.extend(f"  - {v}" for v in rc.value_system)
    lines.append("=== END REALITY CONTEXT ===")
    return "\n".join(lines)


def _compile_code_block(cb: CodeBlock, op_id: str, graph) -> tuple[TransactionPlan, BudgetEstimate]:
    """Compile an EXECUTE_CODE block into a TransactionPlan + BudgetEstimate.

    Code blocks are deterministic local execution.  The plan carries the
    raw body; the runtime engine detects the ``code_block_body`` field
    and dispatches to the local executor instead of the LLM.

    Routing: a sentinel ``RoutingDecision`` is emitted with tier
    ``ModelTier.LOCAL_CODE`` so the budget table can label it clearly.
    Cost is fixed at $0 (no tokens), and latency is a conservative
    100ms estimate covering process overhead for typical business-rule
    computations.  No contract is generated -- code-block correctness is
    enforced by the language interpreter itself, not by post-hoc validation.
    """
    # Find this op's parallel group (almost always None for code blocks
    # because their barrier semantics prevent parallel scheduling, but we
    # honour whatever the analyzer produced).
    parallel_group = None
    for group_name, members in graph.parallel_groups.items():
        if op_id in members:
            parallel_group = group_name
            break

    # Sentinel routing -- LOCAL_CODE tier signals "no LLM dispatch".
    # operation_type is INTELLIGENTLY purely as a default; the runtime
    # never inspects it for code blocks (it checks code_block_body first).
    routing = RoutingDecision(
        operation_type=ReasoningType.INTELLIGENTLY,
        base_tier=ModelTier.LOCAL_CODE,
        escalated_to=None,
        escalation_reason=f"EXECUTE_CODE block ({cb.language}) -- local execution",
        template_name="code_execution",
        temperature=0.0,
        neuron_budget=0,
        max_output_tokens=0,
    )

    tx = TransactionPlan(
        id=op_id,
        name=f"code_block_{cb.language}",
        # operation_type is required by the model; we reuse INTELLIGENTLY
        # as the most generic value.  The runtime checks code_block_body
        # first, so this enum value never reaches the dispatcher.
        operation_type=ReasoningType.INTELLIGENTLY,
        style=None,
        depth=DepthLevel.STANDARD,
        retrieval_budget=RetrievalBudget.NONE,
        routing=routing,
        contract=None,
        input_refs=[],
        output_ref=None,
        parallel_group=parallel_group,
        depends_on=graph.edges.get(op_id, []),
        code_block_body=cb.body,
        code_block_language=cb.language,
    )

    # Budget: $0 cost, fixed local-execution latency estimate.
    budget = BudgetEstimate(
        transaction_id=op_id,
        model="local-code",
        neurons=0,
        est_tokens_in=0,
        est_tokens_out=0,
        est_cost=0.0,
        est_latency_ms=100,
    )
    return tx, budget


def _output_has_qualitative_text(op: OperationNode) -> bool:
    """Check if OUTPUT_FORMAT contains fields requiring text generation.

    This matters for routing: BERT-class models cannot produce free-form
    prose, so if the output schema includes TEXT or QUALITATIVE fields,
    the routing logic must escalate to a generative model.
    """
    if not op.boundary or not op.boundary.output_format:
        return False
    schema = op.boundary.output_format
    return _schema_has_text_fields(schema)


def _schema_has_text_fields(schema) -> bool:
    """Recursively check if a schema has TEXT or LIST OF TEXT fields.

    Walks the nested ``OutputFormatSchema`` tree.  Returns True as soon as
    any leaf field is TEXT, QUALITATIVE, or LIST OF TEXT.
    """
    if hasattr(schema, "fields"):
        for f in schema.fields:
            if f.field_type in ("TEXT", "QUALITATIVE"):
                return True
            if f.is_list and f.element_schema and f.element_schema.field_type == "TEXT":
                return True
            if f.fields:
                if _schema_has_text_fields(f):
                    return True
            if f.element_schema and _schema_has_text_fields(f.element_schema):
                return True
    return False


def _generate_contract(op: OperationNode) -> TransactionContract:
    """Generate a validation contract from an operation's boundary spec.

    The contract is a set of checks the runtime runs against the model's
    response to verify it meets the AILang program's constraints:

    - **MUST_INCLUDE** items become CRITICAL-severity semantic scans.
      If the response is missing required content, the runtime rolls back
      and retries (up to ``max_retries``).

    - **CANNOT_INCLUDE** items become MINOR-severity warnings.  They are
      flagged but not blocking because full semantic exclusion detection
      requires a BERT terminology diff pass, which is a runtime concern.

    - **Retrieval compliance** checks are auto-generated when
      RETRIEVAL_BUDGET is NONE or MINIMAL.  These detect if the model
      smuggled in external knowledge it was not supposed to use.
    """
    must_checks: list[ContractCheck] = []
    cannot_checks: list[ContractCheck] = []

    if op.boundary:
        # Each MUST_INCLUDE item becomes a CRITICAL check -- the response
        # is considered invalid (rolled back) if it is missing.
        for item in op.boundary.must_include:
            must_checks.append(ContractCheck(
                name=f"must-include-{item}",
                detection_method="SEMANTIC_SCAN",
                target_field="response",
                expected_content=item,
                severity=Severity.CRITICAL,
                message=f"Missing required content: {item}",
            ))

        # CANNOT_INCLUDE items are MINOR (warn-only) at compile time.
        # Full enforcement would require a BERT semantic scan at runtime,
        # so the compiler conservatively flags rather than blocks.
        for item in op.boundary.cannot_include:
            cannot_checks.append(ContractCheck(
                name=f"cannot-include-{item}",
                detection_method="SEMANTIC_SCAN",
                target_field="response",
                expected_content=item,
                severity=Severity.MINOR,
                message=f"Prohibited content detected: {item}",
            ))

    # Auto-generated retrieval compliance checks.
    # When RETRIEVAL_BUDGET is NONE, the model should not reference any
    # external knowledge.  When MINIMAL, it should not go deep.
    # These use specialized detection methods (TERMINOLOGY_DIFF and
    # KNOWLEDGE_DEPTH_SCAN) that the runtime resolves with a BERT pass.
    retrieval_level = op.retrieval_budget
    if retrieval_level == RetrievalBudget.NONE:
        cannot_checks.append(ContractCheck(
            name="retrieval-compliance-none",
            detection_method="TERMINOLOGY_DIFF",
            target_field="response",
            expected_content="external_knowledge",
            severity=Severity.MINOR,
            message="RETRIEVAL_BUDGET: NONE -- flagged for review",
        ))
    elif retrieval_level == RetrievalBudget.MINIMAL:
        cannot_checks.append(ContractCheck(
            name="retrieval-compliance-minimal",
            detection_method="KNOWLEDGE_DEPTH_SCAN",
            target_field="response",
            expected_content="deep_external_knowledge",
            severity=Severity.MINOR,
            message="RETRIEVAL_BUDGET: MINIMAL may have exceeded terminology resolution",
        ))

    output_schema = None
    if op.boundary and op.boundary.output_format:
        output_schema = op.boundary.output_format

    return TransactionContract(
        must_include_checks=must_checks,
        cannot_include_checks=cannot_checks,
        output_format_schema=output_schema,
        retrieval_compliance_level=op.retrieval_budget,
        max_retries=2,
    )


def _calculate_baseline_cost(transactions: list[TransactionPlan]) -> float:
    """Calculate what it would cost if everything ran on Opus with full retrieval.

    This is the "naive" baseline: every operation goes to Opus (the most
    expensive tier) with average token counts (3K in, 1K out).  The AIEX
    compiler's value proposition is the savings percentage between this
    baseline and the optimized plan's actual cost.
    """
    opus_cost_in = 0.015
    opus_cost_out = 0.075
    total = 0.0
    for tx in transactions:
        # Code blocks have no LLM baseline -- they would cost $0 in any
        # plan, optimized or naive.  Excluding them keeps the savings
        # percentage honest.
        if tx.code_block_body is not None:
            continue
        est_in = 3000  # average input tokens per operation
        est_out = 1000  # average output tokens per operation
        total += (est_in / 1000 * opus_cost_in) + (est_out / 1000 * opus_cost_out)
    return total


def _calculate_total_latency(
    transactions: list[TransactionPlan],
    budgets: list[BudgetEstimate],
    parallel_groups: dict[str, list[str]],
) -> int:
    """Calculate total latency accounting for parallel execution.

    Sequential operations contribute their full latency to the total.
    For parallel groups, only the slowest member counts (since they all
    run concurrently).  Total = sum(sequential) + sum(max-per-group).
    """
    budget_map = {b.transaction_id: b for b in budgets}
    tx_map = {tx.id: tx for tx in transactions}

    # Build a reverse lookup: transaction ID -> group name (if any).
    in_group: dict[str, str] = {}
    for group_name, members in parallel_groups.items():
        for m in members:
            in_group[m] = group_name

    # Accumulate latency: parallel members contribute only their group's
    # max; everything else adds directly to the sequential total.
    group_latencies: dict[str, int] = {}
    sequential_latency = 0

    for tx in transactions:
        latency = budget_map[tx.id].est_latency_ms if tx.id in budget_map else 4000
        if tx.id in in_group:
            group = in_group[tx.id]
            group_latencies[group] = max(group_latencies.get(group, 0), latency)
        else:
            sequential_latency += latency

    return sequential_latency + sum(group_latencies.values())
