"""
ALMF: Agent Long-Term Memory Format.

A high-density binary-packed graph-vector container and retrieval engine
achieving >= 5000:1 smart compression for autonomous AI agent memory.
"""

from .cluster import (
    ClusterRecord,
    EntityCommunityAggregator,
    HierarchicalAggregator,
    SimHashClusterer,
    TemplateLogClusterer,
)
from .codec import (
    decode,
    encode,
    load_from_file,
    save_to_file,
)
from .index import (
    SimHashIndex,
    SimHashMatch,
    hamming_distance,
    hamming_similarity,
    simhash64,
)
from .parser import (
    parse_ast,
    parse_directory,
    parse_file,
    parse_markdown,
    parse_python_ast,
    parse_trace_log,
)
from .query import (
    ScoredFact,
    compute_hybrid_score,
    count_tokens,
    extract_graph_facts,
    query_facts,
    query_subgraph,
    render_context,
    score_fact,
)
from .router import (
    CompressionRoute,
    RoutedItem,
    SmartCompressionRouter,
    estimate_tokens,
)
from .types import (
    ALMFHeader,
    ClusterRecord,
    CompressionModality,
    Entity,
    EntityType,
    EpisodicNode,
    Fact,
    HeaderFlags,
    Hyperedge,
    MacroProposition,
    MemoryGraph,
    SectionEntry,
    SectionType,
    VaultPointer,
    HEADER_SIZE,
    SECTION_ENTRY_SIZE,
)
from .vault import (
    BlobVault,
)

# Contract alias for PROJECT.md Interface Contract (almf.model)
from . import types as model

__version__ = "2.0.0"

__all__ = [
    "__version__",
    "ALMFHeader",
    "BlobVault",
    "ClusterRecord",
    "CompressionModality",
    "CompressionRoute",
    "Entity",
    "EntityCommunityAggregator",
    "EntityType",
    "EpisodicNode",
    "Fact",
    "HeaderFlags",
    "HierarchicalAggregator",
    "Hyperedge",
    "MacroProposition",
    "MemoryGraph",
    "RoutedItem",
    "ScoredFact",
    "SectionEntry",
    "SectionType",
    "SimHashClusterer",
    "SimHashIndex",
    "SimHashMatch",
    "SmartCompressionRouter",
    "TemplateLogClusterer",
    "VaultPointer",
    "compute_hybrid_score",
    "count_tokens",
    "decode",
    "encode",
    "estimate_tokens",
    "extract_graph_facts",
    "hamming_distance",
    "hamming_similarity",
    "load_from_file",
    "model",
    "parse_ast",
    "parse_directory",
    "parse_file",
    "parse_markdown",
    "parse_python_ast",
    "parse_trace_log",
    "query_facts",
    "query_subgraph",
    "render_context",
    "save_to_file",
    "score_fact",
    "simhash64",
    "HEADER_SIZE",
    "SECTION_ENTRY_SIZE",
]
