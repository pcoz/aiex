"""AIEX compiler package -- transforms an AILang AST into an executable plan.

The compilation pipeline has three stages, each handled by its own module:

1. **analyzer** -- Builds a dependency graph from the parsed AST.  Operations
   become nodes; data-flow relationships become directed edges.  The analyzer
   also detects groups of operations that can run in parallel (no mutual
   dependencies), which the runtime uses to issue concurrent model calls.

2. **budget** -- Estimates the token cost, dollar cost, and latency for each
   operation *before* execution begins.  The estimates are based on the neuron
   count model from the AILang v0.8.2 specification, the DEPTH multiplier,
   the RETRIEVAL_BUDGET savings, and the per-tier pricing table.

3. **compiler** -- Orchestrates the full pipeline: analyze -> route -> contract
   -> budget -> emit.  Produces an ``ExecutionPlan`` containing one
   ``TransactionPlan`` per operation, ready for the runtime dispatcher.

Typical usage::

    from aiex.compiler import compile_ailang

    plan = compile_ailang(program, source_file="my_script.ailang")
"""

from aiex.compiler.compiler import compile_ailang
from aiex.compiler.analyzer import analyze_dependencies
from aiex.compiler.budget import calculate_budget

__all__ = ["compile_ailang", "analyze_dependencies", "calculate_budget"]
