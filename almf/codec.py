"""
ALMF Binary Container Codec — High-Performance Serialization & Storage Engine.

Implements Section 5 (Binary File Container Specification: ALMF v1) and
Section 6 (Compression Algorithms & Multi-Stream Encoding) of docs/ALMF_SPECIFICATION.md.
"""

from __future__ import annotations

import io
import json
import os
import struct
import time
import zlib
from typing import Any, BinaryIO, Dict, List, Optional, Tuple, Union

try:
    import brotli
    _HAS_BROTLI = True
except ImportError:  # pragma: no cover
    _HAS_BROTLI = False

from almf.types import (
    ALMFHeader,
    EntityType,
    EpisodicNode,
    HeaderFlags,
    Hyperedge,
    MemoryGraph,
    SectionEntry,
    SectionType,
    VaultPointer,
    HEADER_SIZE,
    SECTION_ENTRY_SIZE,
)
from almf.vault import BlobVault


FLAG_COMPRESSION_RAW = 0
FLAG_COMPRESSION_BROTLI = 1
FLAG_COMPRESSION_DEFLATE = 2


def _compress_block(data: bytes, compression_level: int = 11, prefer_brotli: bool = True) -> Tuple[bytes, int]:
    """
    Compresses a section data block using Brotli or Deflate.
    Returns (compressed_bytes, flag_codec).
    """
    if len(data) == 0:
        return b"", FLAG_COMPRESSION_RAW

    if prefer_brotli and _HAS_BROTLI:
        try:
            # Brotli quality: 0 to 11
            q = max(0, min(11, compression_level))
            compressed = brotli.compress(data, quality=q)
            if len(compressed) < len(data):
                return compressed, FLAG_COMPRESSION_BROTLI
        except Exception:
            pass

    # Fallback to Deflate / zlib level 9
    try:
        compressed = zlib.compress(data, level=9)
        if len(compressed) < len(data):
            return compressed, FLAG_COMPRESSION_DEFLATE
    except Exception:
        pass

    return data, FLAG_COMPRESSION_RAW


def _decompress_block(data: bytes, flag: int, uncompressed_size: int) -> bytes:
    """
    Decompresses a section data block using the indicated codec flag.
    """
    if len(data) == 0 or flag == FLAG_COMPRESSION_RAW:
        return data

    if flag == FLAG_COMPRESSION_BROTLI:
        if not _HAS_BROTLI:
            raise RuntimeError("Cannot decompress Brotli block: brotli package is not installed")
        decompressed = brotli.decompress(data)
    elif flag == FLAG_COMPRESSION_DEFLATE:
        decompressed = zlib.decompress(data)
    else:
        raise ValueError(f"Unknown section compression flag: {flag}")

    if uncompressed_size > 0 and len(decompressed) != uncompressed_size:
        raise ValueError(
            f"Decompressed block size mismatch: expected {uncompressed_size} bytes, got {len(decompressed)} bytes"
        )
    return decompressed


# =============================================================================
# Section Serializers
# =============================================================================

def _serialize_string_pool(pool: List[str]) -> bytes:
    """
    Serializes Section 1: String Intern Pool.
    Format:
      StringCount: uint32
      Lengths: Array of uint32 [StringCount]
      Data: UTF-8 concatenated bytes
    """
    out = io.BytesIO()
    string_count = len(pool)
    out.write(struct.pack("<I", string_count))

    encoded_strings = [s.encode("utf-8") for s in pool]
    for encoded in encoded_strings:
        out.write(struct.pack("<I", len(encoded)))

    for encoded in encoded_strings:
        out.write(encoded)

    return out.getvalue()


def _deserialize_string_pool(data: bytes) -> List[str]:
    """Deserializes Section 1: String Intern Pool."""
    if len(data) < 4:
        return []

    offset = 0
    (string_count,) = struct.unpack_from("<I", data, offset)
    offset += 4

    lengths: List[int] = []
    for _ in range(string_count):
        if offset + 4 > len(data):
            raise ValueError("Truncated string pool length table")
        (l,) = struct.unpack_from("<I", data, offset)
        lengths.append(l)
        offset += 4

    pool: List[str] = []
    for l in lengths:
        if offset + l > len(data):
            raise ValueError("Truncated string pool data block")
        s = data[offset : offset + l].decode("utf-8", errors="replace")
        pool.append(s)
        offset += l

    return pool


def _serialize_entity_registry(graph: MemoryGraph) -> bytes:
    """
    Serializes Section 2: Entity Registry.
    Format:
      EntityCount: uint32
      For each entity:
        EntityID: uint32
        EntityType: uint8
        NameStringID: uint32
        AttributeCount: uint16
        For each attribute:
          KeyStringID: uint32
          ValueStringID: uint32
    """
    out = io.BytesIO()
    entities = list(graph.entities.values())
    out.write(struct.pack("<I", len(entities)))

    for e in entities:
        name_id = graph.intern_string(e.name)
        etype_val = e.type.value if hasattr(e.type, "value") else int(e.type)
        attrs = e.attributes or {}
        out.write(struct.pack("<IBIH", e.id, etype_val, name_id, len(attrs)))

        for k, v in attrs.items():
            k_id = graph.intern_string(str(k))
            val_str = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v)
            v_id = graph.intern_string(val_str)
            out.write(struct.pack("<II", k_id, v_id))

    return out.getvalue()


def _deserialize_entity_registry(data: bytes, string_pool: List[str], graph: MemoryGraph) -> None:
    """Deserializes Section 2: Entity Registry into graph.entities."""
    if len(data) < 4:
        return

    offset = 0
    (entity_count,) = struct.unpack_from("<I", data, offset)
    offset += 4

    for _ in range(entity_count):
        if offset + 11 > len(data):
            raise ValueError("Truncated entity record")
        eid, etype_val, name_id, attr_count = struct.unpack_from("<IBIH", data, offset)
        offset += 11

        name = string_pool[name_id] if 0 <= name_id < len(string_pool) else f"entity_{eid}"
        try:
            etype = EntityType(etype_val)
        except ValueError:
            etype = EntityType.CONCEPT

        attrs: Dict[str, Any] = {}
        for _ in range(attr_count):
            if offset + 8 > len(data):
                raise ValueError("Truncated entity attributes")
            k_id, v_id = struct.unpack_from("<II", data, offset)
            offset += 8
            k = string_pool[k_id] if 0 <= k_id < len(string_pool) else f"key_{k_id}"
            v_raw = string_pool[v_id] if 0 <= v_id < len(string_pool) else ""
            try:
                attrs[k] = json.loads(v_raw)
            except Exception:
                attrs[k] = v_raw

        graph.add_entity(name=name, entity_type=etype, attributes=attrs, entity_id=eid)


def _serialize_proposition_hypergraph(graph: MemoryGraph) -> bytes:
    """
    Serializes Section 3: Proposition Hypergraph in columnar format.
    Format:
      EdgeCount: uint32
      SubjectIDs: array of uint32
      PredicateIDs: array of uint32 (interned)
      ObjectIDs: array of uint32
      ScopeIDs: array of uint32 (interned)
      EpochDeltas: array of int32 (delta-encoded)
      Confidences: array of uint8 (round(c * 255))
      ValidIntervals: array of (uint32, uint32)
    """
    out = io.BytesIO()
    edges = graph.hyperedges
    edge_count = len(edges)
    out.write(struct.pack("<I", edge_count))

    if edge_count == 0:
        return out.getvalue()

    subject_ids: List[int] = []
    predicate_ids: List[int] = []
    object_ids: List[int] = []
    scope_ids: List[int] = []
    epochs: List[int] = []
    confidences: List[int] = []
    valid_intervals: List[Tuple[int, int]] = []

    for edge in edges:
        subject_ids.append(edge.subject_id)
        predicate_ids.append(graph.intern_string(edge.predicate))
        object_ids.append(edge.object_id)
        scope_ids.append(graph.intern_string(edge.scope))
        epochs.append(edge.epoch)
        conf_byte = max(0, min(255, int(round(edge.confidence * 255.0))))
        confidences.append(conf_byte)

        if edge.temporal_validity:
            s_ep, e_ep = edge.temporal_validity
            valid_intervals.append((s_ep, e_ep if e_ep is not None else 0))
        else:
            valid_intervals.append((0, 0))

    # Pack SubjectIDs
    out.write(struct.pack(f"<{edge_count}I", *subject_ids))
    # Pack PredicateIDs
    out.write(struct.pack(f"<{edge_count}I", *predicate_ids))
    # Pack ObjectIDs
    out.write(struct.pack(f"<{edge_count}I", *object_ids))
    # Pack ScopeIDs
    out.write(struct.pack(f"<{edge_count}I", *scope_ids))

    # Pack EpochDeltas (signed delta: e_0, e_1 - e_0, ...)
    deltas = [epochs[0]] + [epochs[i] - epochs[i - 1] for i in range(1, edge_count)]
    out.write(struct.pack(f"<{edge_count}i", *deltas))

    # Pack Confidences (uint8)
    out.write(struct.pack(f"<{edge_count}B", *confidences))

    # Pack ValidIntervals (2 * uint32)
    flat_intervals: List[int] = []
    for s_ep, e_ep in valid_intervals:
        flat_intervals.extend([s_ep, e_ep])
    out.write(struct.pack(f"<{edge_count * 2}I", *flat_intervals))

    return out.getvalue()


def _deserialize_proposition_hypergraph(data: bytes, string_pool: List[str], graph: MemoryGraph) -> None:
    """Deserializes Section 3: Proposition Hypergraph from columnar arrays."""
    if len(data) < 4:
        return

    offset = 0
    (edge_count,) = struct.unpack_from("<I", data, offset)
    offset += 4

    if edge_count == 0:
        return

    uint32_size = edge_count * 4
    int32_size = edge_count * 4
    uint8_size = edge_count * 1
    interval_size = edge_count * 8

    total_expected = 4 + 4 * uint32_size + int32_size + uint8_size + interval_size
    if len(data) < total_expected:
        raise ValueError(f"Corrupted hypergraph columnar block: expected {total_expected} bytes, got {len(data)}")

    subjects = struct.unpack_from(f"<{edge_count}I", data, offset)
    offset += uint32_size

    predicates = struct.unpack_from(f"<{edge_count}I", data, offset)
    offset += uint32_size

    objects = struct.unpack_from(f"<{edge_count}I", data, offset)
    offset += uint32_size

    scopes = struct.unpack_from(f"<{edge_count}I", data, offset)
    offset += uint32_size

    deltas = struct.unpack_from(f"<{edge_count}i", data, offset)
    offset += int32_size

    conf_bytes = struct.unpack_from(f"<{edge_count}B", data, offset)
    offset += uint8_size

    intervals = struct.unpack_from(f"<{edge_count * 2}I", data, offset)
    offset += interval_size

    # Reconstruct epochs from deltas
    epochs: List[int] = []
    current_epoch = 0
    for d in deltas:
        current_epoch += d
        epochs.append(current_epoch)

    for i in range(edge_count):
        sub_id = subjects[i]
        pred_id = predicates[i]
        obj_id = objects[i]
        scope_id = scopes[i]
        epoch = epochs[i]
        conf_raw = conf_bytes[i] / 255.0
        # Preserve clean round decimal if close
        conf = round(conf_raw, 2) if abs(conf_raw - round(conf_raw, 2)) < 0.005 else round(conf_raw, 4)

        pred = string_pool[pred_id] if 0 <= pred_id < len(string_pool) else f"pred_{pred_id}"
        scope = string_pool[scope_id] if 0 <= scope_id < len(string_pool) else "global"

        s_ep = intervals[i * 2]
        e_ep = intervals[i * 2 + 1]
        valid_interval = (s_ep, e_ep if e_ep != 0 else None) if (s_ep != 0 or e_ep != 0) else None

        graph.add_hyperedge(
            subject_id=sub_id,
            predicate=pred,
            object_id=obj_id,
            scope=scope,
            epoch=epoch,
            confidence=conf,
            temporal_validity=valid_interval,
            edge_id=i + 1,
        )


def _serialize_episodic_tree(graph: MemoryGraph) -> bytes:
    """
    Serializes Section 4: Hierarchical Episodic Tree.
    Format:
      NodeCount: uint32
      For each node:
        NodeID: uint32
        Level: uint8
        SummaryStringID: uint32
        TurnStart: uint32
        TurnEnd: uint32
        EntityCount: uint16
        EntityIDs: array of uint32
    """
    out = io.BytesIO()
    nodes = graph.episodic_nodes
    out.write(struct.pack("<I", len(nodes)))

    for n in nodes:
        sum_id = graph.intern_string(n.summary)
        t_start, t_end = n.turn_range if n.turn_range else (0, 0)
        e_ids = n.entity_ids or []
        out.write(struct.pack("<IBIIIH", n.id, n.level, sum_id, t_start, t_end, len(e_ids)))
        if e_ids:
            out.write(struct.pack(f"<{len(e_ids)}I", *e_ids))

    return out.getvalue()


def _deserialize_episodic_tree(data: bytes, string_pool: List[str], graph: MemoryGraph) -> None:
    """Deserializes Section 4: Hierarchical Episodic Tree into graph.episodic_nodes."""
    if len(data) < 4:
        return

    offset = 0
    (node_count,) = struct.unpack_from("<I", data, offset)
    offset += 4

    for _ in range(node_count):
        if offset + 19 > len(data):
            raise ValueError("Truncated episodic node record")
        nid, level, sum_id, t_start, t_end, e_count = struct.unpack_from("<IBIIIH", data, offset)
        offset += 19

        e_ids: List[int] = []
        if e_count > 0:
            if offset + e_count * 4 > len(data):
                raise ValueError("Truncated episodic node entity references")
            e_ids = list(struct.unpack_from(f"<{e_count}I", data, offset))
            offset += e_count * 4

        summary = string_pool[sum_id] if 0 <= sum_id < len(string_pool) else ""
        graph.add_episodic_node(
            level=level,
            summary=summary,
            turn_range=(t_start, t_end),
            entity_ids=e_ids,
            node_id=nid,
        )


def _serialize_semantic_index(graph: MemoryGraph) -> bytes:
    """
    Serializes Section 5: Semantic Search Index (SimHash-64).
    Format:
      RecordCount: uint32
      For each record:
        TargetRecordID: uint32
        SimHash: uint64
    """
    index_records: List[Tuple[int, int]] = graph.metadata.get("simhash_index", [])
    out = io.BytesIO()
    out.write(struct.pack("<I", len(index_records)))
    for tid, sh in index_records:
        out.write(struct.pack("<IQ", tid, sh))
    return out.getvalue()


def _deserialize_semantic_index(data: bytes, graph: MemoryGraph) -> None:
    """Deserializes Section 5: Semantic Search Index."""
    if len(data) < 4:
        return
    offset = 0
    (count,) = struct.unpack_from("<I", data, offset)
    offset += 4

    records: List[Tuple[int, int]] = []
    for _ in range(count):
        if offset + 12 > len(data):
            raise ValueError("Truncated SimHash index record")
        tid, sh = struct.unpack_from("<IQ", data, offset)
        records.append((tid, sh))
        offset += 12

    if records:
        graph.metadata["simhash_index"] = records


def _serialize_blob_vault(vault: Optional[BlobVault]) -> bytes:
    """Serializes Section 6: Cold Archival Storage & Blob Vault."""
    if vault is None or len(vault) == 0:
        return b""
    return vault.pack()


def _deserialize_blob_vault(data: bytes, graph: MemoryGraph) -> Optional[BlobVault]:
    """Deserializes Section 6 into BlobVault and registers pointers on MemoryGraph."""
    if len(data) == 0:
        return None
    vault = BlobVault.from_bytes(data)
    graph.vault = vault
    for ptr in vault.list_pointers():
        graph.add_vault_pointer(ptr)
    return vault


# =============================================================================
# Master Codec: encode & decode
# =============================================================================

def _intern_all_graph_strings(graph: MemoryGraph) -> None:
    """
    Ensures all entity names, attribute keys/values, predicates, scopes,
    and episodic summaries are interned into graph.string_pool before serialization.
    """
    for e in graph.entities.values():
        graph.intern_string(e.name)
        if e.attributes:
            for k, v in e.attributes.items():
                graph.intern_string(str(k))
                val_str = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v)
                graph.intern_string(val_str)
    for h in graph.hyperedges:
        graph.intern_string(h.predicate)
        graph.intern_string(h.scope)
    for n in graph.episodic_nodes:
        graph.intern_string(n.summary)


def encode(
    graph: MemoryGraph,
    compression_level: int = 11,
    use_brotli: bool = True,
    vault: Optional[BlobVault] = None,
) -> bytes:
    """
    Encodes an in-memory MemoryGraph into the high-performance binary `.almf` container.

    Layout:
      Offset 0: 32-Byte Master File Header (ALMFHeader)
      Offset 32: Section Directory (SectionCount: uint16 + 6 * 16B SectionEntry)
      Payload: Blocks 1 through 6
    """
    # Use graph's attached vault if no explicit vault provided
    effective_vault = vault if vault is not None else getattr(graph, "vault", None)

    # Pre-pass: intern all strings so Section 1 contains complete symbol dictionary
    _intern_all_graph_strings(graph)

    # 1. Serialize Section 1: String Pool
    raw_s1 = _serialize_string_pool(graph.string_pool)
    c_s1, f_s1 = _compress_block(raw_s1, compression_level, use_brotli)

    # 2. Serialize Section 2: Entity Registry
    raw_s2 = _serialize_entity_registry(graph)
    c_s2, f_s2 = _compress_block(raw_s2, compression_level, use_brotli)

    # 3. Serialize Section 3: Proposition Hypergraph
    raw_s3 = _serialize_proposition_hypergraph(graph)
    c_s3, f_s3 = _compress_block(raw_s3, compression_level, use_brotli)

    # 4. Serialize Section 4: Hierarchical Episodic Tree
    raw_s4 = _serialize_episodic_tree(graph)
    c_s4, f_s4 = _compress_block(raw_s4, compression_level, use_brotli)

    # 5. Serialize Section 5: Semantic Search Index
    raw_s5 = _serialize_semantic_index(graph)
    c_s5, f_s5 = raw_s5, FLAG_COMPRESSION_RAW

    # 6. Serialize Section 6: Cold Archival Storage & Blob Vault
    raw_s6 = _serialize_blob_vault(effective_vault)
    # Section 6 already applies internal chunk compression via BlobVault.pack()
    c_s6, f_s6 = raw_s6, FLAG_COMPRESSION_RAW

    # Assemble Section Directory & Functional Blocks
    section_count = 6
    section_dir_size = 2 + section_count * SECTION_ENTRY_SIZE
    data_start_offset = HEADER_SIZE + section_dir_size

    blocks = [
        (SectionType.STRING_POOL, f_s1, c_s1, len(raw_s1)),
        (SectionType.ENTITY_REGISTRY, f_s2, c_s2, len(raw_s2)),
        (SectionType.PROPOSITION_HYPERGRAPH, f_s3, c_s3, len(raw_s3)),
        (SectionType.EPISODIC_TREE, f_s4, c_s4, len(raw_s4)),
        (SectionType.SIMHASH_INDEX, f_s5, c_s5, len(raw_s5)),
        (SectionType.COLD_ARCHIVE, f_s6, c_s6, len(raw_s6)),
    ]

    entries: List[SectionEntry] = []
    payload_buffer = bytearray()
    current_offset = data_start_offset

    for stype, flag, compressed_bytes, orig_len in blocks:
        entry = SectionEntry(
            section_type=stype,
            flags=flag,
            offset=current_offset,
            length=len(compressed_bytes),
            uncompressed_length=orig_len if flag != FLAG_COMPRESSION_RAW else 0,
        )
        entries.append(entry)
        payload_buffer.extend(compressed_bytes)
        current_offset += len(compressed_bytes)

    # Build Section Directory Binary
    section_dir_bytes = bytearray()
    section_dir_bytes.extend(struct.pack("<H", section_count))
    for entry in entries:
        section_dir_bytes.extend(entry.pack())

    # Build container payload after offset 32: [Section Directory] + [Data Blocks]
    body_after_header = bytes(section_dir_bytes) + bytes(payload_buffer)

    # Compute CRC32 of everything after the 32-byte header
    crc32_val = zlib.crc32(body_after_header) & 0xFFFFFFFF

    # Determine Header Flags
    header_flags = HeaderFlags.LITTLE_ENDIAN
    if any(f == FLAG_COMPRESSION_BROTLI for _, f, _, _ in blocks):
        header_flags |= HeaderFlags.COMPRESSION_BROTLI
    if any(f == FLAG_COMPRESSION_DEFLATE for _, f, _, _ in blocks):
        header_flags |= HeaderFlags.COMPRESSION_DEFLATE
    if len(c_s5) > 4:
        header_flags |= HeaderFlags.SIMHASH_INDEX_PRESENT
    if len(c_s6) > 0:
        header_flags |= HeaderFlags.VAULT_PRESENT

    now_ms = int(time.time() * 1000)
    created_at = graph.metadata.get("created_at_ms", now_ms)

    header = ALMFHeader(
        magic=b"ALMF",
        version=1,
        flags=int(header_flags),
        crc32=crc32_val,
        created_at=created_at,
        updated_at=now_ms,
        section_table_offset=HEADER_SIZE,
    )

    header_bytes = header.pack()
    return header_bytes + body_after_header


def decode(data: bytes) -> MemoryGraph:
    """
    Decodes an `.almf` binary container into an in-memory MemoryGraph.
    Verifies magic bytes, CRC32 integrity, and unpacks all 6 functional sections.
    """
    if len(data) < HEADER_SIZE:
        raise ValueError(f"Insufficient data length for ALMF container: {len(data)} (min 32 bytes)")

    # 1. Unpack Master File Header
    header = ALMFHeader.unpack(data[:HEADER_SIZE])
    if header.magic != b"ALMF":
        raise ValueError(f"Invalid ALMF magic identifier: {header.magic!r}")

    # 2. Verify CRC32 checksum over entire container following header
    computed_crc = zlib.crc32(data[HEADER_SIZE:]) & 0xFFFFFFFF
    if computed_crc != header.crc32:
        raise ValueError(
            f"CRC32 integrity failure: expected {header.crc32:#010x}, calculated {computed_crc:#010x}"
        )

    # 3. Read Section Directory
    dir_offset = header.section_table_offset
    if dir_offset + 2 > len(data):
        raise ValueError("Truncated section directory header")

    (section_count,) = struct.unpack_from("<H", data, dir_offset)
    dir_offset += 2

    entries: Dict[SectionType, SectionEntry] = {}
    for _ in range(section_count):
        if dir_offset + SECTION_ENTRY_SIZE > len(data):
            raise ValueError("Truncated section directory entry")
        entry = SectionEntry.unpack(data[dir_offset : dir_offset + SECTION_ENTRY_SIZE])
        entries[entry.section_type] = entry
        dir_offset += SECTION_ENTRY_SIZE

    # Create empty graph
    graph = MemoryGraph(
        metadata={
            "created_at_ms": header.created_at,
            "updated_at_ms": header.updated_at,
            "version": header.version,
            "flags": header.flags,
        }
    )

    # 4. Decompress and Deserialize Section 1: String Pool
    s1_entry = entries.get(SectionType.STRING_POOL)
    if s1_entry and s1_entry.length > 0:
        raw_s1_bytes = data[s1_entry.offset : s1_entry.offset + s1_entry.length]
        s1_decomp = _decompress_block(raw_s1_bytes, s1_entry.flags, s1_entry.uncompressed_length)
        graph.string_pool = _deserialize_string_pool(s1_decomp)
        graph._rebuild_caches()

    # 5. Decompress and Deserialize Section 2: Entity Registry
    s2_entry = entries.get(SectionType.ENTITY_REGISTRY)
    if s2_entry and s2_entry.length > 0:
        raw_s2_bytes = data[s2_entry.offset : s2_entry.offset + s2_entry.length]
        s2_decomp = _decompress_block(raw_s2_bytes, s2_entry.flags, s2_entry.uncompressed_length)
        _deserialize_entity_registry(s2_decomp, graph.string_pool, graph)

    # 6. Decompress and Deserialize Section 3: Proposition Hypergraph
    s3_entry = entries.get(SectionType.PROPOSITION_HYPERGRAPH)
    if s3_entry and s3_entry.length > 0:
        raw_s3_bytes = data[s3_entry.offset : s3_entry.offset + s3_entry.length]
        s3_decomp = _decompress_block(raw_s3_bytes, s3_entry.flags, s3_entry.uncompressed_length)
        _deserialize_proposition_hypergraph(s3_decomp, graph.string_pool, graph)

    # 7. Decompress and Deserialize Section 4: Episodic Tree
    s4_entry = entries.get(SectionType.EPISODIC_TREE)
    if s4_entry and s4_entry.length > 0:
        raw_s4_bytes = data[s4_entry.offset : s4_entry.offset + s4_entry.length]
        s4_decomp = _decompress_block(raw_s4_bytes, s4_entry.flags, s4_entry.uncompressed_length)
        _deserialize_episodic_tree(s4_decomp, graph.string_pool, graph)

    # 8. Decompress and Deserialize Section 5: Semantic Search Index
    s5_entry = entries.get(SectionType.SIMHASH_INDEX)
    if s5_entry and s5_entry.length > 0:
        raw_s5_bytes = data[s5_entry.offset : s5_entry.offset + s5_entry.length]
        s5_decomp = _decompress_block(raw_s5_bytes, s5_entry.flags, s5_entry.uncompressed_length)
        _deserialize_semantic_index(s5_decomp, graph)

    # 9. Decompress and Deserialize Section 6: Blob Vault
    s6_entry = entries.get(SectionType.COLD_ARCHIVE)
    if s6_entry and s6_entry.length > 0:
        raw_s6_bytes = data[s6_entry.offset : s6_entry.offset + s6_entry.length]
        _deserialize_blob_vault(raw_s6_bytes, graph)

    graph._rebuild_caches()
    return graph


def save_to_file(
    graph: MemoryGraph,
    filepath: str,
    compression_level: int = 11,
    use_brotli: bool = True,
    vault: Optional[BlobVault] = None,
) -> int:
    """
    Encodes and writes a MemoryGraph to an `.almf` file on disk.
    Returns the total number of bytes written.
    """
    encoded_bytes = encode(
        graph=graph,
        compression_level=compression_level,
        use_brotli=use_brotli,
        vault=vault,
    )
    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
    with open(filepath, "wb") as f:
        f.write(encoded_bytes)
    return len(encoded_bytes)


def load_from_file(filepath: str) -> MemoryGraph:
    """
    Reads an `.almf` file from disk and decodes it into a MemoryGraph.
    """
    with open(filepath, "rb") as f:
        data = f.read()
    return decode(data)
