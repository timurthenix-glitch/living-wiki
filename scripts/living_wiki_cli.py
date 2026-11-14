#!/usr/bin/env python3
"""
Living Wiki CLI — Cross-platform management and verification tool.
Usage:
    python scripts/living_wiki_cli.py hash [--check] [--update]
    python scripts/living_wiki_cli.py sync-pointers
    python scripts/living_wiki_cli.py check-pointers
    python scripts/living_wiki_cli.py check-version
    python scripts/living_wiki_cli.py check-json
    python scripts/living_wiki_cli.py check-size [FILE] [--profile compact|standard|expanded]
    python scripts/living_wiki_cli.py lock {acquire|release|status} [--agent AGENT] [--ttl SECONDS] [--timeout SECONDS]
    python scripts/living_wiki_cli.py journal {append|consolidate|status} [--agent AGENT] [--section SECTION] [FACT]
    python scripts/living_wiki_cli.py pack [INPUT] [-o OUTPUT] [--mode {auto,trace,arch,code}]
    python scripts/living_wiki_cli.py unpack INPUT [-o OUTPUT_DIR] [--reconstruct-sections]
    python scripts/living_wiki_cli.py query INPUT QUERY [--budget TOKENS] [--tokenizer TOKENIZER]
    python scripts/living_wiki_cli.py stats INPUT
    python scripts/living_wiki_cli.py migrate {--to-almf|--to-md} [--input PATH] [--output PATH] [--force]
"""

import sys
import os
import re
import json
import time
import uuid
import hashlib
import shutil
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from almf.types import (
        EntityType,
        SectionType,
        MemoryGraph,
        ALMFHeader,
        SectionEntry,
        Entity,
        HEADER_SIZE,
        SECTION_ENTRY_SIZE,
    )
    from almf.codec import save_to_file, load_from_file
    from almf.parser import parse_markdown, parse_directory, parse_file
    from almf.index import SimHashIndex
    from almf.query import render_context
    from almf.cli import _cmd_stats as almf_stats_cmd
    _ALMF_AVAILABLE = True
    _ALMF_IMPORT_ERROR = None
except ImportError as err:
    _ALMF_AVAILABLE = False
    _ALMF_IMPORT_ERROR = str(err)

TARGET_POINTERS = [
    ".agents/rules/living-wiki.md",
    ".clinerules",
    ".cursor/rules/living-wiki.md",
    ".github/copilot-instructions.md",
    ".junie/guidelines.md",
    ".kiro/steering/living-wiki.md",
    ".qoder/rules/living-wiki.md",
    ".windsurf/rules/living-wiki.md",
]

PROFILES = {
    "compact": {"warning": 5400, "ceiling": 6000},
    "standard": {"warning": 13500, "ceiling": 15000},
    "expanded": {"warning": 45000, "ceiling": 50000},
}


def cmd_hash(args):
    agents_file = ROOT / "AGENTS.md"
    if not agents_file.exists():
        print("::error::AGENTS.md not found", file=sys.stderr)
        return 1

    with open(agents_file, "rb") as f:
        raw_bytes = f.read()

    lines = [
        line.replace(b"\r", b"")
        for line in raw_bytes.splitlines(keepends=True)
        if not line.startswith(b"rules_hash:")
    ]
    computed = hashlib.sha256(b"".join(lines)).hexdigest()

    m = re.search(r"^rules_hash:\s*([0-9a-f]+)", raw_bytes.decode("utf-8", errors="replace"), re.M)
    stored = m.group(1) if m else None

    if getattr(args, "check", False):
        if not stored:
            print("::error::No rules_hash found in AGENTS.md frontmatter", file=sys.stderr)
            return 1
        if computed != stored:
            print(f"::error::rules_hash mismatch: stored ({stored}) != computed ({computed})", file=sys.stderr)
            return 1
        print(f"OK: rules_hash {computed} matches content")
        return 0

    if getattr(args, "update", False):
        text = raw_bytes.decode("utf-8")
        if re.search(r"^rules_hash:\s*[0-9a-f]+", text, re.M):
            new_text = re.sub(r"^rules_hash:\s*[0-9a-f]+", f"rules_hash: {computed}", text, flags=re.M)
        else:
            new_text = f"rules_hash: {computed}\n" + text
        with open(agents_file, "wb") as f:
            f.write(new_text.encode("utf-8"))
        print(f"Updated rules_hash in AGENTS.md to {computed}")
        return 0

    print(computed)
    return 0


def cmd_sync_pointers(args):
    source = ROOT / "templates" / "rule-pointer.md"
    if not source.exists():
        print(f"::error::Template not found: {source}", file=sys.stderr)
        return 1

    content = source.read_text(encoding="utf-8").replace("\r\n", "\n")

    # Clean up legacy .clinerules directory if present
    cline_dir = ROOT / ".clinerules"
    if cline_dir.is_dir():
        print("Removing legacy .clinerules directory...")
        shutil.rmtree(cline_dir)

    for rel_path in TARGET_POINTERS:
        dest = ROOT / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8", newline="\n")
        print(f"synced {rel_path}")

    print("OK: all rule pointers synced successfully")
    return 0


def cmd_check_pointers(args):
    source = ROOT / "templates" / "rule-pointer.md"
    if not source.exists():
        print(f"::error::Template not found: {source}", file=sys.stderr)
        return 1

    expected = source.read_text(encoding="utf-8").replace("\r\n", "\n")
    failed = False

    for rel_path in TARGET_POINTERS:
        dest = ROOT / rel_path
        if not dest.is_file():
            print(f"::error::{rel_path} does not exist — run sync-pointers", file=sys.stderr)
            failed = True
            continue
        actual = dest.read_text(encoding="utf-8").replace("\r\n", "\n")
        if actual != expected:
            print(f"::error::{rel_path} is out of sync with {source} — run sync-pointers", file=sys.stderr)
            failed = True

    if failed:
        return 1
    print("OK: all rule pointer files are in sync")
    return 0


def cmd_check_version(args):
    plugin_path = ROOT / ".claude-plugin" / "plugin.json"
    agents_path = ROOT / "AGENTS.md"
    skill_path = ROOT / "skills" / "living-wiki" / "SKILL.md"

    plugin_ver = None
    if plugin_path.exists():
        try:
            data = json.loads(plugin_path.read_text(encoding="utf-8"))
            plugin_ver = data.get("version")
        except Exception as e:
            print(f"::error::Failed to parse {plugin_path}: {e}", file=sys.stderr)
            return 1

    agents_ver = None
    if agents_path.exists():
        text = agents_path.read_text(encoding="utf-8")
        m = re.search(r"^bootstrap_version:\s*v?([0-9]+(?:\.[0-9]+)?)", text, re.M)
        if m:
            agents_ver = m.group(1)

    skill_ver = None
    if skill_path.exists():
        text = skill_path.read_text(encoding="utf-8")
        m = re.search(r"\(skill v([0-9]+(?:\.[0-9]+)?)\)", text)
        if m:
            skill_ver = m.group(1)

    print(f"plugin.json version:        {plugin_ver or '<none>'}")
    print(f"AGENTS.md bootstrap_version: {agents_ver or '<none>'}")
    print(f"SKILL.md skill version:      {skill_ver or '<none>'}")

    if not plugin_ver or not agents_ver:
        print("::error::Could not extract version fields from plugin.json or AGENTS.md", file=sys.stderr)
        return 1

    if skill_ver and agents_ver != skill_ver:
        print(f"::error::AGENTS.md bootstrap_version ({agents_ver}) and SKILL.md ({skill_ver}) mismatch", file=sys.stderr)
        return 1

    def to_tuple(v):
        parts = v.split(".")
        return (int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)

    if to_tuple(plugin_ver) < to_tuple(agents_ver):
        print(f"::error::plugin.json ({plugin_ver}) is behind rules marker ({agents_ver})", file=sys.stderr)
        return 1

    print(f"OK: version synchronization verified (plugin {plugin_ver} >= rules {agents_ver})")
    return 0


def cmd_check_json(args):
    json_files = [
        ROOT / ".claude-plugin" / "plugin.json",
        ROOT / ".claude-plugin" / "marketplace.json",
        ROOT / "hooks" / "hooks.json",
    ]
    failed = False
    for jf in json_files:
        if not jf.exists():
            continue
        try:
            json.loads(jf.read_text(encoding="utf-8"))
            print(f"OK: {jf.relative_to(ROOT)}")
        except Exception as e:
            print(f"::error::{jf.relative_to(ROOT)} is not valid JSON: {e}", file=sys.stderr)
            failed = True
    return 1 if failed else 0


def cmd_check_size(args):
    target = Path(args.file) if args.file else ROOT / "MEMORY.md"
    if not target.is_absolute():
        target = ROOT / target

    if not target.exists():
        print(f"::error::Target file {target} does not exist", file=sys.stderr)
        return 1

    raw_text = target.read_text(encoding="utf-8")
    clean_text = raw_text.replace("\r", "")
    char_count = len(clean_text)
    byte_count = len(clean_text.encode("utf-8"))
    token_est = int(char_count / 1.5)

    profile = PROFILES.get(args.profile, PROFILES["compact"])
    warning = profile["warning"]
    ceiling = profile["ceiling"]

    pct = (char_count / ceiling) * 100
    status = "OK"
    exit_code = 0

    if char_count > ceiling:
        status = "OVERFLOW (Eviction Required)"
        exit_code = 1
    elif char_count >= warning:
        status = "WARNING (Approaching Threshold)"

    print("=" * 60)
    print(f"File:               {target.name}")
    print(f"Characters:         {char_count:,} / {ceiling:,} ({pct:.1f}%)")
    print(f"Warning Threshold:  {warning:,} characters")
    print(f"UTF-8 Bytes:        {byte_count:,} bytes")
    print(f"Estimated Tokens:   ~{token_est:,} tokens (Cyrillic ~1.5 ch/tok)")
    print(f"Status:             {status}")
    print("=" * 60)
    return exit_code


def cmd_lock(args):
    lock_file = ROOT / "MEMORY.md.lock"
    action = args.action

    if action == "status":
        if not lock_file.exists():
            print("Lock status: FREE")
            return 0
        try:
            data = json.loads(lock_file.read_text(encoding="utf-8"))
            exp = data.get("expires_at", 0)
            remaining = max(0, exp - time.time())
            print(f"Lock status: HELD by {data.get('agent')} (PID {data.get('pid')}), expires in {remaining:.1f}s")
            return 0
        except Exception as e:
            print(f"Lock status: CORRUPT ({e})")
            return 1

    if action == "acquire":
        agent_name = args.agent or "agent"
        ttl = args.ttl or 15.0
        token = str(uuid.uuid4())
        start_time = time.time()
        timeout = args.timeout or 3.0

        while time.time() - start_time < timeout:
            now = time.time()
            payload = {
                "version": 1,
                "agent": agent_name,
                "pid": os.getpid(),
                "token": token,
                "acquired_at": now,
                "expires_at": now + ttl,
            }
            try:
                fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f)
                print(f"OK: lock acquired (token {token})")
                return 0
            except FileExistsError:
                try:
                    existing = json.loads(lock_file.read_text(encoding="utf-8"))
                    if time.time() > existing.get("expires_at", 0):
                        os.unlink(str(lock_file))
                        continue
                except Exception:
                    pass
                time.sleep(0.2)

        print("::error::Could not acquire lock before timeout", file=sys.stderr)
        return 1

    if action == "release":
        if not lock_file.exists():
            print("OK: lock already free")
            return 0
        try:
            os.unlink(str(lock_file))
            print("OK: lock released")
            return 0
        except Exception as e:
            print(f"::error::Failed to release lock: {e}", file=sys.stderr)
            return 1

    return 0


def cmd_journal(args):
    journal_file = ROOT / "memory" / "journal.log"
    memory_file = ROOT / "MEMORY.md"
    action = args.action

    if action == "status":
        if not journal_file.exists():
            print("Journal: empty (0 entries)")
            return 0
        lines = [l for l in journal_file.read_text(encoding="utf-8").splitlines() if l.strip()]
        print(f"Journal: {len(lines)} pending entries")
        return 0

    if action == "append":
        if not args.fact:
            print("::error::No fact text provided", file=sys.stderr)
            return 1
        journal_file.parent.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        agent = args.agent or "unknown"
        section = args.section or "Решения"
        entry = f"[{ts}] [agent:{agent}] [section:{section}] {args.fact}\n"
        with open(journal_file, "a", encoding="utf-8") as f:
            f.write(entry)
        print(f"OK: appended to journal: {args.fact}")
        return 0

    if action == "consolidate":
        if not journal_file.exists():
            print("Journal is empty, nothing to consolidate.")
            return 0
        raw_lines = journal_file.read_text(encoding="utf-8").splitlines()
        entries = [l.strip() for l in raw_lines if l.strip()]
        if not entries:
            print("No entries to consolidate.")
            return 0

        grouped = {}
        pattern = re.compile(r"^\[.*?\]\s*\[agent:.*?\]\s*\[section:(.*?)\]\s*(.*)$")
        for e in entries:
            m = pattern.match(e)
            if m:
                sec, text = m.group(1).strip(), m.group(2).strip()
            else:
                sec, text = "Решения", e
            grouped.setdefault(sec, []).append(text)

        mem_text = memory_file.read_text(encoding="utf-8") if memory_file.exists() else ""
        for sec, facts in grouped.items():
            header = f"## {sec}"
            if header in mem_text:
                new_block = "\n".join(f"- {fact}" for fact in facts) + "\n"
                mem_text = mem_text.replace(header, f"{header}\n{new_block}", 1)
            else:
                mem_text += f"\n\n{header}\n" + "\n".join(f"- {fact}" for fact in facts) + "\n"

        tmp_mem = memory_file.with_suffix(".tmp")
        tmp_mem.write_text(mem_text, encoding="utf-8")
        tmp_mem.replace(memory_file)

        journal_file.unlink()
        print(f"OK: consolidated {len(entries)} entries into {memory_file.name}")
        return 0

    return 0


def reconstruct_living_wiki_markdown(graph: "MemoryGraph") -> tuple:
    """
    Reconstructs canonical Living Wiki 5-section MEMORY.md and any topic files from MemoryGraph.
    Returns (memory_md_content, {relative_path: file_content}).
    """
    canonical_sections = [
        "О пользователе",
        "Проект и стек",
        "Решения",
        "Грабли",
        "Открытые вопросы",
    ]

    scope_map = {
        "user_profile": "О пользователе",
        "project_stack": "Проект и стек",
        "architecture_decisions": "Решения",
        "architecture": "Решения",
        "gotchas_and_pitfalls": "Грабли",
        "rules_and_invariants": "Грабли",
        "open_questions": "Открытые вопросы",
    }

    type_map = {
        EntityType.ACTOR: "О пользователе",
        EntityType.ARTIFACT: "Проект и стек",
        EntityType.DECISION: "Решения",
        EntityType.CONSTRAINT: "Грабли",
        EntityType.ERROR: "Грабли",
        EntityType.CONCEPT: "Открытые вопросы",
        EntityType.CONFIGURATION: "Проект и стек",
        EntityType.METRIC: "Проект и стек",
    }

    doc_entities = {}
    for ent in graph.entities.values():
        if ent.type == EntityType.ARTIFACT and ent.attributes.get("source") == "markdown":
            doc_entities[ent.id] = ent
        elif ent.name == "MEMORY.md" or ent.attributes.get("path", "").endswith("MEMORY.md"):
            doc_entities[ent.id] = ent

    memory_doc_id = None
    for did, dent in doc_entities.items():
        if dent.name == "MEMORY.md" or dent.attributes.get("path", "").endswith("MEMORY.md"):
            memory_doc_id = did
            break

    if memory_doc_id is None and doc_entities:
        memory_doc_id = next(iter(doc_entities.keys()))

    doc_edges = {}
    for h in graph.hyperedges:
        if h.predicate in ("records_fact", "points_to_topic", "enforces_rule", "configures"):
            doc_edges.setdefault(h.subject_id, []).append(h)

    def format_bullet(ent: "Entity", scope: str = "") -> str:
        if ent.attributes.get("cold_pointer"):
            ptr = ent.attributes["cold_pointer"]
            return f"- {ent.name} — {ptr}"
        if ent.attributes.get("rule_text"):
            return f"- !RULE: {ent.attributes['rule_text']}"
        if ent.attributes.get("fact_text"):
            return f"- {ent.attributes['fact_text']}"
        if ent.attributes.get("raw"):
            return f"- {ent.attributes['raw']}"
        custom_attrs = {
            k: v for k, v in ent.attributes.items()
            if k not in ("line", "scope", "path", "source", "section", "cold_pointer", "fact_text", "rule_text", "raw")
        }
        if custom_attrs:
            k, v = next(iter(custom_attrs.items()))
            return f"- {k}: {v}"
        return f"- {ent.name}"

    def get_canonical_section(ent: "Entity", scope: str = "") -> str:
        if scope in scope_map:
            return scope_map[scope]
        ent_scope = ent.attributes.get("scope", "")
        if ent_scope in scope_map:
            return scope_map[ent_scope]
        if ent.type in type_map:
            return type_map[ent.type]
        return "Решения"

    memory_facts = {sec: [] for sec in canonical_sections}
    handled_entities = set()

    if memory_doc_id is not None and memory_doc_id in doc_edges:
        for h in doc_edges[memory_doc_id]:
            ent = graph.entities.get(h.object_id)
            if not ent or ent.id in handled_entities:
                continue
            handled_entities.add(ent.id)
            sec = get_canonical_section(ent, h.scope)
            bullet = format_bullet(ent, h.scope)
            if bullet not in memory_facts[sec]:
                memory_facts[sec].append(bullet)
    elif not doc_entities:
        for ent in graph.entities.values():
            if ent.type == EntityType.ARTIFACT and ("::" in ent.name or ent.name.endswith(".md")):
                continue
            if ent.attributes.get("section") and "::" in ent.name:
                continue
            sec = get_canonical_section(ent)
            bullet = format_bullet(ent)
            if bullet not in memory_facts[sec]:
                memory_facts[sec].append(bullet)

    mem_lines = ["# Память\n"]
    for sec in canonical_sections:
        mem_lines.append(f"## {sec}")
        for b in memory_facts[sec]:
            mem_lines.append(b)
        mem_lines.append("")

    memory_md_content = "\n".join(mem_lines).rstrip() + "\n"

    topic_files = {}
    for did, dent in doc_entities.items():
        if did == memory_doc_id:
            continue
        rel_path = dent.attributes.get("path") or f"memory/{dent.name}"
        if not rel_path.startswith("memory/"):
            rel_path = f"memory/{os.path.basename(rel_path)}"

        topic_title = dent.name
        if topic_title.endswith(".md"):
            topic_title = topic_title[:-3]

        topic_lines = [f"# {topic_title}\n"]
        if did in doc_edges:
            for h in doc_edges[did]:
                ent = graph.entities.get(h.object_id)
                if not ent:
                    continue
                topic_lines.append(format_bullet(ent, h.scope))
        topic_files[rel_path] = "\n".join(topic_lines).rstrip() + "\n"

    return memory_md_content, topic_files


def cmd_pack(args):
    if not _ALMF_AVAILABLE:
        print(f"::error::ALMF engine unavailable: {_ALMF_IMPORT_ERROR}", file=sys.stderr)
        return 1

    input_arg = getattr(args, "input", None)
    if not input_arg:
        if (Path.cwd() / "MEMORY.md").exists():
            input_path = Path.cwd() / "MEMORY.md"
        elif (ROOT / "MEMORY.md").exists():
            input_path = ROOT / "MEMORY.md"
        else:
            print("::error::No input provided and MEMORY.md not found in working directory", file=sys.stderr)
            return 1
    else:
        input_path = Path(input_arg)
        if not input_path.is_absolute():
            input_path = Path.cwd() / input_path

    if not input_path.exists():
        print(f"::error::Input path does not exist: {input_path}", file=sys.stderr)
        return 1

    output_path = Path(args.output) if getattr(args, "output", None) else Path.cwd() / "memory.almf"
    if not output_path.is_absolute():
        output_path = Path.cwd() / output_path

    mode = getattr(args, "mode", "auto") or "auto"
    raw_bytes = 0

    if input_path.is_dir():
        for root, _, files in os.walk(str(input_path)):
            for f in files:
                raw_bytes += os.path.getsize(os.path.join(root, f))
        graph = parse_directory(str(input_path), modality=mode)
    else:
        raw_bytes = os.path.getsize(str(input_path))
        graph = MemoryGraph()
        epoch = 0
        parse_file(str(input_path), modality=mode, graph=graph, epoch=epoch)

        memory_dir = input_path.parent / "memory"
        if memory_dir.is_dir():
            for root, _, files in os.walk(str(memory_dir)):
                for f in sorted(files):
                    if f.endswith(".md"):
                        fpath = os.path.join(root, f)
                        raw_bytes += os.path.getsize(fpath)
                        epoch += 1
                        parse_file(fpath, modality=mode, graph=graph, epoch=epoch)

    indexer = SimHashIndex()
    indexer.index_graph(graph)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    out_size = save_to_file(graph, str(output_path))

    ratio = (raw_bytes / max(1, out_size)) if raw_bytes > 0 else 1.0
    print(f"Packed {input_path} -> {output_path}")
    print(f"  Raw Input:      {raw_bytes:,} bytes")
    print(f"  ALMF Container: {out_size:,} bytes")
    print(f"  Compression:    {ratio:.1f}:1")
    print(f"  Entities:       {len(graph.entities):,}")
    print(f"  Hyperedges:     {len(graph.hyperedges):,}")
    print(f"  Episodic Nodes: {len(graph.episodic_nodes):,}")
    return 0


def cmd_unpack(args):
    if not _ALMF_AVAILABLE:
        print(f"::error::ALMF engine unavailable: {_ALMF_IMPORT_ERROR}", file=sys.stderr)
        return 1

    input_path = Path(args.input)
    if not input_path.is_absolute():
        input_path = Path.cwd() / input_path

    if not input_path.is_file():
        print(f"::error::Container file does not exist: {input_path}", file=sys.stderr)
        return 1

    output_dir = Path(args.output_dir) if getattr(args, "output_dir", None) else Path.cwd()
    if not output_dir.is_absolute():
        output_dir = Path.cwd() / output_dir

    output_dir.mkdir(parents=True, exist_ok=True)

    graph = load_from_file(str(input_path))

    memory_md_content, topic_files = reconstruct_living_wiki_markdown(graph)
    memory_path = output_dir / "MEMORY.md"
    memory_path.write_text(memory_md_content, encoding="utf-8")

    for rel_path, content in topic_files.items():
        topic_path = output_dir / rel_path
        topic_path.parent.mkdir(parents=True, exist_ok=True)
        topic_path.write_text(content, encoding="utf-8")

    entities_path = output_dir / "entities.json"
    with open(entities_path, "w", encoding="utf-8") as f:
        json.dump([e.to_dict() for e in graph.entities.values()], f, indent=2, ensure_ascii=False)

    edges_path = output_dir / "hyperedges.json"
    with open(edges_path, "w", encoding="utf-8") as f:
        json.dump([h.to_dict() for h in graph.hyperedges], f, indent=2, ensure_ascii=False)

    episodes_path = output_dir / "episodic_nodes.json"
    with open(episodes_path, "w", encoding="utf-8") as f:
        json.dump([n.to_dict() for n in graph.episodic_nodes], f, indent=2, ensure_ascii=False)

    if graph.vault and len(graph.vault) > 0:
        vault_dir = output_dir / "vault"
        vault_dir.mkdir(parents=True, exist_ok=True)
        for ptr in graph.vault.list_pointers():
            try:
                raw_data = graph.vault.get(ptr)
                b_name = ptr.metadata.get("name") or f"blob_{ptr.hash}.bin"
                with open(vault_dir / b_name, "wb") as bf:
                    bf.write(raw_data)
            except Exception:
                pass

    print(f"Unpacked {input_path} -> {output_dir}")
    print(f"  Entities: {len(graph.entities):,} -> entities.json")
    print(f"  Hyperedges: {len(graph.hyperedges):,} -> hyperedges.json")
    print(f"  Episodic Nodes: {len(graph.episodic_nodes):,} -> episodic_nodes.json")
    print(f"  Reconstructed: MEMORY.md (5 canonical sections)")
    if topic_files:
        print(f"  Reconstructed Topics: {len(topic_files)} files in memory/")
    return 0


def cmd_query(args):
    if not _ALMF_AVAILABLE:
        print(f"::error::ALMF engine unavailable: {_ALMF_IMPORT_ERROR}", file=sys.stderr)
        return 1

    input_path = Path(args.input)
    if not input_path.is_absolute():
        input_path = Path.cwd() / input_path

    if not input_path.is_file():
        print(f"::error::Container file does not exist: {input_path}", file=sys.stderr)
        return 1

    budget = args.budget if getattr(args, "budget", None) is not None else 500
    tokenizer = getattr(args, "tokenizer", "o200k_base") or "o200k_base"

    graph = load_from_file(str(input_path))
    rendered = render_context(
        graph=graph,
        query=args.query,
        token_budget=budget,
        tokenizer_name=tokenizer,
    )

    if not rendered:
        print("[No matching context found within budget]")
    else:
        print(rendered)
    return 0


def cmd_stats(args):
    if not _ALMF_AVAILABLE:
        print(f"::error::ALMF engine unavailable: {_ALMF_IMPORT_ERROR}", file=sys.stderr)
        return 1

    input_path = Path(args.input)
    if not input_path.is_absolute():
        input_path = Path.cwd() / input_path

    if not input_path.is_file():
        print(f"::error::Container file does not exist: {input_path}", file=sys.stderr)
        return 1

    ns = argparse.Namespace(input_path=str(input_path))
    return almf_stats_cmd(ns)


def cmd_migrate(args):
    if not _ALMF_AVAILABLE:
        print(f"::error::ALMF engine unavailable: {_ALMF_IMPORT_ERROR}", file=sys.stderr)
        return 1

    force = getattr(args, "force", False)

    if getattr(args, "to_almf", False):
        in_path = Path(args.input) if getattr(args, "input", None) else (Path.cwd() / "MEMORY.md")
        if not in_path.is_absolute():
            in_path = Path.cwd() / in_path

        if not in_path.exists() and not getattr(args, "input", None):
            in_path = ROOT / "MEMORY.md"

        if not in_path.exists():
            print(f"::error::Input Markdown not found: {in_path}", file=sys.stderr)
            return 1

        out_path = Path(args.output) if getattr(args, "output", None) else (Path.cwd() / "memory.almf")
        if not out_path.is_absolute():
            out_path = Path.cwd() / out_path

        if out_path.exists() and not force:
            print(f"::error::Target file already exists: {out_path}. Use --force to overwrite.", file=sys.stderr)
            return 1

        pack_args = argparse.Namespace(
            input=str(in_path),
            output=str(out_path),
            mode="auto",
        )
        res = cmd_pack(pack_args)
        if res == 0:
            print(f"OK: successfully migrated Markdown -> ALMF ({out_path}) [non-destructive]")
        return res

    elif getattr(args, "to_md", False):
        in_path = Path(args.input) if getattr(args, "input", None) else (Path.cwd() / "memory.almf")
        if not in_path.is_absolute():
            in_path = Path.cwd() / in_path

        if not in_path.is_file():
            print(f"::error::Input ALMF container not found: {in_path}", file=sys.stderr)
            return 1

        out_dir = Path(args.output) if getattr(args, "output", None) else Path.cwd()
        if not out_dir.is_absolute():
            out_dir = Path.cwd() / out_dir

        target_mem = out_dir / "MEMORY.md"
        if target_mem.exists() and not force:
            print(f"::error::Target file already exists: {target_mem}. Use --force to overwrite.", file=sys.stderr)
            return 1

        graph = load_from_file(str(in_path))
        memory_md_content, topic_files = reconstruct_living_wiki_markdown(graph)

        out_dir.mkdir(parents=True, exist_ok=True)
        target_mem.write_text(memory_md_content, encoding="utf-8")

        for rel_path, content in topic_files.items():
            topic_path = out_dir / rel_path
            if topic_path.exists() and not force:
                print(f"::error::Target topic file already exists: {topic_path}. Use --force to overwrite.", file=sys.stderr)
                return 1
            topic_path.parent.mkdir(parents=True, exist_ok=True)
            topic_path.write_text(content, encoding="utf-8")

        print(f"OK: successfully migrated ALMF -> Markdown ({target_mem}) [non-destructive]")
        if topic_files:
            print(f"  Reconstructed {len(topic_files)} topic file(s) in {out_dir / 'memory'}")
        return 0

    return 1


def main():
    parser = argparse.ArgumentParser(description="Living Wiki Management CLI")
    subparsers = parser.add_subparsers(dest="command")

    # hash
    p_hash = subparsers.add_parser("hash", help="Calculate or verify rules_hash")
    p_hash.add_argument("--check", action="store_true", help="Verify hash matches AGENTS.md frontmatter")
    p_hash.add_argument("--update", action="store_true", help="Update rules_hash in AGENTS.md")

    # sync-pointers
    subparsers.add_parser("sync-pointers", help="Sync templates/rule-pointer.md to all tool targets")

    # check-pointers
    subparsers.add_parser("check-pointers", help="Check that all tool rule-pointers match template")

    # check-version
    subparsers.add_parser("check-version", help="Verify version synchronization")

    # check-json
    subparsers.add_parser("check-json", help="Validate plugin and marketplace JSON manifests")

    # check-size
    p_size = subparsers.add_parser("check-size", help="Check MEMORY.md character and byte capacity")
    p_size.add_argument("file", nargs="?", default="MEMORY.md", help="File to check (default: MEMORY.md)")
    p_size.add_argument("--profile", choices=["compact", "standard", "expanded"], default="compact", help="Memory profile")

    # lock
    p_lock = subparsers.add_parser("lock", help="Manage advisory lockfile MEMORY.md.lock")
    p_lock.add_argument("action", choices=["acquire", "release", "status"])
    p_lock.add_argument("--agent", default="agent", help="Agent identifier")
    p_lock.add_argument("--ttl", type=float, default=15.0, help="Lock TTL in seconds")
    p_lock.add_argument("--timeout", type=float, default=3.0, help="Lock acquisition timeout in seconds")

    # journal
    p_jour = subparsers.add_parser("journal", help="Manage append-only staging journal")
    p_jour.add_argument("action", choices=["append", "consolidate", "status"])
    p_jour.add_argument("fact", nargs="?", help="Fact text to append")
    p_jour.add_argument("--agent", default="agent", help="Agent identifier")
    p_jour.add_argument("--section", default="Решения", help="Target section in MEMORY.md")

    # pack
    p_pack = subparsers.add_parser("pack", help="Ingest Markdown or directory into .almf binary container")
    p_pack.add_argument("input", nargs="?", help="Input file or directory (default: MEMORY.md)")
    p_pack.add_argument("-o", "--output", default="memory.almf", help="Output .almf file (default: memory.almf)")
    p_pack.add_argument("--mode", choices=["auto", "trace", "arch", "code"], default="auto", help="Compression mode")

    # unpack
    p_unpack = subparsers.add_parser("unpack", help="Decompress .almf container and reconstruct canonical 5-section Markdown")
    p_unpack.add_argument("input", help="Input .almf file")
    p_unpack.add_argument("-o", "--output-dir", default=".", help="Output directory (default: .)")
    p_unpack.add_argument("--reconstruct-sections", action="store_true", default=True, help="Reconstruct canonical 5 sections")

    # query
    p_query = subparsers.add_parser("query", help="Query facts from .almf container within token budget")
    p_query.add_argument("input", help="Input .almf file")
    p_query.add_argument("query", help="Natural language query string")
    p_query.add_argument("--budget", type=int, default=500, help="Token budget (default: 500)")
    p_query.add_argument("--tokenizer", default="o200k_base", help="Tokenizer model name (default: o200k_base)")

    # stats
    p_stats = subparsers.add_parser("stats", help="Display .almf container layout, block stats, and entity counts")
    p_stats.add_argument("input", help="Input .almf file")

    # migrate
    p_mig = subparsers.add_parser("migrate", help="Non-destructive migration between Markdown and ALMF")
    g_mig = p_mig.add_mutually_exclusive_group(required=True)
    g_mig.add_argument("--to-almf", action="store_true", help="Migrate Markdown memory to ALMF binary container")
    g_mig.add_argument("--to-md", action="store_true", help="Migrate ALMF binary container to canonical Markdown memory")
    p_mig.add_argument("--input", "-i", help="Input path (default: MEMORY.md for --to-almf, memory.almf for --to-md)")
    p_mig.add_argument("--output", "-o", help="Output path (default: memory.almf for --to-almf, . for --to-md)")
    p_mig.add_argument("--force", "-f", action="store_true", help="Overwrite existing target file(s)")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1

    cmds = {
        "hash": cmd_hash,
        "sync-pointers": cmd_sync_pointers,
        "check-pointers": cmd_check_pointers,
        "check-version": cmd_check_version,
        "check-json": cmd_check_json,
        "check-size": cmd_check_size,
        "lock": cmd_lock,
        "journal": cmd_journal,
        "pack": cmd_pack,
        "unpack": cmd_unpack,
        "query": cmd_query,
        "stats": cmd_stats,
        "migrate": cmd_migrate,
    }
    return cmds[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
