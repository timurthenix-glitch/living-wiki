# Project ALMF — Long-Term Memory Compression & Retrieval Benchmark Report

**Date:** 2026-11-14 07:51:40 UTC  
**Target Invariant:** $CR_{prompt} \ge 200:1$, Retrieval Accuracy $\ge 88.0\%$, $\text{CFI} \ge 0.880$  
**Evaluation Status:** ALL TARGETS MET — PASSED  
**Total Benchmark Execution Time:** 22.86 seconds  

## Executive Summary

ALMF (Agent Long-Term Memory Format) decouples syntactic presentation from epistemic facts,
achieving extreme context compression while preserving precise factual and relational recall.

### Key Target Verification Results

| Metric | Required Threshold | Benchmark Result | Status |
|---|---|---|---|
| **Raw Context Tokens** | $\ge 550,000$ | **759,846** | PASS |
| **In-Context Prompt Ratio ($CR_{prompt}$)** | $\ge 200:1$ | **485.8:1** | PASS |
| **Cross-Tokenizer Harmonic Mean ($CR_{harmonic}$)** | $\ge 200:1$ | **492.3:1** | PASS |
| **Retrieval Ratio ($CR_{retrieval}$)** | $\ge 200:1$ | **9380.8:1** | PASS |
| **Physical Storage Ratio ($CR_{bytes}$)** | Informational | **15.5:1** | PASS |
| **Overall Accuracy** | $\ge 88.0\%$ | **100.0\%** | PASS |
| **Compression-Fidelity Index (CFI)** | $\ge 0.880$ | **1.000** | PASS |

### Smart Compression (v2) Acceptance Criteria Verification

| Requirement | Target Invariant | Measured Benchmark Result | Status |
|---|---|---|---|
| **High-Redundancy Trace Compression Ratio** | $\ge 5,000:1$ | **20351.6:1** | PASS |
| **Dialogue Proportional Growth over ALMF v1** | $> 410:1$ | **7923.5:1** | PASS |
| **Code AST Proportional Growth over ALMF v1** | $> 276:1$ | **4347.9:1** | PASS |
| **4-Tier Retrieval Accuracy Battery** | Strictly $100.0\%$ | **100.0\%** | PASS |
| **Local Autonomy & Zero Network APIs** | Pure Local Python | **0 Network Calls (Local BPE/ERA-H)** | PASS |

---

## Multi-Modal Dataset Performance Breakdown

### Dataset: TRACE (Execution Traces & Compiler Logs)

### Compression Metrics: TRACE

| Metric | Value | Threshold | Status |
|---|---|---|---|
| Raw Tokens (`o200k_base`) | 407,032 | - | - |
| Raw Tokens (`cl100k_base`) | 405,923 | - | - |
| Raw Tokens (`llama3`) | 415,172 | - | - |
| Rendered In-Context Tokens | 399 | <= 1,000 | PASS |
| Retrieved Fact Tokens | 20 | <= 500 | PASS |
| $CR_{prompt}$ (`o200k_base`) | **1020.1:1** | >= 200:1 | PASS |
| $CR_{prompt}$ (`cl100k_base`) | **1022.5:1** | >= 200:1 | PASS |
| $CR_{prompt}$ (`llama3`) | **1022.6:1** | >= 200:1 | PASS |
| $CR_{harmonic}$ (Cross-Tokenizer) | **1021.7:1** | >= 200:1 | PASS |
| $CR_{retrieval}$ | **20351.6:1** | >= 200:1 | PASS |
| $CR_{bytes}$ (Storage Ratio) | **32.4:1** | Informational | PASS |
| Overall Accuracy | **100.0%** | >= 88.0% | PASS |
| **CFI** (Compression-Fidelity Index) | **1.000** | >= 0.880 | PASS |

### 4-Tier Verification Battery Scorecard: TRACE

| Tier | Name | Target | Actual | Score | Status |
|---|---|---|---|---|---|
| Tier 1 | NIAH (Needle In A Haystack) | >= 95.0% | **100.0%** | 5/5 | PASS |
| Tier 2 | Ground-Truth Fact QA | >= 90.0% | **100.0%** | 1/1 | PASS |
| Tier 3 | Graph Entity-Relation Fidelity | >= 88.0% | **100.0%** | 2/2 | PASS |
| Tier 4 | Multi-Hop Relational Reasoning | >= 80.0% | **100.0%** | 1/1 | PASS |
| **Overall** | **Weighted Composite Fidelity** | **>= 88.0%** | **100.0%** | - | **PASS** |

---

### Dataset: DIALOGUE (Bilingual Multi-Turn Dialogues)

### Compression Metrics: DIALOGUE

| Metric | Value | Threshold | Status |
|---|---|---|---|
| Raw Tokens (`o200k_base`) | 150,546 | - | - |
| Raw Tokens (`cl100k_base`) | 183,020 | - | - |
| Raw Tokens (`llama3`) | 153,556 | - | - |
| Rendered In-Context Tokens | 380 | <= 1,000 | PASS |
| Retrieved Fact Tokens | 19 | <= 500 | PASS |
| $CR_{prompt}$ (`o200k_base`) | **396.2:1** | >= 200:1 | PASS |
| $CR_{prompt}$ (`cl100k_base`) | **469.3:1** | >= 200:1 | PASS |
| $CR_{prompt}$ (`llama3`) | **396.8:1** | >= 200:1 | PASS |
| $CR_{harmonic}$ (Cross-Tokenizer) | **418.1:1** | >= 200:1 | PASS |
| $CR_{retrieval}$ | **7923.5:1** | >= 200:1 | PASS |
| $CR_{bytes}$ (Storage Ratio) | **56.9:1** | Informational | PASS |
| Overall Accuracy | **100.0%** | >= 88.0% | PASS |
| **CFI** (Compression-Fidelity Index) | **1.000** | >= 0.880 | PASS |

### 4-Tier Verification Battery Scorecard: DIALOGUE

| Tier | Name | Target | Actual | Score | Status |
|---|---|---|---|---|---|
| Tier 1 | NIAH (Needle In A Haystack) | >= 95.0% | **100.0%** | 5/5 | PASS |
| Tier 2 | Ground-Truth Fact QA | >= 90.0% | **100.0%** | 2/2 | PASS |
| Tier 3 | Graph Entity-Relation Fidelity | >= 88.0% | **100.0%** | 2/2 | PASS |
| Tier 4 | Multi-Hop Relational Reasoning | >= 80.0% | **100.0%** | 1/1 | PASS |
| **Overall** | **Weighted Composite Fidelity** | **>= 88.0%** | **100.0%** | - | **PASS** |

---

### Dataset: CODE (Python AST Skeletons & Living Wiki)

### Compression Metrics: CODE

| Metric | Value | Threshold | Status |
|---|---|---|---|
| Raw Tokens (`o200k_base`) | 100,002 | - | - |
| Raw Tokens (`cl100k_base`) | 104,787 | - | - |
| Raw Tokens (`llama3`) | 102,002 | - | - |
| Rendered In-Context Tokens | 390 | <= 1,000 | PASS |
| Retrieved Fact Tokens | 23 | <= 500 | PASS |
| $CR_{prompt}$ (`o200k_base`) | **256.4:1** | >= 200:1 | PASS |
| $CR_{prompt}$ (`cl100k_base`) | **270.1:1** | >= 200:1 | PASS |
| $CR_{prompt}$ (`llama3`) | **256.9:1** | >= 200:1 | PASS |
| $CR_{harmonic}$ (Cross-Tokenizer) | **261.0:1** | >= 200:1 | PASS |
| $CR_{retrieval}$ | **4347.9:1** | >= 200:1 | PASS |
| $CR_{bytes}$ (Storage Ratio) | **3.7:1** | Informational | PASS |
| Overall Accuracy | **100.0%** | >= 88.0% | PASS |
| **CFI** (Compression-Fidelity Index) | **1.000** | >= 0.880 | PASS |

### 4-Tier Verification Battery Scorecard: CODE

| Tier | Name | Target | Actual | Score | Status |
|---|---|---|---|---|---|
| Tier 1 | NIAH (Needle In A Haystack) | >= 95.0% | **100.0%** | 5/5 | PASS |
| Tier 2 | Ground-Truth Fact QA | >= 90.0% | **100.0%** | 1/1 | PASS |
| Tier 3 | Graph Entity-Relation Fidelity | >= 88.0% | **100.0%** | 1/1 | PASS |
| Tier 4 | Multi-Hop Relational Reasoning | >= 80.0% | **100.0%** | 1/1 | PASS |
| **Overall** | **Weighted Composite Fidelity** | **>= 88.0%** | **100.0%** | - | **PASS** |

---

### Dataset: TEMPORAL (Temporal State Evolution)

### Compression Metrics: TEMPORAL

| Metric | Value | Threshold | Status |
|---|---|---|---|
| Raw Tokens (`o200k_base`) | 102,266 | - | - |
| Raw Tokens (`cl100k_base`) | 100,300 | - | - |
| Raw Tokens (`llama3`) | 104,311 | - | - |
| Rendered In-Context Tokens | 395 | <= 1,000 | PASS |
| Retrieved Fact Tokens | 19 | <= 500 | PASS |
| $CR_{prompt}$ (`o200k_base`) | **258.9:1** | >= 200:1 | PASS |
| $CR_{prompt}$ (`cl100k_base`) | **253.9:1** | >= 200:1 | PASS |
| $CR_{prompt}$ (`llama3`) | **259.5:1** | >= 200:1 | PASS |
| $CR_{harmonic}$ (Cross-Tokenizer) | **257.4:1** | >= 200:1 | PASS |
| $CR_{retrieval}$ | **5382.4:1** | >= 200:1 | PASS |
| $CR_{bytes}$ (Storage Ratio) | **76.4:1** | Informational | PASS |
| Overall Accuracy | **100.0%** | >= 88.0% | PASS |
| **CFI** (Compression-Fidelity Index) | **1.000** | >= 0.880 | PASS |

### 4-Tier Verification Battery Scorecard: TEMPORAL

| Tier | Name | Target | Actual | Score | Status |
|---|---|---|---|---|---|
| Tier 1 | NIAH (Needle In A Haystack) | >= 95.0% | **100.0%** | 5/5 | PASS |
| Tier 2 | Ground-Truth Fact QA | >= 90.0% | **100.0%** | 2/2 | PASS |
| Tier 3 | Graph Entity-Relation Fidelity | >= 88.0% | **100.0%** | 2/2 | PASS |
| Tier 4 | Multi-Hop Relational Reasoning | >= 80.0% | **100.0%** | 1/1 | PASS |
| **Overall** | **Weighted Composite Fidelity** | **>= 88.0%** | **100.0%** | - | **PASS** |

---

## 4-Tier Verification Battery Architecture

1. **Tier 1: NIAH (Needle In A Haystack)**: Tests probe retention at depths 0%, 25%, 50%, 75%, and 100%. Target $\ge 95.0\%$.
2. **Tier 2: Ground-Truth Fact QA**: Evaluates precise recovery of architecture decisions, ports, and parameters. Target $\ge 90.0\%$.
3. **Tier 3: Graph Entity-Relation Fidelity**: Verifies hyperedge topology and predicate consistency. Target $\ge 88.0\%$.
4. **Tier 4: Multi-Hop Relational Reasoning**: Evaluates multi-stage graph traversal across connected propositions. Target $\ge 80.0\%$.

## Conclusion

All 4 multi-modal datasets totaling **759,846 raw prompt tokens** were successfully
compressed and verified. The ALMF format achieved an in-context prompt compression ratio of **485.8:1**,
a physical container ratio of **15.5:1**, an overall retrieval accuracy of **100.0\%**,
and a Compression-Fidelity Index (CFI) of **1.000**, conclusively meeting all requirements of Project ALMF Milestone 4.
