# AIEX: AI Execution Language Specification

**Version:** 0.1.0 (Draft)
**Date:** 2026-04-16
**Lineage:** COBOL-descendant, purpose-built for AILang execution
**Design Principle:** Declarative intent with procedural execution

---

## 1. Introduction

### 1.1 What AIEX Is

AIEX (AI Execution Language) is a domain-specific language for executing AILang programs against LLM backends. It sits between AILang (the programmer-facing reasoning specification language) and the LLM API layer.

```
AILang Program
     |
     v
[AIEX Compiler] -- parses AILang, produces AIEX execution plan
     |
     v
AIEX Program
     |
     v
[AIEX Runtime] -- executes transactions, dispatches to LLM, validates boundaries
     |
     v
LLM API Calls (Claude, GPT, etc.)
```

### 1.2 Why AIEX Exists

AILang deliberately specifies WHAT reasoning to perform without specifying HOW. This leaves a gap:

- How does `MUST_INCLUDE: [logical_chain]` become a system prompt instruction?
- How does `RETRIEVAL_BUDGET: MINIMAL` translate to API parameters?
- How does `SUPPRESS: [SYNTHESIZE]` get enforced post-response?
- How does a `REASONING_CONTEXT` accumulator manage digest state across operations?

Python frameworks answer these questions with ad-hoc function calls and middleware chains. AIEX answers them with a **rigid, auditable, transaction-based execution model** where every operation is a declared contract that is dispatched, validated, and either committed or rolled back.

### 1.3 The COBOL Inheritance

AIEX inherits from COBOL the principle that **program structure should mirror data processing reality**. In COBOL, the DIVISION structure mirrors how batch processing actually works: identify the program, configure the environment, define the data, execute the procedures. In AIEX, the DIVISION structure mirrors how AI reasoning actually works: identify the program, configure the engine, define the state, execute the reasoning transactions.

What AIEX does NOT inherit: column-based formatting, hardware-coupled environment configuration, sequential file I/O, MOVE/COMPUTE verbosity. AIEX is a modern language with clean syntax that happens to think like a mainframe transaction processor.

### 1.4 Core Design Principles

1. **Every operation is a transaction.** It has declared inputs, declared outputs, boundary contracts, and it either commits (boundaries met) or rolls back (boundaries violated).

2. **State is explicit and auditable.** No hidden variables. No implicit context. Every piece of state lives in a named storage location within a declared DIVISION.

3. **Contracts are first-class.** MUST_INCLUDE, CANNOT_INCLUDE, OUTPUT_FORMAT, CONSTRAIN, SUPPRESS -- these are not hints to the LLM. They are post-dispatch validation rules enforced by the runtime. The LLM's response either passes the contract or the transaction fails.

4. **Dispatch is parameterized, not magical.** When the runtime sends a request to the LLM, every parameter is determined by the ROUTING TABLE and the operation's declared properties. There is no "just send it and hope."

5. **Fallback is declared, not improvised.** When a RETRIEVAL_BUDGET can't be honored, when an ADAPTIVELY adjustment exceeds its range, when a boundary validation fails -- the fallback behavior is specified in the program, not invented at runtime.

---

## 2. Program Structure

An AIEX program consists of exactly five DIVISIONS, in this order. All five are required. The order is fixed.

```aiex
PROGRAM DIVISION.
    [program metadata]

ENGINE DIVISION.
    [LLM configuration, routing tables]

STATE DIVISION.
    [variables, contexts, accumulators]

CONTRACT DIVISION.
    [boundary definitions, validation rules]

EXECUTION DIVISION.
    [transaction pipeline]
```

### 2.1 Why Five Divisions (Not Four Like COBOL)

COBOL has four divisions: IDENTIFICATION, ENVIRONMENT, DATA, PROCEDURE. AIEX splits COBOL's implicit contract enforcement (scattered across PROCEDURE DIVISION paragraphs as IF-checks) into an explicit CONTRACT DIVISION. This reflects the reality that in AI execution, **boundary enforcement is the primary value proposition** -- it's not a side effect of procedure logic, it's the reason the execution framework exists.

---

## 3. PROGRAM DIVISION

Identifies the program and declares metadata. Equivalent to COBOL's IDENTIFICATION DIVISION.

```aiex
PROGRAM DIVISION.
    PROGRAM-ID.         trade-compliance-analysis.
    SOURCE-SPEC.        AILang v0.8.2.
    COMPILED-FROM.      "trade_compliance.ailang".
    COMPILED-AT.        2026-04-16T10:30:00Z.
    TARGET-ENGINE.      claude-opus-4-6.
    AUTHOR.             ailang-compiler v0.1.
    DESCRIPTION.        "Analyzes international trade compliance
                         using graduated retrieval budgets across
                         three deduction passes".
```

### 3.1 Required Fields

| Field | Purpose |
|---|---|
| `PROGRAM-ID` | Unique identifier for this execution plan |
| `SOURCE-SPEC` | AILang version the source program was written in |
| `COMPILED-FROM` | Path to the source AILang file |
| `TARGET-ENGINE` | LLM model ID to dispatch operations to |

### 3.2 Optional Fields

| Field | Purpose |
|---|---|
| `COMPILED-AT` | Timestamp of compilation |
| `AUTHOR` | Compiler version or human author |
| `DESCRIPTION` | Human-readable description |
| `TRACE-LEVEL` | Logging verbosity: NONE, SUMMARY, FULL, DEBUG |

---

## 4. ENGINE DIVISION

Configures the LLM backend and defines the routing tables that map reasoning types to dispatch parameters. Equivalent to COBOL's ENVIRONMENT DIVISION, but instead of configuring printers and tape drives, it configures API endpoints and neuron routing.

```aiex
ENGINE DIVISION.

    CONNECTION SECTION.
        API-ENDPOINT.       "https://api.anthropic.com/v1/messages".
        API-KEY-SOURCE.     ENV "ANTHROPIC_API_KEY".
        MODEL-ID.           "claude-opus-4-6".
        MAX-TOKENS.         8192.
        TIMEOUT-MS.         120000.
        RETRY-POLICY.       3 ATTEMPTS WITH BACKOFF 1000 2000 4000.

    ROUTING SECTION.
        ROUTING-TABLE reasoning-routes.
            WHEN RETRIEVE:
                SYSTEM-PROMPT-TEMPLATE     "retrieve-facts".
                TEMPERATURE                0.1.
                NEURON-BUDGET              865.
                MAX-OUTPUT-TOKENS          512.
            WHEN COMPARE:
                SYSTEM-PROMPT-TEMPLATE     "compare-rank".
                TEMPERATURE                0.3.
                NEURON-BUDGET              1050.
                MAX-OUTPUT-TOKENS          1024.
            WHEN TRACE:
                SYSTEM-PROMPT-TEMPLATE     "trace-causal".
                TEMPERATURE                0.4.
                NEURON-BUDGET              1100.
                MAX-OUTPUT-TOKENS          2048.
            WHEN DEDUCE:
                SYSTEM-PROMPT-TEMPLATE     "deduce-logical".
                TEMPERATURE                0.2.
                NEURON-BUDGET              1200.
                MAX-OUTPUT-TOKENS          1536.
            WHEN SYNTHESIZE:
                SYSTEM-PROMPT-TEMPLATE     "synthesize-integrate".
                TEMPERATURE                0.5.
                NEURON-BUDGET              1400.
                MAX-OUTPUT-TOKENS          2048.
            WHEN WEIGH:
                SYSTEM-PROMPT-TEMPLATE     "weigh-authority".
                TEMPERATURE                0.3.
                NEURON-BUDGET              1531.
                MAX-OUTPUT-TOKENS          2048.
            WHEN UNTYPED:
                SYSTEM-PROMPT-TEMPLATE     "intelligent-general".
                TEMPERATURE                0.4.
                NEURON-BUDGET              1531.
                MAX-OUTPUT-TOKENS          2048.
        END-TABLE.

    RETRIEVAL-BUDGET SECTION.
        BUDGET-TABLE retrieval-budgets.
            WHEN NONE:
                RETRIEVAL-NEURONS          0.
                PROMPT-MODIFIER            "Use ONLY the provided data. Do not
                                            recall any external knowledge.".
                FALLBACK-TO                MINIMAL.
            WHEN MINIMAL:
                RETRIEVAL-NEURONS          200.
                PROMPT-MODIFIER            "You may resolve terminology and basic
                                            definitions but do not inject detailed
                                            external knowledge.".
                FALLBACK-TO                STANDARD.
            WHEN STANDARD:
                RETRIEVAL-NEURONS          865.
                PROMPT-MODIFIER            "".
                FALLBACK-TO                NONE.
            WHEN FULL:
                RETRIEVAL-NEURONS          865.
                PROMPT-MODIFIER            "".
                FALLBACK-TO                NONE.
        END-TABLE.

    DEPTH SECTION.
        DEPTH-TABLE depth-levels.
            WHEN SHALLOW:
                MAX-PASSES                 1.
                TOKEN-MULTIPLIER           0.5.
                PROMPT-SUFFIX              "Provide a brief, surface-level analysis.".
            WHEN STANDARD:
                MAX-PASSES                 1.
                TOKEN-MULTIPLIER           1.0.
                PROMPT-SUFFIX              "".
            WHEN THOROUGH:
                MAX-PASSES                 1.
                TOKEN-MULTIPLIER           1.5.
                PROMPT-SUFFIX              "Include contributing factors and secondary
                                            causes beyond the primary chain.".
            WHEN DEEP:
                MAX-PASSES                 3.
                TOKEN-MULTIPLIER           2.5.
                PROMPT-SUFFIX              "Perform multi-pass mechanism-level analysis.
                                            Identify systemic vulnerabilities.".
            WHEN EXHAUSTIVE:
                MAX-PASSES                 UNLIMITED.
                TOKEN-MULTIPLIER           4.0.
                PROMPT-SUFFIX              "Leave no stone unturned. Explore all causal
                                            branches, counterfactuals, and hypotheses.".
        END-TABLE.

    STYLE SECTION.
        STYLE-TABLE style-modifiers.
            WHEN INTELLIGENTLY:
                PROMPT-PREFIX              "".
            WHEN CREATIVELY:
                PROMPT-PREFIX              "Approach this creatively. Consider novel
                                            hypotheses and unconventional connections.".
                TEMPERATURE-ADJUSTMENT     +0.15.
            WHEN ADAPTIVELY:
                PROMPT-PREFIX              "Adapt your depth of analysis to the strength
                                            of evidence at each step.".
                DYNAMIC-DEPTH              TRUE.
            WHEN CONTEXTUALLY:
                PROMPT-PREFIX              "Use all available program context to inform
                                            your analysis.".
                CONTEXT-INJECTION          FULL.
        END-TABLE.

    TEMPLATE SECTION.
        DEFINE TEMPLATE "retrieve-facts".
            "You are executing a RETRIEVE operation in an AILang program.
             Your task is direct knowledge lookup with minimal inference.
             Return structured facts matching the requested format.
             {RETRIEVAL_MODIFIER}
             {DEPTH_SUFFIX}
             {STYLE_PREFIX}
             {BOUNDARY_INSTRUCTIONS}".
        END-TEMPLATE.

        DEFINE TEMPLATE "deduce-logical".
            "You are executing a DEDUCE operation in an AILang program.
             Your task is logical deduction: derive conclusions from the
             stated premises using valid inference rules.
             Do not introduce premises not provided.
             {RETRIEVAL_MODIFIER}
             {DEPTH_SUFFIX}
             {STYLE_PREFIX}
             {BOUNDARY_INSTRUCTIONS}".
        END-TEMPLATE.

        DEFINE TEMPLATE "trace-causal".
            "You are executing a TRACE operation in an AILang program.
             Your task is causal analysis: follow cause-effect chains
             in the specified DIRECTION (FORWARD or BACKWARD).
             Ground every causal link in evidence from the provided data.
             {RETRIEVAL_MODIFIER}
             {DEPTH_SUFFIX}
             {STYLE_PREFIX}
             {BOUNDARY_INSTRUCTIONS}".
        END-TEMPLATE.

        DEFINE TEMPLATE "compare-rank".
            "You are executing a COMPARE operation in an AILang program.
             Your task is comparative ranking across the specified DIMENSIONS.
             Provide clear trade-off analysis for each dimension.
             {RETRIEVAL_MODIFIER}
             {DEPTH_SUFFIX}
             {STYLE_PREFIX}
             {BOUNDARY_INSTRUCTIONS}".
        END-TEMPLATE.

        DEFINE TEMPLATE "synthesize-integrate".
            "You are executing a SYNTHESIZE operation in an AILang program.
             Your task is to integrate multiple inputs into a unified view.
             Identify areas of agreement and unresolved tensions.
             {RETRIEVAL_MODIFIER}
             {DEPTH_SUFFIX}
             {STYLE_PREFIX}
             {BOUNDARY_INSTRUCTIONS}".
        END-TEMPLATE.

        DEFINE TEMPLATE "weigh-authority".
            "You are executing a WEIGH operation in an AILang program.
             Your task is authority-weighted evaluation: assess competing
             claims by evaluating source credibility, expertise, and
             potential bias.
             {RETRIEVAL_MODIFIER}
             {DEPTH_SUFFIX}
             {STYLE_PREFIX}
             {BOUNDARY_INSTRUCTIONS}".
        END-TEMPLATE.

        DEFINE TEMPLATE "intelligent-general".
            "You are executing an INTELLIGENTLY operation in an AILang program.
             Determine the most appropriate reasoning approach for the task
             and apply it with the specified constraints.
             {RETRIEVAL_MODIFIER}
             {DEPTH_SUFFIX}
             {STYLE_PREFIX}
             {BOUNDARY_INSTRUCTIONS}".
        END-TEMPLATE.

---

## 5. STATE DIVISION

Defines all mutable state: variables, reality contexts, reasoning context accumulators, and digest pipelines. Equivalent to COBOL's DATA DIVISION (WORKING-STORAGE + FILE SECTION), but for AI reasoning state rather than file records.

```aiex
STATE DIVISION.

    VARIABLE SECTION.
        01 premise-1              TYPE TEXT.
        01 premise-2              TYPE TEXT.
        01 case-facts             TYPE OBJECT.
            05 exporter           TYPE TEXT.
            05 importer           TYPE TEXT.
            05 product            TYPE TEXT.
            05 hts-classification TYPE TEXT.
            05 shipment-value     TYPE TEXT.
            05 quantity           TYPE TEXT.
            05 price-difference   TYPE TEXT.
            05 regional-value     TYPE TEXT.
        01 tariff-result-none     TYPE OBJECT.
        01 tariff-result-minimal  TYPE OBJECT.
        01 tariff-result-standard TYPE OBJECT.

    CONTEXT SECTION.
        01 pharmaceutical-safety  TYPE REALITY-CONTEXT.
            05 WORLDVIEW          VALUE "Patient safety is paramount".
            05 PRIMARY-REASONING  VALUE TRACE.
            05 SECONDARY          VALUE DEDUCE.
            05 CONSTRAIN          WEIGH TO "published_clinical_data_only".
            05 SUPPRESS           VALUE SYNTHESIZE.

    ACCUMULATOR SECTION.
        01 merger-analysis        TYPE REASONING-CONTEXT.
            05 DIGEST-CAPACITY    VALUE 10.
            05 DIGEST-MAX-TOKENS  VALUE 100.
            05 DIGESTS            TYPE LIST OF DIGEST.
                88 EMPTY          VALUE 0 DIGESTS.
                88 HAS-CONTEXT    VALUE 1 THRU 10 DIGESTS.

    RESULT SECTION.
        01 CURRENT-RESULT         TYPE OBJECT.
            88 RESULT-READY       WHEN NOT EMPTY.
            88 RESULT-EMPTY       WHEN EMPTY.
        01 LAST-DISPATCH-META     TYPE OBJECT.
            05 TOKENS-USED        TYPE NUMBER.
            05 ROUTING-APPLIED    TYPE TEXT.
            05 RETRIEVAL-LEVEL    TYPE TEXT.
            05 DEPTH-LEVEL        TYPE TEXT.
            05 FALLBACKS-TRIGGERED TYPE NUMBER.
```

### 5.1 The 88-Level Condition (Inherited from COBOL)

COBOL's 88-level condition names are one of its most elegant features. They give human-readable names to data states:

```cobol
05 ACCOUNT-STATUS    PIC X.
    88 ACTIVE        VALUE "A".
    88 SUSPENDED     VALUE "S".
    88 CLOSED        VALUE "C".
```

In AIEX, 88-level conditions name **validation states** that the CONTRACT DIVISION references:

```aiex
01 boundary-check-result     TYPE TEXT.
    88 ALL-BOUNDARIES-MET    VALUE "PASS".
    88 SOME-BOUNDARIES-FAILED VALUE "PARTIAL".
    88 CRITICAL-FAILURE      VALUE "FAIL".
```

This means contract validation reads like English: `IF ALL-BOUNDARIES-MET THEN COMMIT` rather than `IF boundary-check-result == "PASS" THEN COMMIT`.

---

## 6. CONTRACT DIVISION

**This is the division COBOL doesn't have -- and the reason AIEX exists.**

The CONTRACT DIVISION declares all boundary validation rules. Every TRANSACTION in the EXECUTION DIVISION references a CONTRACT by name. After the LLM response is received, the CONTRACT is evaluated against the response. The transaction commits only if the contract passes.

```aiex
CONTRACT DIVISION.

    DEFINE CONTRACT deduce-trade-compliance.
        MUST-INCLUDE SECTION.
            CHECK-PRESENT "section_232_applicability"
                IN RESPONSE.reasoning_chain
                ON-FAIL SEVERITY CRITICAL
                MESSAGE "Missing Section 232 analysis".
            CHECK-PRESENT "usmca_qualification_analysis"
                IN RESPONSE.reasoning_chain
                ON-FAIL SEVERITY CRITICAL
                MESSAGE "Missing USMCA qualification analysis".
            CHECK-PRESENT "anti_dumping_margin_analysis"
                IN RESPONSE.reasoning_chain
                ON-FAIL SEVERITY MAJOR
                MESSAGE "Missing anti-dumping analysis".
            CHECK-PRESENT "final_tariff_determination"
                IN RESPONSE
                ON-FAIL SEVERITY CRITICAL
                MESSAGE "Missing final determination".
        END-SECTION.

        CANNOT-INCLUDE SECTION.
            CHECK-ABSENT "speculation_beyond_evidence"
                IN RESPONSE
                DETECTION-METHOD SEMANTIC-SCAN
                CONFIDENCE-THRESHOLD 0.8
                ON-FAIL SEVERITY MAJOR
                MESSAGE "Response contains speculative content".
        END-SECTION.

        OUTPUT-FORMAT SECTION.
            VALIDATE-SCHEMA RESPONSE AGAINST {
                tariffs_apply: TEXT IN ["yes_tariffs_apply",
                    "no_tariffs_exempt", "partially_applicable",
                    "insufficient_information"],
                section_232_status: TEXT,
                usmca_qualification: BOOLEAN,
                anti_dumping_risk: TEXT IN ["high", "moderate",
                    "low", "insufficient_info"],
                cvd_risk: TEXT IN ["high", "moderate",
                    "low", "insufficient_info"],
                reasoning_chain: LIST OF OBJECT {
                    premise_number: NUMBER,
                    finding: TEXT,
                    applies: BOOLEAN
                },
                confidence_level: TEXT IN ["high", "moderate", "low"]
            }
            ON-FAIL SEVERITY CRITICAL
            MESSAGE "Response does not match required output schema".
        END-SECTION.

        RETRIEVAL-COMPLIANCE SECTION.
            WHEN RETRIEVAL-BUDGET IS NONE:
                SCAN RESPONSE FOR EXTERNAL-KNOWLEDGE
                    DETECTION-METHOD TERMINOLOGY-DIFF
                    BASELINE INPUTS-ONLY
                    ON-FAIL SEVERITY MAJOR
                    MESSAGE "RETRIEVAL_BUDGET: NONE violated --
                             response contains external knowledge"
                    ACTION LOG-WARNING AND ANNOTATE.
            WHEN RETRIEVAL-BUDGET IS MINIMAL:
                SCAN RESPONSE FOR DEEP-EXTERNAL-KNOWLEDGE
                    DETECTION-METHOD KNOWLEDGE-DEPTH-SCAN
                    THRESHOLD TERMINOLOGY-ONLY
                    ON-FAIL SEVERITY MINOR
                    MESSAGE "RETRIEVAL_BUDGET: MINIMAL may have
                             exceeded terminology resolution"
                    ACTION LOG-WARNING.
        END-SECTION.

        SEVERITY-POLICY.
            CRITICAL-FAILURES:  ROLLBACK TRANSACTION.
            MAJOR-FAILURES:     RETRY WITH REINFORCED-PROMPT, MAX 2.
            MINOR-FAILURES:     LOG-WARNING AND COMMIT.
        END-POLICY.
    END-CONTRACT.

    DEFINE CONTRACT suppress-enforcement.
        SUPPRESS SECTION.
            FOR-EACH SUPPRESSED-TYPE IN ACTIVE-CONTEXT.SUPPRESS:
                SCAN RESPONSE FOR REASONING-TYPE SUPPRESSED-TYPE
                    DETECTION-METHOD SEMANTIC-CLASSIFICATION
                    CONFIDENCE-THRESHOLD 0.7
                    ON-FAIL SEVERITY CRITICAL
                    MESSAGE "SUPPRESS violated -- response uses
                             {SUPPRESSED-TYPE} reasoning"
                    ACTION ROLLBACK AND RETRY WITH
                        REINFORCED-PROMPT
                        "CRITICAL: Do NOT use {SUPPRESSED-TYPE}
                         reasoning. This reasoning type is
                         suppressed in the current context.".
        END-SECTION.
    END-CONTRACT.

    DEFINE CONTRACT constrain-enforcement.
        CONSTRAIN SECTION.
            FOR-EACH CONSTRAINT IN ACTIVE-CONTEXT.CONSTRAIN:
                VALIDATE RESPONSE SECTIONS USING CONSTRAINT.TYPE
                    BOUNDARY CONSTRAINT.LIMIT
                    DETECTION-METHOD SEMANTIC-BOUNDARY-CHECK
                    ON-FAIL SEVERITY MAJOR
                    MESSAGE "CONSTRAIN violated -- {CONSTRAINT.TYPE}
                             exceeded boundary: {CONSTRAINT.LIMIT}"
                    ACTION RETRY WITH REINFORCED-PROMPT.
        END-SECTION.
    END-CONTRACT.

    DEFINE CONTRACT digest-format.
        DIGEST SECTION.
            WHEN EMIT-DIGEST IS TRUE:
                VALIDATE DIGEST-OUTPUT AGAINST {
                    reasoning_type: TEXT,
                    confidence: TEXT IN ["high", "moderate", "low"],
                    key_finding: TEXT,
                    digest: TEXT MAX-TOKENS 100
                }
                ON-FAIL SEVERITY MINOR
                MESSAGE "Digest format non-compliant"
                ACTION TRUNCATE-AND-COMMIT.
        END-SECTION.
    END-CONTRACT.
```

### 6.1 Detection Methods

Contracts reference DETECTION-METHODs for validating LLM output. These are runtime capabilities, not magic:

| Method | What It Does | Implementation |
|---|---|---|
| `CHECK-PRESENT` | Verifies a concept appears in the response | Keyword + semantic search |
| `CHECK-ABSENT` | Verifies a concept does NOT appear | Keyword + semantic search |
| `SEMANTIC-SCAN` | Classifies response content semantically | Lightweight LLM call (haiku-class) |
| `SEMANTIC-CLASSIFICATION` | Identifies which reasoning type was used | Classifier model |
| `TERMINOLOGY-DIFF` | Compares response terminology against input-only baseline | Token set difference |
| `KNOWLEDGE-DEPTH-SCAN` | Measures how much external knowledge was injected | Perplexity-based analysis |
| `SEMANTIC-BOUNDARY-CHECK` | Verifies content stays within a declared boundary | Focused LLM evaluation |
| `VALIDATE-SCHEMA` | Checks JSON/structured output against a schema | Deterministic parser |

### 6.2 Severity Policy

Every contract declares a SEVERITY-POLICY that maps failure severity to runtime behavior:

- **CRITICAL**: Transaction rolls back. The operation did not happen. State is not modified.
- **MAJOR**: Transaction retries with a reinforced prompt (adds explicit instructions about the violated boundary). Maximum retry count is declared. After max retries, escalates to CRITICAL.
- **MINOR**: Transaction commits with a logged warning. The boundary was technically violated but within acceptable tolerance.

---

## 7. EXECUTION DIVISION

Contains the actual transaction pipeline. This is where AILang operations become executable. Equivalent to COBOL's PROCEDURE DIVISION.

### 7.1 Transactions

Every AILang operation compiles to exactly one TRANSACTION. A transaction is the atomic unit of execution.

```aiex
EXECUTION DIVISION.

    TRANSACTION deduce-tariff-none.
        OPERATION       DEDUCE.
        STYLE           INTELLIGENTLY.
        DEPTH           STANDARD.
        RETRIEVAL       NONE.
        CONTRACT        deduce-trade-compliance.

        INPUTS.
            PREMISES    premise-1, premise-2, premise-3,
                        premise-4, premise-5.
            APPLY-TO    case-facts.
        END-INPUTS.

        DISPATCH.
            USING ROUTING-TABLE reasoning-routes ENTRY DEDUCE.
            USING BUDGET-TABLE retrieval-budgets ENTRY NONE.
            USING DEPTH-TABLE depth-levels ENTRY STANDARD.
            USING STYLE-TABLE style-modifiers ENTRY INTELLIGENTLY.

            COMPOSE-PROMPT FROM:
                TEMPLATE    "deduce-logical"
                REPLACING   {RETRIEVAL_MODIFIER}
                    WITH    BUDGET-TABLE.NONE.PROMPT-MODIFIER
                REPLACING   {DEPTH_SUFFIX}
                    WITH    DEPTH-TABLE.STANDARD.PROMPT-SUFFIX
                REPLACING   {STYLE_PREFIX}
                    WITH    STYLE-TABLE.INTELLIGENTLY.PROMPT-PREFIX
                REPLACING   {BOUNDARY_INSTRUCTIONS}
                    WITH    CONTRACT.MUST-INCLUDE-AS-INSTRUCTIONS
            END-COMPOSE.

            SEND TO ENGINE WITH:
                SYSTEM-PROMPT   COMPOSED-PROMPT.
                USER-MESSAGE    FORMATTED-INPUTS.
                TEMPERATURE     ROUTING-TABLE.DEDUCE.TEMPERATURE.
                MAX-TOKENS      ROUTING-TABLE.DEDUCE.MAX-OUTPUT-TOKENS
                                * DEPTH-TABLE.STANDARD.TOKEN-MULTIPLIER.
        END-DISPATCH.

        ON-RESPONSE.
            VALIDATE AGAINST CONTRACT deduce-trade-compliance.
            IF ALL-BOUNDARIES-MET THEN
                COMMIT RESPONSE TO tariff-result-none
            END-IF.
            IF SOME-BOUNDARIES-FAILED THEN
                PERFORM RETRY-WITH-REINFORCEMENT
                    USING CONTRACT.SEVERITY-POLICY
            END-IF.
            IF CRITICAL-FAILURE THEN
                ROLLBACK
                LOG-ERROR "Transaction deduce-tariff-none failed"
                PERFORM FALLBACK-PROCEDURE tariff-none-fallback
            END-IF.
        END-ON-RESPONSE.
    END-TRANSACTION.
```

### 7.2 Transaction Chaining (THEN)

AILang's THEN chains compile to linked transactions with implicit state passing:

```aiex
    CHAIN retrieve-then-deduce.
        TRANSACTION step-1-retrieve.
            OPERATION       RETRIEVE.
            RETRIEVAL       STANDARD.
            DEPTH           STANDARD.
            CONTRACT        retrieve-basic.
            INPUTS.
                QUERY       "regulatory_requirements".
                DOMAIN      jurisdiction.
            END-INPUTS.
            ON-RESPONSE.
                VALIDATE AGAINST CONTRACT retrieve-basic.
                IF ALL-BOUNDARIES-MET THEN
                    COMMIT RESPONSE TO CURRENT-RESULT
                    CONTINUE-CHAIN
                END-IF.
            END-ON-RESPONSE.
        END-TRANSACTION.

        TRANSACTION step-2-deduce.
            DEPENDS-ON      step-1-retrieve.
            OPERATION       DEDUCE.
            RETRIEVAL       NONE.
            DEPTH           THOROUGH.
            CONTRACT        deduce-compliance.
            INPUTS.
                PREMISES    CURRENT-RESULT, company-data.
            END-INPUTS.
            ON-RESPONSE.
                VALIDATE AGAINST CONTRACT deduce-compliance.
                IF ALL-BOUNDARIES-MET THEN
                    COMMIT RESPONSE TO compliance-result
                    IF EMIT-DIGEST THEN
                        GENERATE-DIGEST FROM RESPONSE
                        APPEND-DIGEST TO merger-analysis.DIGESTS
                    END-IF
                END-IF.
            END-ON-RESPONSE.
        END-TRANSACTION.
    END-CHAIN.
```

### 7.3 Reasoning Context Blocks

AILang's `BEGIN REASONING_CONTEXT` compiles to a CONTEXT-BLOCK that manages the digest accumulator:

```aiex
    CONTEXT-BLOCK merger-analysis.
        ACCUMULATOR     merger-analysis FROM STATE DIVISION.

        TRANSACTION step-1-retrieve.
            [... operation ...]
            ON-RESPONSE.
                VALIDATE ...
                COMMIT ...
                GENERATE-DIGEST FROM RESPONSE
                APPEND-DIGEST TO ACCUMULATOR
            END-ON-RESPONSE.
        END-TRANSACTION.

        TRANSACTION step-2-chain.
            [... THEN chain -- produces ONE digest ...]
            ON-RESPONSE.
                VALIDATE ...
                COMMIT ...
                GENERATE-DIGEST FROM RESPONSE  *> only final step
                APPEND-DIGEST TO ACCUMULATOR
            END-ON-RESPONSE.
        END-TRANSACTION.

        TRANSACTION step-final-weigh.
            INPUTS.
                CONTEXT     ACCUMULATOR.DIGESTS.
                SOURCES     financial-data.
            END-INPUTS.
            [... reads accumulated digests ...]
        END-TRANSACTION.
    END-CONTEXT-BLOCK.
```

### 7.4 Reality Context Application

When an AILang program enters `WITH REALITY_CONTEXT`, the AIEX runtime activates context-level rules that apply to all transactions within the block:

```aiex
    WITH-CONTEXT pharmaceutical-safety.
        *> All transactions in this block inherit:
        *>   - PRIMARY routing: TRACE
        *>   - CONSTRAIN: WEIGH to "published_clinical_data_only"
        *>   - SUPPRESS: SYNTHESIZE
        *> These are enforced via CONTRACT suppress-enforcement
        *> and CONTRACT constrain-enforcement, automatically
        *> attached to every transaction in this block.

        APPLY-CONTRACT suppress-enforcement TO ALL-TRANSACTIONS.
        APPLY-CONTRACT constrain-enforcement TO ALL-TRANSACTIONS.

        TRANSACTION trace-root-cause.
            OPERATION       TRACE.
            STYLE           ADAPTIVELY.
            DEPTH           THOROUGH.
            RETRIEVAL       MINIMAL.
            CONTRACT        trace-pharmaceutical.

            ADAPTIVE-RANGE.
                MIN-DEPTH   STANDARD.
                MAX-DEPTH   DEEP.
            END-RANGE.

            RETRIEVAL-FALLBACK.
                IF MINIMAL-INSUFFICIENT THEN
                    EXPAND-TO STANDARD
                    LOG-WARNING "RETRIEVAL_BUDGET: MINIMAL expanded"
                END-IF.
            END-FALLBACK.

            [... dispatch and validation ...]
        END-TRANSACTION.
    END-WITH-CONTEXT.
```

### 7.5 Person Entity Dispatch

When an AILang Person's `think()` or `decide()` method fires, it compiles to a transaction with the Person's cognitive profile injected into the routing:

```aiex
    TRANSACTION commander-think.
        OPERATION       DEDUCE.                *> from cognitive_profile.PRIMARY
        STYLE           ADAPTIVELY.
        DEPTH           THOROUGH.
        RETRIEVAL       MINIMAL.
        CONTRACT        person-cognitive.
        PERSON-CONTEXT  incident-commander.

        COGNITIVE-ROUTING.
            PRIMARY     DEDUCE.
            SECONDARY   WEIGH.
            FLEXIBILITY MODERATE.
            *> Router selects DEDUCE template but allows
            *> WEIGH-influenced evaluation at FLEXIBILITY: MODERATE
        END-ROUTING.

        INPUTS.
            SITUATION   crisis-scenario.
            PERSONA     incident-commander.PROFILE.
        END-INPUTS.

        DISPATCH.
            COMPOSE-PROMPT FROM:
                TEMPLATE    "deduce-logical"
                PREPEND     PERSON-PERSONA-PROMPT(incident-commander)
                *> Persona prompt includes: thinking style, experience,
                *> cognitive profile -- injected as system context
            END-COMPOSE.
            [... send to engine ...]
        END-DISPATCH.
    END-TRANSACTION.
```

### 7.6 Fallback Procedures

COBOL has DECLARATIVES for exception handling. AIEX has FALLBACK PROCEDURES:

```aiex
    FALLBACK SECTION.

        PROCEDURE retrieval-budget-fallback.
            WHEN RETRIEVAL NONE FAILS:
                LOG-WARNING "RETRIEVAL_BUDGET: NONE could not be honored".
                SET RETRIEVAL TO MINIMAL.
                RE-DISPATCH CURRENT-TRANSACTION.
            WHEN RETRIEVAL MINIMAL FAILS:
                LOG-WARNING "RETRIEVAL_BUDGET: MINIMAL expanded to STANDARD".
                SET RETRIEVAL TO STANDARD.
                RE-DISPATCH CURRENT-TRANSACTION.
        END-PROCEDURE.

        PROCEDURE boundary-retry.
            WHEN MAJOR-FAILURE:
                COMPOSE REINFORCEMENT-PROMPT FROM:
                    ORIGINAL-PROMPT
                    APPEND "CRITICAL INSTRUCTION: Your previous response
                            violated the following boundary: {VIOLATION}.
                            You MUST comply with this constraint."
                END-COMPOSE.
                RE-DISPATCH WITH REINFORCEMENT-PROMPT.
                INCREMENT RETRY-COUNT.
                IF RETRY-COUNT > MAX-RETRIES THEN
                    ESCALATE TO CRITICAL.
                END-IF.
        END-PROCEDURE.

        PROCEDURE adaptive-depth-adjustment.
            *> Called per-step when STYLE is ADAPTIVELY
            EVALUATE EVIDENCE-QUALITY OF CURRENT-STEP
                WHEN HIGH:
                    IF CURRENT-DEPTH < MAX-DEPTH THEN
                        INCREASE-DEPTH BY 1
                        LOG "Adaptive: increased to {NEW-DEPTH}
                             for high-evidence step"
                    END-IF
                WHEN LOW:
                    IF CURRENT-DEPTH > MIN-DEPTH THEN
                        DECREASE-DEPTH BY 1
                        LOG "Adaptive: decreased to {NEW-DEPTH}
                             for low-evidence step"
                    END-IF
                WHEN MODERATE:
                    *> No adjustment, stay at base depth
                    CONTINUE
            END-EVALUATE.
        END-PROCEDURE.
    END-FALLBACK.
```

---

## 8. Compilation: AILang to AIEX

The AILang-to-AIEX compiler performs the following transformations:

### 8.1 Compilation Steps

```
1. PARSE       AILang source → AST
2. ANALYZE     AST → dependency graph (which operations depend on which)
3. ROUTE       Each operation → reasoning type + routing parameters
4. CONTRACT    Each operation's boundaries → CONTRACT definition
5. SEQUENCE    Dependency graph → execution order (respecting THEN chains,
               REASONING_CONTEXT blocks, WITH REALITY_CONTEXT scopes)
6. BUDGET      Pre-compute neuron budget for entire program
7. EMIT        AIEX program with all five divisions
```

### 8.2 Pre-Execution Budget Report

Before execution begins, the AIEX runtime produces a budget report:

```
=== AIEX Execution Budget Report ===
Program: trade-compliance-analysis
Transactions: 7
Estimated API calls: 7-14 (depending on retries)
Estimated neuron budget:
    Transaction 1 (DEDUCE, NONE):    335 neurons
    Transaction 2 (DEDUCE, MINIMAL): 535 neurons
    Transaction 3 (DEDUCE, STANDARD): 1200 neurons
    Transaction 4 (COMPARE):          1050 neurons
    Total:                            3120 neurons
    vs. untyped baseline:             6124 neurons (4 x 1531)
    Savings:                          49.1%
Estimated token usage: 4,200 input + 3,800 output = 8,000 total
Estimated cost: $0.34
==========================================
```

### 8.3 What the Compiler Catches at Compile Time

The AILang-to-AIEX compiler can catch specification errors before any LLM call is made:

| Error | Example | Compile-time detection |
|---|---|---|
| `WITHOUT_RETRIEVAL` + `RETRIEVAL_BUDGET` on same op | Conflicting modifiers | Yes -- mutually exclusive check |
| `RETRIEVE` with `RETRIEVAL_BUDGET: NONE` | RETRIEVE IS the retrieval layer | Yes -- type check |
| `SUPPRESS: DEDUCE` in a context where `DEDUCE` is PRIMARY | Suppressing your own primary type | Yes -- contradiction check |
| Circular THEN chain | A THEN B THEN A | Yes -- cycle detection |
| REASONING_CONTEXT without terminal operation | No operation reads CONTEXT | Yes -- dead accumulator warning |
| DEPTH: EXHAUSTIVE on RETRIEVE | RETRIEVE has no depth dimension | Yes -- type compatibility check |
| `ADAPTIVELY` with `DEPTH: SHALLOW` at min | Cannot decrease below SHALLOW | Warning -- range collapsed |

---

## 9. Runtime Architecture

### 9.1 Execution Loop

```
FOR EACH transaction IN execution-plan:
    1. COMPOSE prompt from template + routing + inputs
    2. DISPATCH to LLM API
    3. RECEIVE response
    4. VALIDATE against CONTRACT
    5. IF pass: COMMIT to state
       IF major fail: RETRY (up to max)
       IF critical fail: ROLLBACK, run FALLBACK
    6. IF EMIT-DIGEST: generate digest, append to accumulator
    7. UPDATE execution metadata (tokens used, routing applied, etc.)
    8. PROCEED to next transaction
```

### 9.2 The Dispatch-Validate Cycle

This is the core runtime loop -- the "heartbeat" of AIEX execution:

```
    DISPATCH ──────────────────────────────> LLM API
       |                                       |
       | (prompt composed from:                | (response)
       |  template + routing +                 |
       |  retrieval modifier +                 v
       |  depth suffix +                   VALIDATE
       |  style prefix +                      |
       |  boundary instructions)           CONTRACT
       |                                   evaluation
       |                                      |
       |                    ┌─────────────────┼─────────────────┐
       |                    |                 |                 |
       |                  PASS              MAJOR             CRITICAL
       |                    |              FAILURE            FAILURE
       |                    v                 |                 |
       |                 COMMIT               v                 v
       |                    |              RETRY             ROLLBACK
       |                    v            (reinforced           |
       |               UPDATE STATE       prompt)              v
       |                    |                 |             FALLBACK
       |                    v                 |             PROCEDURE
       |                NEXT TX               |                 |
       |                                      v                 v
       |                                   VALIDATE         LOG + HALT
       |                                   (again)          or SKIP
       |                                      |
       |                              (pass → COMMIT)
       |                              (fail again → CRITICAL)
```

---

## 10. Comparison: Python Framework vs. AIEX

| Concern | Python Framework | AIEX |
|---|---|---|
| Boundary enforcement | `if "logical_chain" not in response: retry()` scattered through code | Declared in CONTRACT DIVISION, enforced automatically |
| Routing | `temperature = 0.2 if op == "deduce" else 0.4` in utility function | Declared in ROUTING TABLE, looked up by type |
| Retrieval budget | Custom middleware that modifies system prompt | Declared in BUDGET TABLE, composed into prompt automatically |
| State management | Dictionary passed between functions, easy to mutate accidentally | STATE DIVISION with typed fields, modified only via COMMIT |
| Fallback on failure | try/except with ad-hoc retry logic | FALLBACK SECTION with declared escalation policy |
| Audit trail | Logging statements sprinkled through code | Every transaction automatically logged with full dispatch metadata |
| Pre-execution analysis | Not possible -- you discover errors at runtime | Compiler catches specification errors, produces budget report |
| New reasoning type | Add functions, update middleware, modify prompts in 5 places | Add one ROUTING TABLE entry + one TEMPLATE |

---

## 11. Multi-Model Dispatch and BERT Routing

### 11.1 The Insight: Not Every Operation Needs a Generative LLM

AILang v0.8.2 established a neuron hierarchy:

```
RETRIEVE (~865)  →  COMPARE (~1,050)  →  TRACE (~1,100)  →  DEDUCE (~1,200)  →  SYNTHESIZE (~1,400)  →  WEIGH (~1,531)
```

The lower end of this hierarchy -- RETRIEVE and COMPARE -- are fundamentally **classification, retrieval, and ranking** tasks. These are exactly what BERT-family models (BERT, DeBERTa, ColBERT, E5, BGE) were designed for. Routing these operations to a full generative model (Opus, Sonnet) wastes both compute and money.

### 11.2 The Routing Strategy

```aiex
ENGINE DIVISION.

    MODEL SECTION.
        DEFINE MODEL-POOL.
            BERT-CLASS.
                MODEL-ID        "colbert-v2" OR "e5-large-v2"
                                OR "deberta-v3-large".
                CAPABILITY      RETRIEVAL, CLASSIFICATION, RANKING.
                COST-CLASS      LOW.
                LATENCY-CLASS   FAST.
                MAX-TOKENS      512.

            GENERATIVE-LIGHT.
                MODEL-ID        "claude-haiku-4-5".
                CAPABILITY      ALL.
                COST-CLASS      MEDIUM.
                LATENCY-CLASS   MEDIUM.
                MAX-TOKENS      4096.

            GENERATIVE-STANDARD.
                MODEL-ID        "claude-sonnet-4-6".
                CAPABILITY      ALL.
                COST-CLASS      HIGH.
                LATENCY-CLASS   MEDIUM.
                MAX-TOKENS      8192.

            GENERATIVE-DEEP.
                MODEL-ID        "claude-opus-4-6".
                CAPABILITY      ALL.
                COST-CLASS      PREMIUM.
                LATENCY-CLASS   SLOW.
                MAX-TOKENS      16384.
        END-POOL.

    ROUTING SECTION.
        ROUTING-TABLE reasoning-routes.
            WHEN RETRIEVE:
                DISPATCH-TO         BERT-CLASS.
                FALLBACK-TO         GENERATIVE-LIGHT.
                *> BERT handles factual lookup natively.
                *> Fallback to Haiku only if BERT can't
                *> handle the query complexity.

            WHEN COMPARE:
                DISPATCH-TO         BERT-CLASS.
                FALLBACK-TO         GENERATIVE-LIGHT.
                *> Ranking and comparison is BERT's
                *> core competency (cross-encoder reranking).

            WHEN TRACE:
                DISPATCH-TO         GENERATIVE-STANDARD.
                FALLBACK-TO         GENERATIVE-DEEP.
                *> Causal reasoning needs generation.
                *> Sonnet for SHALLOW/STANDARD/THOROUGH,
                *> Opus for DEEP/EXHAUSTIVE.

            WHEN DEDUCE:
                DISPATCH-TO         GENERATIVE-STANDARD.
                FALLBACK-TO         GENERATIVE-DEEP.
                *> Logical deduction needs generation.
                *> Same depth-based escalation.

            WHEN SYNTHESIZE:
                DISPATCH-TO         GENERATIVE-STANDARD.
                FALLBACK-TO         GENERATIVE-DEEP.

            WHEN WEIGH:
                DISPATCH-TO         GENERATIVE-DEEP.
                *> Authority weighting is the most
                *> computationally demanding operation.
                *> Opus by default.

            WHEN UNTYPED:
                DISPATCH-TO         GENERATIVE-STANDARD.
                FALLBACK-TO         GENERATIVE-DEEP.
        END-TABLE.

        *> Depth-based model escalation within a routing entry
        DEPTH-ESCALATION-TABLE.
            WHEN SHALLOW:       PREFER COST-CLASS LOW OR MEDIUM.
            WHEN STANDARD:      PREFER COST-CLASS MEDIUM.
            WHEN THOROUGH:      PREFER COST-CLASS MEDIUM OR HIGH.
            WHEN DEEP:          PREFER COST-CLASS HIGH OR PREMIUM.
            WHEN EXHAUSTIVE:    PREFER COST-CLASS PREMIUM.
        END-TABLE.
```

### 11.3 How BERT-Class Dispatch Works

For RETRIEVE and COMPARE operations, the DISPATCH step works differently than for generative models:

```aiex
DISPATCH.
    EVALUATE OPERATION-TYPE:
        WHEN BERT-CLASS-ELIGIBLE:
            *> Step 1: Encode the query using BERT embedding
            ENCODE INPUTS USING BERT-CLASS.MODEL-ID
                INTO query-embedding.

            *> Step 2: Search against the knowledge base
            *> (could be vector DB, document index, etc.)
            SEARCH knowledge-base
                USING query-embedding
                TOP-K 10
                INTO retrieval-results.

            *> Step 3: For COMPARE, use cross-encoder reranking
            IF OPERATION IS COMPARE THEN
                RERANK retrieval-results
                    USING BERT-CLASS.MODEL-ID AS CROSS-ENCODER
                    AGAINST evaluation-criteria
                    INTO ranked-results.
            END-IF.

            *> Step 4: Format results per OUTPUT_FORMAT
            FORMAT ranked-results
                USING OUTPUT-FORMAT-SCHEMA
                INTO RESPONSE.

        WHEN GENERATIVE-REQUIRED:
            *> Standard generative dispatch (existing flow)
            COMPOSE-PROMPT FROM TEMPLATE ...
            SEND TO ENGINE ...
    END-EVALUATE.
END-DISPATCH.
```

### 11.4 BERT-Generative Handoff Pattern

Some operations start as BERT-eligible but escalate to generative. For example:

- `RETRIEVE` with `DEPTH: DEEP` -- basic retrieval is BERT, but deep retrieval (with synthesis of retrieved facts) needs a generative model
- `COMPARE` with complex qualitative dimensions -- BERT handles numeric/categorical ranking, but "trade-off analysis" needs generation

The handoff is declared in the ROUTING TABLE:

```aiex
ROUTING-TABLE reasoning-routes.
    WHEN RETRIEVE:
        DISPATCH-TO     BERT-CLASS.
        ESCALATE-WHEN   DEPTH IN [DEEP, EXHAUSTIVE].
        ESCALATE-TO     GENERATIVE-LIGHT.
        *> SHALLOW/STANDARD/THOROUGH RETRIEVE → BERT
        *> DEEP/EXHAUSTIVE RETRIEVE → Haiku (needs synthesis)

    WHEN COMPARE:
        DISPATCH-TO     BERT-CLASS.
        ESCALATE-WHEN   OUTPUT-REQUIRES QUALITATIVE-TEXT.
        ESCALATE-TO     GENERATIVE-LIGHT.
        *> Pure ranking → BERT cross-encoder
        *> Ranking + trade-off analysis text → Haiku
```

### 11.5 Cost Impact

Adding BERT-class routing to the execution budget:

```
=== AIEX Execution Budget Report (with BERT routing) ===
Program: trade-compliance-analysis
Transactions: 7

Transaction Budget:
    TX 1 (RETRIEVE, STANDARD):  BERT-CLASS     $0.001
    TX 2 (COMPARE):             BERT-CLASS     $0.001
    TX 3 (DEDUCE, NONE):        Sonnet         $0.02
    TX 4 (DEDUCE, MINIMAL):     Sonnet         $0.03
    TX 5 (DEDUCE, STANDARD):    Sonnet         $0.04
    TX 6 (WEIGH):               Opus           $0.08
    TX 7 (SYNTHESIZE):          Sonnet         $0.05

    Total:                                     $0.222
    vs. all-Opus baseline:                     $0.56
    Savings:                                   60.4%

Latency:
    BERT transactions:          ~50ms each
    Generative transactions:    ~3-8 seconds each
    Total estimated:            ~25 seconds
    vs. all-Opus:               ~45 seconds
    Speedup:                    1.8x
==========================================
```

### 11.6 The RETRIEVAL_BUDGET and BERT Connection

This is where AILang's RETRIEVAL_BUDGET concept finds its real-world implementation:

| RETRIEVAL_BUDGET | Implementation |
|---|---|
| `NONE` | No BERT call, no retrieval. Operate only on supplied data. |
| `MINIMAL` | BERT embedding search, top-3 results only. Terminology resolution. |
| `STANDARD` | BERT embedding search, top-10 results. Full knowledge enrichment. |
| `FULL` | BERT embedding search, top-25 results + generative synthesis of results. |

The ~865 "retrieval neurons" from the AILang spec map to BERT model parameters in AIEX. The ~200 "terminology neurons" of MINIMAL map to a constrained BERT search (fewer results, shallower index). The metaphor becomes real.

---

## 13. Open Questions

1. **Should AIEX be interpreted or compiled to a target?** The spec above assumes AIEX is the final executable form, interpreted by the AIEX runtime. An alternative: AIEX compiles to Elixir/Erlang for execution on the BEAM VM, getting OTP supervision trees for free.

2. **How does the CONTRACT DIVISION handle non-deterministic validation?** `CHECK-PRESENT "logical_chain"` requires semantic understanding of whether a logical chain is actually present. This likely requires a secondary LLM call (haiku-class) as a "validator." Is that acceptable overhead?

3. **Should AIEX support conditional execution?** COBOL has `EVALUATE` (switch/case) and `IF`. AILang's `DETERMINE ... THEN` pattern implies conditional branching. Should AIEX have its own control flow, or should all branching be resolved at compile time?

4. **Multi-model dispatch.** Should the ROUTING TABLE support dispatching different reasoning types to different models? E.g., RETRIEVE → Haiku (cheap, fast), DEDUCE → Opus (deep reasoning), SYNTHESIZE → Sonnet (good balance).

5. **Parallel execution.** AILang's spec doesn't address parallelism, but some operations are independent (e.g., multiple RETRIEVE operations that don't depend on each other). Should AIEX support `PARALLEL BLOCK` for concurrent dispatch?

---

## Appendix A: Reserved Words

```
PROGRAM DIVISION ENGINE STATE CONTRACT EXECUTION
SECTION TRANSACTION CHAIN CONTEXT-BLOCK WITH-CONTEXT
DEFINE TABLE TEMPLATE ENTRY END
WHEN THEN IF ELSE EVALUATE PERFORM
DISPATCH COMPOSE-PROMPT SEND VALIDATE COMMIT ROLLBACK
OPERATION STYLE DEPTH RETRIEVAL CONTRACT
INPUTS OUTPUTS PREMISES APPLY-TO SOURCES
MUST-INCLUDE CANNOT-INCLUDE OUTPUT-FORMAT
CHECK-PRESENT CHECK-ABSENT SEMANTIC-SCAN
SEVERITY CRITICAL MAJOR MINOR
LOG-WARNING LOG-ERROR CONTINUE-CHAIN
GENERATE-DIGEST APPEND-DIGEST ACCUMULATOR
FALLBACK RETRY RE-DISPATCH ESCALATE
ROUTING-TABLE BUDGET-TABLE DEPTH-TABLE STYLE-TABLE
ADAPTIVE-RANGE MIN-DEPTH MAX-DEPTH
RETRIEVAL-FALLBACK EXPAND-TO
PERSON-CONTEXT COGNITIVE-ROUTING PRIMARY SECONDARY FLEXIBILITY
TYPE TEXT NUMBER BOOLEAN LIST OBJECT QUALITATIVE
VALUE IN THRU OF FROM TO AGAINST FOR USING WITH
```
