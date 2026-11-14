"""
tests.test_almf_tier5_adversarial: Milestone 2 Tier 5 Adversarial & Stress Coverage Suite.

Validates algorithmic limits, stress conditions, and boundary invariants for:
1. `almf.cluster` (Drain template miner, SimHash Hamming clustering, Entity Community BFS partitioner, Hierarchical Aggregator)
2. `almf.parser` (AST structural skeletons, Living Wiki Markdown parser, Trace invariant miner)
"""

from __future__ import annotations

import os
import sys
import time
import unittest

from almf.cluster import (
    EntityCommunityAggregator,
    HierarchicalAggregator,
    SimHashClusterer,
    TemplateLogClusterer,
)
from almf.parser import (
    parse_markdown,
    parse_python_ast,
    parse_trace_log,
)
from almf.types import (
    Entity,
    EntityType,
    EpisodicNode,
    Hyperedge,
    MacroProposition,
    MemoryGraph,
)


class TestTier5ClusterStress(unittest.TestCase):
    """Tier 5 Adversarial Stress & Robustness Tests for almf.cluster."""

    def test_drain_pathological_path_and_regex_tokens(self):
        """Verifies 1000-segment paths and 800 parameters tokenize in < 50ms without regex catastrophic backtracking."""
        clusterer = TemplateLogClusterer(max_depth=4, sim_threshold=0.5)
        long_path = "/" + "/".join([f"sub_path_{i}" for i in range(1000)])
        line = f"2026-11-14T07:15:00Z ERROR accessing {long_path} with status 404"

        t0 = time.perf_counter()
        tokens, params = clusterer.tokenize(line)
        dt = time.perf_counter() - t0

        self.assertLess(dt, 0.05, f"Tokenization took too long: {dt:.4f}s")
        self.assertIn("<*>", tokens)
        self.assertGreaterEqual(len(params), 1)

    def test_drain_extreme_depths(self):
        """Verifies TemplateLogClusterer behaves correctly under extreme depths (depth=1 and depth=50)."""
        c_deep = TemplateLogClusterer(max_depth=50, sim_threshold=0.8)
        line1 = "PREFIX " + "TOKEN " * 40 + "SUFFIX"
        line2 = "PREFIX " + "TOKEN " * 39 + "DIFFERENT SUFFIX"
        rec1 = c_deep.mine_log(line1, item_id=1)
        rec2 = c_deep.mine_log(line2, item_id=2)
        self.assertIsNotNone(rec1)
        self.assertIsNotNone(rec2)

        c_shallow = TemplateLogClusterer(max_depth=1, sim_threshold=0.5)
        rec_s1 = c_shallow.mine_log("ERROR 10.0.0.1 failed", item_id=1)
        rec_s2 = c_shallow.mine_log("ERROR 10.0.0.2 failed", item_id=2)
        self.assertEqual(rec_s1.cluster_id, rec_s2.cluster_id)
        self.assertEqual(len(c_shallow.clusters), 1)

    def test_drain_null_byte_and_unicode_streams(self):
        """Verifies Drain clustering handles null bytes, emoji, and Cyrillic in log messages."""
        clusterer = TemplateLogClusterer()
        log_line = "2026-11-14T07:20:00Z \x00 Crash at \U0001F600 in \u0441\u0435\u0440\u0432\u0438\u0441 code 500"
        rec = clusterer.mine_log(log_line, item_id=42)
        self.assertIsNotNone(rec)
        self.assertIn(42, rec.member_ids)
        self.assertIn("<*>", rec.signature)

    def test_simhash_stop_word_degradation(self):
        """Verifies pure stop-word strings fallback to 64-bit Hamming distance without division by zero."""
        clusterer = SimHashClusterer(max_hamming_distance=12)
        sw_text1 = "is on with the to at for in a an and"
        sw_text2 = "is on with the to at for in a an and"
        records = clusterer.cluster_texts([sw_text1, sw_text2])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].member_ids, [0, 1])

    def test_simhash_cyrillic_morphology(self):
        """Verifies Russian inflectional variants stem and cluster properly."""
        clusterer = SimHashClusterer(max_hamming_distance=12)
        texts = [
            "База данных Postgres запущена успешно",
            "База данных Postgres была запущена успешно",
            "Сервис Redis был остановлен администратором",
        ]
        records = clusterer.cluster_texts(texts)
        self.assertGreaterEqual(len(records), 2)
        self.assertIn("postgres", records[0].signature.lower())

    def test_simhash_high_volume_stream(self):
        """Verifies 1,000 items clustered in < 0.6s without performance regression."""
        clusterer = SimHashClusterer(max_hamming_distance=12)
        items = [(i, f"Worker process #{i % 5} handled batch {i} successfully", 0) for i in range(1000)]
        t0 = time.perf_counter()
        records = clusterer.cluster(items)
        dt = time.perf_counter() - t0
        self.assertLess(dt, 0.60, f"Clustering 1,000 items took {dt:.4f}s")
        self.assertLessEqual(len(records), 10)

    def test_community_dense_cyclic_graph(self):
        """Verifies 500-node Hamiltonian cycle graph aggregates into exactly 1 community in < 0.1s."""
        agg = EntityCommunityAggregator()
        g = MemoryGraph()
        nodes = [g.add_entity(name=f"node_{i}", entity_type=EntityType.CONCEPT) for i in range(500)]
        for i in range(500):
            g.add_hyperedge(subject_id=nodes[i].id, predicate="connects_to", object_id=nodes[(i + 1) % 500].id)

        t0 = time.perf_counter()
        comms = agg.aggregate(g)
        dt = time.perf_counter() - t0

        self.assertLess(dt, 0.10)
        self.assertEqual(len(comms), 1)
        self.assertEqual(len(comms[0].member_ids), 500)

    def test_community_star_topology_hub_identification(self):
        """Verifies 1,000-leaf star graph identifies hub as central entity."""
        agg = EntityCommunityAggregator()
        g = MemoryGraph()
        hub = g.add_entity(name="master_orchestrator", entity_type=EntityType.CONCEPT)
        for i in range(1000):
            leaf = g.add_entity(name=f"worker_{i}", entity_type=EntityType.ACTOR)
            g.add_hyperedge(subject_id=hub.id, predicate="dispatches", object_id=leaf.id)

        t0 = time.perf_counter()
        comms = agg.aggregate(g)
        dt = time.perf_counter() - t0

        self.assertLess(dt, 0.20)
        self.assertEqual(len(comms), 1)
        self.assertIn("master_orchestrator", comms[0].signature)
        self.assertEqual(len(comms[0].member_ids), 1001)

    def test_community_dangling_and_ghost_hyperedges(self):
        """Verifies hyperedges pointing to nonexistent entity IDs do not raise KeyError."""
        agg = EntityCommunityAggregator()
        g = MemoryGraph()
        e1 = g.add_entity(name="real_entity_1", entity_type=EntityType.CONCEPT)
        e2 = g.add_entity(name="real_entity_2", entity_type=EntityType.CONCEPT)
        g.add_hyperedge(subject_id=e1.id, predicate="points_to_ghost", object_id=999999)
        g.add_hyperedge(subject_id=888888, predicate="ghost_points_to", object_id=e2.id)
        g.add_hyperedge(subject_id=777777, predicate="ghost_to_ghost", object_id=666666)

        comms = agg.aggregate(g)
        self.assertEqual(len(comms), 2)
        self.assertEqual(comms[0].member_ids, [e1.id])
        self.assertEqual(comms[1].member_ids, [e2.id])

    def test_hierarchical_isolated_aggregator_invariants(self):
        """Verifies clean HierarchicalAggregator synthesizes L0, L1 and preserves L2 losslessly."""
        ha = HierarchicalAggregator()
        g = MemoryGraph()
        err = g.add_entity(name="PGTimeout", entity_type=EntityType.ERROR, attributes={"error_message": "Conn timeout", "count": 3})
        fix = g.add_entity(name="restart_pg", entity_type=EntityType.DECISION)
        edge = g.add_hyperedge(subject_id=err.id, predicate="resolves_with", object_id=fix.id)

        ha.aggregate(g)

        # L2 hyperedge intact
        self.assertIn(edge, g.hyperedges)
        # L0 trace macro present
        l0 = [mp for mp in g.macro_propositions if mp.cluster_type == "trace_macro"]
        self.assertEqual(len(l0), 1)
        self.assertIn("OPERATIONAL", l0[0].statement)
        # L1 cluster present
        l1 = [mp for mp in g.macro_propositions if mp.cluster_type == "trace_template"]
        self.assertEqual(len(l1), 1)
        self.assertIn("PGTimeout", l1[0].statement)


class TestTier5ParserStress(unittest.TestCase):
    """Tier 5 Adversarial Stress & Robustness Tests for almf.parser."""

    def test_ast_null_byte_syntax_error_handling(self):
        """Verifies Python code with null bytes is converted to SyntaxError ERROR entity without crashing."""
        null_code = "def bad_func():\x00 pass\n"
        g = parse_python_ast(null_code, module_name="null_test")
        err_entities = [e for e in g.entities.values() if e.type == EntityType.ERROR]
        self.assertEqual(len(err_entities), 1)
        self.assertIn("SyntaxError", err_entities[0].name)

    def test_ast_deep_nesting_classes(self):
        """Verifies 80 levels of nested classes parse without hitting RecursionError."""
        nesting_depth = 80
        nested_code = ""
        for d in range(nesting_depth):
            nested_code += f"{'    '*d}class Level_{d}:\n"
        nested_code += f"{'    '*nesting_depth}def leaf(self): pass\n"

        g = parse_python_ast(nested_code, module_name="deep_nesting")
        self.assertGreaterEqual(len(g.entities), 81)

    def test_ast_massive_module_performance(self):
        """Verifies 500 function definitions parse in < 0.05s."""
        many_fn_code = "\n".join([f"def fn_{i}(x: int = {i}) -> int: return x" for i in range(500)])
        t0 = time.perf_counter()
        g = parse_python_ast(many_fn_code, module_name="massive_ast")
        dt = time.perf_counter() - t0
        self.assertLess(dt, 0.05, f"AST parsing took {dt:.4f}s")
        self.assertEqual(len(g.entities), 501)

    def test_markdown_null_bytes_and_50k_line(self):
        """Verifies Markdown with null bytes and 50k char line parses safely."""
        content = "# Header\n- Fact 1: normal\n- Fact 2: \x00 null byte\n" + ("X" * 50000)
        g = parse_markdown(content, filename="null_long.md")
        self.assertGreaterEqual(len(g.entities), 3)

    def test_markdown_directory_traversal_pointers(self):
        """Verifies topic pointers with directory traversal paths are ingested as inert metadata."""
        content = "# Memory\n- Root Secrets — memory/../../../../etc/shadow.md\n"
        g = parse_markdown(content, filename="traversal.md")
        pointer_ents = [e for e in g.entities.values() if "cold_pointer" in e.attributes]
        self.assertEqual(len(pointer_ents), 1)
        self.assertEqual(pointer_ents[0].attributes["cold_pointer"], "memory/../../../../etc/shadow.md")

    def test_markdown_high_volume_facts(self):
        """Verifies 1,000 markdown facts parse in < 0.20s."""
        lines = ["# Bulk Facts\n"] + [f"- item_{i}: val_{i}" for i in range(1000)]
        content = "\n".join(lines)
        t0 = time.perf_counter()
        g = parse_markdown(content, filename="bulk.md")
        dt = time.perf_counter() - t0
        self.assertLess(dt, 0.20, f"Markdown parsing took {dt:.4f}s")
        self.assertGreaterEqual(len(g.entities), 1000)

    def test_trace_ansi_escape_codes_handling(self):
        """Verifies trace log with ANSI color sequences parses without exception."""
        trace = "\x1b[31m[ERROR]\x1b[0m 2026-11-14 Connection failed\nAssertionError: Expected 200 got 500\nFix applied: restart"
        g = parse_trace_log(trace, trace_id="ansi_log")
        self.assertGreaterEqual(len(g.entities), 3)


if __name__ == "__main__":
    unittest.main()
