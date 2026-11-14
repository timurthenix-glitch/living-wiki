"""
Unit and Integration Test Suite for ALMF Milestone 3:
- SimHash-64 fingerprinting and bitwise Hamming search
- Hybrid query engine and fact ranking (SimHash + lexical + confidence + recency)
- Knapsack context allocator with strict token budget enforcement (<= budget)
- Ingestion parsers (Markdown Living Wiki bilingual, Python AST skeleton, Trace invariant)
- CLI commands (pack, unpack, query, stats) and module execution (`python -m almf`)
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

from almf import (
    BlobVault,
    CompressionModality,
    Entity,
    EntityType,
    EpisodicNode,
    Fact,
    Hyperedge,
    MemoryGraph,
    SimHashIndex,
    SimHashMatch,
    compute_hybrid_score,
    count_tokens,
    decode,
    encode,
    extract_graph_facts,
    hamming_distance,
    hamming_similarity,
    load_from_file,
    parse_directory,
    parse_file,
    parse_markdown,
    parse_python_ast,
    parse_trace_log,
    query_facts,
    render_context,
    save_to_file,
    simhash64,
)
from almf.cli import main as cli_main


class TestSimHash64(unittest.TestCase):
    """Verifies SimHash-64 fingerprinting, bitwise Hamming distance, and similarity math."""

    def test_deterministic_fingerprint(self) -> None:
        """Identical text must always produce identical 64-bit fingerprints."""
        text = "PostgreSQL database running on port 5432 with SSL enabled"
        h1 = simhash64(text)
        h2 = simhash64(text)
        self.assertEqual(h1, h2)
        self.assertIsInstance(h1, int)
        self.assertGreater(h1, 0)
        self.assertEqual(hamming_distance(h1, h2), 0)
        self.assertAlmostEqual(hamming_similarity(h1, h2), 1.0)

    def test_empty_string(self) -> None:
        """Empty or whitespace-only strings must return fingerprint 0."""
        self.assertEqual(simhash64(""), 0)
        self.assertEqual(simhash64("   "), 0)
        self.assertEqual(hamming_distance(0, 0), 0)

    def test_near_duplicate_similarity(self) -> None:
        """Near-duplicate texts must have small Hamming distance and high similarity."""
        s1 = "auth service deploys to k8s cluster in us-east-1"
        s2 = "auth service deploys to k8s cluster in us-east-2"
        h1 = simhash64(s1)
        h2 = simhash64(s2)
        dist = hamming_distance(h1, h2)
        sim = hamming_similarity(h1, h2)
        self.assertLessEqual(dist, 16)
        self.assertGreaterEqual(sim, 0.75)

    def test_unrelated_text_divergence(self) -> None:
        """Semantically unrelated texts must diverge significantly (~32 bit distance)."""
        s1 = "auth service deploys to k8s cluster with jwt validation"
        s2 = "baking delicious chocolate cookies with vanilla sugar"
        h1 = simhash64(s1)
        h2 = simhash64(s2)
        dist = hamming_distance(h1, h2)
        self.assertGreaterEqual(dist, 18)
        self.assertLessEqual(hamming_similarity(h1, h2), 0.72)

    def test_multilingual_cyrillic_support(self) -> None:
        """Cyrillic Russian text must be tokenized and fingerprinted cleanly."""
        r1 = "База данных Postgresql запущена на порту 5432"
        r2 = "База данных Postgresql настроена на порт 5432"
        r3 = "Рецепт приготовления яблочного пирога с корицей"
        h1 = simhash64(r1)
        h2 = simhash64(r2)
        h3 = simhash64(r3)
        self.assertGreater(h1, 0)
        self.assertGreater(h2, 0)
        dist_close = hamming_distance(h1, h2)
        dist_far = hamming_distance(h1, h3)
        self.assertLess(dist_close, dist_far)
        self.assertGreater(hamming_similarity(h1, h2), hamming_similarity(h1, h3))

    def test_simhash_index_candidate_search(self) -> None:
        """SimHashIndex must index documents and retrieve top-k sorted by distance."""
        index = SimHashIndex()
        docs = [
            (1, "PostgreSQL relational database service"),
            (2, "Redis in-memory caching cluster"),
            (3, "Kubernetes ingress controller routing rules"),
            (4, "PostgreSQL database replication standby replica"),
            (5, "User authentication JWT ES256 tokens"),
        ]
        for doc_id, text in docs:
            index.add_document(doc_id, text)

        self.assertEqual(len(index), 5)
        # Search for postgres
        results = index.search("postgres database setup", max_hamming_distance=24)
        self.assertIn(1, results)
        self.assertIn(4, results)

        detailed = index.search_detailed("postgres database setup", max_hamming_distance=24)
        self.assertTrue(all(isinstance(m, SimHashMatch) for m in detailed))
        # Distance must be ascending
        for i in range(len(detailed) - 1):
            self.assertLessEqual(detailed[i].distance, detailed[i + 1].distance)

    def test_graph_indexing_and_metadata_sync(self) -> None:
        """SimHashIndex must index all graph components and sync with Section 5 metadata."""
        graph = MemoryGraph()
        e1 = graph.add_entity("svc_auth", EntityType.ACTOR, {"port": 8080})
        e2 = graph.add_entity("db_users", EntityType.ARTIFACT, {"engine": "postgres"})
        h1 = graph.add_hyperedge(e1.id, "connects_to", e2.id)
        n1 = graph.add_episodic_node(1, "Initialized auth connection to user database")

        index = SimHashIndex()
        index.index_graph(graph)

        self.assertIn("simhash_index", graph.metadata)
        self.assertEqual(len(graph.metadata["simhash_index"]), len(graph.entities) + len(graph.hyperedges) + len(graph.episodic_nodes))

        # Test sync into new index
        index2 = SimHashIndex()
        index2.sync_from_metadata(graph)
        self.assertEqual(len(index2), len(index))
        matches = index2.search("auth connection postgres", max_hamming_distance=28)
        self.assertGreater(len(matches), 0)

    def test_simhash_index_scale_latency_sub_millisecond(self) -> None:
        """SimHashIndex must index 10,000 items and achieve < 1.0 ms query latency."""
        import time

        index = SimHashIndex()
        for i in range(10000):
            index.add_document(i, f"service_{i} alpha beta storage node_{i} port_{5000 + i % 1000}")

        self.assertEqual(len(index), 10000)

        # Warm-up query
        index.search_detailed("alpha storage fast service", max_hamming_distance=24, top_k=5)

        queries = [
            "alpha storage fast service",
            "node 500 port 5500",
            "beta storage database node",
            "completely unrelated culinary recipe",
            "service 9999 port 5999",
        ]
        iterations = 20
        total_queries = iterations * len(queries)

        # Benchmark search_detailed over 100 queries
        t0 = time.perf_counter()
        for _ in range(iterations):
            for q in queries:
                res = index.search_detailed(q, max_hamming_distance=24, top_k=5)
                self.assertLessEqual(len(res), 5)
        elapsed = time.perf_counter() - t0
        avg_ms = (elapsed * 1000.0) / total_queries
        self.assertLess(avg_ms, 1.0, f"SimHash search_detailed latency was {avg_ms:.3f} ms, expected < 1.0 ms")

        # Benchmark search over 100 queries
        t0 = time.perf_counter()
        for _ in range(iterations):
            for q in queries:
                ids = index.search(q, max_hamming_distance=24, top_k=5)
                self.assertLessEqual(len(ids), 5)
        elapsed_s = time.perf_counter() - t0
        avg_s_ms = (elapsed_s * 1000.0) / total_queries
        self.assertLess(avg_s_ms, 1.0, f"SimHash search (id only) latency was {avg_s_ms:.3f} ms, expected < 1.0 ms")


class TestFactAndHybridQuery(unittest.TestCase):
    """Verifies Fact representation and hybrid ranking engine."""

    def test_fact_dataclass_properties(self) -> None:
        """Fact must support both .object and .object_val and flexible attributes."""
        f1 = Fact(
            id=1,
            subject="auth_svc",
            predicate="deploys_to",
            object="k8s_prod",
            confidence=0.95,
            epoch=12,
            attributes={"zone": "us-east-1"},
        )
        self.assertEqual(f1.object, "k8s_prod")
        self.assertEqual(f1.object_val, "k8s_prod")
        self.assertEqual(f1.confidence, 0.95)
        self.assertEqual(f1.epoch, 12)
        self.assertEqual(f1.attributes["zone"], "us-east-1")
        self.assertIn("auth_svc", f1.statement)

        d = f1.to_dict()
        f2 = Fact.from_dict(d)
        self.assertEqual(f2.subject, f1.subject)
        self.assertEqual(f2.object, f1.object)
        self.assertEqual(f2.attributes, f1.attributes)

    def test_extract_graph_facts(self) -> None:
        """extract_graph_facts must convert hyperedges, entities, and episodes into Facts."""
        graph = MemoryGraph()
        e1 = graph.add_entity("worker", EntityType.ACTOR, {"timeout": 30})
        e2 = graph.add_entity("queue", EntityType.ARTIFACT)
        graph.add_hyperedge(e1.id, "consumes_from", e2.id, confidence=0.9)
        graph.add_episodic_node(1, "Worker started consuming from queue")

        facts = extract_graph_facts(graph)
        self.assertGreaterEqual(len(facts), 3)
        subjects = [f.subject for f in facts]
        self.assertIn("worker", subjects)

    def test_hybrid_scoring_components(self) -> None:
        """Hybrid score must reflect SimHash, lexical overlap, confidence, and recency."""
        fact_recent = Fact(id=1, statement="@worker: consumes_from(#queue)", subject="worker", predicate="consumes_from", object_val="queue", confidence=1.0, epoch=100)
        fact_old = Fact(id=2, statement="@worker: consumes_from(#queue)", subject="worker", predicate="consumes_from", object_val="queue", confidence=1.0, epoch=10)

        q_tokens = {"worker", "queue"}
        q_sh = simhash64("worker queue")

        sf_recent = compute_hybrid_score(fact_recent, "worker queue", q_tokens, q_sh, max_epoch=100)
        sf_old = compute_hybrid_score(fact_old, "worker queue", q_tokens, q_sh, max_epoch=100)

        # Recent fact must score higher than old fact due to recency
        self.assertGreater(sf_recent.score, sf_old.score)
        self.assertGreater(sf_recent.recency_score, sf_old.recency_score)

    def test_query_facts_confidence_gating(self) -> None:
        """query_facts must respect min_confidence threshold."""
        graph = MemoryGraph()
        e1 = graph.add_entity("alpha", EntityType.CONCEPT)
        e2 = graph.add_entity("beta", EntityType.CONCEPT)
        graph.add_hyperedge(e1.id, "links_to", e2.id, confidence=0.9)
        graph.add_hyperedge(e1.id, "weakly_links_to", e2.id, confidence=0.4)

        facts_high = query_facts(graph, "links", top_k=5, min_confidence=0.7)
        self.assertEqual(len(facts_high), 1)
        self.assertEqual(facts_high[0].predicate, "links_to")

        facts_all = query_facts(graph, "links", top_k=5, min_confidence=0.3)
        self.assertEqual(len(facts_all), 2)


class TestKnapsackContextAllocator(unittest.TestCase):
    """Verifies greedy knapsack context budget enforcement (strictly <= budget)."""

    def setUp(self) -> None:
        self.graph = MemoryGraph()
        # Seed graph with 20 distinct facts across multiple epochs
        for i in range(1, 21):
            e_svc = self.graph.add_entity(f"service_{i:02d}", EntityType.ACTOR, {"port": 8000 + i, "active": True})
            e_target = self.graph.add_entity(f"target_{i:02d}", EntityType.ARTIFACT)
            self.graph.add_hyperedge(
                subject_id=e_svc.id,
                predicate="routes_traffic_to",
                object_id=e_target.id,
                scope=f"tier_{i % 3}",
                epoch=i * 5,
                confidence=0.85 + (i % 15) * 0.01,
            )
        self.graph.add_episodic_node(0, "Active cluster state: all 20 services operational with tier routing.")
        self.graph.add_episodic_node(1, "Milestone 1 completed: baseline routing table initialized.")

    def test_token_counter(self) -> None:
        """count_tokens must return positive token counts for non-empty strings."""
        text = "Hello world! This is a test proposition."
        cnt = count_tokens(text)
        self.assertGreater(cnt, 0)
        self.assertEqual(count_tokens(""), 0)

    def test_knapsack_strict_budget_enforcement(self) -> None:
        """render_context must NEVER exceed the requested token_budget across diverse limits."""
        budgets = [10, 25, 50, 100, 200, 500, 1000]
        query = "service routes traffic to target"

        for b in budgets:
            ctx = render_context(self.graph, query, token_budget=b, tokenizer_name="o200k_base")
            tok_count = count_tokens(ctx, tokenizer_name="o200k_base")
            self.assertLessEqual(
                tok_count,
                b,
                f"Budget violation: requested {b} tokens, got {tok_count} tokens:\n{ctx}",
            )
            if b >= 50:
                self.assertGreater(tok_count, 0)
                self.assertIn("routes_traffic_to", ctx)

    def test_knapsack_zero_and_minimal_budget(self) -> None:
        """Knapsack must handle 0 or tiny budgets without crashing or exceeding limit."""
        ctx_0 = render_context(self.graph, "service", token_budget=0)
        self.assertEqual(ctx_0, "")

        ctx_5 = render_context(self.graph, "service", token_budget=5)
        self.assertLessEqual(count_tokens(ctx_5), 5)

    def test_context_output_is_clean_markdown_dsl(self) -> None:
        """Output must be clean readable Markdown/DSL text without binary artifacts."""
        ctx = render_context(self.graph, "service routes traffic", token_budget=300)
        self.assertIsInstance(ctx, str)
        # Verify absence of binary null bytes or unprintable control characters
        self.assertNotIn("\x00", ctx)
        self.assertNotIn("\ufffd", ctx)
        lines = ctx.splitlines()
        self.assertGreater(len(lines), 1)
        # Must contain DSL markers like @ or ! or [ALMF-STATE:
        has_dsl = any(line.startswith(("@", "!", "[")) for line in lines)
        self.assertTrue(has_dsl)


class TestIngestionParsers(unittest.TestCase):
    """Verifies bilingual Markdown, Python AST, and Trace log ingestion parsers."""

    def test_markdown_parser_bilingual(self) -> None:
        """parse_markdown must parse Latin and Cyrillic headers, gotchas, rules, and topic pointers."""
        md_text = """# Память проекта
## О пользователе
- Пользователь предпочитает краткие ответы без преамбул.
- Часовой пояс: UTC+3.

## Проект и стек
- Основной стек: Python 3.14, Brotli, SQLite FTS5.
- Архитектура и слои — memory/architecture.md

## Решения
- Принято решение использовать SimHash-64 для индексации.
- Формат контейнера: ALMF v1 Little-Endian.

## Грабли
- Не использовать native plan system во избежание auto-approve коллизии.
- Лимит горячей памяти: 6000 символов.

!RULE: pod_memory_must_exceed_heap_by_256Mi
"""
        graph = parse_markdown(md_text, filename="MEMORY.md")
        self.assertGreater(len(graph.entities), 5)
        self.assertGreater(len(graph.hyperedges), 3)

        # Check topic pointer preservation
        topic_ent = graph.find_entity_by_name("Архитектура и слои")
        self.assertIsNotNone(topic_ent)
        self.assertEqual(topic_ent.attributes.get("cold_pointer"), "memory/architecture.md")

        # Check rule extraction
        rule_ents = [e for e in graph.entities.values() if e.type == EntityType.CONSTRAINT]
        self.assertGreater(len(rule_ents), 0)

        # Query extracted memory
        results = query_facts(graph, "какой лимит горячей памяти?")
        self.assertGreater(len(results), 0)
        self.assertTrue(any("6000" in f.statement for f in results))

    def test_markdown_parser_vault_pointer_extraction(self) -> None:
        """Secrets and exact tokens in Markdown must be stored in vault and replaced with @ptr."""
        md_text = """## Configurations
- api_token: mock_token_99887766554433221100aabbccddeeff
- database_port: 5432
"""
        graph = parse_markdown(md_text)
        self.assertIsNotNone(graph.vault)
        self.assertGreater(len(graph.vault), 0)
        self.assertGreater(len(graph.vault_pointers), 0)

        # Verify pointer symbol exists in entity attributes
        token_ents = [e for e in graph.entities.values() if any("@ptr(" in str(v) for v in e.attributes.values())]
        self.assertGreater(len(token_ents), 0)

    def test_python_ast_skeleton_parser(self) -> None:
        """parse_python_ast must extract classes, signatures, docstrings and prune function bodies."""
        code = '''"""Core memory service module."""

class MemoryService:
    """Manages long-term agent memory storage."""

    def __init__(self, capacity: int = 1000) -> None:
        self.capacity = capacity
        # Complex internal loop to be pruned
        for i in range(100):
            print(i)

    async def fetch_fact(self, query: str, top_k: int = 5) -> list[str]:
        """Fetches top-k relevant facts."""
        results = []
        return results

def standalone_helper(x: int, y: int = 0) -> int:
    """Computes coordinate offset."""
    return x + y
'''
        graph = parse_python_ast(code, module_name="memory_svc")
        self.assertGreater(len(graph.entities), 3)

        # Check class and methods
        cls_ent = graph.find_entity_by_name("memory_svc.MemoryService")
        self.assertIsNotNone(cls_ent)
        self.assertEqual(cls_ent.attributes.get("kind"), "class")

        init_ent = graph.find_entity_by_name("memory_svc.MemoryService.__init__")
        self.assertIsNotNone(init_ent)
        self.assertIn("def __init__(self, capacity: int = 1000) -> None", init_ent.attributes["signature"])

        fetch_ent = graph.find_entity_by_name("memory_svc.MemoryService.fetch_fact")
        self.assertIsNotNone(fetch_ent)
        self.assertTrue(fetch_ent.attributes.get("is_async"))
        self.assertIn("async def fetch_fact", fetch_ent.attributes["signature"])

        fn_ent = graph.find_entity_by_name("memory_svc.standalone_helper")
        self.assertIsNotNone(fn_ent)
        self.assertIn("def standalone_helper(x: int, y: int = 0) -> int", fn_ent.attributes["signature"])

    def test_trace_log_parser(self) -> None:
        """parse_trace_log must collapse procedural error logs into concise invariant triples."""
        log_text = """$ pytest tests/test_payment.py
============================= test session starts =============================
collected 4 items

tests/test_payment.py .F..                                               [100%]

================================== FAILURES ===================================
___________________________ test_stripe_webhook_handler ___________________________

    def test_stripe_webhook_handler():
>       assert response.status_code == 200
E       AssertionError: Expected status 200, got 500: DatabaseConnectionError: connection pool exhausted

tests/test_payment.py:42: AssertionError
=========================== short test summary info ===========================
FAILED tests/test_payment.py::test_stripe_webhook_handler - AssertionError: connection pool exhausted
Command exited with code 1
"""
        graph = parse_trace_log(log_text, trace_id="ci_run_481")
        self.assertGreater(len(graph.entities), 2)
        self.assertGreater(len(graph.hyperedges), 1)

        err_ents = [e for e in graph.entities.values() if e.type == EntityType.ERROR]
        self.assertGreater(len(err_ents), 0)
        err = err_ents[0]
        self.assertIn("invariant", err.attributes)
        self.assertIn("!INVARIANT:", err.attributes["invariant"])

    def test_unified_parse_file_and_directory(self) -> None:
        """parse_file and parse_directory must correctly route multiple files by extension."""
        with tempfile.TemporaryDirectory() as tmpdir:
            f_md = os.path.join(tmpdir, "MEMORY.md")
            with open(f_md, "w", encoding="utf-8") as f:
                f.write("## Rules\n- Rule A: strictly test before commit\n")

            f_py = os.path.join(tmpdir, "engine.py")
            with open(f_py, "w", encoding="utf-8") as f:
                f.write("def start_engine() -> bool:\n    return True\n")

            graph = parse_directory(tmpdir)
            self.assertGreater(len(graph.entities), 3)
            self.assertGreater(len(graph.hyperedges), 1)


class TestCLIOperations(unittest.TestCase):
    """Verifies CLI pack, unpack, query, and stats subcommands."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.input_file = os.path.join(self.tmpdir.name, "sample_memory.md")
        with open(self.input_file, "w", encoding="utf-8") as f:
            f.write("""## System Architecture
- Auth Service connects to PostgreSQL on port 5432.
- Heap memory limit is set to 768Mi.
- Pod memory limit is set to 1024Mi.

## Security Policies
!RULE: pod_memory_must_exceed_heap_by_256Mi
- jwt_algorithm: ES256
""")
        self.almf_file = os.path.join(self.tmpdir.name, "container.almf")
        self.unpack_dir = os.path.join(self.tmpdir.name, "unpacked")

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_cli_pack_and_stats(self) -> None:
        """CLI pack must create a valid .almf file, and stats must exit code 0."""
        # 1. Test pack
        ret_pack = cli_main(["pack", self.input_file, "-o", self.almf_file])
        self.assertEqual(ret_pack, 0)
        self.assertTrue(os.path.isfile(self.almf_file))
        self.assertGreater(os.path.getsize(self.almf_file), 32)

        # 2. Test stats
        ret_stats = cli_main(["stats", self.almf_file])
        self.assertEqual(ret_stats, 0)

    def test_cli_unpack(self) -> None:
        """CLI unpack must restore MEMORY.md and JSON entity/edge descriptors."""
        cli_main(["pack", self.input_file, "-o", self.almf_file])
        ret_unpack = cli_main(["unpack", self.almf_file, "-o", self.unpack_dir])
        self.assertEqual(ret_unpack, 0)

        self.assertTrue(os.path.isfile(os.path.join(self.unpack_dir, "MEMORY.md")))
        self.assertTrue(os.path.isfile(os.path.join(self.unpack_dir, "entities.json")))
        self.assertTrue(os.path.isfile(os.path.join(self.unpack_dir, "hyperedges.json")))
        self.assertTrue(os.path.isfile(os.path.join(self.unpack_dir, "episodic_nodes.json")))

    def test_cli_query(self) -> None:
        """CLI query must return relevant context within budget."""
        cli_main(["pack", self.input_file, "-o", self.almf_file])
        ret_query = cli_main(["query", self.almf_file, "PostgreSQL port", "--budget", "150"])
        self.assertEqual(ret_query, 0)

    def test_cli_query_zero_budget(self) -> None:
        """CLI query with explicit --budget 0 must return 0 and no context."""
        cli_main(["pack", self.input_file, "-o", self.almf_file])
        cmd = [sys.executable, "-m", "almf", "query", self.almf_file, "PostgreSQL", "--budget", "0"]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("[No matching context found within budget]", proc.stdout)

    def test_python_module_execution(self) -> None:
        """`python -m almf ...` must execute successfully as a subprocess."""
        cli_main(["pack", self.input_file, "-o", self.almf_file])
        cmd = [sys.executable, "-m", "almf", "query", self.almf_file, "heap memory limit", "--budget", "100"]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("768Mi", proc.stdout)


if __name__ == "__main__":
    unittest.main()
