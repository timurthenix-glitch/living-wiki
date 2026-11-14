"""
almf.query: Hybrid Knowledge Retrieval Engine & Knapsack Context Allocator.

Implements Section 7 (Query Semantics & Knapsack Context Budget Allocator)
of docs/ALMF_SPECIFICATION.md:
- Hybrid scoring: SimHash Hamming similarity + lexical keyword overlap + confidence + recency.
- Dynamic proposition filtering with temporal validity checks.
- Greedy 0/1 knapsack context allocator strictly enforcing token budget constraints (<= token_budget).
- Clean Markdown/DSL context formatting ready for agent prompt injection.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

try:
    import tiktoken
    _HAS_TIKTOKEN = True
except ImportError:  # pragma: no cover
    _HAS_TIKTOKEN = False

from almf.index import hamming_similarity, simhash64
from almf.types import (
    CompressionModality,
    Entity,
    EntityType,
    EpisodicNode,
    Fact,
    Hyperedge,
    MacroProposition,
    MemoryGraph,
)


_WORD_RE = re.compile(r"[\w\-]+", re.UNICODE)


def count_tokens(text: str, tokenizer_name: str = "o200k_base") -> int:
    """
    Accurately counts prompt tokens using tiktoken (e.g. o200k_base, cl100k_base)
    with a reliable heuristic fallback (~3.5 chars/token).
    """
    if not text:
        return 0
    if _HAS_TIKTOKEN:
        try:
            enc = tiktoken.get_encoding(tokenizer_name)
            return len(enc.encode(text, disallowed_special=()))
        except Exception:
            pass
    # Reliable character ratio fallback (3.5 chars per token for mixed Latin/Cyrillic/symbols)
    return max(1, int(math.ceil(len(text) / 3.5)))


def _extract_tokens(text: str) -> Set[str]:
    """Tokenizes text into lowercase word tokens, splitting compound identifiers."""
    if not text:
        return set()
    raw = _WORD_RE.findall(text.lower())
    tokens: Set[str] = set()
    for w in raw:
        tokens.add(w)
        subparts = re.split(r"[_\-]+", w)
        if len(subparts) > 1:
            for sp in subparts:
                if sp:
                    tokens.add(sp)
    return tokens


# =============================================================================
# 1. Graph Fact Extraction
# =============================================================================

def extract_graph_facts(
    graph: MemoryGraph,
    current_epoch: Optional[int] = None,
    include_episodes: bool = True,
    include_macro: bool = True,
) -> List[Fact]:
    """
    Extracts all active facts and propositions from an in-memory MemoryGraph.
    Converts MacroPropositions, Hyperedges, Entity configurations, and Episodic summaries into canonical Facts.
    """
    facts: List[Fact] = []
    fact_id = 1

    # Determine reference epoch for temporal validity
    all_epochs = [h.epoch for h in graph.hyperedges] + [0]
    ref_epoch = current_epoch if current_epoch is not None else max(all_epochs)

    macro_props = getattr(graph, "macro_propositions", [])
    cache_key = (
        ref_epoch,
        include_episodes,
        include_macro,
        len(graph.entities),
        len(graph.hyperedges),
        len(macro_props),
    )
    if getattr(graph, "_cached_facts_key", None) == cache_key and hasattr(graph, "_cached_facts"):
        return graph._cached_facts

    # 1. Macro-Propositions (Level 0 / Level 1 global & meso invariants)
    if include_macro and macro_props:
        for mp in macro_props:
            modality = (
                CompressionModality.TRACE_INVARIANT
                if "trace" in mp.cluster_type.lower()
                else CompressionModality.ARCH_DSL
            )
            facts.append(
                Fact(
                    id=fact_id,
                    statement=mp.statement,
                    subject=f"macro_{mp.cluster_type}_{mp.id}",
                    predicate="asserts_macro_invariant",
                    object_val=mp.statement,
                    confidence=mp.confidence,
                    provenance=f"macro_proposition_{mp.id}",
                    epoch=mp.epoch_range[1] if mp.epoch_range else ref_epoch,
                    scope=mp.cluster_type,
                    modality=modality,
                    attributes={
                        "is_macro": True,
                        "cluster_type": mp.cluster_type,
                        "member_count": mp.member_count,
                        "member_ids": mp.member_ids,
                        "epoch_range": mp.epoch_range,
                        **mp.attributes,
                    },
                )
            )
            fact_id += 1

    # 2. Hyperedge propositions
    for edge in graph.hyperedges:
        if not edge.is_active_at(ref_epoch):
            continue

        subj = graph.get_entity(edge.subject_id)
        subj_name = subj.name if subj else f"entity_{edge.subject_id}"

        obj = graph.get_entity(edge.object_id)
        obj_name = obj.name if obj else f"entity_{edge.object_id}"

        # Determine modality
        modality = CompressionModality.ARCH_DSL
        if edge.predicate.startswith("!") or "INVARIANT" in edge.predicate:
            modality = CompressionModality.TRACE_INVARIANT
        elif "AST" in edge.scope or "code" in edge.scope:
            modality = CompressionModality.AST_SKELETON

        # Build clean canonical statement
        if edge.scope and edge.scope != "global":
            stmt = f"@{subj_name}: {edge.predicate}(#{obj_name}) [scope={edge.scope}]"
        else:
            stmt = f"@{subj_name}: {edge.predicate}(#{obj_name})"

        edge_attrs: Dict[str, Any] = {}
        if subj and subj.attributes:
            edge_attrs.update(subj.attributes)

        facts.append(
            Fact(
                id=fact_id,
                statement=stmt,
                subject=subj_name,
                predicate=edge.predicate,
                object_val=obj_name,
                confidence=edge.confidence,
                provenance=f"hyperedge_{edge.id}",
                epoch=edge.epoch,
                scope=edge.scope,
                modality=modality,
                attributes=edge_attrs,
            )
        )
        fact_id += 1

    # 2. Entity Attribute facts (configurations, constraints, decisions)
    for entity in graph.entities.values():
        if not entity.attributes:
            continue

        attr_pairs = []
        for k, v in entity.attributes.items():
            if str(v).startswith("@ptr("):
                attr_pairs.append(f"{k}={v}")
            elif isinstance(v, str) and (" " in v or len(v) > 10):
                attr_pairs.append(f'{k}="{v}"')
            else:
                attr_pairs.append(f"{k}={v}")

        if attr_pairs:
            stmt = f"@{entity.name}: {', '.join(attr_pairs)}"
            mod = (
                CompressionModality.ARCH_DSL
                if entity.type in (EntityType.CONFIGURATION, EntityType.CONSTRAINT, EntityType.DECISION)
                else CompressionModality.ARCH_DSL
            )
            facts.append(
                Fact(
                    id=fact_id,
                    statement=stmt,
                    subject=entity.name,
                    predicate="configures",
                    object_val=", ".join(attr_pairs),
                    confidence=1.0,
                    provenance=f"entity_{entity.id}",
                    epoch=ref_epoch,
                    scope="global",
                    modality=mod,
                    attributes=entity.attributes,
                )
            )
            fact_id += 1

    # 3. Episodic Nodes
    if include_episodes:
        for node in graph.episodic_nodes:
            if include_macro and any(mp.statement == node.summary for mp in macro_props):
                continue
            stmt = f"!EPISODE(L{node.level}): {node.summary} [turns {node.turn_range[0]}..{node.turn_range[1]}]"
            facts.append(
                Fact(
                    id=fact_id,
                    statement=stmt,
                    subject=f"episode_L{node.level}",
                    predicate="summarizes",
                    object_val=node.summary,
                    confidence=0.95,
                    provenance=f"episodic_node_{node.id}",
                    epoch=ref_epoch,
                    scope="episodic",
                    modality=CompressionModality.ARCH_DSL,
                    attributes={"turn_range": node.turn_range, "level": node.level},
                )
            )
            fact_id += 1

    graph._cached_facts_key = cache_key
    graph._cached_facts = facts
    return facts


# =============================================================================
# 2. Hybrid Scoring Engine
# =============================================================================

@dataclass
class ScoredFact:
    """A Fact paired with its hybrid relevance score components."""
    fact: Fact
    score: float
    simhash_score: float
    lexical_score: float
    confidence_score: float
    recency_score: float


def compute_hybrid_score(
    fact: Fact,
    query: str,
    query_tokens: Set[str],
    query_simhash: int,
    max_epoch: int,
    w_simhash: float = 0.35,
    w_lexical: float = 0.35,
    w_confidence: float = 0.15,
    w_recency: float = 0.15,
) -> ScoredFact:
    """
    Computes multi-component hybrid relevance score between a query and a Fact:
      Score = w_sim * S_simhash + w_lex * S_lexical + w_conf * S_conf + w_rec * S_rec
    """
    # 1. SimHash Hamming Similarity & Feature Extraction (cached on fact)
    fact_simhash = getattr(fact, "_simhash", None)
    fact_tokens = getattr(fact, "_tokens", None)
    if fact_simhash is None or fact_tokens is None:
        fact_search_text = f"{fact.statement} {fact.subject} {fact.predicate} {fact.object_val}"
        if fact.attributes:
            for k, v in fact.attributes.items():
                fact_search_text += f" {k} {v}"
        fact_simhash = simhash64(fact_search_text)
        fact_tokens = _extract_tokens(fact_search_text)
        fact._simhash = fact_simhash
        fact._tokens = fact_tokens

    simhash_score = hamming_similarity(query_simhash, fact_simhash)

    # 2. Lexical Token Overlap (Jaccard + Recall)
    if not query_tokens:
        lexical_score = 0.0
    else:
        overlap = query_tokens & fact_tokens
        jaccard = len(overlap) / max(1, len(query_tokens | fact_tokens))
        recall = len(overlap) / max(1, len(query_tokens))
        lexical_score = 0.6 * recall + 0.4 * jaccard

    # 3. Confidence Weight
    confidence_score = max(0.0, min(1.0, fact.confidence))

    # 4. Recency Weight
    epoch_delta = max(0, max_epoch - fact.epoch)
    recency_score = math.exp(-0.02 * epoch_delta)

    # Combined weighted score
    total_score = (
        w_simhash * simhash_score
        + w_lexical * lexical_score
        + w_confidence * confidence_score
        + w_recency * recency_score
    )

    return ScoredFact(
        fact=fact,
        score=total_score,
        simhash_score=simhash_score,
        lexical_score=lexical_score,
        confidence_score=confidence_score,
        recency_score=recency_score,
    )


# =============================================================================
# 3. Query Facts
# =============================================================================

def query_facts(
    graph: MemoryGraph,
    query: str,
    top_k: int = 5,
    min_confidence: float = 0.7,
    current_epoch: Optional[int] = None,
) -> List[Fact]:
    """
    Retrieves the top-k most relevant Facts from a MemoryGraph for a given query.
    Applies temporal filtering, confidence gating, and hybrid ranking.
    """
    all_facts = extract_graph_facts(graph, current_epoch=current_epoch, include_episodes=True)
    if not all_facts:
        return []

    # Filter by confidence threshold
    valid_facts = [f for f in all_facts if f.confidence >= min_confidence]
    if not valid_facts:
        return []

    query_tokens = _extract_tokens(query)
    q_simhash = simhash64(query)
    all_epochs = [f.epoch for f in valid_facts] + [0]
    max_epoch = max(all_epochs)

    scored: List[ScoredFact] = []
    for fact in valid_facts:
        sf = compute_hybrid_score(
            fact=fact,
            query=query,
            query_tokens=query_tokens,
            query_simhash=q_simhash,
            max_epoch=max_epoch,
        )
        scored.append(sf)

    # Sort descending by total score
    scored.sort(key=lambda x: (x.score, x.lexical_score, x.simhash_score), reverse=True)

    return [s.fact for s in scored[:top_k]]


def score_fact(
    fact: Fact,
    query: str,
    max_epoch: Optional[int] = None,
    w_simhash: float = 0.35,
    w_lexical: float = 0.35,
    w_confidence: float = 0.15,
    w_recency: float = 0.15,
) -> ScoredFact:
    """Scores an individual Fact against a query using hybrid scoring."""
    q_tokens = _extract_tokens(query)
    q_simhash = simhash64(query)
    ref_max_epoch = max_epoch if max_epoch is not None else fact.epoch
    return compute_hybrid_score(
        fact=fact,
        query=query,
        query_tokens=q_tokens,
        query_simhash=q_simhash,
        max_epoch=ref_max_epoch,
        w_simhash=w_simhash,
        w_lexical=w_lexical,
        w_confidence=w_confidence,
        w_recency=w_recency,
    )


def query_subgraph(
    graph: MemoryGraph,
    query: str,
    top_k: int = 5,
    depth: int = 1,
) -> MemoryGraph:
    """
    Extracts a localized subgraph matching the query, containing the relevant
    entities, hyperedges, and macro-propositions.
    """
    subgraph = MemoryGraph(vault=graph.vault)
    top_facts = query_facts(graph, query, top_k=top_k)
    entity_names = set()
    for f in top_facts:
        entity_names.add(f.subject)
        if f.object_val:
            entity_names.add(f.object_val)

    for e in graph.entities.values():
        if e.name in entity_names:
            subgraph.add_entity(e.name, e.type, e.attributes, entity_id=e.id)

    for h in graph.hyperedges:
        s_ent = graph.get_entity(h.subject_id)
        o_ent = graph.get_entity(h.object_id)
        if (s_ent and s_ent.name in entity_names) or (o_ent and o_ent.name in entity_names):
            subgraph.add_hyperedge(
                subject_id=h.subject_id,
                predicate=h.predicate,
                object_id=h.object_id,
                scope=h.scope,
                epoch=h.epoch,
                confidence=h.confidence,
                temporal_validity=h.temporal_validity,
                edge_id=h.id,
            )

    for mp in getattr(graph, "macro_propositions", []):
        subgraph.add_macro_proposition(mp)

    return subgraph


# =============================================================================
# 4. Greedy Knapsack Context Allocator & Renderer
# =============================================================================

def render_context(
    graph: MemoryGraph,
    query: str,
    token_budget: int = 500,
    tokenizer_name: str = "o200k_base",
    min_confidence: float = 0.0,
    relevance_floor: float = 0.20,
) -> str:
    """
    Renders an optimal, bounded prompt context answering query within token_budget.
    Uses a greedy 0/1 knapsack optimization algorithm with dynamic relevance floor:
      max sum(Utility_i * x_i) subject to sum(Cost_i * x_i) <= token_budget.

    The rendered output is strictly guaranteed to have token count <= token_budget.
    """
    if token_budget <= 0:
        return ""

    all_facts = extract_graph_facts(graph, include_episodes=True, include_macro=True)
    if not all_facts:
        return ""

    valid_facts = [f for f in all_facts if f.confidence >= min_confidence]
    if not valid_facts:
        return ""

    query_tokens = _extract_tokens(query)
    q_simhash = simhash64(query)
    max_epoch = max([f.epoch for f in valid_facts] + [0])

    # 1. Score all candidate facts with fast estimated cost and macro boost
    scored_facts: List[Tuple[ScoredFact, int, float]] = []
    for fact in valid_facts:
        sf = compute_hybrid_score(
            fact=fact,
            query=query,
            query_tokens=query_tokens,
            query_simhash=q_simhash,
            max_epoch=max_epoch,
        )
        est_cost = max(1, len(fact.statement) // 4)
        member_boost = 1.0
        if fact.attributes.get("is_macro", False):
            cnt = fact.attributes.get("member_count", 1)
            member_boost = 1.0 + 0.15 * min(10, math.log2(max(2, cnt)))

        density = (sf.score * member_boost) / est_cost
        scored_facts.append((sf, est_cost, density))

    # 2. Dynamic Relevance Floor Cutoff
    max_score = max((sf.score for sf, _, _ in scored_facts), default=0.0)
    if max_score <= 0.0 or (relevance_floor > 0.0 and max_score < relevance_floor):
        return ""

    if relevance_floor > 0.0:
        if not query or not query.strip() or not query_tokens:
            return ""
        dynamic_floor = max(relevance_floor, 0.65 * max_score)
        filtered_candidates = [
            item for item in scored_facts
            if item[0].score >= dynamic_floor and (
                item[0].lexical_score > 0.0
                or item[0].simhash_score >= 0.70
            )
        ]
    else:
        filtered_candidates = list(scored_facts)

    if not filtered_candidates:
        return ""

    filtered_candidates.sort(key=lambda item: (item[0].score * 0.6 + item[2] * 0.4), reverse=True)

    # 3. Build header if token budget accommodates it comfortably
    header = f"[ALMF-STATE: epoch={max_epoch} active_budget={token_budget}tok]\n"
    header_tokens = count_tokens(header, tokenizer_name=tokenizer_name)

    chosen_lines: List[str] = []
    if token_budget >= 40 and header_tokens < token_budget // 2 and len(filtered_candidates) > 2:
        chosen_lines.append(header.strip())
        current_tokens = count_tokens("\n".join(chosen_lines) + "\n", tokenizer_name=tokenizer_name)
    else:
        current_tokens = 0

    # 4. Greedy Knapsack Selection
    seen_statements: Set[str] = set()

    for sf, cost, _ in filtered_candidates[:100]:
        stmt = sf.fact.statement
        if stmt in seen_statements:
            continue

        tentative = list(chosen_lines) + [stmt]
        tentative_text = "\n".join(tentative)
        tentative_tokens = count_tokens(tentative_text, tokenizer_name=tokenizer_name)

        if tentative_tokens <= token_budget:
            chosen_lines.append(stmt)
            seen_statements.add(stmt)
            current_tokens = tentative_tokens
            if current_tokens >= token_budget:
                break

    # 5. Strict Budget Invariant Guarantee (<= token_budget)
    result = "\n".join(chosen_lines).strip()
    while result and count_tokens(result, tokenizer_name=tokenizer_name) > token_budget:
        if "\n" in result:
            result = result.rsplit("\n", 1)[0].strip()
        else:
            words = result.split()
            if len(words) > 1:
                result = " ".join(words[:-1])
            else:
                result = ""

    return result
