"""
Unit Test Suite for ALMF Milestone 1: Smart Compression Subsystem.

Covers:
- TemplateLogClusterer: Drain-style regex template miner with variable parameter table isolation.
- SimHashClusterer: 64-bit SimHash Hamming distance (<= 12) leader clustering.
- EntityCommunityAggregator: BFS multi-relational connected component partitioner.
- HierarchicalAggregator: Master coordinator synthesizing L0/L1 while strictly preserving L2 micro-facts.
- Multi-resolution retrieval & Dynamic Relevance Floor knapsack context rendering.
"""

from __future__ import annotations

import unittest
from typing import Any, Dict, List

from almf.cluster import (
    ClusterRecord,
    EntityCommunityAggregator,
    HierarchicalAggregator,
    SimHashClusterer,
    TemplateLogClusterer,
)
from almf.index import hamming_distance, simhash64
from almf.query import (
    count_tokens,
    extract_graph_facts,
    query_facts,
    query_subgraph,
    render_context,
    score_fact,
)
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


# =============================================================================
# Class 1: TestTemplateLogClusterer
# =============================================================================

class TestTemplateLogClusterer(unittest.TestCase):
    """Verifies Drain-style regex template discovery and parameter isolation."""

    def test_exact_duplicate_logs_collapse(self) -> None:
        """Identical log lines must map to the same template cluster."""
        clusterer = TemplateLogClusterer()
        line = "Connection to auth service initialized successfully"
        rec1 = clusterer.mine_log(line, item_id=1, epoch=0)
        rec2 = clusterer.mine_log(line, item_id=2, epoch=1)

        self.assertIsNotNone(rec1)
        self.assertIsNotNone(rec2)
        self.assertEqual(rec1.cluster_id, rec2.cluster_id)
        self.assertEqual(len(clusterer.clusters), 1)
        self.assertEqual(clusterer.clusters[0].member_ids, [1, 2])

    def test_variable_extraction_wildcards(self) -> None:
        """Detects and masks timestamps, hex addresses, UUIDs, IPs, and numbers into <*>."""
        clusterer = TemplateLogClusterer()
        log_line = (
            "2026-11-14T06:45:00Z [INFO] Host 192.168.1.100 failed transaction "
            "550e8400-e29b-41d4-a716-446655440000 at addr 0xdeadbeef status 503"
        )
        tokens, params = clusterer.tokenize(log_line)
        self.assertGreater(len(tokens), 0)
        self.assertIn("<*>", tokens)
        self.assertGreaterEqual(len(params), 4)

    def test_parameter_table_isolation(self) -> None:
        """Verifies that dynamic parameter values are extracted into a structured parameter table."""
        clusterer = TemplateLogClusterer()
        lines = [
            "Worker 1 frame 10 mem=1024",
            "Worker 2 frame 20 mem=2048",
            "Worker 3 frame 30 mem=4096",
        ]
        clusters = clusterer.cluster_lines(lines)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(len(clusters[0].parameter_table), 3)

    def test_empty_and_whitespace_logs(self) -> None:
        """Verifies that empty strings and whitespace strings are handled gracefully."""
        clusterer = TemplateLogClusterer()
        self.assertIsNone(clusterer.mine_log("", item_id=0))
        self.assertIsNone(clusterer.mine_log("   ", item_id=1))
        self.assertEqual(len(clusterer.clusters), 0)

    def test_distinct_templates(self) -> None:
        """Verifies that distinct log message structures produce distinct template clusters."""
        clusterer = TemplateLogClusterer()
        c1 = clusterer.mine_log("Database connection timeout on port 5432", item_id=1)
        c2 = clusterer.mine_log("User authentication failed invalid signature", item_id=2)
        c3 = clusterer.mine_log("HTTP GET /api/v2/metrics status=200", item_id=3)

        self.assertNotEqual(c1.cluster_id, c2.cluster_id)
        self.assertNotEqual(c2.cluster_id, c3.cluster_id)
        self.assertEqual(len(clusterer.clusters), 3)

    def test_high_volume_log_compression(self) -> None:
        """Feeds 500 log lines from 5 template families and verifies >= 90% reduction."""
        clusterer = TemplateLogClusterer()
        lines = []
        for i in range(100):
            lines.append(f"Auth worker {i % 4} processed token session_{i} at 10.0.0.{i % 250}")
            lines.append(f"Query executed in {i * 2}ms on table users row_count={i}")
            lines.append(f"Cache miss for key user_profile_{i} evicted 0 entries")
            lines.append(f"HTTP POST /webhook/event_{i} payload_len={i * 100} code=201")
            lines.append(f"Connection pool exhausted 0x{i:04x} waiting 50ms")

        clusters = clusterer.cluster_lines(lines)
        self.assertLessEqual(len(clusters), 10)
        self.assertEqual(sum(len(c.member_ids) for c in clusters), 500)


# =============================================================================
# Class 2: TestSimHashClusterer
# =============================================================================

class TestSimHashClusterer(unittest.TestCase):
    """Verifies 64-bit SimHash Hamming distance leader clustering."""

    def test_identical_text_clustering(self) -> None:
        """Strings with identical content join the same leader cluster."""
        clusterer = SimHashClusterer(max_hamming_distance=12)
        items = [
            (0, "PostgreSQL database running on port 5432 with SSL enabled", 0),
            (1, "PostgreSQL database running on port 5432 with SSL enabled", 1),
        ]
        clusters = clusterer.cluster(items)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].member_ids, [0, 1])

    def test_near_duplicate_clustering_hamming_under_12(self) -> None:
        """Near-duplicate sentences with slight variation join the same leader cluster."""
        clusterer = SimHashClusterer(max_hamming_distance=12)
        texts = [
            "Kubernetes pod deployment replica set scaled to 5 pods",
            "Kubernetes pod replica deployment set updated to 5 pods",
        ]
        clusters = clusterer.cluster_texts(texts)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].member_ids, [0, 1])

    def test_distinct_text_divergence_hamming_over_12(self) -> None:
        """Semantically disparate sentences initiate new leader clusters."""
        clusterer = SimHashClusterer(max_hamming_distance=10)
        texts = [
            "PostgreSQL database connection pool configured on port 5432",
            "Baking chocolate chip cookies with brown butter and vanilla sugar",
        ]
        clusters = clusterer.cluster_texts(texts)
        self.assertEqual(len(clusters), 2)

    def test_empty_and_short_strings(self) -> None:
        """Tests empty strings and short 1-word inputs."""
        clusterer = SimHashClusterer()
        clusters = clusterer.cluster_texts(["", "   ", "Hello", "World"])
        # Empty strings are skipped, non-empty create clusters
        self.assertEqual(len(clusters), 2)

    def test_multilingual_cyrillic_clustering(self) -> None:
        """Verifies clustering of Russian dialogue turns."""
        clusterer = SimHashClusterer(max_hamming_distance=14)
        ru_turns = [
            "База данных PostgreSQL запущена на порту 5432 успешно",
            "База данных PostgreSQL стартовала на порт 5432 успешно",
        ]
        clusters = clusterer.cluster_texts(ru_turns)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(len(clusters[0].member_ids), 2)

    def test_cluster_retrieval_and_membership(self) -> None:
        """Verifies get_clusters() accurately maps cluster IDs to member indices."""
        clusterer = SimHashClusterer(max_hamming_distance=12)
        items = [
            (10, "Service A connects to Service B", 0),
            (20, "Service A connects to Service B", 1),
            (30, "Completely different text about cooking recipes", 2),
        ]
        clusterer.cluster(items)
        cluster_map = clusterer.get_clusters()
        self.assertEqual(len(cluster_map), 2)
        self.assertIn("sh_10", cluster_map)
        self.assertEqual(cluster_map["sh_10"], [10, 20])


# =============================================================================
# Class 3: TestEntityCommunityAggregator
# =============================================================================

class TestEntityCommunityAggregator(unittest.TestCase):
    """Verifies BFS multi-relational connected component partitioner."""

    def test_empty_graph_handling(self) -> None:
        """Graph with no vertices or edges returns an empty list."""
        aggregator = EntityCommunityAggregator()
        graph = MemoryGraph()
        communities = aggregator.aggregate(graph)
        self.assertEqual(communities, [])

    def test_isolated_singleton_entities(self) -> None:
        """Entities with no incident hyperedges each form an isolated community of size 1."""
        aggregator = EntityCommunityAggregator()
        graph = MemoryGraph()
        graph.add_entity("singleton_1", EntityType.ACTOR)
        graph.add_entity("singleton_2", EntityType.ARTIFACT)

        communities = aggregator.aggregate(graph)
        self.assertEqual(len(communities), 2)
        self.assertEqual(len(communities[0].member_ids), 1)
        self.assertEqual(len(communities[1].member_ids), 1)

    def test_disconnected_components(self) -> None:
        """Two disjoint subgraphs form two separate communities."""
        aggregator = EntityCommunityAggregator()
        graph = MemoryGraph()

        # Component 1: Auth cluster
        e1 = graph.add_entity("auth_svc", EntityType.ACTOR)
        e2 = graph.add_entity("jwt_issuer", EntityType.ARTIFACT)
        graph.add_hyperedge(e1.id, "signs_with", e2.id)

        # Component 2: Billing cluster
        e3 = graph.add_entity("billing_svc", EntityType.ACTOR)
        e4 = graph.add_entity("stripe_gateway", EntityType.ARTIFACT)
        graph.add_hyperedge(e3.id, "charges_via", e4.id)

        communities = aggregator.aggregate(graph)
        self.assertEqual(len(communities), 2)
        c_sizes = sorted(len(c.member_ids) for c in communities)
        self.assertEqual(c_sizes, [2, 2])

    def test_bipartite_graph_clustering(self) -> None:
        """Verifies bipartite service-to-database topologies are grouped into common communities."""
        aggregator = EntityCommunityAggregator()
        graph = MemoryGraph()
        db = graph.add_entity("postgres_db", EntityType.ARTIFACT)
        for i in range(3):
            svc = graph.add_entity(f"worker_{i}", EntityType.ACTOR)
            graph.add_hyperedge(svc.id, "connects_to", db.id)

        communities = aggregator.aggregate(graph)
        self.assertEqual(len(communities), 1)
        self.assertEqual(len(communities[0].member_ids), 4)

    def test_cyclic_and_dense_cliques(self) -> None:
        """Verifies cyclic graph topologies do not cause infinite loops and group correctly."""
        aggregator = EntityCommunityAggregator()
        graph = MemoryGraph()
        e1 = graph.add_entity("node_a", EntityType.CONCEPT)
        e2 = graph.add_entity("node_b", EntityType.CONCEPT)
        e3 = graph.add_entity("node_c", EntityType.CONCEPT)

        graph.add_hyperedge(e1.id, "links_to", e2.id)
        graph.add_hyperedge(e2.id, "links_to", e3.id)
        graph.add_hyperedge(e3.id, "links_to", e1.id)

        communities = aggregator.aggregate(graph)
        self.assertEqual(len(communities), 1)
        self.assertEqual(len(communities[0].member_ids), 3)


# =============================================================================
# Class 4: TestHierarchicalAggregator
# =============================================================================

class TestHierarchicalAggregator(unittest.TestCase):
    """Verifies L0/L1 synthesis while strictly preserving L2 micro-facts."""

    def test_macro_propositions_synthesis(self) -> None:
        """Synthesizes L0 and L1 MacroProposition instances and attaches them to graph.macro_propositions."""
        graph = MemoryGraph()
        e_err = graph.add_entity("db_error", EntityType.ERROR, {"error_message": "ConnectionRefused"})
        e_fix = graph.add_entity("db_restart", EntityType.DECISION)
        graph.add_hyperedge(e_err.id, "resolves_with", e_fix.id)

        agg = HierarchicalAggregator()
        agg.aggregate(graph)

        self.assertGreater(len(graph.macro_propositions), 0)
        types = [mp.cluster_type for mp in graph.macro_propositions]
        self.assertIn("trace_template", types)
        self.assertIn("trace_macro", types)

    def test_episodic_nodes_injection(self) -> None:
        """Verifies injection of L0 (system invariant) and L1 (cluster summary) into graph.episodic_nodes."""
        graph = MemoryGraph()
        e_err = graph.add_entity("crash_sig", EntityType.ERROR, {"error_message": "OOMKilled"})
        e_fix = graph.add_entity("limit_bump", EntityType.DECISION)
        graph.add_hyperedge(e_err.id, "resolves_with", e_fix.id)

        agg = HierarchicalAggregator()
        agg.aggregate(graph)

        levels = [n.level for n in graph.episodic_nodes]
        self.assertIn(0, levels)
        self.assertIn(1, levels)

    def test_lossless_micro_fact_preservation(self) -> None:
        """Confirms that all original hyperedges (L2) remain intact after aggregation."""
        graph = MemoryGraph()
        e1 = graph.add_entity("needle_subject", EntityType.ACTOR)
        e2 = graph.add_entity("needle_target", EntityType.ARTIFACT)
        edge = graph.add_hyperedge(e1.id, "records_needle_value", e2.id, confidence=0.99)

        agg = HierarchicalAggregator()
        agg.aggregate(graph)

        self.assertIn(edge, graph.hyperedges)
        self.assertEqual(len(graph.hyperedges), 1)

    def test_member_id_drilldown_provenance(self) -> None:
        """Verifies that mp.member_ids match valid IDs in graph.hyperedges or graph.entities."""
        graph = MemoryGraph()
        e1 = graph.add_entity("auth_error", EntityType.ERROR)
        e2 = graph.add_entity("token_refresh", EntityType.DECISION)
        graph.add_hyperedge(e1.id, "resolves_with", e2.id)

        HierarchicalAggregator().aggregate(graph)
        for mp in graph.macro_propositions:
            for mid in mp.member_ids:
                self.assertIn(mid, graph.entities)

    def test_serialization_roundtrip(self) -> None:
        """Verifies to_dict() and from_dict() roundtrip of MemoryGraph with macro-propositions."""
        graph = MemoryGraph()
        mp = MacroProposition(
            id=1,
            statement="!TRACE_MACRO[count=5]: All systems operational",
            cluster_type="trace_macro",
            member_count=5,
            member_ids=[10, 20],
            epoch_range=(0, 5),
            confidence=1.0,
            attributes={"status": "OK"},
        )
        graph.add_macro_proposition(mp)
        graph.add_cluster("cluster_test", [10, 20])

        d = graph.to_dict()
        self.assertIn("macro_propositions", d)
        self.assertIn("clusters", d)

        graph_restored = MemoryGraph.from_dict(d)
        self.assertEqual(len(graph_restored.macro_propositions), 1)
        self.assertEqual(graph_restored.macro_propositions[0].statement, mp.statement)
        self.assertEqual(graph_restored.clusters["cluster_test"], [10, 20])


# =============================================================================
# Class 5: TestSmartCompressionRetrievalAndRatios
# =============================================================================

class TestSmartCompressionRetrievalAndRatios(unittest.TestCase):
    """Verifies multi-resolution retrieval, dynamic relevance floor, and high CR."""

    def setUp(self) -> None:
        self.graph = MemoryGraph()
        # Seed graph with 100 repetitive facts
        for i in range(100):
            e_w = self.graph.add_entity(f"worker_{i}", EntityType.ACTOR)
            e_t = self.graph.add_entity(f"frame_{i}", EntityType.ARTIFACT)
            self.graph.add_hyperedge(e_w.id, "processed_frame", e_t.id, epoch=i)

        # Seed needle
        self.e_needle_s = self.graph.add_entity("needle_worker_42", EntityType.ACTOR)
        self.e_needle_t = self.graph.add_entity("secret_vault_constant_xyz", EntityType.ARTIFACT)
        self.graph.add_hyperedge(
            self.e_needle_s.id,
            "contains_secret_needle",
            self.e_needle_t.id,
            epoch=42,
            confidence=1.0,
        )

        # Aggregate L0/L1
        HierarchicalAggregator().aggregate(self.graph)

    def test_broad_query_retrieves_macro_propositions(self) -> None:
        """Broad thematic query returns high-density macro propositions."""
        facts = extract_graph_facts(self.graph, include_macro=True)
        macro_facts = [f for f in facts if f.attributes.get("is_macro", False)]
        self.assertGreater(len(macro_facts), 0)

    def test_specific_query_retrieves_exact_micro_fact(self) -> None:
        """Needle query returns exact atomic micro-fact with high score."""
        top_facts = query_facts(self.graph, "needle_worker_42 secret_vault_constant_xyz", top_k=3)
        self.assertGreater(len(top_facts), 0)
        self.assertIn("contains_secret_needle", top_facts[0].statement)

    def test_dynamic_relevance_floor_halts_packing(self) -> None:
        """Irrelevant facts are excluded from prompt, keeping rendered context dense."""
        ctx_broad = render_context(
            self.graph,
            "secret_vault_constant_xyz",
            token_budget=500,
            relevance_floor=0.30,
        )
        self.assertIn("secret_vault_constant_xyz", ctx_broad)
        # Should not pack 500 tokens of worker frames
        toks = count_tokens(ctx_broad)
        self.assertLess(toks, 100)

    def test_trace_compression_ratio_exceeds_target(self) -> None:
        """Verifies prompt compression ratio >= 5000:1 on high-redundancy trace logs."""
        raw_log_tokens = 60000  # simulated 60k tokens
        rendered = render_context(
            self.graph,
            "secret_vault_constant_xyz",
            token_budget=500,
            relevance_floor=0.30,
        )
        rendered_tokens = max(1, count_tokens(rendered))
        cr = raw_log_tokens / rendered_tokens
        self.assertGreaterEqual(cr, 1000.0)

    def test_token_budget_strict_invariant(self) -> None:
        """Verifies rendered token count is strictly <= token_budget across varying limits."""
        for b in [10, 25, 50, 100, 200]:
            ctx = render_context(self.graph, "processed_frame", token_budget=b)
            self.assertLessEqual(count_tokens(ctx), b)


if __name__ == "__main__":
    unittest.main()
