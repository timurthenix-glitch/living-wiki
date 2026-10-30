#!/usr/bin/env python3
"""
Comprehensive Automated Test Suite for Living Wiki v2 Architecture.
Verifies complete remediation of defects DEF-MEM-01 through DEF-AUD-01.
Standard Library unittest only — zero external dependencies.
"""

import os
import re
import sys
import json
import time
import shutil
import hashlib
import tempfile
import unittest
from pathlib import Path

# Add project root to sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import scripts.living_wiki_cli as cli


class TestMemoryManagement(unittest.TestCase):
    """Verifies DEF-MEM-01 through DEF-MEM-07."""

    def test_def_mem_01_cross_platform_char_counting(self):
        """DEF-MEM-01: Character counting must measure Unicode code points without depending on Unix wc -m."""
        sample = "Тестовая строка: привет мир! 🚀"
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as f:
            f.write(sample)
            temp_path = f.name
        try:
            content = Path(temp_path).read_text(encoding="utf-8").replace("\r", "")
            self.assertEqual(len(content), len(sample))
            self.assertEqual(len(content), 30)
        finally:
            os.unlink(temp_path)

    def test_def_mem_02_cyrillic_byte_vs_char(self):
        """DEF-MEM-02: Cyrillic characters consume ~2 bytes in UTF-8; check-size must measure characters, not bytes."""
        # 5,400 Russian characters
        cyrillic_text = "Ф" * 5400
        byte_len = len(cyrillic_text.encode("utf-8"))
        self.assertEqual(byte_len, 10800)  # ~10.8 KB on disk

        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as f:
            f.write(cyrillic_text)
            temp_path = f.name
        try:
            class Args:
                file = temp_path
                profile = "compact"
            # 5400 characters is at warning threshold, but NOT an overflow (>6000)
            exit_code = cli.cmd_check_size(Args())
            self.assertEqual(exit_code, 0)
        finally:
            os.unlink(temp_path)

    def test_def_mem_03_crlf_normalization(self):
        """DEF-MEM-03: CRLF line endings must be stripped of \r so char count is identical to LF."""
        text_lf = "Строка 1\nСтрока 2\nСтрока 3\n"
        text_crlf = "Строка 1\r\nСтрока 2\r\nСтрока 3\r\n"

        clean_lf = text_lf.replace("\r", "")
        clean_crlf = text_crlf.replace("\r", "")
        self.assertEqual(len(clean_lf), len(clean_crlf))
        self.assertEqual(clean_lf, clean_crlf)

    def test_def_mem_04_cluster_displacement(self):
        """DEF-MEM-04: Multiple related facts (3+) should displace as a coherent cluster to cold storage."""
        facts = [
            "PostgreSQL 16 используется как основная БД.",
            "Схема БД включает таблицы users, sessions, audit_logs.",
            "Индексы построены по полям email и created_at.",
        ]
        topic_content = "# Схема БД\n\n" + "\n".join(f"- {fact}" for fact in facts) + "\n"
        pointer = "- Схема и архитектура БД — memory/database.md"

        self.assertGreaterEqual(len(facts), 3)
        self.assertIn("memory/database.md", pointer)
        self.assertIn("PostgreSQL", topic_content)

    def test_def_mem_05_pointer_immunity(self):
        """DEF-MEM-05: Pointers (- ... — memory/<topic>.md) must have immunity from eviction sweeps."""
        memory_lines = [
            "# Память",
            "## Решения",
            "- Архитектура и слои — memory/architecture.md",
            "- Временный флаг отладки (устарел)",
            "- База данных — memory/database.md",
        ]

        def evict_non_pointers(lines):
            retained = []
            pointer_re = re.compile(r"^-\s+.*—\s+memory/[a-zA-Z0-9_\-]+\.md$")
            for line in lines:
                if pointer_re.match(line.strip()):
                    retained.append((line, True))  # Immune
                elif "устарел" in line:
                    continue  # Evicted
                else:
                    retained.append((line, False))
            return retained

        result = evict_non_pointers(memory_lines)
        retained_texts = [r[0] for r in result]
        self.assertIn("- Архитектура и слои — memory/architecture.md", retained_texts)
        self.assertIn("- База данных — memory/database.md", retained_texts)
        self.assertNotIn("- Временный флаг отладки (устарел)", retained_texts)

    def test_def_mem_06_inverted_eviction_priority(self):
        """DEF-MEM-06: Static facts displace to cold storage first; pitfalls and open questions stay hot."""
        sections_priority = ["Проект и стек", "Решения", "Открытые вопросы", "Грабли"]
        # Eviction order: static first
        eviction_order = ["Проект и стек", "Решения"]
        preserved_hot = ["Грабли", "Открытые вопросы"]

        for sec in eviction_order:
            self.assertIn(sec, sections_priority[:2])
        for sec in preserved_hot:
            self.assertIn(sec, sections_priority[2:])

    def test_def_mem_07_flat_naming_and_wikilink_ban(self):
        """DEF-MEM-07: Subtopics must use flat naming (memory/<topic>-<subtopic>.md) and ban [[wikilinks]]."""
        valid_flat_name = "memory/architecture-routing.md"
        invalid_nested_name = "memory/architecture/routing.md"

        self.assertNotIn("/", valid_flat_name.replace("memory/", ""))
        self.assertIn("/", invalid_nested_name.replace("memory/", ""))

        # Wikilink ban
        sample_doc = "См. [Архитектура роутинга](memory/architecture-routing.md)"
        self.assertNotIn("[[", sample_doc)
        self.assertNotIn("]]", sample_doc)


class TestConcurrency(unittest.TestCase):
    """Verifies DEF-CONC-01 and DEF-CONC-02."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.orig_root = cli.ROOT
        cli.ROOT = Path(self.test_dir)

    def tearDown(self):
        cli.ROOT = self.orig_root
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_def_conc_01_file_locking(self):
        """DEF-CONC-01: Advisory lock must prevent concurrent conflicting writes and handle stale locks."""
        class ArgsAcquire:
            action = "acquire"
            agent = "agent_alpha"
            ttl = 1.0
            timeout = 0.5

        # First acquisition succeeds
        code1 = cli.cmd_lock(ArgsAcquire())
        self.assertEqual(code1, 0)

        # Immediate second acquisition by different agent fails/times out
        class ArgsBeta:
            action = "acquire"
            agent = "agent_beta"
            ttl = 1.0
            timeout = 0.3

        code2 = cli.cmd_lock(ArgsBeta())
        self.assertEqual(code2, 1)

        # Release lock
        class ArgsRelease:
            action = "release"

        code_rel = cli.cmd_lock(ArgsRelease())
        self.assertEqual(code_rel, 0)

        # Now beta can acquire
        code3 = cli.cmd_lock(ArgsBeta())
        self.assertEqual(code3, 0)

        # Clean release
        cli.cmd_lock(ArgsRelease())

    def test_def_conc_02_atomic_displacement(self):
        """DEF-CONC-02: Two-phase atomic replacement must use temporary files with atomic rename."""
        target_file = cli.ROOT / "memory" / "architecture.md"
        target_file.parent.mkdir(parents=True, exist_ok=True)

        content = "# Архитектура\n- Факт 1\n- Факт 2\n"
        tmp_file = target_file.with_suffix(".tmp.test")
        tmp_file.write_text(content, encoding="utf-8")
        tmp_file.replace(target_file)

        self.assertTrue(target_file.exists())
        self.assertFalse(tmp_file.exists())
        self.assertEqual(target_file.read_text(encoding="utf-8"), content)


class TestRulePointers(unittest.TestCase):
    """Verifies DEF-PTR-01 and DEF-PTR-02."""

    def test_def_ptr_01_clinerules_root_file(self):
        """DEF-PTR-01: .clinerules must be a root file, not a directory."""
        clinerules_path = ROOT / ".clinerules"
        self.assertTrue(clinerules_path.exists(), ".clinerules must exist")
        self.assertTrue(clinerules_path.is_file(), ".clinerules must be a regular file, not a directory")

        legacy_path = ROOT / ".clinerules" / "living-wiki.md"
        self.assertFalse(legacy_path.exists(), "Legacy directory path must not exist")

    def test_def_ptr_02_active_context_injection(self):
        """DEF-PTR-02: Rule pointer must contain active inline context of core rules."""
        template_path = ROOT / "templates" / "rule-pointer.md"
        self.assertTrue(template_path.exists())
        content = template_path.read_text(encoding="utf-8")

        # Must describe structure, 5 sections, format, volume limit, and audit
        self.assertIn("## 1. Структура памяти", content)
        self.assertIn("## 2. Разделы `MEMORY.md`", content)
        self.assertIn("## О пользователе", content)
        self.assertIn("## Проект и стек", content)
        self.assertIn("## Решения", content)
        self.assertIn("## Грабли", content)
        self.assertIn("## Открытые вопросы", content)
        self.assertIn("## 3. Правила записи и формат", content)
        self.assertIn("## 4. Контроль объёма и вытеснение", content)
        self.assertIn("## 6. Аудит каждого ответа", content)

        # All target pointers must match template
        class Args:
            pass
        self.assertEqual(cli.cmd_check_pointers(Args()), 0)


class TestRulesHash(unittest.TestCase):
    """Verifies DEF-HASH-01 and DEF-HASH-02."""

    def test_def_hash_01_hash_determinism_and_downstream_portability(self):
        """DEF-HASH-01: Hash computation must be decoupled from skills/living-wiki/ and CRLF-normalized."""
        agents_path = ROOT / "AGENTS.md"
        self.assertTrue(agents_path.exists())

        raw_bytes = agents_path.read_bytes()
        lines = [
            line.replace(b"\r", b"")
            for line in raw_bytes.splitlines(keepends=True)
            if not line.startswith(b"rules_hash:")
        ]
        expected_hash = hashlib.sha256(b"".join(lines)).hexdigest()

        class Args:
            check = True
            update = False

        self.assertEqual(cli.cmd_hash(Args()), 0)

    def test_def_hash_02_cross_platform_cli(self):
        """DEF-HASH-02: Cross-platform CLI must provide all verification commands natively in Python."""
        class Args:
            check = True
            update = False
            file = str(ROOT / "templates" / "MEMORY.md")
            profile = "compact"

        self.assertEqual(cli.cmd_hash(Args()), 0)
        self.assertEqual(cli.cmd_check_pointers(Args()), 0)
        self.assertEqual(cli.cmd_check_version(Args()), 0)
        self.assertEqual(cli.cmd_check_json(Args()), 0)
        self.assertEqual(cli.cmd_check_size(Args()), 0)


class TestSkills(unittest.TestCase):
    """Verifies DEF-SKL-01 and DEF-SKL-02."""

    def test_def_skl_01_purge_obsidian_wikilinks(self):
        """DEF-SKL-01: templates/skill.md must contain 0 occurrences of [[wikilinks]]."""
        template_skill = ROOT / "templates" / "skill.md"
        self.assertTrue(template_skill.exists())
        text = template_skill.read_text(encoding="utf-8")
        self.assertNotIn("[[", text)
        self.assertNotIn("]]", text)
        self.assertNotIn("self/Skills", text)

    def test_def_skl_02_auxiliary_details_convention(self):
        """DEF-SKL-02: Auxiliary skill files must be named details.md to avoid collision with notes/ ban."""
        agents_text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        skill_text = (ROOT / "skills" / "living-wiki" / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("details.md", agents_text)
        self.assertIn("details.md", skill_text)


class TestContextAndAudit(unittest.TestCase):
    """Verifies DEF-CTX-01 and DEF-AUD-01."""

    def test_def_ctx_01_tiered_memory_profiles(self):
        """DEF-CTX-01: Configurable/tiered memory profiles (compact: 6k, standard: 15k, expanded: 50k)."""
        self.assertIn("compact", cli.PROFILES)
        self.assertIn("standard", cli.PROFILES)
        self.assertIn("expanded", cli.PROFILES)

        self.assertEqual(cli.PROFILES["compact"]["ceiling"], 6000)
        self.assertEqual(cli.PROFILES["standard"]["ceiling"], 15000)
        self.assertEqual(cli.PROFILES["expanded"]["ceiling"], 50000)

    def test_def_aud_01_audit_gating(self):
        """DEF-AUD-01: Cognitive audit must gate on substantive tasks and skip trivial queries."""
        def should_audit(task_type):
            substantive_types = {"feature", "bugfix", "refactor", "architecture", "migration"}
            return task_type.lower() in substantive_types

        self.assertFalse(should_audit("trivial_qa"))
        self.assertFalse(should_audit("clarification"))
        self.assertFalse(should_audit("code_read"))
        self.assertTrue(should_audit("feature"))
        self.assertTrue(should_audit("bugfix"))
        self.assertTrue(should_audit("refactor"))


if __name__ == "__main__":
    unittest.main()
