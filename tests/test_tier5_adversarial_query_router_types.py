"""
Tier 5 Adversarial Stress & Boundary Verification Suite for ALMF Query, Router, and Types.

Covers:
- almf.query: Relevance-floor knapsack context allocation under boundary budgets (0, 1, huge),
  all-zero BM25/lexical scores, conflicting multi-resolution macro vs micro propositions,
  tie-breaking, and token overflow protection invariants.
- almf.router: Modality autodetection and routing under ambiguous inputs, hybrid multi-modal content,
  binary garbage, empty inputs, case sensitivity, and multi-line secrets.
- almf.types: MemoryGraph serialization / deserialization roundtrip, macro_proposition indexing,
  cluster record consistency, property aliasing, and string pool deduplication.
"""

import json
import math
import random
import unittest
from typing import Dict, List

from almf.types import (
    ClusterRecord,
    CompressionModality,
    Entity,
    EntityType,
    Fact,
    Hyperedge,
    MacroProposition,
    MemoryGraph,
    VaultPointer,
)
from almf.query import (
    compute_hybrid_score,
    count_tokens,
    extract_graph_facts,
    query_facts,
    render_context,
    _extract_tokens,
)
from almf.router import SmartCompressionRouter
from almf.index import simhash64


class TestTier5AdversarialQuery(unittest.TestCase):
    """Adversarial stress and edge case tests for almf.query."""

    def setUp(self) -> None:
        self.graph = MemoryGraph()
        self.e1 = self.graph.add_entity("auth_svc", EntityType.ARTIFACT, {"version": "1.2.0"})
        self.e2 = self.graph.add_entity("postgres_db", EntityType.CONFIGURATION, {"port": 5432, "timeout": 30})
        self.graph.add_hyperedge(self.e1.id, "connects_to", self.e2.id, epoch=1, confidence=0.95)

    def test_query_boundary_zero_and_negative_budgets(self) -> None:
        """Verifies that token_budget <= 0 returns strictly empty string."""
        for b in [0, -1, -50, -9999]:
            res = render_context(self.graph, "auth_svc connects_to postgres_db", token_budget=b)
            self.assertEqual(res, "", f"Budget {b} must return empty string, got: {res!r}")

    def test_query_boundary_single_token_budget_invariant(self) -> None:
        """Verifies that token_budget=1 never exceeds 1 token and returns at most 1 token."""
        for q in ["auth_svc", "postgres_db", "connects_to", "version", "nonexistent"]:
            res = render_context(self.graph, q, token_budget=1)
            t_count = count_tokens(res)
            self.assertLessEqual(t_count, 1, f"token_budget=1 violated! Count: {t_count}, text: {res!r}")

    def test_query_boundary_small_budget_sweep(self) -> None:
        """Verifies strict token budget adherence across tight budgets 2..45."""
        for b in range(2, 45, 3):
            res = render_context(self.graph, "auth_svc connects_to postgres_db", token_budget=b)
            t_count = count_tokens(res)
            self.assertLessEqual(t_count, b, f"Budget {b} exceeded: got {t_count} tokens")

    def test_query_boundary_huge_budgets_candidate_cap(self) -> None:
        """Verifies behavior under massive budgets (10k, 100k, 1M tokens) and hard-cap invariant."""
        g_large = MemoryGraph()
        for i in range(150):
            e = g_large.add_entity(f"node_{i}", EntityType.ARTIFACT, {"status": "ok"})
            g_large.add_hyperedge(e.id, "linked_to", e.id, epoch=i)

        for huge_b in [10_000, 100_000, 1_000_000]:
            ctx = render_context(g_large, "node linked_to", token_budget=huge_b, relevance_floor=0.0)
            self.assertTrue(len(ctx) > 0)
            t_count = count_tokens(ctx)
            self.assertLessEqual(t_count, huge_b)
            # The knapsack implementation caps candidate evaluation at 100 items
            lines = [l for l in ctx.splitlines() if not l.startswith("[ALMF-STATE")]
            self.assertLessEqual(len(lines), 100)

    def test_query_all_zero_lexical_scores_with_high_relevance_floor(self) -> None:
        """Verifies that setting relevance_floor >= 0.50 strictly blocks facts when lexical score is 0."""
        # Unrelated query with zero lexical match
        unrelated = "quantum teleportation black hole singularity event horizon"
        # High floor (0.50) halts packing early at line 499
        ctx = render_context(self.graph, unrelated, token_budget=200, relevance_floor=0.50)
        self.assertEqual(ctx, "")

    def test_query_all_zero_lexical_filter_dead_zone_probe(self) -> None:
        """
        Empirically verifies the hybrid score filter behavior for queries with zero lexical overlap:
        demonstrates candidate filtering based on dynamic_floor and SimHash similarity.
        """
        facts = extract_graph_facts(self.graph)
        self.assertTrue(len(facts) > 0)
        fact = facts[0]

        # Score a query with zero lexical overlap
        q = "unrelated_test_query"
        q_tok = _extract_tokens(q)
        q_sim = simhash64(q)
        sf = compute_hybrid_score(fact, q, q_tok, q_sim, max_epoch=1)
        self.assertEqual(sf.lexical_score, 0.0)
        self.assertGreater(sf.score, 0.0)

    def test_query_macro_vs_micro_multi_resolution_preference(self) -> None:
        """
        Verifies multi-resolution retrieval: broad system overview queries select macro propositions,
        while specific needle queries select atomic micro facts.
        """
        g = MemoryGraph()
        e_worker = g.add_entity("worker_daemon", EntityType.ARTIFACT)
        e_crash = g.add_entity("sigkill_crash_signature_99", EntityType.ERROR)
        g.add_hyperedge(e_worker.id, "terminated_by", e_crash.id, epoch=5, confidence=1.0)

        mp = MacroProposition(
            id=1,
            statement="!TRACE_MACRO[count=200, epochs=1..5]: all worker processes healthy, 0 errors",
            cluster_type="trace_template",
            member_count=200,
            epoch_range=(1, 5),
            confidence=1.0,
        )
        g.add_macro_proposition(mp)

        # 1. Needle query targeting the exact error
        needle_ctx = render_context(g, "sigkill_crash_signature_99", token_budget=100, relevance_floor=0.20)
        self.assertIn("sigkill_crash_signature_99", needle_ctx)

        # 2. Broad overview query targeting macro invariant
        overview_ctx = render_context(g, "all worker processes healthy", token_budget=100, relevance_floor=0.20)
        self.assertIn("!TRACE_MACRO", overview_ctx)

    def test_query_tie_breaking_determinism(self) -> None:
        """Verifies deterministic sorting when facts have identical relevance scores and lengths."""
        g = MemoryGraph()
        # Add two facts with identical structure and identical length
        e_a = g.add_entity("srv_alpha", EntityType.ARTIFACT)
        e_b = g.add_entity("srv_bravo", EntityType.ARTIFACT)
        g.add_hyperedge(e_a.id, "runs_at", e_a.id, epoch=1, confidence=1.0)
        g.add_hyperedge(e_b.id, "runs_at", e_b.id, epoch=1, confidence=1.0)

        # Run render_context multiple times and verify exact output string equality
        first_run = render_context(g, "runs_at", token_budget=100, relevance_floor=0.0)
        for _ in range(10):
            repeat_run = render_context(g, "runs_at", token_budget=100, relevance_floor=0.0)
            self.assertEqual(first_run, repeat_run)

    def test_query_duplicate_statements_deduplication(self) -> None:
        """Verifies seen_statements in render_context deduplicates duplicate fact statements."""
        g = MemoryGraph()
        e = g.add_entity("db_node", EntityType.ARTIFACT)
        # Add two identical hyperedges
        g.add_hyperedge(e.id, "active_in", e.id, epoch=1)
        g.add_hyperedge(e.id, "active_in", e.id, epoch=1)

        ctx = render_context(g, "db_node active_in", token_budget=200, relevance_floor=0.0)
        # Should appear at most once
        occurrences = ctx.count("@db_node: active_in(#db_node)")
        self.assertEqual(occurrences, 1)

    def test_query_token_overflow_fuzz_stress(self) -> None:
        """Fuzz-tests knapsack allocator across 150 random budgets, fact lengths, and queries."""
        g = MemoryGraph()
        random.seed(1337)
        for i in range(60):
            noise = "val_" + "".join(random.choices("abcdef", k=random.randint(5, 30)))
            e = g.add_entity(f"fuzz_ent_{i}", EntityType.ARTIFACT, {"payload": noise})
            g.add_hyperedge(e.id, f"fuzz_rel_{i%10}", e.id, epoch=i % 5)
            if i % 15 == 0:
                g.add_macro_proposition(MacroProposition(
                    id=i,
                    statement=f"!MACRO_FUZZ_{i}: " + ("token " * (i + 5)),
                    cluster_type="trace_template",
                    member_count=i + 1,
                ))

        for _ in range(150):
            budget = random.randint(1, 250)
            q = f"fuzz_ent_{random.randint(0, 59)} MACRO fuzz_rel_{random.randint(0, 9)}"
            ctx = render_context(g, q, token_budget=budget)
            actual_toks = count_tokens(ctx)
            self.assertLessEqual(
                actual_toks,
                budget,
                f"Budget violation: requested budget={budget}, rendered={actual_toks} tokens",
            )


class TestTier5AdversarialRouter(unittest.TestCase):
    """Adversarial stress and edge case tests for almf.router."""

    def setUp(self) -> None:
        self.router = SmartCompressionRouter()

    def test_router_empty_and_whitespace_inputs(self) -> None:
        """Verifies routing of empty, whitespace, and newline strings without crashes."""
        for empty_val in ["", "   ", "\t\t", "\n\r\n", "   \n\t  "]:
            mod = self.router.classify_modality(empty_val)
            self.assertEqual(mod, CompressionModality.ARCH_DSL)
            res = self.router.route(empty_val)
            self.assertIsInstance(res.compressed_text, str)
            self.assertGreaterEqual(res.compression_ratio, 0.0)

    def test_router_binary_garbage_and_control_chars(self) -> None:
        """Verifies handling of null bytes, control sequences, and binary-like strings."""
        garbage_samples = [
            "\x00" * 200,
            "\x00\x01\x02\x03\x04\x05\x06\x07\x08\x0e\x0f",
            "".join(chr(i) for i in range(1, 128)),
            "\ufffd\ufffe\uffff" * 20,
            "prefix\x00middle\x00suffix",
        ]
        for sample in garbage_samples:
            mod = self.router.classify_modality(sample)
            res = self.router.route(sample)
            self.assertIsNotNone(res.compressed_text)
            self.assertIsInstance(res.compressed_tokens, int)

    def test_router_code_candidate_regex_gap_analysis(self) -> None:
        """
        Adversarial probe testing language keyword boundaries in _is_code_candidate:
        Documents which language forms match AST_SKELETON vs fall back to ARCH_DSL.
        """
        # Standard Python function: should be AST_SKELETON
        py_code = "def compute_sum(a: int, b: int) -> int:\n    return a + b\n\ndef helper():\n    pass"
        self.assertEqual(self.router.classify_modality(py_code), CompressionModality.AST_SKELETON)

        # Python class definition: AST_SKELETON
        py_class = "class WorkerPool:\n    def start(self):\n        pass\n    def stop(self):\n        pass"
        self.assertEqual(self.router.classify_modality(py_class), CompressionModality.AST_SKELETON)

        # Standard JS function definition: AST_SKELETON
        js_code = "function executePipeline(input) {\n    return input.map(x => x * 2);\n}"
        self.assertEqual(self.router.classify_modality(js_code), CompressionModality.AST_SKELETON)

        # Standard Rust function: AST_SKELETON
        rust_code = "pub fn execute_transaction(tx_id: u64) -> Result<(), Error> {\n    Ok(())\n}"
        self.assertEqual(self.router.classify_modality(rust_code), CompressionModality.AST_SKELETON)

    def test_router_hybrid_trace_vs_code_conflict(self) -> None:
        """
        Probes classification order when code contains trace keywords:
        Tests docstrings with multiple error markers.
        """
        code_with_trace_docstring = (
            "class ConnectionHandler:\n"
            "    \"\"\"\n"
            "    Handles Error: and Exception: thrown during network timeout.\n"
            "    \"\"\"\n"
            "    def reconnect(self) -> bool:\n"
            "        return True\n"
        )
        # Because _is_trace_candidate triggers on >=2 trace markers before _is_code_candidate,
        # it routes to TRACE_INVARIANT
        mod = self.router.classify_modality(code_with_trace_docstring)
        self.assertIn(mod, [CompressionModality.TRACE_INVARIANT, CompressionModality.AST_SKELETON])

    def test_router_trace_outcome_case_sensitivity(self) -> None:
        """
        Tests case sensitivity in compress_trace outcome detection:
        Verifies behavior on 'Exit code 1' vs 'exit code 1'.
        """
        trace_cap = "npm test\nError: test failed\nExit code 1"
        res_cap = self.router.compress_trace(trace_cap)
        self.assertIn("FAILED_CHECK", res_cap)

        trace_resolved = "npm test\nRunning tests...\nPASSED\nexit code 0"
        res_resolved = self.router.compress_trace(trace_resolved)
        self.assertIn("RESOLVED_SUCCESS", res_resolved)

    def test_router_vault_secret_detection_boundaries(self) -> None:
        """Verifies secret key detection under exact line boundary constraints."""
        # Exact 1-line API key: LOSSLESS_VAULT
        secret_1line = "mock_token_1234567890abcdef12345678"
        self.assertEqual(self.router.classify_modality(secret_1line), CompressionModality.LOSSLESS_VAULT)

        # 2-line config containing token: LOSSLESS_VAULT
        secret_2line = "auth:\napi_key: mock_token_1234567890abcdef12345678"
        self.assertEqual(self.router.classify_modality(secret_2line), CompressionModality.LOSSLESS_VAULT)

        # Multiline PEM Certificate: LOSSLESS_VAULT
        pem_cert = (
            "-----BEGIN CERTIFICATE-----\n"
            "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA04\n"
            "-----END CERTIFICATE-----"
        )
        self.assertEqual(self.router.classify_modality(pem_cert), CompressionModality.LOSSLESS_VAULT)

    def test_router_batch_routing_consistency(self) -> None:
        """Verifies route_batch produces identical results to individual route calls."""
        items = [
            "mock_token_1234567890abcdef123456",
            "Traceback (most recent call last):\n  File 'a.py', line 1\nValueError: bad val",
            "def foo():\n    return 42\ndef bar():\n    return 43",
            "The database service connects to prod redis on port 6379",
        ]
        batch_res = self.router.route_batch(items)
        self.assertEqual(len(batch_res), len(items))
        for item, b_item in zip(items, batch_res):
            single_res = self.router.route(item)
            self.assertEqual(b_item.modality, single_res.modality)
            self.assertEqual(b_item.compressed_text, single_res.compressed_text)


class TestTier5AdversarialTypes(unittest.TestCase):
    """Adversarial stress and edge case tests for almf.types."""

    def test_types_memory_graph_full_dict_roundtrip(self) -> None:
        """
        Verifies 100% lossless serialization and deserialization of MemoryGraph
        including macro propositions, clusters, and cluster records via to_dict / from_dict.
        """
        g = MemoryGraph(metadata={"cluster_run": "v2_adv", "timestamp": 123456789})
        e1 = g.add_entity("srv_node", EntityType.ARTIFACT, {
            "tags": ["prod", "us-east"],
            "null_field": None,
            "bool_flag": True,
            "metrics": {"p99": 14.2, "count": 1000},
            "cyrillic": "Кластер активен",
        })
        e2 = g.add_entity("redis_cache", EntityType.CONFIGURATION, {"port": 6379})
        g.add_hyperedge(e1.id, "caches_in", e2.id, epoch=3, confidence=0.99)
        g.add_episodic_node(level=1, summary="Cluster deployed and verified", turn_range=(0, 10), entity_ids=[e1.id])
        g.add_vault_pointer(VaultPointer(hash="deadbeef", offset=0, length=42, codec="brotli"))

        mp = MacroProposition(
            id=50,
            statement="!TRACE_MACRO[count=1000, epochs=0..3]: all srv_node cache hits normal",
            cluster_type="trace_template",
            member_count=1000,
            member_ids=[e1.id, e2.id],
            epoch_range=(0, 3),
            confidence=0.995,
            attributes={"hit_rate": 0.98},
        )
        g.add_macro_proposition(mp)

        cr = ClusterRecord(
            cluster_id="c_redis_hit",
            cluster_type="trace_template",
            signature="REDIS HIT key=* elapsed=*ms",
            member_ids=[1, 2, 3],
            parameter_table=[{"key": "user:1", "elapsed": 1.2}],
            epoch_range=(0, 3),
            confidence=1.0,
            metadata={"miner": "drain"},
        )
        g.add_cluster("c_redis_hit", [1, 2, 3], record=cr)

        # Serialize to dict and JSON roundtrip
        d = g.to_dict()
        json_repr = json.dumps(d, ensure_ascii=False)
        d_reloaded = json.loads(json_repr)
        g_reconstructed = MemoryGraph.from_dict(d_reloaded)

        # Invariant verifications
        self.assertEqual(len(g_reconstructed.entities), 2)
        self.assertEqual(len(g_reconstructed.hyperedges), 1)
        self.assertEqual(len(g_reconstructed.episodic_nodes), 1)
        self.assertEqual(len(g_reconstructed.macro_propositions), 1)
        self.assertEqual(len(g_reconstructed.clusters), 1)
        self.assertEqual(len(g_reconstructed.cluster_records), 1)

        # MacroProposition fidelity
        restored_mp = g_reconstructed.macro_propositions[0]
        self.assertEqual(restored_mp.id, 50)
        self.assertEqual(restored_mp.statement, mp.statement)
        self.assertEqual(restored_mp.member_count, 1000)
        self.assertEqual(restored_mp.member_ids, [e1.id, e2.id])
        self.assertEqual(restored_mp.epoch_range, (0, 3))
        self.assertEqual(restored_mp.attributes["hit_rate"], 0.98)

        # ClusterRecord fidelity
        restored_cr = g_reconstructed.cluster_records["c_redis_hit"]
        self.assertEqual(restored_cr.signature, "REDIS HIT key=* elapsed=*ms")
        self.assertEqual(restored_cr.template, restored_cr.signature)
        self.assertEqual(restored_cr.count, 3)
        self.assertEqual(restored_cr.parameters, [{"key": "user:1", "elapsed": 1.2}])

    def test_types_cluster_record_consistency_and_orphans(self) -> None:
        """Verifies cluster membership can exist with or without an attached ClusterRecord."""
        g = MemoryGraph()
        # Cluster with full record
        cr = ClusterRecord(cluster_id="c_with_record", cluster_type="type_a", signature="sig_a", member_ids=[1, 2])
        g.add_cluster("c_with_record", [1, 2], record=cr)

        # Cluster without record (orphaned cluster)
        g.add_cluster("c_orphan", [3, 4, 5])

        self.assertIn("c_with_record", g.cluster_records)
        self.assertNotIn("c_orphan", g.cluster_records)
        self.assertEqual(g.clusters["c_orphan"], [3, 4, 5])

        # Roundtrip preserving asymmetry
        g2 = MemoryGraph.from_dict(g.to_dict())
        self.assertIn("c_with_record", g2.cluster_records)
        self.assertNotIn("c_orphan", g2.cluster_records)
        self.assertEqual(g2.clusters["c_orphan"], [3, 4, 5])

    def test_types_macro_proposition_string_pool_deduplication(self) -> None:
        """Verifies that adding multiple macro propositions with shared statements does not duplicate pool strings."""
        g = MemoryGraph()
        initial_pool_len = len(g.string_pool)

        mp1 = MacroProposition(id=1, statement="Shared Macro Statement", cluster_type="trace", member_count=10)
        g.add_macro_proposition(mp1)
        after_mp1_len = len(g.string_pool)
        self.assertEqual(after_mp1_len, initial_pool_len + 2)  # statement + cluster_type

        # Add second macro proposition with identical statement and type
        mp2 = MacroProposition(id=2, statement="Shared Macro Statement", cluster_type="trace", member_count=20)
        g.add_macro_proposition(mp2)
        after_mp2_len = len(g.string_pool)
        self.assertEqual(after_mp2_len, after_mp1_len, "String pool should not grow when interning identical string")

    def test_types_memory_graph_from_dict_minimal_empty(self) -> None:
        """Verifies MemoryGraph.from_dict gracefully constructs a valid graph from empty dict."""
        empty_g = MemoryGraph.from_dict({})
        self.assertEqual(len(empty_g.entities), 0)
        self.assertEqual(len(empty_g.hyperedges), 0)
        self.assertEqual(len(empty_g.macro_propositions), 0)
        self.assertEqual(len(empty_g.clusters), 0)
        self.assertEqual(len(empty_g.cluster_records), 0)
        # Verify internal caches work
        self.assertIsNone(empty_g.find_entity_by_name("nonexistent"))


if __name__ == "__main__":
    unittest.main()
