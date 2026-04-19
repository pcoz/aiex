"""Dependency analyzer: determines execution order and parallelism.

This module is the first stage of the AIEX compilation pipeline.  It walks
the parsed AILang AST and builds a **dependency graph** -- a directed acyclic
graph (DAG) where:

* **Nodes** are individual reasoning operations (RETRIEVE, COMPARE, DEDUCE, etc.).
* **Edges** encode "must happen before" relationships: an edge from A to B means
  A cannot begin until B has finished, because A consumes B's output.

Two kinds of references create edges:

1. **Result references** -- When operation A's ``from_sources``, ``apply_to``,
   ``sources``, or ``against`` fields name another operation's ``result_name``,
   A depends on that operation.  This is a true data dependency.

2. **Variable references** -- ``SET`` variables are immutable inputs declared
   before any operation runs.  A reference to a SET variable does **not**
   create an edge because the value is already available -- no operation
   needs to produce it first.

After the DAG is built, the analyzer runs a topological sort and then
identifies **parallel groups**: sets of operations that share the same
dependency signature (identical prerequisite set) and therefore can be
dispatched concurrently by the runtime.

The resulting ``DependencyGraph`` is consumed by ``compiler.py``, which uses
the topological order to generate ``TransactionPlan`` objects and the parallel
groups to annotate which transactions can overlap.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from aiex.models.ast_nodes import (
    CodeBlock,
    OperationNode,
    Program,
    ReasoningContextBlock,
    ThenChain,
    WithContextBlock,
)


@dataclass
class DependencyGraph:
    """Directed acyclic graph of operation dependencies.

    ``nodes`` maps a unique transaction ID (e.g. ``"tx-3-comparison"``) to the
    AST ``OperationNode`` it represents.

    ``edges`` maps each transaction ID to the list of IDs it **depends on**
    (i.e. must wait for).  The direction is "from consumer to producer".

    ``parallel_groups`` is populated after construction by
    ``_identify_parallel_groups`` and maps a group label (``"parallel-1"``,
    ``"parallel-2"``, ...) to the list of transaction IDs that can run
    concurrently.
    """

    nodes: dict[str, OperationNode] = field(default_factory=dict)
    edges: dict[str, list[str]] = field(default_factory=dict)  # node -> depends_on
    parallel_groups: dict[str, list[str]] = field(default_factory=dict)

    def add_node(self, op_id: str, op: OperationNode) -> None:
        self.nodes[op_id] = op
        if op_id not in self.edges:
            self.edges[op_id] = []

    def add_edge(self, from_id: str, depends_on: str) -> None:
        if from_id not in self.edges:
            self.edges[from_id] = []
        self.edges[from_id].append(depends_on)

    def topological_sort(self) -> list[str]:
        """Return node IDs in dependency order (dependencies before dependents).

        Uses a recursive DFS post-order traversal.  Because we append *after*
        visiting all dependencies, the resulting list is in valid execution
        order -- every node appears after all of its prerequisites.
        """
        visited: set[str] = set()
        result: list[str] = []

        def visit(node_id: str) -> None:
            if node_id in visited:
                return
            visited.add(node_id)
            for dep in self.edges.get(node_id, []):
                visit(dep)
            result.append(node_id)

        for node_id in self.nodes:
            visit(node_id)
        return result


def analyze_dependencies(program: Program) -> DependencyGraph:
    """Analyze an AILang program AST and produce a dependency graph.

    Rules:
    - Operations that read from a variable SET earlier are independent of each other
      (SET variables are immutable inputs).
    - An operation that references another operation's result_name depends on it.
    - Operations inside a REASONING_CONTEXT are sequential (each may read prior digests).
    - THEN chains are sequential by definition.
    - Operations in a WITH REALITY_CONTEXT that don't reference each other's results
      are independent (the context just applies routing/constraints).
    """
    graph = DependencyGraph()
    # Collect SET variable names so we can distinguish them from result
    # references.  SET variables are immutable inputs available from the
    # start -- referencing one should NOT create a dependency edge.
    variable_names = {v.name for v in program.variables}
    # Maps an operation's result_name to its transaction ID so that later
    # operations referencing that name can look up the producer.
    result_names: dict[str, str] = {}  # result_name -> op_id
    op_counter = 0

    def get_op_id(op: OperationNode) -> str:
        """Generate a stable, human-readable transaction ID.

        Format: ``tx-<sequence>-<result_name>``.  The sequence number
        guarantees uniqueness even if two operations share a result_name
        (which would be a user error but should not crash the compiler).
        """
        nonlocal op_counter
        op_counter += 1
        name = op.result_name or f"op_{op_counter}"
        return f"tx-{op_counter}-{name}"

    def get_references(op: OperationNode) -> set[str]:
        """Collect every name this operation reads from.

        This includes FROM sources, APPLY TO targets, SOURCES, and AGAINST
        references.  The caller checks each name against ``result_names``
        to decide whether it creates a dependency edge (result reference)
        or can be ignored (SET variable / literal).
        """
        refs: set[str] = set()
        for src in op.from_sources:
            refs.add(src)
        if op.apply_to:
            refs.add(op.apply_to)
        for src in op.sources:
            refs.add(src)
        if op.against:
            refs.add(op.against)
        return refs

    # ``barrier_deps`` records the IDs of CodeBlocks already registered.
    # CodeBlocks act as ordering barriers because they may mutate AILang
    # variable state in arbitrary ways (any prior variable can be read,
    # any new variable can be written).  Without flow analysis of the
    # opaque code body, the safe rule is: every operation that follows a
    # CodeBlock must wait for it to complete.  This trades parallelism for
    # correctness around code blocks; pure-reasoning sections of a program
    # retain full parallelism.
    barrier_deps: list[str] = []

    def process_operation(op: OperationNode, context_deps: list[str] | None = None) -> str:
        """Register a single operation in the graph and wire up its edges.

        ``context_deps`` carries structural dependencies inherited from
        enclosing blocks (e.g. the previous operation inside a
        REASONING_CONTEXT or a THEN chain).  These are added on top of
        any data-flow edges discovered via ``get_references`` and any
        CodeBlock barriers that precede this op in source order.
        """
        op_id = get_op_id(op)
        graph.add_node(op_id, op)

        # Only create edges for references that point to another
        # operation's result -- SET variable references are ignored
        # because those values are available before execution starts.
        refs = get_references(op)
        for ref in refs:
            if ref in result_names:
                graph.add_edge(op_id, result_names[ref])

        # Structural dependencies from enclosing context blocks
        # (REASONING_CONTEXT sequencing, THEN chain ordering, etc.)
        if context_deps:
            for dep in context_deps:
                graph.add_edge(op_id, dep)

        # Wait for every prior CodeBlock to commit before running.  The
        # code may have mutated state this operation reads.
        for dep in barrier_deps:
            graph.add_edge(op_id, dep)

        # Register this operation's result_name so future operations
        # can discover it and create a dependency edge.
        if op.result_name:
            result_names[op.result_name] = op_id

        return op_id

    def process_code_block(cb: CodeBlock, context_deps: list[str] | None = None) -> str:
        """Register a CodeBlock as an ordering barrier.

        A CodeBlock has no declared inputs/outputs in the AILang grammar
        (the body is opaque to the compiler), so we apply the conservative
        rule: it depends on every transaction registered so far, and every
        subsequent transaction will depend on it (via ``barrier_deps``).
        This guarantees the variable-state contract: by the time the code
        runs, every prior reasoning op has committed, and every following
        reasoning op sees the code's writes.
        """
        nonlocal op_counter
        op_counter += 1
        cb_id = f"tx-{op_counter}-codeblock"
        graph.add_node(cb_id, cb)
        # Depend on every transaction already registered: the code may
        # read any AILang variable they produced.
        for prev_id in list(graph.nodes.keys()):
            if prev_id != cb_id:
                graph.add_edge(cb_id, prev_id)
        # Inherit structural deps from enclosing context blocks.
        if context_deps:
            for dep in context_deps:
                graph.add_edge(cb_id, dep)
        # Register as a barrier so every future operation waits for us.
        barrier_deps.append(cb_id)
        return cb_id

    def process_item(item, context_deps: list[str] | None = None) -> list[str]:
        """Process a program item, return list of op_ids created.

        Dispatches based on AST node type to apply the correct dependency
        semantics for each block kind.
        """
        if isinstance(item, OperationNode):
            # Standalone operation -- only data-flow and inherited deps.
            return [process_operation(item, context_deps)]

        elif isinstance(item, CodeBlock):
            # Code block -- ordering barrier (see process_code_block).
            return [process_code_block(item, context_deps)]

        elif isinstance(item, ThenChain):
            # THEN chains impose strict sequential ordering: each step
            # depends on the previous one, forming a linear chain of
            # edges regardless of whether data actually flows between them.
            chain_ids = []
            prev_id = None
            for chain_op in item.operations:
                deps = list(context_deps or [])
                if prev_id:
                    deps.append(prev_id)
                op_id = process_operation(chain_op, deps)
                chain_ids.append(op_id)
                prev_id = op_id
            return chain_ids

        elif isinstance(item, WithContextBlock):
            # WITH REALITY_CONTEXT blocks scope routing/constraints but
            # do NOT impose ordering.  Operations inside are independent
            # of each other unless they share data-flow references.
            # We simply pass through the inherited context_deps unchanged.
            ids = []
            for sub_item in item.operations:
                ids.extend(process_item(sub_item, context_deps))
            return ids

        elif isinstance(item, ReasoningContextBlock):
            # REASONING_CONTEXT blocks are sequential: each operation
            # may consume the digest emitted by the previous one, so we
            # chain them by adding the prior operation as a dependency.
            # This mirrors the AILang spec's rule that reasoning contexts
            # enforce step-by-step ordering.
            ids = []
            prev_id = None
            for sub_item in item.operations:
                deps = list(context_deps or [])
                if prev_id:
                    deps.append(prev_id)
                sub_ids = process_item(sub_item, deps)
                ids.extend(sub_ids)
                if sub_ids:
                    prev_id = sub_ids[-1]
            return ids
        return []

    for item in program.operations:
        process_item(item)

    # After all operations are registered, run a topological sort and
    # then discover which operations can execute concurrently.
    all_ids = graph.topological_sort()
    _identify_parallel_groups(graph, all_ids)

    return graph


def _identify_parallel_groups(graph: DependencyGraph, sorted_ids: list[str]) -> None:
    """Find operations that can run in parallel (no mutual dependencies).

    The heuristic: two operations are candidates for parallel execution if
    they have the **exact same dependency signature** (i.e. they wait on
    the same set of predecessors).  This is a necessary but not sufficient
    condition -- we additionally verify that no candidate depends on any
    other candidate in the group (which would happen if an operation was
    mistakenly given the same signature due to a transitive edge).

    Groups of 2+ truly independent operations are recorded in
    ``graph.parallel_groups`` so the runtime can dispatch them concurrently
    and the budget calculator can compute wall-clock latency correctly
    (max-of-group rather than sum).
    """
    group_counter = 0

    # Build reverse dependency map (unused here but constructed for
    # potential future use in smarter grouping strategies).
    depended_by: dict[str, set[str]] = {nid: set() for nid in graph.nodes}
    for nid, deps in graph.edges.items():
        for dep in deps:
            depended_by[dep].add(nid)

    # Group operations by their dependency signature -- a sorted tuple of
    # the IDs they depend on.  Operations with identical signatures are at
    # the same "depth" in the DAG and are candidates for parallel execution.
    dep_signature: dict[tuple[str, ...], list[str]] = {}
    for nid in sorted_ids:
        sig = tuple(sorted(graph.edges.get(nid, [])))
        dep_signature.setdefault(sig, []).append(nid)

    for sig, members in dep_signature.items():
        if len(members) > 1:
            # Safety check: confirm no member directly depends on another.
            # This guards against edge cases where two operations share the
            # same signature but one was added as a dependency of the other
            # through a different path.
            member_set = set(members)
            truly_parallel = []
            for m in members:
                deps_of_m = set(graph.edges.get(m, []))
                if not deps_of_m.intersection(member_set):
                    truly_parallel.append(m)
            if len(truly_parallel) > 1:
                group_counter += 1
                group_name = f"parallel-{group_counter}"
                graph.parallel_groups[group_name] = truly_parallel
