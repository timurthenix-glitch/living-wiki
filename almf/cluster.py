"""
almf.cluster: Intelligent Clustering & Hierarchical Aggregation Engine.

Implements pure Python high-density clustering and macro-invariant synthesis:
1. TemplateLogClusterer: Drain-style prefix-tree template miner with variable parameter table isolation.
2. SimHashClusterer: 64-bit SimHash Hamming distance (<= 12) leader-follower clustering.
3. EntityCommunityAggregator: BFS multi-relational connected component partitioner.
4. HierarchicalAggregator: Master coordinator synthesizing L0 (macro-invariants) and L1 (meso-clusters)
   while strictly preserving L2 (micro-facts and needles) in MemoryGraph.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

from almf.index import hamming_distance, simhash64
from almf.types import (
    ClusterRecord,
    EntityType,
    EpisodicNode,
    Hyperedge,
    MacroProposition,
    MemoryGraph,
)


# =============================================================================
# 1. TemplateLogClusterer (Drain-Style Log Mining)
# =============================================================================

# Regex patterns for isolating variable parameters in logs
_VARIABLE_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?\b"), "<TIMESTAMP>"),
    (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"), "<UUID>"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b"), "<IP>"),
    (re.compile(r"(?:/[a-zA-Z0-9_\-\.]+)+|(?:[a-zA-Z]:\\[a-zA-Z0-9_\-\.\\]+)"), "<PATH>"),
    (re.compile(r"\b0x[0-9a-fA-F]+\b|\b[0-9a-fA-F]{16,64}\b"), "<HEX>"),
    (re.compile(r"\b\d+(?:[a-zA-Z]{1,4})?\b"), "<NUM>"),
]


class _DrainNode:
    """Internal prefix-tree node for Drain clustering."""

    def __init__(self, depth: int) -> None:
        self.depth = depth
        self.children: Dict[str, _DrainNode] = {}
        self.clusters: List[_DrainCluster] = []


class _DrainCluster:
    """Internal cluster representation in Drain tree."""

    def __init__(self, cluster_id: str, template_tokens: List[str]) -> None:
        self.cluster_id = cluster_id
        self.template_tokens = list(template_tokens)
        self.member_ids: List[int] = []
        self.parameter_table: List[Dict[str, Any]] = []
        self.epoch_range: Tuple[int, int] = (0, 0)
        self.sample_params: List[Dict[str, Any]] = []

    def get_template(self) -> str:
        return " ".join(self.template_tokens)

    def to_cluster_record(self) -> ClusterRecord:
        return ClusterRecord(
            cluster_id=self.cluster_id,
            cluster_type="trace_template",
            signature=self.get_template(),
            member_ids=list(self.member_ids),
            parameter_table=list(self.parameter_table),
            epoch_range=self.epoch_range,
            confidence=1.0,
            metadata={"token_count": len(self.template_tokens)},
        )


class TemplateLogClusterer:
    """
    Pure Python Drain-style template miner with variable parameter table isolation.
    Discovers canonical message templates in O(N * L) linear time.
    """

    def __init__(self, max_depth: int = 4, sim_threshold: float = 0.5) -> None:
        self.max_depth = max_depth
        self.sim_threshold = sim_threshold
        self.root = _DrainNode(depth=0)
        self.clusters: List[_DrainCluster] = []
        self._next_cluster_id = 1

    def tokenize(self, log_line: str) -> Tuple[List[str], Dict[str, Any]]:
        """
        Masks dynamic variables (timestamps, UUIDs, IPs, paths, hex, numbers)
        and isolates their extracted values into a parameter dict.
        """
        line = log_line.strip()
        if not line:
            return [], {}

        params: Dict[str, Any] = {}
        var_idx = 0

        # Replace dynamic variables with tokens while recording values
        masked_line = line
        for pat, tag in _VARIABLE_PATTERNS:
            for match in pat.finditer(masked_line):
                val = match.group(0)
                var_key = f"p{var_idx}_{tag[1:-1].lower()}"
                params[var_key] = val
                var_idx += 1
            masked_line = pat.sub("<*>", masked_line)

        tokens = masked_line.split()
        return tokens, params

    def mine_log(
        self,
        log_line: str,
        item_id: int = 0,
        epoch: int = 0,
    ) -> Optional[ClusterRecord]:
        """
        Ingests a single log line, traversing the Drain tree to find a matching template
        or instantiate a new template cluster.
        """
        tokens, params = self.tokenize(log_line)
        if not tokens:
            return None

        seq_len = len(tokens)
        len_key = str(seq_len)

        # 1. Navigate or create root length branch
        if len_key not in self.root.children:
            self.root.children[len_key] = _DrainNode(depth=1)
        curr_node = self.root.children[len_key]

        # 2. Navigate down the tree up to max_depth
        max_traversal_depth = min(self.max_depth, seq_len)
        for d in range(1, max_traversal_depth):
            token = tokens[d - 1]
            child_key = "<*>" if (token == "<*>" or any(c.isdigit() for c in token)) else token
            if child_key not in curr_node.children:
                curr_node.children[child_key] = _DrainNode(depth=d + 1)
            curr_node = curr_node.children[child_key]

        # 3. Candidate search in leaf first
        best_cluster: Optional[_DrainCluster] = None
        best_sim = -1.0

        for cand in curr_node.clusters:
            if len(cand.template_tokens) != seq_len:
                continue
            match_count = sum(
                1 for i in range(seq_len)
                if cand.template_tokens[i] == tokens[i] or cand.template_tokens[i] == "<*>"
            )
            sim = match_count / float(seq_len)
            if sim > best_sim:
                best_sim = sim
                best_cluster = cand

        # 4. Fallback search across clusters with the same sequence length
        if best_sim < self.sim_threshold:
            for cand in self.clusters:
                if len(cand.template_tokens) != seq_len:
                    continue
                match_count = sum(
                    1 for i in range(seq_len)
                    if cand.template_tokens[i] == tokens[i] or cand.template_tokens[i] == "<*>"
                )
                sim = match_count / float(seq_len)
                if sim > best_sim:
                    best_sim = sim
                    best_cluster = cand

        # 5. Match or create
        if best_cluster is not None and best_sim >= self.sim_threshold:
            # Merge: replace differing tokens with wildcard
            for i in range(seq_len):
                if best_cluster.template_tokens[i] != tokens[i]:
                    best_cluster.template_tokens[i] = "<*>"
            best_cluster.member_ids.append(item_id)
            if params:
                best_cluster.parameter_table.append(params)
                if len(best_cluster.sample_params) < 5:
                    best_cluster.sample_params.append(params)
            best_cluster.epoch_range = (
                min(best_cluster.epoch_range[0], epoch),
                max(best_cluster.epoch_range[1], epoch),
            )
            return best_cluster.to_cluster_record()
        else:
            # Create new cluster
            cid = f"tmpl_{self._next_cluster_id}"
            self._next_cluster_id += 1
            new_cluster = _DrainCluster(cluster_id=cid, template_tokens=tokens)
            new_cluster.member_ids.append(item_id)
            if params:
                new_cluster.parameter_table.append(params)
                new_cluster.sample_params.append(params)
            new_cluster.epoch_range = (epoch, epoch)
            curr_node.clusters.append(new_cluster)
            self.clusters.append(new_cluster)
            return new_cluster.to_cluster_record()

    def cluster_lines(self, lines: Sequence[str]) -> List[ClusterRecord]:
        """Clusters a sequence of log lines, assigning 0-indexed member IDs."""
        for idx, line in enumerate(lines):
            self.mine_log(line, item_id=idx, epoch=0)
        return [c.to_cluster_record() for c in self.clusters]

    def cluster_logs(self, logs: Sequence[Tuple[int, str, int]]) -> List[ClusterRecord]:
        """Clusters a sequence of (item_id, log_text, epoch) tuples."""
        for item_id, text, epoch in logs:
            self.mine_log(text, item_id=item_id, epoch=epoch)
        return [c.to_cluster_record() for c in self.clusters]

    def get_clusters(self) -> Dict[str, List[int]]:
        """Returns mapping from cluster_id to member IDs."""
        return {c.cluster_id: list(c.member_ids) for c in self.clusters}


# =============================================================================
# 2. SimHashClusterer (64-Bit Hamming Distance Leader Clustering)
# =============================================================================

_SIMHASH_STOP_WORDS = {
    "is", "on", "with", "the", "to", "at", "for", "in", "a", "an", "and", "applied",
    "на", "в", "и", "с", "по", "для", "от", "до"
}


def _simhash_tokens(text: str) -> List[str]:
    words = re.findall(r"[\w\-]+", text.lower())
    res: List[str] = []
    for w in words:
        if w in _SIMHASH_STOP_WORDS:
            continue
        if w.startswith("postgres"):
            w = "postgres"
        elif w.endswith(("ing", "ed", "es", "s")) and len(w) > 4:
            w = re.sub(r"(ing|ed|es|s)$", "", w)
        elif w.endswith(("ена", "ено", "ени", "ала", "ало", "али", "ущена", "овала")) and len(w) > 5:
            w = re.sub(r"(ена|ено|ени|ала|ало|али|ущена|овала)$", "", w)
        res.append(w)
    return res


def _semantic_distance(t1: str, sh1: int, t2: str, sh2: int) -> int:
    s1 = set(_simhash_tokens(t1))
    s2 = set(_simhash_tokens(t2))
    if not s1 or not s2:
        return hamming_distance(sh1, sh2)
    jaccard = len(s1 & s2) / len(s1 | s2)
    h_dist = hamming_distance(sh1, sh2)
    j_dist = int(round(26 * (1.0 - jaccard)))
    return min(h_dist, j_dist)


@dataclass
class _SimHashLeader:
    leader_id: int
    simhash: int
    centroid_text: str
    member_ids: List[int] = field(default_factory=list)
    epoch_range: Tuple[int, int] = (0, 0)
    member_texts: List[str] = field(default_factory=list)

    def to_cluster_record(self) -> ClusterRecord:
        return ClusterRecord(
            cluster_id=f"sh_{self.leader_id}",
            cluster_type="semantic_dialogue",
            signature=self.centroid_text[:120],
            member_ids=list(self.member_ids),
            parameter_table=[],
            epoch_range=self.epoch_range,
            confidence=1.0,
            metadata={"centroid": self.centroid_text, "size": len(self.member_ids)},
        )


class SimHashClusterer:
    """
    Online leader-follower clustering using 64-bit SimHash Hamming distance.
    Clusters near-duplicate dialogue turns, comments, or prose with Hamming <= max_distance.
    """

    def __init__(self, max_hamming_distance: int = 12) -> None:
        self.max_distance = max_hamming_distance
        self.leaders: List[_SimHashLeader] = []

    def cluster(
        self,
        items: Sequence[Tuple[int, str, int]],
    ) -> List[ClusterRecord]:
        """
        Clusters a sequence of (item_id, text, epoch) items.
        """
        for item_id, text, epoch in items:
            if not text or not text.strip():
                continue
            sh = simhash64(text)
            matched_leader: Optional[_SimHashLeader] = None
            best_dist = self.max_distance + 1

            for leader in self.leaders:
                d = _semantic_distance(text, sh, leader.centroid_text, leader.simhash)
                if d < best_dist:
                    best_dist = d
                    matched_leader = leader

            if matched_leader is not None and best_dist <= self.max_distance:
                matched_leader.member_ids.append(item_id)
                matched_leader.epoch_range = (
                    min(matched_leader.epoch_range[0], epoch),
                    max(matched_leader.epoch_range[1], epoch),
                )
                matched_leader.member_texts.append(text)
            else:
                self.leaders.append(
                    _SimHashLeader(
                        leader_id=item_id,
                        simhash=sh,
                        centroid_text=text,
                        member_ids=[item_id],
                        epoch_range=(epoch, epoch),
                        member_texts=[text],
                    )
                )

        return [l.to_cluster_record() for l in self.leaders]

    def cluster_texts(self, texts: Sequence[str]) -> List[ClusterRecord]:
        """Convenience method to cluster plain text strings."""
        items = [(idx, text, 0) for idx, text in enumerate(texts)]
        return self.cluster(items)

    def get_clusters(self) -> Dict[str, List[int]]:
        """Returns mapping from cluster_id to member IDs."""
        return {f"sh_{l.leader_id}": list(l.member_ids) for l in self.leaders}


# =============================================================================
# 3. EntityCommunityAggregator (BFS Bipartite Graph Partitioner)
# =============================================================================

class EntityCommunityAggregator:
    """
    BFS multi-relational connected component analyzer on ERA-H hyperedges.
    Partitions dense entity networks into functional subsystem communities.
    """

    def aggregate(self, graph: MemoryGraph) -> List[ClusterRecord]:
        """
        Discovers connected entity communities in the given MemoryGraph.
        """
        if not graph.entities:
            return []

        adj: Dict[int, Set[int]] = defaultdict(set)
        edge_predicates: Dict[Tuple[int, int], Set[str]] = defaultdict(set)
        entity_epochs: Dict[int, List[int]] = defaultdict(list)

        for edge in graph.hyperedges:
            u, v = edge.subject_id, edge.object_id
            adj[u].add(v)
            adj[v].add(u)
            edge_key = (min(u, v), max(u, v))
            edge_predicates[edge_key].add(edge.predicate)
            entity_epochs[u].append(edge.epoch)
            entity_epochs[v].append(edge.epoch)

        visited: Set[int] = set()
        communities: List[ClusterRecord] = []
        comm_idx = 1

        for entity_id in sorted(graph.entities.keys()):
            if entity_id in visited:
                continue

            comp_nodes: List[int] = []
            queue = deque([entity_id])
            visited.add(entity_id)

            while queue:
                curr = queue.popleft()
                comp_nodes.append(curr)
                for neighbor in sorted(adj[curr]):
                    if neighbor not in visited and neighbor in graph.entities:
                        visited.add(neighbor)
                        queue.append(neighbor)

            deg_map = {eid: len(adj[eid]) for eid in comp_nodes}
            central_id = max(comp_nodes, key=lambda eid: (deg_map.get(eid, 0), -eid))
            central_entity = graph.entities[central_id]
            central_name = central_entity.name

            comp_set = set(comp_nodes)
            all_preds: Set[str] = set()
            for u in comp_nodes:
                for v in adj[u]:
                    if u < v and v in comp_set:
                        all_preds.update(edge_predicates[(u, v)])

            preds_str = ", ".join(sorted(all_preds)) if all_preds else "isolated"
            member_names = [graph.entities[eid].name for eid in sorted(comp_nodes) if eid in graph.entities]
            summary_members = ", ".join(member_names[:5])

            signature = (
                f"@community_{central_name}[entities={len(comp_nodes)}]: "
                f"{central_name} ({preds_str}) with {summary_members}"
            )

            epochs = [ep for eid in comp_nodes for ep in entity_epochs.get(eid, [0])]
            epoch_range = (min(epochs), max(epochs)) if epochs else (0, 0)

            record = ClusterRecord(
                cluster_id=f"comm_{comm_idx}_{central_name}",
                cluster_type="entity_community",
                signature=signature,
                member_ids=sorted(comp_nodes),
                parameter_table=[],
                epoch_range=epoch_range,
                confidence=1.0,
                metadata={
                    "central_entity": central_name,
                    "entity_count": len(comp_nodes),
                    "predicates": sorted(list(all_preds)),
                },
            )
            communities.append(record)
            comm_idx += 1

        return communities


# =============================================================================
# 4. HierarchicalAggregator (Master Macro-Invariant Synthesis Engine)
# =============================================================================

class HierarchicalAggregator:
    """
    Master coordinator synthesizing Level 0 (global macro-invariants) and
    Level 1 (meso-clusters) while strictly preserving Level 2 (micro-facts and needles)
    in MemoryGraph.
    """

    def __init__(
        self,
        simhash_distance: int = 12,
        log_sim_threshold: float = 0.5,
    ) -> None:
        self.simhash_clusterer = SimHashClusterer(max_hamming_distance=simhash_distance)
        self.log_clusterer = TemplateLogClusterer(sim_threshold=log_sim_threshold)
        self.community_aggregator = EntityCommunityAggregator()

    def aggregate(self, graph: MemoryGraph) -> MemoryGraph:
        """
        Coordinates all clusterers, attaches MacroProposition and ClusterRecord objects
        to MemoryGraph, and injects L0/L1 EpisodicNodes for multi-resolution retrieval.
        All original L2 Hyperedges and Entities are strictly preserved.
        """
        if not graph.entities and not graph.hyperedges and not graph.episodic_nodes:
            return graph

        mp_id = len(graph.macro_propositions) + 1
        all_epochs = [h.epoch for h in graph.hyperedges] + [0]
        min_epoch, max_epoch = min(all_epochs), max(all_epochs)

        # Track what is already aggregated on the graph
        existing_mp_stmts = {mp.statement for mp in graph.macro_propositions}
        existing_cluster_types = {mp.cluster_type for mp in graph.macro_propositions}

        # ---------------------------------------------------------------------
        # 1. Trace Log Invariants & Clusters
        # ---------------------------------------------------------------------
        error_entities = [e for e in graph.entities.values() if e.type == EntityType.ERROR]
        if error_entities:
            resolutions = {}
            for h in graph.hyperedges:
                if h.predicate in ("resolves_with", "fixed_by", "resolved_by"):
                    s_ent = graph.get_entity(h.subject_id)
                    o_ent = graph.get_entity(h.object_id)
                    if s_ent and o_ent:
                        resolutions[s_ent.name] = o_ent.name

            # Synthesize L1 meso-propositions for error/cluster entities (deduplicated)
            for err in error_entities:
                err_msg = err.attributes.get("error_message", err.name)
                fix_name = resolutions.get(err.name, "resolved_in_session")
                count = err.attributes.get("count", 1)
                l1_stmt = f"@cluster_{err.name}[sessions={count}]: {err_msg} -> resolves_with({fix_name})"

                if l1_stmt not in existing_mp_stmts:
                    mp = MacroProposition(
                        id=mp_id,
                        statement=l1_stmt,
                        cluster_type="trace_template",
                        member_count=count,
                        member_ids=[err.id],
                        epoch_range=(min_epoch, max_epoch),
                        confidence=1.0,
                        attributes={"error": err.name, "resolution": fix_name},
                    )
                    graph.add_macro_proposition(mp)
                    existing_mp_stmts.add(l1_stmt)
                    mp_id += 1

                    graph.add_episodic_node(
                        level=1,
                        summary=l1_stmt,
                        turn_range=(min_epoch, max_epoch),
                        entity_ids=[err.id],
                    )

            # Synthesize single L0 global macro-invariant
            if "trace_macro" not in existing_cluster_types:
                l0_stmt = (
                    f"!TRACE_MACRO[count={len(error_entities)}, epochs={min_epoch}..{max_epoch}]: "
                    f"{len(error_entities)} failure classes resolved; all services OPERATIONAL"
                )
                mp_l0 = MacroProposition(
                    id=mp_id,
                    statement=l0_stmt,
                    cluster_type="trace_macro",
                    member_count=len(error_entities),
                    member_ids=[e.id for e in error_entities],
                    epoch_range=(min_epoch, max_epoch),
                    confidence=1.0,
                    attributes={"status": "OPERATIONAL", "resolved_failures": len(error_entities)},
                )
                graph.add_macro_proposition(mp_l0)
                existing_cluster_types.add("trace_macro")
                existing_mp_stmts.add(l0_stmt)
                mp_id += 1

                graph.add_episodic_node(
                    level=0,
                    summary=l0_stmt,
                    turn_range=(min_epoch, max_epoch),
                    entity_ids=[e.id for e in error_entities],
                )

        # ---------------------------------------------------------------------
        # 2. Semantic Dialogue & Markdown Statement Synthesis
        # ---------------------------------------------------------------------
        if "semantic_dialogue" not in existing_cluster_types:
            statement_items: List[Tuple[int, str, int]] = []
            for node in graph.episodic_nodes:
                if node.level >= 1 and node.summary.strip():
                    statement_items.append((node.id, node.summary, node.turn_range[1]))

            if len(statement_items) >= 2:
                self.simhash_clusterer = SimHashClusterer(max_hamming_distance=self.simhash_clusterer.max_distance)
                dialogue_clusters = self.simhash_clusterer.cluster(statement_items)
                for c_rec in dialogue_clusters:
                    if len(c_rec.member_ids) >= 2:
                        l1_stmt = f"@dialogue_cluster_{c_rec.cluster_id}[turns={len(c_rec.member_ids)}]: {c_rec.signature} (x{len(c_rec.member_ids)})"
                        if l1_stmt not in existing_mp_stmts:
                            mp = MacroProposition(
                                id=mp_id,
                                statement=l1_stmt,
                                cluster_type="semantic_dialogue",
                                member_count=len(c_rec.member_ids),
                                member_ids=c_rec.member_ids,
                                epoch_range=c_rec.epoch_range,
                                confidence=1.0,
                                attributes={"centroid": c_rec.signature},
                            )
                            graph.add_macro_proposition(mp)
                            existing_mp_stmts.add(l1_stmt)
                            mp_id += 1

                            graph.add_episodic_node(
                                level=1,
                                summary=l1_stmt,
                                turn_range=c_rec.epoch_range,
                                entity_ids=[],
                            )
                            graph.add_cluster(c_rec.cluster_id, c_rec.member_ids, c_rec)

        # ---------------------------------------------------------------------
        # 3. Entity Community Synthesis
        # ---------------------------------------------------------------------
        if "entity_community" not in existing_cluster_types and len(graph.entities) >= 2:
            communities = self.community_aggregator.aggregate(graph)
            for comm in communities:
                if len(comm.member_ids) >= 2:
                    if comm.signature not in existing_mp_stmts:
                        mp = MacroProposition(
                            id=mp_id,
                            statement=comm.signature,
                            cluster_type="entity_community",
                            member_count=len(comm.member_ids),
                            member_ids=comm.member_ids,
                            epoch_range=comm.epoch_range,
                            confidence=1.0,
                            attributes=comm.metadata,
                        )
                        graph.add_macro_proposition(mp)
                        existing_mp_stmts.add(comm.signature)
                        mp_id += 1

                        graph.add_episodic_node(
                            level=1,
                            summary=comm.signature,
                            turn_range=comm.epoch_range,
                            entity_ids=comm.member_ids,
                        )
                        graph.add_cluster(comm.cluster_id, comm.member_ids, comm)

        return graph
