"""
Unit and Integration Test Suite for ALMF Binary Codec, Blob Vault, and Smart Compression Router.

Covers:
- ALMF binary container serialization and deserialization roundtrips.
- CRC32 checksum corruption detection, magic validation, and truncation handling.
- Columnar hypergraph packing, open-ended epoch intervals, and temporal filtering.
- BlobVault lossless storage, 1-token @ptr(hash) generation, pack/unpack.
- SmartCompressionRouter multimodal classification and compression ratios across
  TRACE_INVARIANT, ARCH_DSL, AST_SKELETON, and LOSSLESS_VAULT.
- Large graph stress testing and file I/O operations.
"""

import os
import struct
import tempfile
import unittest
import zlib

from almf import (
    ALMFHeader,
    BlobVault,
    CompressionModality,
    Entity,
    EntityType,
    EpisodicNode,
    HeaderFlags,
    Hyperedge,
    MemoryGraph,
    RoutedItem,
    SectionEntry,
    SectionType,
    SmartCompressionRouter,
    VaultPointer,
    decode,
    encode,
    estimate_tokens,
    load_from_file,
    save_to_file,
    HEADER_SIZE,
    SECTION_ENTRY_SIZE,
)


class TestALMFCodecRoundtrip(unittest.TestCase):
    """Verifies binary encode and decode roundtrip fidelity across diverse graph structures."""

    def test_empty_graph_roundtrip(self) -> None:
        """Verifies that an empty MemoryGraph encodes and decodes cleanly."""
        graph = MemoryGraph()
        encoded = encode(graph)
        self.assertGreater(len(encoded), HEADER_SIZE)
        self.assertEqual(encoded[:4], b"ALMF")

        decoded = decode(encoded)
        self.assertEqual(len(decoded.entities), 0)
        self.assertEqual(len(decoded.hyperedges), 0)
        self.assertEqual(len(decoded.episodic_nodes), 0)
        self.assertEqual(len(decoded.vault_pointers), 0)

    def test_basic_graph_roundtrip(self) -> None:
        """Verifies entities, hyperedges, episodic nodes, and attributes roundtrip exactly."""
        graph = MemoryGraph()
        e1 = graph.add_entity("agent_core", EntityType.ACTOR, {"role": "worker", "level": 2})
        e2 = graph.add_entity("db_postgres", EntityType.ARTIFACT, {"port": 5432, "ssl": True})
        e3 = graph.add_entity("rule_backup", EntityType.CONSTRAINT, {"interval_hours": 24})

        h1 = graph.add_hyperedge(
            subject_id=e1.id,
            predicate="connects_to",
            object_id=e2.id,
            scope="infrastructure",
            epoch=1,
            confidence=0.95,
            temporal_validity=(1, 10),
        )
        h2 = graph.add_hyperedge(
            subject_id=e2.id,
            predicate="constrained_by",
            object_id=e3.id,
            scope="compliance",
            epoch=2,
            confidence=0.88,
            temporal_validity=(2, 0),  # open-ended validity
        )

        n1 = graph.add_episodic_node(
            level=1,
            summary="Established database connection and verified constraint compliance",
            turn_range=(1, 8),
            entity_ids=[e1.id, e2.id, e3.id],
        )

        encoded = encode(graph)
        decoded = decode(encoded)

        # Entity assertions
        self.assertEqual(len(decoded.entities), 3)
        dec_e1 = decoded.find_entity_by_name("agent_core")
        self.assertIsNotNone(dec_e1)
        self.assertEqual(dec_e1.type, EntityType.ACTOR)
        self.assertEqual(dec_e1.attributes["role"], "worker")
        self.assertEqual(dec_e1.attributes["level"], 2)

        dec_e2 = decoded.find_entity_by_name("db_postgres")
        self.assertIsNotNone(dec_e2)
        self.assertEqual(dec_e2.attributes["port"], 5432)
        self.assertTrue(dec_e2.attributes["ssl"])

        # Hyperedge assertions
        self.assertEqual(len(decoded.hyperedges), 2)
        edge1 = decoded.hyperedges[0]
        self.assertEqual(edge1.subject_id, e1.id)
        self.assertEqual(edge1.predicate, "connects_to")
        self.assertEqual(edge1.object_id, e2.id)
        self.assertEqual(edge1.scope, "infrastructure")
        self.assertEqual(edge1.epoch, 1)
        self.assertAlmostEqual(edge1.confidence, 0.95, places=2)
        self.assertEqual(edge1.temporal_validity, (1, 10))
        self.assertTrue(edge1.is_active_at(5))
        self.assertFalse(edge1.is_active_at(10))

        edge2 = decoded.hyperedges[1]
        self.assertEqual(edge2.predicate, "constrained_by")
        self.assertEqual(edge2.epoch, 2)
        # Open-ended validity check (end_epoch = 0 / None)
        self.assertTrue(edge2.is_active_at(2))
        self.assertTrue(edge2.is_active_at(500))
        self.assertFalse(edge2.is_active_at(1))

        # Episodic Node assertions
        self.assertEqual(len(decoded.episodic_nodes), 1)
        node = decoded.episodic_nodes[0]
        self.assertEqual(node.level, 1)
        self.assertEqual(node.turn_range, (1, 8))
        self.assertEqual(node.entity_ids, [e1.id, e2.id, e3.id])
        self.assertIn("Established database connection", node.summary)

    def test_unicode_and_special_characters_roundtrip(self) -> None:
        """Verifies strings with Cyrillic, emoji, math symbols, and newlines roundtrip faithfully."""
        graph = MemoryGraph()
        name_cyrillic = "Агент_Памяти_Оркестратор_🤖_Ω"
        summary_multiline = "Строка 1: Инициализация.\nСтрока 2: Проверка целостности данных (100% OK).\nСтрока 3: Конец."

        graph.add_entity(name_cyrillic, EntityType.ACTOR, {"описание": "Специалист по долгосрочной памяти", "статус": "активен"})
        graph.add_episodic_node(level=0, summary=summary_multiline, turn_range=(0, 1))

        encoded = encode(graph)
        decoded = decode(encoded)

        self.assertIsNotNone(decoded.find_entity_by_name(name_cyrillic))
        ent = decoded.find_entity_by_name(name_cyrillic)
        self.assertEqual(ent.attributes["описание"], "Специалист по долгосрочной памяти")
        self.assertEqual(decoded.episodic_nodes[0].summary, summary_multiline)

    def test_large_graph_stress_roundtrip(self) -> None:
        """Stress-tests encoding and decoding across 500 entities and 1,000 hyperedges."""
        graph = MemoryGraph()
        num_entities = 500
        num_edges = 1000

        for i in range(1, num_entities + 1):
            etype = EntityType(1 + (i % 8))
            graph.add_entity(f"entity_{i}", etype, {"index": i, "even": i % 2 == 0})

        for j in range(1, num_edges + 1):
            s_id = (j % num_entities) + 1
            o_id = ((j * 3) % num_entities) + 1
            epoch = j // 10
            graph.add_hyperedge(
                subject_id=s_id,
                predicate=f"rel_{j % 20}",
                object_id=o_id,
                scope=f"scope_{j % 5}",
                epoch=epoch,
                confidence=0.85,
            )

        encoded = encode(graph)
        self.assertGreater(len(encoded), 1000)

        decoded = decode(encoded)
        self.assertEqual(len(decoded.entities), num_entities)
        self.assertEqual(len(decoded.hyperedges), num_edges)

        # Spot check specific elements
        e_test = decoded.find_entity_by_name("entity_250")
        self.assertIsNotNone(e_test)
        self.assertEqual(e_test.attributes["index"], 250)
        self.assertTrue(e_test.attributes["even"])

        edge_test = decoded.hyperedges[499]
        self.assertEqual(edge_test.predicate, "rel_0")

    def test_file_io_save_and_load(self) -> None:
        """Verifies save_to_file and load_from_file disk roundtrip."""
        graph = MemoryGraph()
        graph.add_entity("node_disk", EntityType.ARTIFACT, {"source": "disk_test"})
        graph.add_hyperedge(1, "persists_to", 1, epoch=1)

        with tempfile.NamedTemporaryFile(suffix=".almf", delete=False) as tf:
            path = tf.name

        try:
            bytes_written = save_to_file(graph, path)
            self.assertGreater(bytes_written, 0)
            self.assertEqual(os.path.getsize(path), bytes_written)

            loaded_graph = load_from_file(path)
            self.assertIsNotNone(loaded_graph.find_entity_by_name("node_disk"))
            self.assertEqual(len(loaded_graph.hyperedges), 1)
        finally:
            if os.path.exists(path):
                os.remove(path)


class TestALMFCodecIntegrityAndErrors(unittest.TestCase):
    """Verifies CRC32 checksum, magic byte enforcement, truncation, and corruption handling."""

    def test_corrupted_crc32_detection(self) -> None:
        """Bit-flip in data payload triggers CRC32 validation error."""
        graph = MemoryGraph()
        graph.add_entity("e1", EntityType.CONCEPT)
        encoded = bytearray(encode(graph))

        # Flip a bit in the payload (offset 50)
        encoded[50] ^= 0xFF

        with self.assertRaises(ValueError) as ctx:
            decode(bytes(encoded))
        self.assertIn("CRC32 integrity failure", str(ctx.exception))

    def test_invalid_magic_bytes_detection(self) -> None:
        """Invalid magic byte header triggers immediate ValueError."""
        graph = MemoryGraph()
        encoded = bytearray(encode(graph))
        encoded[0:4] = b"BADM"

        with self.assertRaises(ValueError) as ctx:
            decode(bytes(encoded))
        self.assertIn("Invalid ALMF magic", str(ctx.exception))

    def test_truncated_buffer_detection(self) -> None:
        """Buffers smaller than 32 bytes or truncated midway trigger clean exceptions."""
        with self.assertRaises(ValueError):
            decode(b"")

        with self.assertRaises(ValueError):
            decode(b"ALMF" + b"\x00" * 10)

        graph = MemoryGraph()
        graph.add_entity("e1", EntityType.CONCEPT)
        encoded = encode(graph)

        # Truncate after header but before section data completes
        truncated = encoded[:HEADER_SIZE + 10]
        # Recomputing CRC would still fail on truncated sections
        with self.assertRaises(ValueError):
            decode(truncated)


class TestBlobVault(unittest.TestCase):
    """Verifies BlobVault lossless storage, pointer substitution, packing, and unpacking."""

    def setUp(self) -> None:
        self.vault = BlobVault()

    def test_put_get_and_pointers(self) -> None:
        """Tests basic string/byte put, pointer generation, and retrieval."""
        secret = "mock_token_test_api_token_999888777666"
        ptr = self.vault.put(secret, name="test_secret")

        # Verify pointer format
        self.assertTrue(ptr.pointer_symbol.startswith("@ptr("))
        self.assertTrue(ptr.pointer_symbol.endswith(")"))
        self.assertEqual(len(ptr.hash), 8)

        # Verification via pointer object, pointer symbol, and hash
        self.assertEqual(self.vault.get(ptr), secret.encode("utf-8"))
        self.assertEqual(self.vault.get(ptr.pointer_symbol), secret.encode("utf-8"))
        self.assertEqual(self.vault.get_str(ptr.hash), secret)

    def test_raw_bytes_and_binary_data(self) -> None:
        """Tests arbitrary binary data storage."""
        raw = bytes([i % 256 for i in range(2048)])
        ptr = self.vault.put(raw, name="binary_blob")
        retrieved = self.vault.get(ptr)
        self.assertEqual(retrieved, raw)

    def test_pack_and_unpack_roundtrip(self) -> None:
        """Tests binary serialization and deserialization of the vault."""
        b1 = "constant_api_url_https://api.internal.cloud"
        b2 = "BEGIN PRIVATE KEY\nMIIEvgIBADANBgkqhkiG9w0BAQEFAASC\nEND PRIVATE KEY"
        b3 = "x" * 5000  # Highly compressible string

        p1 = self.vault.put(b1, name="url")
        p2 = self.vault.put(b2, name="key")
        p3 = self.vault.put(b3, name="large")

        packed = self.vault.pack()
        self.assertGreater(len(packed), 8)
        self.assertEqual(packed[:4], b"VLT1")

        unpacked_vault = BlobVault.from_bytes(packed)
        self.assertEqual(len(unpacked_vault), 3)
        self.assertEqual(unpacked_vault.get_str(p1.pointer_symbol), b1)
        self.assertEqual(unpacked_vault.get_str(p2.pointer_symbol), b2)
        self.assertEqual(unpacked_vault.get_str(p3.pointer_symbol), b3)

    def test_hash_collision_handling(self) -> None:
        """Verifies collision handling produces distinct hash keys."""
        # Intentionally force distinct data
        d1 = b"payload_alpha_1"
        d2 = b"payload_alpha_2"
        p1 = self.vault.put(d1)
        p2 = self.vault.put(d2)
        self.assertNotEqual(p1.hash, p2.hash)

    def test_delete_and_has(self) -> None:
        """Tests membership check and deletion."""
        ptr = self.vault.put("temporary_secret")
        self.assertTrue(self.vault.has(ptr))
        self.assertTrue(self.vault.delete(ptr))
        self.assertFalse(self.vault.has(ptr))
        with self.assertRaises(KeyError):
            self.vault.get(ptr)


class TestSmartCompressionRouter(unittest.TestCase):
    """Verifies SmartCompressionRouter across TRACE_INVARIANT, ARCH_DSL, AST_SKELETON, and LOSSLESS_VAULT."""

    def setUp(self) -> None:
        self.vault = BlobVault()
        self.router = SmartCompressionRouter(vault=self.vault)

    def test_trace_invariant_regime(self) -> None:
        """Verifies TRACE_INVARIANT strips procedural noise and extracts invariant."""
        # Simulate a 1,500-token verbose error trace
        trace_steps = "\n".join([f"  File 'worker.py', line {i}\n    RuntimeError: Connection timed out to worker node" for i in range(150)])
        raw_trace = f"""
2026-11-13T22:30:00Z Running distributed task
$ python -m pytest tests/test_distributed.py
Traceback (most recent call last):
{trace_steps}
ConnectionError: Failed to connect to redis cluster at port 6379
FAILED (failures=1)
Restarting redis service...
$ systemctl restart redis
$ python -m pytest tests/test_distributed.py
tests/test_distributed.py ... PASSED
Status: OK
"""
        item = self.router.route_item(raw_trace)
        self.assertEqual(item.modality, CompressionModality.TRACE_INVARIANT)
        self.assertIn("!INVARIANT:", item.compressed_text)
        self.assertIn("RESOLVED_SUCCESS", item.compressed_text)
        # Should achieve significant token reduction
        self.assertGreater(item.original_tokens, 500)
        self.assertLess(item.compressed_tokens, 50)
        self.assertGreater(item.compression_ratio, 25.0)

    def test_arch_dsl_regime(self) -> None:
        """Verifies ARCH_DSL strips prose filler and formats into canonical DSL."""
        raw_arch = """
We decided that all requests from the frontend client must be directed to the auth-service on the k8s-prod cluster.
The timeout_ms must be set to 3000 to prevent gateway exhaustion.
The auth_type is set to JWT-ES256 and retries set to 3.
In order to comply with security standards, all communication must be encrypted and verified by the identity gateway.
Furthermore, this architectural decision is mandatory for all microservices in the production namespace.
"""
        item = self.router.route_item(raw_arch, modality=CompressionModality.ARCH_DSL)
        self.assertEqual(item.modality, CompressionModality.ARCH_DSL)
        self.assertTrue(item.compressed_text.startswith("@"))
        self.assertIn("auth_service", item.compressed_text)
        self.assertIn("#k8s_prod", item.compressed_text)
        self.assertIn("timeout_ms=3000", item.compressed_text)
        self.assertIn("retries=3", item.compressed_text)
        self.assertGreater(item.compression_ratio, 2.0)

    def test_ast_skeleton_regime(self) -> None:
        """Verifies AST_SKELETON strips function bodies and retains signatures and docstrings."""
        raw_code = """
import os
from typing import Dict, List, Optional

class SessionCache:
    \"\"\"Thread-safe in-memory cache for user sessions.\"\"\"
    def __init__(self, ttl_seconds: int = 3600) -> None:
        self.ttl = ttl_seconds
        self._store: Dict[str, Dict] = {}
        for i in range(100):
            self._store[f"user_{i}"] = {"created": i, "data": [x * 2 for x in range(200)]}

    def get_session(self, session_id: str) -> Optional[Dict]:
        \"\"\"Retrieves user session by UUID.\"\"\"
        if session_id in self._store:
            return self._store[session_id]
        computed = []
        for j in range(500):
            computed.append(j ** 3)
        return {"id": session_id, "items": computed}

    async def clear_expired(self) -> int:
        \"\"\"Prunes expired session entries.\"\"\"
        expired_count = 0
        for k in list(self._store.keys()):
            expired_count += 1
            del self._store[k]
        return expired_count
"""
        item = self.router.route_item(raw_code)
        self.assertEqual(item.modality, CompressionModality.AST_SKELETON)
        skeleton = item.compressed_text

        # Signatures and classes must be preserved
        self.assertIn("class SessionCache:", skeleton)
        self.assertIn("def get_session(self, session_id: str) -> Optional[Dict]:", skeleton)
        self.assertIn("async def clear_expired(self) -> int:", skeleton)
        # Docstrings should be retained
        self.assertIn("Thread-safe in-memory cache for user sessions.", skeleton)
        # Internal loops and implementation details stripped
        self.assertNotIn("range(500)", skeleton)
        self.assertNotIn("j ** 3", skeleton)
        self.assertIn("...", skeleton)

        self.assertGreater(item.compression_ratio, 1.5)

    def test_lossless_vault_regime(self) -> None:
        """Verifies LOSSLESS_VAULT stores exact payload and substitutes 1-token @ptr(hash)."""
        secret = "mock_token_51M0abc000111222333444555666777888999"
        item = self.router.route_item(secret)

        self.assertEqual(item.modality, CompressionModality.LOSSLESS_VAULT)
        self.assertEqual(item.compressed_tokens, 1)
        self.assertTrue(item.compressed_text.startswith("@ptr("))
        self.assertIsNotNone(item.vault_pointer)

        # Retrieval fidelity check
        restored = self.vault.get_str(item.vault_pointer)
        self.assertEqual(restored, secret)

    def test_route_batch(self) -> None:
        """Verifies batch routing handles heterogenous inputs correctly."""
        inputs = [
            "mock_token_batch_test_token_1234567890",
            "Traceback (most recent call last):\n  File 'a.py', line 1\nTypeError: bad type\nFAILED\nStatus: OK\nPASSED",
            "def compute_score(x: int) -> float:\n    return x * 1.5\nclass Scorer:\n    pass\n",
            "We decided that all telemetry must be sent to the metrics-service on prod-cluster.",
        ]
        results = self.router.route_batch(inputs)
        self.assertEqual(len(results), 4)
        self.assertEqual(results[0].modality, CompressionModality.LOSSLESS_VAULT)
        self.assertEqual(results[1].modality, CompressionModality.TRACE_INVARIANT)
        self.assertEqual(results[2].modality, CompressionModality.AST_SKELETON)
        self.assertEqual(results[3].modality, CompressionModality.ARCH_DSL)


class TestIntegratedGraphWithVault(unittest.TestCase):
    """Verifies end-to-end integration between MemoryGraph, BlobVault, and Codec."""

    def test_graph_with_vault_roundtrip(self) -> None:
        """Verifies container encodes graph and vault together with full pointer dereferencing."""
        graph = MemoryGraph()
        vault = BlobVault()

        # Add entities with vault pointers
        p1 = vault.put("production_secret_key_9999", name="prod_key")
        p2 = vault.put("^(?=.*[A-Za-z])(?=.*\\d)[A-Za-z\\d]{8,}$", name="password_regex")

        e1 = graph.add_entity("auth_service", EntityType.ACTOR, {"key_ref": p1.pointer_symbol})
        e2 = graph.add_entity("validator", EntityType.CONSTRAINT, {"regex_ref": p2.pointer_symbol})

        graph.add_hyperedge(e1.id, "uses_validator", e2.id, epoch=1)

        # Encode with attached vault
        encoded = encode(graph, vault=vault)
        self.assertGreater(len(encoded), HEADER_SIZE)

        # Decode
        decoded = decode(encoded)
        self.assertIsNotNone(decoded.vault)
        self.assertEqual(len(decoded.vault), 2)

        # Dereference pointers through the decoded graph's vault
        self.assertEqual(decoded.vault.get_str(p1.pointer_symbol), "production_secret_key_9999")
        self.assertEqual(decoded.vault.get_str(p2.pointer_symbol), "^(?=.*[A-Za-z])(?=.*\\d)[A-Za-z\\d]{8,}$")

        dec_e1 = decoded.find_entity_by_name("auth_service")
        self.assertEqual(dec_e1.attributes["key_ref"], p1.pointer_symbol)

    def test_compression_ratio_measurement(self) -> None:
        """Verifies binary container achieves significant compression over raw JSON."""
        import json
        graph = MemoryGraph()
        for i in range(100):
            graph.add_entity(f"component_{i}", EntityType.ACTOR, {"status": "healthy", "region": "us-east-1", "id": i})
            graph.add_hyperedge(i + 1, "communicates_with", (i + 1) % 100 + 1, scope="internal_rpc", epoch=1)

        raw_json_bytes = json.dumps(graph.to_dict()).encode("utf-8")
        encoded_almf = encode(graph)

        self.assertGreater(len(raw_json_bytes), len(encoded_almf) * 10)
        decoded = decode(encoded_almf)
        self.assertEqual(len(decoded.entities), 100)
        self.assertEqual(len(decoded.hyperedges), 100)

    def test_m1_verifier_improvements_end_epoch_and_cache(self) -> None:
        """Verifies M1 verifier fixes: end_epoch=0 open-ended validity and entity cache consistency."""
        # Finding 1: end_epoch = 0 open-ended interval
        edge = Hyperedge(1, 10, "relates_to", 20, temporal_validity=(5, 0))
        self.assertFalse(edge.is_active_at(4))
        self.assertTrue(edge.is_active_at(5))
        self.assertTrue(edge.is_active_at(100))
        self.assertEqual(edge.subject, 10)
        self.assertEqual(edge.object, 20)

        # Finding 2: entity name cache consistency upon updates
        graph = MemoryGraph()
        e1 = graph.add_entity("worker_node", EntityType.ACTOR, {"v": 1})
        self.assertEqual(e1.id, 1)
        e2 = graph.add_entity("worker_node", EntityType.ACTOR, {"v": 2, "extra": "data"})
        self.assertEqual(e2.id, 1)
        self.assertEqual(e2.attributes["v"], 2)
        self.assertEqual(e2.attributes["extra"], "data")
        self.assertEqual(graph.find_entity_by_name("worker_node").id, 1)

    def test_hyperedge_contract_aliases(self) -> None:
        """Verifies Hyperedge accepts subject and object keyword arguments per PROJECT.md contract."""
        edge = Hyperedge(id=42, subject=101, predicate="calls", object=202, scope="api", epoch=3, confidence=0.9)
        self.assertEqual(edge.id, 42)
        self.assertEqual(edge.subject_id, 101)
        self.assertEqual(edge.subject, 101)
        self.assertEqual(edge.object_id, 202)
        self.assertEqual(edge.object, 202)
        self.assertEqual(edge.predicate, "calls")


if __name__ == "__main__":
    unittest.main()
