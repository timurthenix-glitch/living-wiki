"""
almf.cli: Command-Line Interface for the Agent Long-Term Memory Format.

Provides standard CLI operations:
- pack: Ingests documents, code ASTs, or traces into a compressed .almf container.
- unpack: Extracts entities, facts, markdown summaries, and vault blobs from .almf.
- query: Searches and renders optimal context within strict token budgets.
- stats: Displays container metadata, section layout, and graph metrics.
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import sys
import zlib
from typing import List, Optional

from almf.codec import load_from_file, save_to_file
from almf.index import SimHashIndex
from almf.parser import parse_directory, parse_file
from almf.query import count_tokens, query_facts, render_context
from almf.types import (
    ALMFHeader,
    EntityType,
    MemoryGraph,
    SectionEntry,
    SectionType,
    HEADER_SIZE,
    SECTION_ENTRY_SIZE,
)


def _cmd_pack(args: argparse.Namespace) -> int:
    """Executes `almf pack <input_path> -o <output.almf> [--mode auto|trace|arch|code]`."""
    input_path = args.input_path
    output_path = args.output
    mode = getattr(args, "mode", "auto") or "auto"

    if not os.path.exists(input_path):
        print(f"Error: input path does not exist: {input_path}", file=sys.stderr)
        return 1

    # Calculate raw input size
    raw_bytes = 0
    if os.path.isdir(input_path):
        for root, _, files in os.walk(input_path):
            for f in files:
                raw_bytes += os.path.getsize(os.path.join(root, f))
        graph = parse_directory(input_path, modality=mode)
    else:
        raw_bytes = os.path.getsize(input_path)
        graph = parse_file(input_path, modality=mode)

    # Build and attach SimHash index
    indexer = SimHashIndex()
    indexer.index_graph(graph)

    # Save to .almf container
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    out_size = save_to_file(graph, output_path)

    ratio = (raw_bytes / max(1, out_size)) if raw_bytes > 0 else 1.0
    print(f"Packed {input_path} -> {output_path}")
    print(f"  Raw Input:      {raw_bytes:,} bytes")
    print(f"  ALMF Container: {out_size:,} bytes")
    print(f"  Compression:    {ratio:.1f}:1")
    print(f"  Entities:       {len(graph.entities):,}")
    print(f"  Hyperedges:     {len(graph.hyperedges):,}")
    print(f"  Episodic Nodes: {len(graph.episodic_nodes):,}")
    return 0


def _cmd_unpack(args: argparse.Namespace) -> int:
    """Executes `almf unpack <input.almf> -o <output_dir>`."""
    input_path = args.input_path
    output_dir = args.output_dir

    if not os.path.isfile(input_path):
        print(f"Error: container file does not exist: {input_path}", file=sys.stderr)
        return 1

    try:
        graph = load_from_file(input_path)
    except Exception as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1
    os.makedirs(output_dir, exist_ok=True)

    # 1. Export entities.json
    entities_path = os.path.join(output_dir, "entities.json")
    with open(entities_path, "w", encoding="utf-8") as f:
        json.dump([e.to_dict() for e in graph.entities.values()], f, indent=2, ensure_ascii=False)

    # 2. Export hyperedges.json
    edges_path = os.path.join(output_dir, "hyperedges.json")
    with open(edges_path, "w", encoding="utf-8") as f:
        json.dump([h.to_dict() for h in graph.hyperedges], f, indent=2, ensure_ascii=False)

    # 3. Export episodic_nodes.json
    episodes_path = os.path.join(output_dir, "episodic_nodes.json")
    with open(episodes_path, "w", encoding="utf-8") as f:
        json.dump([n.to_dict() for n in graph.episodic_nodes], f, indent=2, ensure_ascii=False)

    # 4. Generate reconstructed MEMORY.md
    memory_path = os.path.join(output_dir, "MEMORY.md")
    with open(memory_path, "w", encoding="utf-8") as f:
        f.write("# Reconstructed Memory State\n\n")
        # Group by EntityType
        type_groups: dict[EntityType, list] = {}
        for e in graph.entities.values():
            type_groups.setdefault(e.type, []).append(e)

        for etype in sorted(type_groups.keys(), key=lambda t: t.value):
            f.write(f"## {etype.name}\n")
            for ent in type_groups[etype]:
                attrs_str = f" ({ent.attributes})" if ent.attributes else ""
                f.write(f"- **{ent.name}**{attrs_str}\n")
            f.write("\n")

        if graph.episodic_nodes:
            f.write("## Episodic Summaries\n")
            for n in graph.episodic_nodes:
                f.write(f"- [L{n.level}] {n.summary}\n")
            f.write("\n")

    # 5. Extract vault blobs if present
    if graph.vault and len(graph.vault) > 0:
        vault_dir = os.path.join(output_dir, "vault")
        os.makedirs(vault_dir, exist_ok=True)
        for ptr in graph.vault.list_pointers():
            try:
                raw_data = graph.vault.get(ptr)
                b_name = ptr.metadata.get("name") or f"blob_{ptr.hash}.bin"
                with open(os.path.join(vault_dir, b_name), "wb") as bf:
                    bf.write(raw_data)
            except Exception:
                pass

    print(f"Unpacked {input_path} -> {output_dir}")
    print(f"  Entities: {len(graph.entities):,} -> entities.json")
    print(f"  Hyperedges: {len(graph.hyperedges):,} -> hyperedges.json")
    print(f"  Episodic Nodes: {len(graph.episodic_nodes):,} -> episodic_nodes.json")
    print(f"  Reconstructed: MEMORY.md")
    return 0


def _cmd_query(args: argparse.Namespace) -> int:
    """Executes `almf query <input.almf> "<query>" [--budget 500]`."""
    input_path = args.input_path
    query_str = args.query
    budget = args.budget if getattr(args, "budget", None) is not None else 500
    tokenizer = getattr(args, "tokenizer", "o200k_base") or "o200k_base"

    if not os.path.isfile(input_path):
        print(f"Error: container file does not exist: {input_path}", file=sys.stderr)
        return 1

    try:
        graph = load_from_file(input_path)
    except Exception as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1
    rendered = render_context(
        graph=graph,
        query=query_str,
        token_budget=budget,
        tokenizer_name=tokenizer,
    )

    if not rendered:
        print("[No matching context found within budget]")
    else:
        print(rendered)
    return 0


def _cmd_stats(args: argparse.Namespace) -> int:
    """Executes `almf stats <input.almf>`: displays block breakdown, compression ratio, entity/edge counts."""
    input_path = args.input_path

    if not os.path.isfile(input_path):
        print(f"Error: container file does not exist: {input_path}", file=sys.stderr)
        return 1

    file_size = os.path.getsize(input_path)
    try:
        with open(input_path, "rb") as f:
            raw_header = f.read(HEADER_SIZE)
            if len(raw_header) < HEADER_SIZE:
                print("Error: file is smaller than 32-byte ALMF header", file=sys.stderr)
                return 1

            header = ALMFHeader.unpack(raw_header)
            f.seek(header.section_table_offset)
            (sec_count,) = struct.unpack("<H", f.read(2))
            entries: List[SectionEntry] = []
            for _ in range(sec_count):
                entries.append(SectionEntry.unpack(f.read(SECTION_ENTRY_SIZE)))

        graph = load_from_file(input_path)
    except Exception as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1

    section_names = {
        SectionType.STRING_POOL: "1: String Intern Pool",
        SectionType.ENTITY_REGISTRY: "2: Entity Registry Table",
        SectionType.PROPOSITION_HYPERGRAPH: "3: Proposition Hypergraph",
        SectionType.EPISODIC_TREE: "4: Hierarchical Episodic Tree",
        SectionType.SIMHASH_INDEX: "5: Semantic Search Index",
        SectionType.COLD_ARCHIVE: "6: Cold Archival Vault",
    }

    codec_names = {
        0: "raw",
        1: "brotli-11",
        2: "deflate-9",
    }

    print("=" * 70)
    print(f"ALMF Container Stats: {os.path.basename(input_path)}")
    print("=" * 70)
    print(f"  Container Size:    {file_size:,} bytes")
    print(f"  Version:           {header.version}")
    print(f"  Flags:             {header.flags:#06x}")
    print(f"  CRC32:             {header.crc32:#010x}")
    print("-" * 70)
    print(f"{'Section':<30} {'Offset':<8} {'Compressed':<12} {'Uncompressed':<14} {'Codec':<10}")
    print("-" * 70)

    total_uncompressed = 0
    total_compressed = 0
    for entry in entries:
        s_name = section_names.get(entry.section_type, f"Section {entry.section_type}")
        codec = codec_names.get(entry.flags, f"flag_{entry.flags}")
        u_len = entry.uncompressed_length if entry.uncompressed_length > 0 else entry.length
        total_compressed += entry.length
        total_uncompressed += u_len
        print(f"{s_name:<30} {entry.offset:<8} {entry.length:<12,d} {u_len:<14,d} {codec:<10}")

    print("-" * 70)
    ratio = (total_uncompressed / max(1, file_size)) if total_uncompressed > 0 else 1.0
    print(f"Physical Ratio:      {ratio:.1f}:1 ({total_uncompressed:,} -> {file_size:,} bytes)")
    print("-" * 70)
    print(f"Knowledge Graph Structure:")
    print(f"  Entities:          {len(graph.entities):,}")
    # Entity type breakdown
    type_counts: dict[str, int] = {}
    for e in graph.entities.values():
        tname = e.type.name if isinstance(e.type, EntityType) else str(e.type)
        type_counts[tname] = type_counts.get(tname, 0) + 1
    for tname, cnt in sorted(type_counts.items()):
        print(f"    - {tname:<15}: {cnt:,}")

    print(f"  Hyperedges:        {len(graph.hyperedges):,}")
    print(f"  Episodic Nodes:    {len(graph.episodic_nodes):,}")
    print(f"  Interned Strings:  {len(graph.string_pool):,}")
    print(f"  Vault Blobs:       {len(graph.vault_pointers):,}")
    print("=" * 70)
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Builds the main argument parser with subcommands."""
    parser = argparse.ArgumentParser(
        prog="almf",
        description="ALMF: Agent Long-Term Memory Format CLI & Engine",
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="Available subcommands")

    # pack
    p_pack = subparsers.add_parser("pack", help="Pack documents/code/traces into an .almf container")
    p_pack.add_argument("input_path", help="Input file or directory to pack")
    p_pack.add_argument("-o", "--output", required=True, help="Output .almf file path")
    p_pack.add_argument(
        "--mode",
        choices=["auto", "trace", "arch", "code"],
        default="auto",
        help="Compression modality hint (default: auto)",
    )

    # unpack
    p_unpack = subparsers.add_parser("unpack", help="Unpack an .almf container to a directory")
    p_unpack.add_argument("input_path", help="Path to .almf container")
    p_unpack.add_argument("-o", "--output-dir", required=True, help="Target output directory")

    # query
    p_query = subparsers.add_parser("query", help="Query .almf memory within a token budget")
    p_query.add_argument("input_path", help="Path to .almf container")
    p_query.add_argument("query", help="Query string")
    p_query.add_argument("--budget", type=int, default=500, help="Token budget (default: 500)")
    p_query.add_argument("--tokenizer", default="o200k_base", help="Tokenizer model name (default: o200k_base)")

    # stats
    p_stats = subparsers.add_parser("stats", help="Display .almf container statistics and section breakdown")
    p_stats.add_argument("input_path", help="Path to .almf container")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Main CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.subcommand:
        parser.print_help()
        return 0

    if args.subcommand == "pack":
        return _cmd_pack(args)
    elif args.subcommand == "unpack":
        return _cmd_unpack(args)
    elif args.subcommand == "query":
        return _cmd_query(args)
    elif args.subcommand == "stats":
        return _cmd_stats(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
