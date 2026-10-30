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
    }
    return cmds[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
