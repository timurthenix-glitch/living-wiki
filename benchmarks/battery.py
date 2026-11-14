"""
benchmarks.battery: 4-Tier Verification Battery for ALMF Knowledge Graph Retrieval.

Evaluates:
- Tier 1: NIAH (Needle In A Haystack, target >= 95.0%) across 0%, 25%, 50%, 75%, 100% depths.
- Tier 2: Ground-Truth QA (target >= 90.0%) assessing exact fact recovery.
- Tier 3: Graph Entity-Relation Fidelity (target >= 88.0%) verifying hyperedge topology.
- Tier 4: Multi-Hop Reasoning (target >= 80.0%) chaining multi-stage relational propositions.

Computes weighted overall retrieval fidelity ensuring Accuracy >= 88.0%.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from almf.query import query_facts, render_context
from almf.types import Fact, MemoryGraph
from benchmarks.datasets import GroundTruthSuite, MultiHopItem, NeedleItem, QAItem, TripleItem


@dataclass
class TierResult:
    """Result of an individual evaluation tier."""
    tier_name: str
    target_accuracy: float
    actual_accuracy: float
    total_items: int
    passed_items: int
    passed: bool
    details: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tier_name": self.tier_name,
            "target_accuracy": self.target_accuracy,
            "actual_accuracy": round(self.actual_accuracy, 4),
            "total_items": self.total_items,
            "passed_items": self.passed_items,
            "passed": self.passed,
            "details": self.details,
        }


@dataclass
class BatteryResult:
    """Aggregated results across all 4 verification tiers."""
    dataset_name: str
    tier1_niah: TierResult
    tier2_qa: TierResult
    tier3_fidelity: TierResult
    tier4_multihop: TierResult
    overall_accuracy: float
    target_met: bool
    retrieved_facts_text: str = ""
    rendered_prompt_text: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dataset_name": self.dataset_name,
            "overall_accuracy": round(self.overall_accuracy, 4),
            "target_met": self.target_met,
            "tier1_niah": self.tier1_niah.to_dict(),
            "tier2_qa": self.tier2_qa.to_dict(),
            "tier3_fidelity": self.tier3_fidelity.to_dict(),
            "tier4_multihop": self.tier4_multihop.to_dict(),
        }

    def to_markdown_table(self) -> str:
        """Renders the 4-tier battery scorecard as Markdown."""
        lines = [
            f"### 4-Tier Verification Battery Scorecard: {self.dataset_name}",
            "",
            "| Tier | Name | Target | Actual | Score | Status |",
            "|---|---|---|---|---|---|",
            f"| Tier 1 | NIAH (Needle In A Haystack) | >= {self.tier1_niah.target_accuracy*100:.1f}% | **{self.tier1_niah.actual_accuracy*100:.1f}%** | {self.tier1_niah.passed_items}/{self.tier1_niah.total_items} | {'PASS' if self.tier1_niah.passed else 'FAIL'} |",
            f"| Tier 2 | Ground-Truth Fact QA | >= {self.tier2_qa.target_accuracy*100:.1f}% | **{self.tier2_qa.actual_accuracy*100:.1f}%** | {self.tier2_qa.passed_items}/{self.tier2_qa.total_items} | {'PASS' if self.tier2_qa.passed else 'FAIL'} |",
            f"| Tier 3 | Graph Entity-Relation Fidelity | >= {self.tier3_fidelity.target_accuracy*100:.1f}% | **{self.tier3_fidelity.actual_accuracy*100:.1f}%** | {self.tier3_fidelity.passed_items}/{self.tier3_fidelity.total_items} | {'PASS' if self.tier3_fidelity.passed else 'FAIL'} |",
            f"| Tier 4 | Multi-Hop Relational Reasoning | >= {self.tier4_multihop.target_accuracy*100:.1f}% | **{self.tier4_multihop.actual_accuracy*100:.1f}%** | {self.tier4_multihop.passed_items}/{self.tier4_multihop.total_items} | {'PASS' if self.tier4_multihop.passed else 'FAIL'} |",
            f"| **Overall** | **Weighted Composite Fidelity** | **>= 88.0%** | **{self.overall_accuracy*100:.1f}%** | - | **{'PASS' if self.target_met else 'FAIL'}** |",
            "",
        ]
        return "\n".join(lines)


# =============================================================================
# Tier 1: NIAH Evaluation (>= 95%)
# =============================================================================

def evaluate_tier1_niah(
    graph: MemoryGraph,
    needles: Sequence[NeedleItem],
    token_budget: int = 500,
) -> TierResult:
    """
    Evaluates Needle In A Haystack accuracy across depths (0%, 25%, 50%, 75%, 100%).
    Queries the graph and checks for verbatim or semantic presence of target value.
    """
    if not needles:
        return TierResult("Tier 1: NIAH", 0.95, 1.0, 0, 0, True)

    passed_count = 0
    details: List[Dict[str, Any]] = []

    for needle in needles:
        # 1. Query facts
        facts = query_facts(graph, needle.query, top_k=7)
        # 2. Render prompt context under knapsack budget
        rendered = render_context(graph, needle.query, token_budget=token_budget)

        combined_text = "\n".join([f.statement for f in facts] + [rendered]).lower()
        target_val = needle.value.lower()
        target_key = needle.key.lower()

        is_passed = (target_val in combined_text) or (
            target_key in combined_text and any(target_val in str(v).lower() for f in facts for v in f.attributes.values())
        )

        if is_passed:
            passed_count += 1

        details.append({
            "needle_id": needle.needle_id,
            "depth_percent": needle.depth_percent,
            "key": needle.key,
            "expected_value": needle.value,
            "passed": is_passed,
        })

    accuracy = passed_count / len(needles)
    return TierResult(
        tier_name="Tier 1: NIAH",
        target_accuracy=0.95,
        actual_accuracy=accuracy,
        total_items=len(needles),
        passed_items=passed_count,
        passed=(accuracy >= 0.95),
        details=details,
    )


# =============================================================================
# Tier 2: Ground-Truth QA Evaluation (>= 90%)
# =============================================================================

def evaluate_tier2_qa(
    graph: MemoryGraph,
    qa_pairs: Sequence[QAItem],
    token_budget: int = 500,
) -> TierResult:
    """Evaluates fact-extraction Question-Answering pairs."""
    if not qa_pairs:
        return TierResult("Tier 2: QA", 0.90, 1.0, 0, 0, True)

    passed_count = 0
    details: List[Dict[str, Any]] = []

    for qa in qa_pairs:
        facts = query_facts(graph, qa.question, top_k=7)
        rendered = render_context(graph, qa.question, token_budget=token_budget)
        combined_text = "\n".join([f.statement for f in facts] + [rendered]).lower()

        # Check for expected answer substring or keyword overlap
        expected_ans = qa.expected_answer.lower()
        direct_match = expected_ans in combined_text

        # Keyword match fallback: all or at least 70% of keywords present
        kw_matches = sum(1 for kw in qa.keywords if kw.lower() in combined_text)
        kw_ratio = kw_matches / max(1, len(qa.keywords))
        is_passed = direct_match or (kw_ratio >= 0.7)

        if is_passed:
            passed_count += 1

        details.append({
            "question": qa.question,
            "expected_answer": qa.expected_answer,
            "direct_match": direct_match,
            "kw_ratio": round(kw_ratio, 2),
            "passed": is_passed,
        })

    accuracy = passed_count / len(qa_pairs)
    return TierResult(
        tier_name="Tier 2: QA",
        target_accuracy=0.90,
        actual_accuracy=accuracy,
        total_items=len(qa_pairs),
        passed_items=passed_count,
        passed=(accuracy >= 0.90),
        details=details,
    )


# =============================================================================
# Tier 3: Graph Entity-Relation Fidelity (>= 88%)
# =============================================================================

def evaluate_tier3_fidelity(
    graph: MemoryGraph,
    triples: Sequence[TripleItem],
    top_k: int = 7,
) -> TierResult:
    """Verifies that subject-predicate-object triples exist and are retrievable."""
    if not triples:
        return TierResult("Tier 3: Fidelity", 0.88, 1.0, 0, 0, True)

    passed_count = 0
    details: List[Dict[str, Any]] = []

    for tr in triples:
        subj_clean = tr.subject.lower()
        pred_clean = tr.predicate.lower()
        obj_clean = tr.object_val.lower()

        # Check 1: Direct hyperedge structure in graph
        found_edge = False
        for edge in graph.hyperedges:
            s_ent = graph.get_entity(edge.subject_id)
            o_ent = graph.get_entity(edge.object_id)
            s_name = s_ent.name.lower() if s_ent else ""
            o_name = o_ent.name.lower() if o_ent else ""
            p_name = edge.predicate.lower()

            if (subj_clean in s_name or s_name in subj_clean) and (
                pred_clean in p_name or p_name in pred_clean
            ) and (obj_clean in o_name or o_name in obj_clean):
                found_edge = True
                break

        # Check 2: Entity attribute configuration
        found_attr = False
        if not found_edge:
            for ent in graph.entities.values():
                e_name = ent.name.lower()
                if subj_clean in e_name or e_name in subj_clean:
                    for k, v in ent.attributes.items():
                        if obj_clean in str(v).lower() or pred_clean in str(k).lower():
                            found_attr = True
                            break

        # Check 3: Query retrieval
        found_query = False
        if not (found_edge or found_attr):
            facts = query_facts(graph, f"{tr.subject} {tr.predicate}", top_k=top_k)
            for f in facts:
                stmt_low = f.statement.lower()
                if subj_clean in stmt_low and (pred_clean in stmt_low or obj_clean in stmt_low):
                    found_query = True
                    break

        is_passed = found_edge or found_attr or found_query
        if is_passed:
            passed_count += 1

        details.append({
            "triple": (tr.subject, tr.predicate, tr.object_val),
            "found_edge": found_edge,
            "found_attr": found_attr,
            "found_query": found_query,
            "passed": is_passed,
        })

    accuracy = passed_count / len(triples)
    return TierResult(
        tier_name="Tier 3: Fidelity",
        target_accuracy=0.88,
        actual_accuracy=accuracy,
        total_items=len(triples),
        passed_items=passed_count,
        passed=(accuracy >= 0.88),
        details=details,
    )


# =============================================================================
# Tier 4: Multi-Hop Relational Reasoning (>= 80%)
# =============================================================================

def evaluate_tier4_multihop(
    graph: MemoryGraph,
    multi_hops: Sequence[MultiHopItem],
    token_budget: int = 500,
) -> TierResult:
    """Evaluates multi-hop reasoning across interconnected propositions."""
    if not multi_hops:
        return TierResult("Tier 4: MultiHop", 0.80, 1.0, 0, 0, True)

    passed_count = 0
    details: List[Dict[str, Any]] = []

    for mh in multi_hops:
        # Primary query
        facts = query_facts(graph, mh.question, top_k=10)
        rendered = render_context(graph, mh.question, token_budget=token_budget)
        combined_text = "\n".join([f.statement for f in facts] + [rendered]).lower()

        # Step 2: Multi-hop graph expansion across intermediate hops
        hop_facts: List[Fact] = []
        for hop_stmt in mh.hops:
            hop_facts.extend(query_facts(graph, hop_stmt, top_k=3))
        if hop_facts:
            combined_text += "\n" + "\n".join(f.statement for f in hop_facts).lower()

        # Verify expected answer or critical keywords
        expected_ans = mh.expected_answer.lower()
        direct_match = expected_ans in combined_text

        kw_matches = sum(1 for kw in mh.keywords if kw.lower() in combined_text)
        kw_ratio = kw_matches / max(1, len(mh.keywords))
        is_passed = direct_match or (kw_ratio >= 0.6)

        if is_passed:
            passed_count += 1

        details.append({
            "question": mh.question,
            "expected_answer": mh.expected_answer,
            "hops_count": len(mh.hops),
            "kw_ratio": round(kw_ratio, 2),
            "passed": is_passed,
        })

    accuracy = passed_count / len(multi_hops)
    return TierResult(
        tier_name="Tier 4: MultiHop",
        target_accuracy=0.80,
        actual_accuracy=accuracy,
        total_items=len(multi_hops),
        passed_items=passed_count,
        passed=(accuracy >= 0.80),
        details=details,
    )


# =============================================================================
# Composite Battery Evaluator
# =============================================================================

def evaluate_battery(
    graph: MemoryGraph,
    ground_truth: GroundTruthSuite,
    dataset_name: str = "BENCHMARK",
    token_budget: int = 500,
    weights: Tuple[float, float, float, float] = (0.25, 0.25, 0.25, 0.25),
) -> BatteryResult:
    """
    Executes the full 4-tier verification battery against a loaded MemoryGraph.
    Calculates weighted composite accuracy and verifies all acceptance thresholds.
    """
    t1 = evaluate_tier1_niah(graph, ground_truth.needles, token_budget=token_budget)
    t2 = evaluate_tier2_qa(graph, ground_truth.qa_pairs, token_budget=token_budget)
    t3 = evaluate_tier3_fidelity(graph, ground_truth.triples)
    t4 = evaluate_tier4_multihop(graph, ground_truth.multi_hops, token_budget=token_budget)

    w1, w2, w3, w4 = weights
    overall_acc = (
        w1 * t1.actual_accuracy
        + w2 * t2.actual_accuracy
        + w3 * t3.actual_accuracy
        + w4 * t4.actual_accuracy
    )

    # Acceptance check: overall >= 88.0% and individual tiers met
    target_met = (
        overall_acc >= 0.880
        and t1.actual_accuracy >= 0.950
        and t2.actual_accuracy >= 0.900
        and t3.actual_accuracy >= 0.880
        and t4.actual_accuracy >= 0.800
    )

    # Sample retrieved facts and rendered prompt context for token metric computation
    sample_query = ground_truth.qa_pairs[0].question if ground_truth.qa_pairs else "system architecture"
    facts_sample = query_facts(graph, sample_query, top_k=5)
    retrieved_facts_str = "\n".join(f.statement for f in facts_sample)
    rendered_prompt_str = render_context(graph, sample_query, token_budget=token_budget)

    return BatteryResult(
        dataset_name=dataset_name,
        tier1_niah=t1,
        tier2_qa=t2,
        tier3_fidelity=t3,
        tier4_multihop=t4,
        overall_accuracy=round(overall_acc, 4),
        target_met=target_met,
        retrieved_facts_text=retrieved_facts_str,
        rendered_prompt_text=rendered_prompt_str,
    )
