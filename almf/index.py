"""
almf.index: SimHash-64 Semantic Fingerprinting & Hamming Distance Index.

Implements Section 5.4 (Block 5: Semantic Search Index) and Section 7.1
of docs/ALMF_SPECIFICATION.md:
- 64-bit SimHash fingerprint generation with multi-lingual (Latin/Cyrillic) tokenization.
- Sub-millisecond bitwise Hamming distance computation via uint64 XOR and hardware `.bit_count()`.
- Indexing and top-k candidate retrieval for entities, hyperedge propositions, and episodic summaries.
"""

from __future__ import annotations

import functools
import hashlib
import heapq
import re
import struct
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from almf.types import Entity, EpisodicNode, Hyperedge, MemoryGraph


# =============================================================================
# 1. SimHash-64 Mathematical Fingerprinting
# =============================================================================

# Token extraction pattern: Unicode word characters, alphanumeric identifiers, and hyphens/underscores
_WORD_RE = re.compile(r"[\w\-]+", re.UNICODE)


def _extract_features(text: str) -> List[Tuple[str, int]]:
    """
    Extracts weighted features (word tokens, subword parts, character trigrams)
    from Latin, Cyrillic, and technical text.
    Returns a list of (feature_string, weight) tuples.
    """
    if not text:
        return []

    cleaned = text.lower().strip()
    words = _WORD_RE.findall(cleaned)
    if not words:
        return []

    features: List[Tuple[str, int]] = []

    # 1. Unigram words with frequency/length weighting
    word_freq: Dict[str, int] = {}
    for w in words:
        word_freq[w] = word_freq.get(w, 0) + 1
        # Also split compound identifiers (snake_case, kebab-case)
        subparts = re.split(r"[_\-]+", w)
        if len(subparts) > 1:
            for sp in subparts:
                if len(sp) >= 2:
                    word_freq[sp] = word_freq.get(sp, 0) + 1

    for w, freq in word_freq.items():
        base_weight = 5 if len(w) >= 3 else 3
        features.append((w, base_weight * freq))

    # 2. Substring character 3-grams for words >= 4 characters (handles morphology/inflections)
    for w in words:
        if len(w) >= 4:
            for i in range(len(w) - 2):
                features.append((f"c3:{w[i:i+3]}", 1))

    return features


def _hash64(feature: str) -> int:
    """Computes a 64-bit unsigned integer hash from a UTF-8 feature string."""
    digest = hashlib.sha256(feature.encode("utf-8")).digest()
    return struct.unpack("<Q", digest[:8])[0]


@functools.lru_cache(maxsize=2048)
def simhash64(text: str) -> int:
    """
    Computes a 64-bit SimHash fingerprint for an arbitrary string.
    Works seamlessly across English, Cyrillic, technical identifiers, and code signatures.
    """
    if not text or not text.strip():
        return 0

    features = _extract_features(text)
    if not features:
        return 0

    # 64-element accumulator vector
    vector = [0] * 64

    for feature_str, weight in features:
        h = _hash64(feature_str)
        for bit in range(64):
            if (h >> bit) & 1:
                vector[bit] += weight
            else:
                vector[bit] -= weight

    # Collapse accumulator into 64-bit fingerprint
    fingerprint = 0
    for bit in range(64):
        if vector[bit] > 0:
            fingerprint |= (1 << bit)

    return fingerprint


def hamming_distance(h1: int, h2: int) -> int:
    """
    Computes bitwise Hamming distance between two 64-bit integer fingerprints
    using hardware population count (.bit_count() in Python 3.10+).
    """
    return (h1 ^ h2).bit_count()


def hamming_similarity(h1: int, h2: int) -> float:
    """
    Computes normalized Hamming similarity in [0.0, 1.0].
    similarity = 1.0 - (Hamming distance / 64.0)
    """
    dist = (h1 ^ h2).bit_count()
    return max(0.0, min(1.0, 1.0 - (dist / 64.0)))


# =============================================================================
# 2. Search Result & Index Records
# =============================================================================

@dataclass
class SimHashMatch:
    """Represents a matched record from a SimHashIndex query."""
    doc_id: int
    distance: int
    similarity: float
    text: str = ""
    target_type: str = "doc"
    payload: Any = None


@dataclass(slots=True)
class _IndexEntry:
    doc_id: int
    simhash: int
    text: str
    target_type: str
    payload: Any

    def __lt__(self, other: _IndexEntry) -> bool:
        return self.doc_id < other.doc_id


# =============================================================================
# 3. SimHashIndex Class
# =============================================================================

class SimHashIndex:
    """
    In-memory 64-bit SimHash search index.
    Provides sub-millisecond Hamming distance candidate filtering and ranking.
    """

    def __init__(self) -> None:
        self._entries: List[_IndexEntry] = []
        self._id_map: Dict[int, _IndexEntry] = {}

    def __len__(self) -> int:
        return len(self._entries)

    def clear(self) -> None:
        """Clears all indexed entries."""
        self._entries.clear()
        self._id_map.clear()

    def add_document(
        self,
        doc_id: int,
        text: str,
        target_type: str = "doc",
        payload: Any = None,
        fingerprint: Optional[int] = None,
    ) -> int:
        """
        Adds a document or object to the SimHash index.
        Returns the computed 64-bit SimHash.
        """
        sh = fingerprint if fingerprint is not None else simhash64(text)
        entry = _IndexEntry(
            doc_id=doc_id,
            simhash=sh,
            text=text,
            target_type=target_type,
            payload=payload,
        )
        if doc_id in self._id_map:
            # Replace existing entry with same doc_id
            old = self._id_map[doc_id]
            idx = self._entries.index(old)
            self._entries[idx] = entry
        else:
            self._entries.append(entry)

        self._id_map[doc_id] = entry
        return sh

    def get_fingerprint(self, doc_id: int) -> Optional[int]:
        """Returns the 64-bit fingerprint for a registered document ID."""
        entry = self._id_map.get(doc_id)
        return entry.simhash if entry else None

    def search(
        self,
        query: str,
        max_hamming_distance: int = 24,
        top_k: Optional[int] = None,
    ) -> List[int]:
        """
        Searches index by query string, filtering by max Hamming distance and
        returning matching doc_ids sorted in ascending order of distance.
        """
        q_hash = simhash64(query)
        if top_k is not None and top_k > 0:
            heap: List[Tuple[int, int]] = []
            it = iter(self._entries)
            max_d = max_hamming_distance

            for entry in it:
                dist = (q_hash ^ entry.simhash).bit_count()
                if dist <= max_d:
                    heap.append((-dist, entry.doc_id))
                    if len(heap) == top_k:
                        heapq.heapify(heap)
                        max_d = -heap[0][0]
                        break

            if len(heap) == top_k:
                for entry in it:
                    dist = (q_hash ^ entry.simhash).bit_count()
                    if dist < max_d:
                        heapq.heapreplace(heap, (-dist, entry.doc_id))
                        max_d = -heap[0][0]

            heap.sort(key=lambda item: -item[0])
            return [doc_id for _, doc_id in heap]
        else:
            candidates: List[Tuple[int, int]] = []
            for entry in self._entries:
                dist = (q_hash ^ entry.simhash).bit_count()
                if dist <= max_hamming_distance:
                    candidates.append((dist, entry.doc_id))
            candidates.sort(key=lambda x: x[0])
            return [doc_id for _, doc_id in candidates]

    def search_with_distance(
        self,
        query: str,
        max_hamming_distance: int = 24,
        top_k: Optional[int] = None,
    ) -> List[Tuple[int, int]]:
        """
        Searches index by query string, returning (doc_id, distance) tuples.
        """
        q_hash = simhash64(query)
        if top_k is not None and top_k > 0:
            heap: List[Tuple[int, int]] = []
            it = iter(self._entries)
            max_d = max_hamming_distance

            for entry in it:
                dist = (q_hash ^ entry.simhash).bit_count()
                if dist <= max_d:
                    heap.append((-dist, entry.doc_id))
                    if len(heap) == top_k:
                        heapq.heapify(heap)
                        max_d = -heap[0][0]
                        break

            if len(heap) == top_k:
                for entry in it:
                    dist = (q_hash ^ entry.simhash).bit_count()
                    if dist < max_d:
                        heapq.heapreplace(heap, (-dist, entry.doc_id))
                        max_d = -heap[0][0]

            heap.sort(key=lambda item: -item[0])
            return [(doc_id, -neg_d) for neg_d, doc_id in heap]
        else:
            candidates: List[Tuple[int, int]] = []
            for entry in self._entries:
                dist = (q_hash ^ entry.simhash).bit_count()
                if dist <= max_hamming_distance:
                    candidates.append((dist, entry.doc_id))
            candidates.sort(key=lambda x: x[0])
            return [(doc_id, d) for d, doc_id in candidates]

    def search_detailed(
        self,
        query: str,
        max_hamming_distance: int = 24,
        top_k: Optional[int] = None,
    ) -> List[SimHashMatch]:
        """
        Executes full Hamming search, returning SimHashMatch descriptors with
        exact distances, normalized similarities, and attached payloads.
        """
        q_hash = simhash64(query)
        if top_k is not None and top_k > 0:
            heap: List[Tuple[int, _IndexEntry]] = []
            it = iter(self._entries)
            max_d = max_hamming_distance

            for entry in it:
                dist = (q_hash ^ entry.simhash).bit_count()
                if dist <= max_d:
                    heap.append((-dist, entry))
                    if len(heap) == top_k:
                        heapq.heapify(heap)
                        max_d = -heap[0][0]
                        break

            if len(heap) == top_k:
                for entry in it:
                    dist = (q_hash ^ entry.simhash).bit_count()
                    if dist < max_d:
                        heapq.heapreplace(heap, (-dist, entry))
                        max_d = -heap[0][0]

            heap.sort(key=lambda item: -item[0])
            return [
                SimHashMatch(
                    doc_id=e.doc_id,
                    distance=-neg_d,
                    similarity=max(0.0, min(1.0, 1.0 - (-neg_d / 64.0))),
                    text=e.text,
                    target_type=e.target_type,
                    payload=e.payload,
                )
                for neg_d, e in heap
            ]
        else:
            candidates: List[Tuple[int, _IndexEntry]] = []
            for entry in self._entries:
                dist = (q_hash ^ entry.simhash).bit_count()
                if dist <= max_hamming_distance:
                    candidates.append((dist, entry))

            candidates.sort(key=lambda x: x[0])
            return [
                SimHashMatch(
                    doc_id=e.doc_id,
                    distance=d,
                    similarity=max(0.0, min(1.0, 1.0 - (d / 64.0))),
                    text=e.text,
                    target_type=e.target_type,
                    payload=e.payload,
                )
                for d, e in candidates
            ]

    # -------------------------------------------------------------------------
    # Graph-Aware Indexing
    # -------------------------------------------------------------------------

    def index_entity(self, entity: Entity) -> int:
        """Indexes an Entity vertex."""
        attr_parts = []
        if entity.attributes:
            for k, v in entity.attributes.items():
                attr_parts.append(f"{k} {v}")
        text = f"{entity.name} {entity.type.name} {' '.join(attr_parts)}"
        return self.add_document(
            doc_id=10_000_000 + entity.id,
            text=text,
            target_type="entity",
            payload=entity,
        )

    def index_hyperedge(self, edge: Hyperedge, graph: MemoryGraph) -> int:
        """Indexes a Hyperedge proposition."""
        subj = graph.get_entity(edge.subject_id)
        subj_name = subj.name if subj else f"entity_{edge.subject_id}"

        obj = graph.get_entity(edge.object_id)
        obj_name = obj.name if obj else f"entity_{edge.object_id}"

        text = f"{subj_name} {edge.predicate} {obj_name} {edge.scope}"
        return self.add_document(
            doc_id=20_000_000 + edge.id,
            text=text,
            target_type="hyperedge",
            payload=edge,
        )

    def index_episodic_node(self, node: EpisodicNode) -> int:
        """Indexes an EpisodicNode summary."""
        text = f"L{node.level} turns_{node.turn_range[0]}_{node.turn_range[1]} {node.summary}"
        return self.add_document(
            doc_id=30_000_000 + node.id,
            text=text,
            target_type="episode",
            payload=node,
        )

    def index_graph(self, graph: MemoryGraph) -> None:
        """
        Indexes all entities, hyperedges, and episodic nodes in a MemoryGraph.
        Also attaches the resulting (target_id, simhash) table to graph.metadata['simhash_index']
        for serialization into Section 5.
        """
        self.clear()

        # 1. Index all entities
        for entity in graph.entities.values():
            self.index_entity(entity)

        # 2. Index all hyperedges
        for edge in graph.hyperedges:
            self.index_hyperedge(edge, graph)

        # 3. Index all episodic nodes
        for node in graph.episodic_nodes:
            self.index_episodic_node(node)

        # 4. Export to graph.metadata['simhash_index'] for binary Section 5 storage
        records: List[Tuple[int, int]] = []
        for entry in self._entries:
            records.append((entry.doc_id, entry.simhash))
        graph.metadata["simhash_index"] = records

    def sync_from_metadata(self, graph: MemoryGraph) -> None:
        """
        Reconstructs index from serialized Section 5 records stored in graph.metadata['simhash_index'].
        """
        self.clear()
        records: List[Tuple[int, int]] = graph.metadata.get("simhash_index", [])
        if not records:
            # Fall back to building from graph if metadata empty
            self.index_graph(graph)
            return

        for tid, sh in records:
            payload = None
            text = ""
            target_type = "doc"

            if tid >= 30_000_000:
                nid = tid - 30_000_000
                for node in graph.episodic_nodes:
                    if node.id == nid:
                        payload = node
                        target_type = "episode"
                        text = node.summary
                        break
            elif tid >= 20_000_000:
                hid = tid - 20_000_000
                for edge in graph.hyperedges:
                    if edge.id == hid:
                        payload = edge
                        target_type = "hyperedge"
                        text = f"{edge.predicate} {edge.scope}"
                        break
            elif tid >= 10_000_000:
                eid = tid - 10_000_000
                if eid in graph.entities:
                    payload = graph.entities[eid]
                    target_type = "entity"
                    text = f"{payload.name} {payload.type.name}"
            else:
                # Direct lookup
                if tid in graph.entities:
                    payload = graph.entities[tid]
                    target_type = "entity"
                    text = f"{payload.name} {payload.type.name}"

            self.add_document(
                doc_id=tid,
                text=text,
                target_type=target_type,
                payload=payload,
                fingerprint=sh,
            )
