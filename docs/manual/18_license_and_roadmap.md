# Chapter 18: License and Roadmap

## License summary

AIEX is source-available, free for non-commercial use, and requires a separate commercial licence for any use inside a revenue-generating activity. For commercial enquiries, contact Edward Chalk at [edward@fleetingswallow.com](mailto:edward@fleetingswallow.com) or via [linkedin.com/in/edward-chalk](https://www.linkedin.com/in/edward-chalk/).

The authoritative text of the licence lives in `LICENSE.md` at the repository root. The preamble reads:

> **Source-available, free for non-commercial use. Commercial use requires a separate licence.**
>
> Copyright (c) 2026 Edward Chalk. All rights reserved.

What follows summarises the terms in plain language. If there is any conflict between this chapter and `LICENSE.md`, the LICENSE.md file governs.

## What is free

Under the non-commercial grant you have a perpetual, worldwide, royalty-free, non-exclusive licence to use, copy, modify, and distribute AIEX for non-commercial purposes. In practice that covers:

- **Personal projects and hobbyist work.** Building an AIEX-powered tool for yourself, writing blog posts, experimenting with the compiler.
- **Self-education.** Reading the source, modifying it to learn, running your own tests.
- **Academic research and teaching.** Including AIEX in a university course, citing it in a paper, using it as the runtime in published research.
- **Evaluation, prototyping, and proof-of-concept work.** Running AIEX inside a company to evaluate whether it fits a commercial use case -- this evaluation itself is free. Once you decide to ship it into production, the commercial grant kicks in.
- **Non-commercial open-source projects** that are themselves non-commercial and that preserve this licence on downstream distribution.

When you redistribute AIEX or a derivative under the non-commercial grant, the three conditions are: include `LICENSE.md` unmodified, preserve all copyright and attribution notices in the source files, and make clear to downstream recipients that commercial use requires separate permission.

## What requires a commercial licence

"Commercial use" in the licence covers four categories, paraphrased here:

- **A product or service offered for sale**, subscription, or any other paid access -- including a freemium tier where the free offering funnels users toward a paid one.
- **Internal use within a for-profit organisation or government agency** in the course of its commercial activities. Running AIEX in your company's data pipeline to make money falls here even if no customer ever touches AIEX directly.
- **Consulting work delivered to a paying client**, whether the client touches AIEX or only consumes its outputs.
- **Hosting AIEX as a service**, whether paid or free, where the hosting itself supports a commercial offering.

If any of the above applies, a separate written licence is required. Commercial licences are available on reasonable terms and can be tailored to the size, sector, and use case of the licensee. Contact details are at the top of this chapter and repeated at the end.

The disclaimer in `LICENSE.md` applies to both grants: the software is provided as-is, without warranty. Make your own judgement about whether AIEX is ready for your use case before betting a production system on it.

## Roadmap

The roadmap below lists directions, not commitments. Priorities shift based on what users actually need; contributions and commercial customers both influence ordering. Nothing here is promised by a date.

**Fixing P1-P7.** The [troubleshooting chapter](17_troubleshooting.md) documents seven known issues from the first live execution. Each has a mitigation today and a fix in mind: the JSON extraction hardening (P1), the sentence-transformers install path (P2), BERT-backed CANNOT_INCLUDE (P3), asyncio-based parallel dispatch (P4), cleaner retry prompt architecture (P5), more robust `.env` resolution (P6), and BERT-based semantic dependency analysis (P7). P4 is the largest by code footprint; P2 and P6 are the smallest. None of the fixes are architectural rewrites -- the runtime's shape is right, these are refinements within it.

**Richer model pool configurability.** Today the routing table, model pool, and budget tables are Python literals in `aiex/models/routing.py`. A TOML- or YAML-based configuration file would let operators pin specific reasoning types to specific models, add models from other providers, or run A/B tests on routing policies without touching source.

**Sandboxed EXECUTE_CODE.** Code blocks execute in-process today. A sandbox option -- subprocess-based at minimum, container-based for stronger isolation -- would let programs that take untrusted inputs use `EXECUTE_CODE` blocks without running arbitrary Python in the host.

**Broader language support in code blocks.** `EXECUTE_CODE` currently handles Python. A Lua or JavaScript backend would widen the set of deterministic-computation use cases without requiring Python dependencies in the host.

**Observability hooks.** OpenTelemetry-style span emission, one span per transaction and one per dispatch, so AIEX runs can slot into existing distributed-tracing infrastructure. The execution log has most of the same information today; this is about making that information reachable from standard tooling.

**Programmatic budget overrides at runtime.** A controlled way to say "for this run, escalate every DEDUCE to Opus" or "cap spend at ten cents and skip any transaction that would push over". The plan already knows the budget; the runtime just needs a hook.

**An `aiex-light` variant.** A stripped-down runtime for embedded and resource-constrained environments: the compiler and runtime without BERT, without Rich output, without the full template library. Suitable for edge deployments where all you want is the dispatch-validate-commit loop against a single model tier.

## Getting in touch

If you are building a business-rules system on top of AIEX -- loan eligibility, trade compliance, medical triage, regulatory screening, any place where qualitative judgement has to happen consistently at scale -- I would genuinely like to hear about it. Commercial licences are available on reasonable terms; so is conversation about where the project should go next.

- **Email:** [edward@fleetingswallow.com](mailto:edward@fleetingswallow.com)
- **LinkedIn:** [linkedin.com/in/edward-chalk](https://www.linkedin.com/in/edward-chalk/)

That is the end of the manual. Thank you for reading.
