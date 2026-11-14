"""
almf.types: Core Data Model and Type Definitions for the Agent Long-Term Memory Format (ALMF v1).

This module defines all foundational primitives for Entity-Relation-Attribute Hypergraphs (ERA-H),
multimodal compression regimes, binary container headers, section descriptors, and episodic nodes.
"""

from __future__ import annotations

import struct
import time
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union


# ============================================================================
# 1. Enums: Entity Types, Sections, Modalities, and Header Flags
# ============================================================================

class EntityType(IntEnum):
    """
    Typed domain classification for vertices in the ERA-H knowledge hypergraph.
    Each entity is represented by an 8-bit unsigned integer (uint8) in binary form.
    """
    ACTOR = 1           # Human user, agent identity, external caller, daemon
    ARTIFACT = 2        # Code file, document, binary, URL, git branch, diff
    CONFIGURATION = 3   # Parameter, flag, port, timeout, environment variable
    CONSTRAINT = 4      # Security boundary, architectural invariant, resource cap
    DECISION = 5        # Architecture choice, consensus item, approved ADR
    ERROR = 6           # Exception, crash signature, OOM, failed precondition
    CONCEPT = 7         # Protocol, abstract topic, framework, data structure
    METRIC = 8          # Benchmark measurement, latency SLA, compression ratio

    @classmethod
    def from_str(cls, name: str) -> "EntityType":
        """Convert case-insensitive string to EntityType."""
        try:
            return cls[name.upper().strip()]
        except KeyError:
            # Fallback to CONCEPT if unknown
            return cls.CONCEPT


class SectionType(IntEnum):
    """
    Distinct functional binary blocks in an ALMF v1 (.almf) container.
    """
    STRING_POOL = 1            # Front-coded / Brotli-compressed string intern dictionary
    ENTITY_REGISTRY = 2        # Array of typed entity vertices and attribute pointers
    PROPOSITION_HYPERGRAPH = 3 # Columnar bit-packed propositions and temporal edges
    EPISODIC_TREE = 4          # Multi-resolution episodic nodes (L0/L1/L2 summaries)
    SIMHASH_INDEX = 5          # 64-bit SimHash bitwise index and optional PQ-16 table
    COLD_ARCHIVE = 6           # Compressed blob vault, raw diffs, tool traces


class CompressionModality(str, Enum):
    """
    Adaptive Smart Encoder Router modalities for information-value-based compression.
    """
    TRACE_INVARIANT = "TRACE_INVARIANT" # 1:1000 - 1:5000: Procedural logs/traces collapsed to Cause->Outcome
    ARCH_DSL = "ARCH_DSL"               # 1:100 - 1:300: High-level architectural rules and decisions
    AST_SKELETON = "AST_SKELETON"       # 1:20 - 1:50: Source code interfaces/signatures without bodies
    LOSSLESS_VAULT = "LOSSLESS_VAULT"   # 1-token pointer substitution for exact byte vault storage
    MACRO_INVARIANT = "MACRO_INVARIANT" # High-volume logs/traces synthesized into global invariants
    CLUSTER_TEMPLATE = "CLUSTER_TEMPLATE" # Repetitive parameterized events grouped by template signature


class HeaderFlags(IntEnum):
    """Bitmask flags stored in the 32-byte ALMF master file header."""
    COMPRESSION_BROTLI = 0x0001
    COMPRESSION_DEFLATE = 0x0002
    SIMHASH_INDEX_PRESENT = 0x0004
    VAULT_PRESENT = 0x0008
    LITTLE_ENDIAN = 0x0010


# ============================================================================
# 2. Vault Pointer: Exact Byte Blob Reference
# ============================================================================

@dataclass
class VaultPointer:
    """
    Represents a 1-token reference pointer (`@ptr(hash)`) pointing to exact data
    stored in the Cold Archival Storage / Blob Vault (Section 6).
    """
    hash: str                       # Truncated hex hash (e.g. 8 chars like 'a3f9c01b')
    offset: int = 0                 # Byte offset within Section 6
    length: int = 0                 # Compressed byte length in storage
    codec: str = "brotli"           # Codec used ('brotli', 'deflate', 'raw')
    original_size: int = 0          # Uncompressed byte size
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def pointer_symbol(self) -> str:
        """Returns the canonical 1-token prompt representation."""
        return f"@ptr({self.hash})"

    @property
    def content_hash(self) -> bytes:
        """Returns binary bytes of the content hash."""
        hex_str = self.metadata.get("sha256", self.hash)
        if len(hex_str) % 2 != 0:
            hex_str += "0"
        try:
            return bytes.fromhex(hex_str)
        except ValueError:
            return self.hash.encode("utf-8")

    def is_valid(self) -> bool:
        """Validates that this pointer has a valid non-empty hash."""
        return bool(self.hash)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "hash": self.hash,
            "offset": self.offset,
            "length": self.length,
            "codec": self.codec,
            "original_size": self.original_size,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "VaultPointer":
        return cls(
            hash=data["hash"],
            offset=data.get("offset", 0),
            length=data.get("length", 0),
            codec=data.get("codec", "brotli"),
            original_size=data.get("original_size", 0),
            metadata=data.get("metadata", {}),
        )


# ============================================================================
# 3. Core ERA-H Data Structures: Entity, Hyperedge, EpisodicNode, Fact
# ============================================================================

@dataclass
class Entity:
    """
    A typed vertex in the ERA-H knowledge hypergraph.
    """
    id: int
    type: EntityType
    name: str
    attributes: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value if isinstance(self.type, EntityType) else int(self.type),
            "name": self.name,
            "attributes": self.attributes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Entity":
        t = data["type"]
        entity_type = EntityType(t) if isinstance(t, int) else EntityType.from_str(str(t))
        return cls(
            id=data["id"],
            type=entity_type,
            name=data["name"],
            attributes=data.get("attributes", {}),
        )


@dataclass
class Hyperedge:
    """
    A directed proposition hyperedge modeling an atomic relational fact with provenance.
    """
    id: int
    subject_id: int = 0
    predicate: str = ""
    object_id: int = 0
    scope: str = "global"
    epoch: int = 0
    confidence: float = 1.0
    temporal_validity: Optional[Tuple[int, Optional[int]]] = None # (start_epoch, end_epoch)

    def __init__(
        self,
        id: int,
        subject_id: Optional[int] = None,
        predicate: str = "",
        object_id: Optional[int] = None,
        scope: str = "global",
        epoch: int = 0,
        confidence: float = 1.0,
        temporal_validity: Optional[Tuple[int, Optional[int]]] = None,
        *,
        subject: Optional[int] = None,
        object: Optional[int] = None,
    ) -> None:
        self.id = id
        self.subject_id = subject_id if subject_id is not None else (subject if subject is not None else 0)
        self.predicate = predicate
        self.object_id = object_id if object_id is not None else (object if object is not None else 0)
        self.scope = scope
        self.epoch = epoch
        self.confidence = confidence
        self.temporal_validity = temporal_validity

    @property
    def subject(self) -> int:
        return self.subject_id

    @property
    def object(self) -> int:
        return self.object_id

    def is_active_at(self, current_epoch: int) -> bool:
        """Determines if the proposition is valid at a given epoch."""
        if self.temporal_validity is None:
            return True
        start_epoch, end_epoch = self.temporal_validity
        if current_epoch < start_epoch:
            return False
        # end_epoch = 0 or None indicates open-ended indefinite validity
        if end_epoch is not None and end_epoch != 0 and current_epoch >= end_epoch:
            return False
        return True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "subject_id": self.subject_id,
            "predicate": self.predicate,
            "object_id": self.object_id,
            "scope": self.scope,
            "epoch": self.epoch,
            "confidence": self.confidence,
            "temporal_validity": list(self.temporal_validity) if self.temporal_validity else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Hyperedge":
        tv = data.get("temporal_validity")
        temporal_tuple = tuple(tv) if tv is not None else None
        subj = data.get("subject_id")
        if subj is None:
            subj = data.get("subject", 0)
        obj = data.get("object_id")
        if obj is None:
            obj = data.get("object", 0)
        return cls(
            id=data["id"],
            subject_id=subj,
            predicate=data.get("predicate", ""),
            object_id=obj,
            scope=data.get("scope", "global"),
            epoch=data.get("epoch", 0),
            confidence=float(data.get("confidence", 1.0)),
            temporal_validity=temporal_tuple,
        )


@dataclass
class EpisodicNode:
    """
    A node in the Hierarchical Episodic Tree (HET).
    Level 0: Active working invariant (prompt scratchpad)
    Level 1: Meso-episodic chapter summaries
    Level 2: Micro-proposition turn groups
    """
    id: int
    level: int
    summary: str
    turn_range: Tuple[int, int] = (0, 0)
    entity_ids: List[int] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "level": self.level,
            "summary": self.summary,
            "turn_range": list(self.turn_range),
            "entity_ids": self.entity_ids,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EpisodicNode":
        tr = data.get("turn_range", [0, 0])
        return cls(
            id=data["id"],
            level=data["level"],
            summary=data["summary"],
            turn_range=(tr[0], tr[1]),
            entity_ids=data.get("entity_ids", []),
        )


@dataclass
class MacroProposition:
    """
    A Level 0 / Level 1 macro-proposition representing a synthesized cluster invariant.
    Captures global system invariants or canonical cluster templates.
    """
    id: int
    statement: str
    cluster_type: str  # 'trace_template', 'semantic_dialogue', 'entity_community'
    member_count: int
    member_ids: List[int] = field(default_factory=list)
    epoch_range: Tuple[int, int] = (0, 0)
    confidence: float = 1.0
    attributes: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "statement": self.statement,
            "cluster_type": self.cluster_type,
            "member_count": self.member_count,
            "member_ids": list(self.member_ids),
            "epoch_range": list(self.epoch_range),
            "confidence": self.confidence,
            "attributes": self.attributes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MacroProposition":
        er = data.get("epoch_range", [0, 0])
        return cls(
            id=data["id"],
            statement=data["statement"],
            cluster_type=data.get("cluster_type", "general"),
            member_count=data.get("member_count", len(data.get("member_ids", []))),
            member_ids=list(data.get("member_ids", [])),
            epoch_range=(er[0], er[1]),
            confidence=float(data.get("confidence", 1.0)),
            attributes=data.get("attributes", {}),
        )


@dataclass
class ClusterRecord:
    """
    Detailed clustering metadata record containing template signature,
    isolated parameter table, and member IDs.
    """
    cluster_id: str
    cluster_type: str  # 'trace_template', 'semantic_dialogue', 'entity_community'
    signature: str
    member_ids: List[int] = field(default_factory=list)
    parameter_table: List[Dict[str, Any]] = field(default_factory=list)
    epoch_range: Tuple[int, int] = (0, 0)
    confidence: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def template(self) -> str:
        """Alias for signature to support Drain miner conventions."""
        return self.signature

    @property
    def count(self) -> int:
        """Alias for len(member_ids)."""
        return len(self.member_ids)

    @property
    def parameters(self) -> List[Dict[str, Any]]:
        """Alias for parameter_table."""
        return self.parameter_table

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "cluster_type": self.cluster_type,
            "signature": self.signature,
            "member_ids": list(self.member_ids),
            "parameter_table": self.parameter_table,
            "epoch_range": list(self.epoch_range),
            "confidence": self.confidence,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ClusterRecord":
        er = data.get("epoch_range", [0, 0])
        return cls(
            cluster_id=data["cluster_id"],
            cluster_type=data.get("cluster_type", "general"),
            signature=data.get("signature", ""),
            member_ids=list(data.get("member_ids", [])),
            parameter_table=list(data.get("parameter_table", [])),
            epoch_range=(er[0], er[1]),
            confidence=float(data.get("confidence", 1.0)),
            metadata=data.get("metadata", {}),
        )


@dataclass
class Fact:
    """
    A retrieved canonical factual proposition rendered for query answering or context injection.
    """
    id: int = 0
    statement: str = ""
    subject: str = ""
    predicate: str = ""
    object_val: str = ""
    confidence: float = 1.0
    provenance: str = ""
    epoch: int = 0
    scope: str = "global"
    modality: CompressionModality = CompressionModality.ARCH_DSL
    attributes: Dict[str, Any] = field(default_factory=dict)

    def __init__(
        self,
        id: int = 0,
        statement: str = "",
        subject: str = "",
        predicate: str = "",
        object_val: Optional[str] = None,
        confidence: float = 1.0,
        provenance: str = "",
        epoch: int = 0,
        scope: str = "global",
        modality: Union[CompressionModality, str] = CompressionModality.ARCH_DSL,
        attributes: Optional[Dict[str, Any]] = None,
        *,
        object: Optional[str] = None,
    ) -> None:
        self.id = id
        self.subject = subject
        self.predicate = predicate
        resolved_obj = object_val if object_val is not None else (object if object is not None else "")
        self.object_val = resolved_obj
        if not statement:
            if subject and predicate and resolved_obj:
                self.statement = f"@{subject}: {predicate}(#{resolved_obj})"
            elif subject and predicate:
                self.statement = f"@{subject}: {predicate}"
            elif subject:
                self.statement = f"@{subject}"
            else:
                self.statement = ""
        else:
            self.statement = statement
        self.confidence = float(confidence)
        self.provenance = provenance
        self.epoch = epoch
        self.scope = scope
        if isinstance(modality, str):
            try:
                self.modality = CompressionModality(modality)
            except ValueError:
                self.modality = CompressionModality.ARCH_DSL
        else:
            self.modality = modality
        self.attributes = attributes or {}

    @property
    def object(self) -> str:
        return self.object_val

    @object.setter
    def object(self, val: str) -> None:
        self.object_val = val

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "statement": self.statement,
            "subject": self.subject,
            "predicate": self.predicate,
            "object_val": self.object_val,
            "object": self.object_val,
            "confidence": self.confidence,
            "provenance": self.provenance,
            "epoch": self.epoch,
            "scope": self.scope,
            "modality": self.modality.value if isinstance(self.modality, CompressionModality) else str(self.modality),
            "attributes": self.attributes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Fact":
        mod = data.get("modality", CompressionModality.ARCH_DSL.value)
        try:
            modality = CompressionModality(mod)
        except ValueError:
            modality = CompressionModality.ARCH_DSL
        obj = data.get("object_val")
        if obj is None:
            obj = data.get("object", "")
        return cls(
            id=data.get("id", 0),
            statement=data.get("statement", ""),
            subject=data.get("subject", ""),
            predicate=data.get("predicate", ""),
            object_val=obj,
            confidence=float(data.get("confidence", 1.0)),
            provenance=data.get("provenance", ""),
            epoch=data.get("epoch", 0),
            scope=data.get("scope", "global"),
            modality=modality,
            attributes=data.get("attributes", {}),
        )


# ============================================================================
# 4. Binary Container Structures: ALMFHeader & SectionEntry
# ============================================================================

HEADER_FORMAT = "<4sHHIQQI"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT) # 32 Bytes

SECTION_ENTRY_FORMAT = "<HHIII"
SECTION_ENTRY_SIZE = struct.calcsize(SECTION_ENTRY_FORMAT) # 16 Bytes


@dataclass
class SectionEntry:
    """
    A 16-byte record describing the location, size, and codec of a functional block.
    """
    section_type: SectionType
    flags: int = 0
    offset: int = 0
    length: int = 0
    uncompressed_length: int = 0

    def pack(self) -> bytes:
        """Serializes the section directory record to 16 binary bytes."""
        st = self.section_type.value if isinstance(self.section_type, SectionType) else int(self.section_type)
        return struct.pack(
            SECTION_ENTRY_FORMAT,
            st,
            self.flags,
            self.offset,
            self.length,
            self.uncompressed_length,
        )

    @classmethod
    def unpack(cls, data: bytes) -> "SectionEntry":
        """Deserializes a 16-byte binary record into a SectionEntry."""
        if len(data) < SECTION_ENTRY_SIZE:
            raise ValueError(f"Data length {len(data)} is insufficient for SectionEntry ({SECTION_ENTRY_SIZE} bytes)")
        st_val, flags, offset, length, uncompressed_len = struct.unpack(
            SECTION_ENTRY_FORMAT, data[:SECTION_ENTRY_SIZE]
        )
        return cls(
            section_type=SectionType(st_val),
            flags=flags,
            offset=offset,
            length=length,
            uncompressed_length=uncompressed_len,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "section_type": self.section_type.value if isinstance(self.section_type, SectionType) else int(self.section_type),
            "flags": self.flags,
            "offset": self.offset,
            "length": self.length,
            "uncompressed_length": self.uncompressed_length,
        }


@dataclass
class ALMFHeader:
    """
    32-Byte Master File Header for .almf binary containers.
    Byte Layout:
      0x00..0x03: Magic 'ALMF' (4B)
      0x04..0x05: Version uint16 (2B)
      0x06..0x07: Flags uint16 (2B)
      0x08..0x0B: CRC32 uint32 (4B)
      0x0C..0x13: CreatedAt uint64 (8B)
      0x14..0x1B: UpdatedAt uint64 (8B)
      0x1C..0x1F: SectionTableOffset uint32 (4B)
    """
    magic: bytes = b"ALMF"
    version: int = 1
    flags: int = HeaderFlags.LITTLE_ENDIAN | HeaderFlags.COMPRESSION_BROTLI | HeaderFlags.SIMHASH_INDEX_PRESENT | HeaderFlags.VAULT_PRESENT
    crc32: int = 0
    created_at: int = field(default_factory=lambda: int(time.time() * 1000))
    updated_at: int = field(default_factory=lambda: int(time.time() * 1000))
    section_table_offset: int = HEADER_SIZE

    def pack(self) -> bytes:
        """Serializes the header to exactly 32 binary bytes."""
        if len(self.magic) != 4:
            raise ValueError(f"Magic must be exactly 4 bytes, got {self.magic!r}")
        return struct.pack(
            HEADER_FORMAT,
            self.magic,
            self.version,
            self.flags,
            self.crc32,
            self.created_at,
            self.updated_at,
            self.section_table_offset,
        )

    @classmethod
    def unpack(cls, data: bytes) -> "ALMFHeader":
        """Deserializes 32 binary bytes into an ALMFHeader."""
        if len(data) < HEADER_SIZE:
            raise ValueError(f"Data length {len(data)} is insufficient for ALMFHeader ({HEADER_SIZE} bytes)")
        magic, version, flags, crc32, created_at, updated_at, section_offset = struct.unpack(
            HEADER_FORMAT, data[:HEADER_SIZE]
        )
        if magic != b"ALMF":
            raise ValueError(f"Invalid ALMF magic: expected b'ALMF', got {magic!r}")
        return cls(
            magic=magic,
            version=version,
            flags=flags,
            crc32=crc32,
            created_at=created_at,
            updated_at=updated_at,
            section_table_offset=section_offset,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "magic": self.magic.decode("ascii", errors="replace"),
            "version": self.version,
            "flags": self.flags,
            "crc32": self.crc32,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "section_table_offset": self.section_table_offset,
        }


class _VaultPointerDict(dict):
    """Dict for vault pointers supporting both hash lookup and integer index."""
    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, int):
            values = list(self.values())
            if 0 <= key < len(values):
                return values[key]
            raise IndexError(f"VaultPointer index out of range: {key}")
        return super().__getitem__(key)


# ============================================================================
# 5. Full MemoryGraph Container
# ============================================================================

@dataclass
class MemoryGraph:
    """
    In-memory representation of an ALMF knowledge graph, containing entities,
    hyperedges, episodic nodes, string pool dictionary, vault pointers, and metadata.
    """
    entities: Dict[int, Entity] = field(default_factory=dict)
    hyperedges: List[Hyperedge] = field(default_factory=list)
    episodic_nodes: List[EpisodicNode] = field(default_factory=list)
    string_pool: List[str] = field(default_factory=list)
    vault_pointers: Dict[str, VaultPointer] = field(default_factory=_VaultPointerDict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    macro_propositions: List[MacroProposition] = field(default_factory=list)
    clusters: Dict[str, List[int]] = field(default_factory=dict)
    cluster_records: Dict[str, ClusterRecord] = field(default_factory=dict)

    # Internal lookup indices
    _string_to_id: Dict[str, int] = field(default_factory=dict, repr=False)
    _name_to_entity_id: Dict[str, int] = field(default_factory=dict, repr=False)
    _vault: Optional[Any] = field(default=None, repr=False)

    @property
    def vault(self) -> Optional[Any]:
        """Optional attached BlobVault instance."""
        return self._vault

    @vault.setter
    def vault(self, value: Optional[Any]) -> None:
        self._vault = value

    def __post_init__(self) -> None:
        """Rebuild internal lookup caches."""
        self._rebuild_caches()

    def _rebuild_caches(self) -> None:
        """Reconstruct lookup caches for strings and entities."""
        self._string_to_id = {s: i for i, s in enumerate(self.string_pool)}
        self._name_to_entity_id = {e.name: e.id for e in self.entities.values()}

    def intern_string(self, text: str) -> int:
        """
        Deduplicates a string into the string pool, returning its unique integer ID.
        """
        if text in self._string_to_id:
            return self._string_to_id[text]
        new_id = len(self.string_pool)
        self.string_pool.append(text)
        self._string_to_id[text] = new_id
        return new_id

    def get_interned_string(self, string_id: int) -> Optional[str]:
        """Resolves an integer string pool ID to its string value."""
        if 0 <= string_id < len(self.string_pool):
            return self.string_pool[string_id]
        return None

    def add_entity(
        self,
        name: str,
        entity_type: Union[EntityType, str],
        attributes: Optional[Dict[str, Any]] = None,
        entity_id: Optional[int] = None,
    ) -> Entity:
        """
        Adds or updates an entity in the graph.
        """
        if isinstance(entity_type, str):
            entity_type = EntityType.from_str(entity_type)

        if entity_id is None:
            if name in self._name_to_entity_id:
                entity_id = self._name_to_entity_id[name]
            else:
                entity_id = max(self.entities.keys(), default=0) + 1 if self.entities else 1

        existing = self.entities.get(entity_id)
        if existing and existing.name != name:
            self._name_to_entity_id.pop(existing.name, None)

        merged_attrs = {}
        if existing and existing.attributes:
            merged_attrs.update(existing.attributes)
        if attributes:
            merged_attrs.update(attributes)

        entity = Entity(
            id=entity_id,
            type=entity_type,
            name=name,
            attributes=merged_attrs,
        )
        self.entities[entity_id] = entity
        self._name_to_entity_id[name] = entity_id
        self.intern_string(name)
        return entity

    def get_entity(self, entity_id: int) -> Optional[Entity]:
        """Looks up an entity by its integer ID."""
        return self.entities.get(entity_id)

    def find_entity_by_name(self, name: str) -> Optional[Entity]:
        """Looks up an entity by its unique name."""
        entity_id = self._name_to_entity_id.get(name)
        if entity_id is not None:
            entity = self.entities.get(entity_id)
            if entity is not None and entity.name == name:
                return entity
        # Fallback linear search (search reverse to get latest)
        for e in reversed(list(self.entities.values())):
            if e.name == name:
                self._name_to_entity_id[name] = e.id
                return e
        return None

    def add_hyperedge(
        self,
        subject_id: int,
        predicate: str,
        object_id: int,
        scope: str = "global",
        epoch: int = 0,
        confidence: float = 1.0,
        temporal_validity: Optional[Tuple[int, Optional[int]]] = None,
        edge_id: Optional[int] = None,
    ) -> Hyperedge:
        """
        Adds a relational proposition hyperedge to the graph.
        """
        if edge_id is None:
            edge_id = len(self.hyperedges) + 1

        edge = Hyperedge(
            id=edge_id,
            subject_id=subject_id,
            predicate=predicate,
            object_id=object_id,
            scope=scope,
            epoch=epoch,
            confidence=confidence,
            temporal_validity=temporal_validity,
        )
        self.hyperedges.append(edge)
        self.intern_string(predicate)
        self.intern_string(scope)
        return edge

    def add_episodic_node(
        self,
        level: int,
        summary: str,
        turn_range: Tuple[int, int] = (0, 0),
        entity_ids: Optional[List[int]] = None,
        node_id: Optional[int] = None,
    ) -> EpisodicNode:
        """
        Adds an episodic summary node (L0, L1, or L2).
        """
        if node_id is None:
            node_id = len(self.episodic_nodes) + 1

        node = EpisodicNode(
            id=node_id,
            level=level,
            summary=summary,
            turn_range=turn_range,
            entity_ids=entity_ids or [],
        )
        self.episodic_nodes.append(node)
        return node

    def add_macro_proposition(self, mp: MacroProposition) -> None:
        """Registers a synthesized Level 0 or Level 1 macro-proposition."""
        self.macro_propositions.append(mp)
        self.intern_string(mp.statement)
        self.intern_string(mp.cluster_type)

    def get_macro_propositions(self) -> List[MacroProposition]:
        """Returns all registered macro-propositions."""
        return list(self.macro_propositions)

    def add_cluster(
        self,
        cluster_id: str,
        member_ids: Sequence[int],
        record: Optional[ClusterRecord] = None,
        cluster_record: Optional[ClusterRecord] = None,
    ) -> None:
        """Registers cluster member IDs and optional full metadata record."""
        self.clusters[cluster_id] = list(member_ids)
        rec = record if record is not None else cluster_record
        if rec is not None:
            self.cluster_records[cluster_id] = rec

    def add_vault_pointer(self, ptr: VaultPointer) -> None:
        """Registers a 1-token vault reference pointer."""
        self.vault_pointers[ptr.hash] = ptr

    def get_vault_pointer(self, hash_str: str) -> Optional[VaultPointer]:
        """Resolves a vault pointer by hash."""
        return self.vault_pointers.get(hash_str)

    def to_dict(self) -> Dict[str, Any]:
        """Serializes the complete MemoryGraph to a JSON-compatible dictionary."""
        return {
            "version": 1,
            "metadata": self.metadata,
            "string_pool": self.string_pool,
            "entities": [e.to_dict() for e in self.entities.values()],
            "hyperedges": [h.to_dict() for h in self.hyperedges],
            "episodic_nodes": [n.to_dict() for n in self.episodic_nodes],
            "vault_pointers": {k: v.to_dict() for k, v in self.vault_pointers.items()},
            "macro_propositions": [m.to_dict() for m in self.macro_propositions],
            "clusters": {k: list(v) for k, v in self.clusters.items()},
            "cluster_records": {k: v.to_dict() for k, v in self.cluster_records.items()},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryGraph":
        """Reconstructs a MemoryGraph from a JSON-compatible dictionary."""
        graph = cls(
            metadata=data.get("metadata", {}),
            string_pool=data.get("string_pool", []),
        )
        for e_data in data.get("entities", []):
            e = Entity.from_dict(e_data)
            graph.entities[e.id] = e
        for h_data in data.get("hyperedges", []):
            graph.hyperedges.append(Hyperedge.from_dict(h_data))
        for n_data in data.get("episodic_nodes", []):
            graph.episodic_nodes.append(EpisodicNode.from_dict(n_data))
        for ptr_data in data.get("vault_pointers", {}).values():
            ptr = VaultPointer.from_dict(ptr_data)
            graph.vault_pointers[ptr.hash] = ptr

        for mp_data in data.get("macro_propositions", []):
            graph.add_macro_proposition(MacroProposition.from_dict(mp_data))
        for cid, members in data.get("clusters", {}).items():
            graph.clusters[cid] = list(members)
        for cid, cr_data in data.get("cluster_records", {}).items():
            graph.cluster_records[cid] = ClusterRecord.from_dict(cr_data)

        graph._rebuild_caches()
        return graph
