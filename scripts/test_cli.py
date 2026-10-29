#!/usr/bin/env python3
"""
test_cli.py — Регрессии на CLI-точку входа `engine.py main()`.

Почему этот файл существует отдельно: test_engine.py и test_memory.py
импортируют классы напрямую и ни разу не вызывают `main()`. Из-за этого
`engine.py match` мог падать с UnboundLocalError в каждом каталоге
(переменная `recorder` не была определена в этой ветке), а весь набор
тестов оставался зелёным.

Здесь команды запускаются именно так, как их вызывают агент и хуки —
через subprocess в отдельном процессе с контролируемым cwd и env.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
ENGINE = SCRIPTS_DIR / "engine.py"


def _make_vault(root: Path) -> Path:
    """Минимальный настоящий vault: self/ или wiki/ — признак корня."""
    (root / "self").mkdir(parents=True, exist_ok=True)
    (root / "wiki").mkdir(parents=True, exist_ok=True)
    (root / "self" / "Lessons-Learned.md").write_text(
        "---\ntitle: Уроки\n---\n\n## Урок\nПроверенный урок.\n", encoding="utf-8"
    )
    return root


class CliTestCase(unittest.TestCase):
    def run_engine(self, *args, cwd=None, env_extra=None):
        env = dict(os.environ)
        # Изоляция от реальной памяти пользователя
        env.pop("LIVING_WIKI_MEMORY_DB", None)
        env.pop("LIVING_WIKI_VAULT", None)
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            [sys.executable, str(ENGINE), *args],
            cwd=str(cwd) if cwd else None,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )


class TestMatchCommand(CliTestCase):
    """Регрессия: `match` падал с UnboundLocalError в ЛЮБОМ каталоге."""

    def test_match_does_not_crash_on_empty_vault(self):
        with tempfile.TemporaryDirectory() as d:
            vault = _make_vault(Path(d))
            res = self.run_engine("match", "совершенно новый вопрос", cwd=vault)
            self.assertNotIn("Traceback", res.stderr)
            self.assertNotIn("UnboundLocalError", res.stderr)
            # Нет совпадений -> код 1, но НЕ падение
            self.assertIn(res.returncode, (0, 1))

    def test_match_finds_what_store_wrote(self):
        with tempfile.TemporaryDirectory() as d:
            vault = _make_vault(Path(d))
            stored = self.run_engine(
                "store", "как чинить тесты", "запускать unittest напрямую", cwd=vault
            )
            self.assertEqual(stored.returncode, 0, stored.stderr)
            found = self.run_engine("match", "как чинить тесты", cwd=vault)
            self.assertEqual(found.returncode, 0, found.stderr)
            self.assertIn("unittest", found.stdout)

    def test_store_and_match_share_one_database(self):
        """Регрессия: storage.py и engine.py выбирали разные корни."""
        with tempfile.TemporaryDirectory() as d:
            vault = _make_vault(Path(d))
            self.run_engine("store", "qd", "ad", cwd=vault)
            self.run_engine("match", "qd", cwd=vault)
            dbs = list(vault.rglob("memory.db"))
            self.assertEqual(len(dbs), 1, f"ожидалась одна база, найдено: {dbs}")


class TestVaultResolution(CliTestCase):
    def test_status_says_not_configured_instead_of_empty(self):
        with tempfile.TemporaryDirectory() as d:
            plain = Path(d) / "myproject"
            plain.mkdir()
            (plain / "app.py").write_text("print(1)", encoding="utf-8")
            # Посторонний AGENTS.md выше по дереву НЕ должен делать этот
            # каталог vault (в старом коде именно так и происходило).
            (Path(d) / "AGENTS.md").write_text("чужой проект", encoding="utf-8")
            res = self.run_engine("status", cwd=plain)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("НЕ НАСТРОЕН", res.stdout)

    def test_store_refuses_to_write_outside_vault(self):
        with tempfile.TemporaryDirectory() as d:
            plain = Path(d) / "myproject"
            plain.mkdir()
            (Path(d) / "AGENTS.md").write_text("чужой проект", encoding="utf-8")
            res = self.run_engine("store", "q", "a", cwd=plain)
            self.assertNotEqual(res.returncode, 0, "запись без vault не должна проходить")
            self.assertNotIn("Traceback", res.stderr)
            # И ничего не создаётся рядом с проектом пользователя
            self.assertEqual(list(Path(d).rglob("memory.db")), [])

    def test_nested_dir_does_not_create_second_database(self):
        with tempfile.TemporaryDirectory() as d:
            vault = _make_vault(Path(d))
            nested = vault / "src" / "components" / "ui"
            nested.mkdir(parents=True)
            self.run_engine("store", "nested вопрос", "nested ответ", cwd=nested)
            self.assertEqual(len(list(vault.rglob("memory.db"))), 1)
            self.assertEqual(list(nested.rglob("memory.db")), [])

    def test_configured_vault_wins_over_cwd(self):
        with tempfile.TemporaryDirectory() as d:
            vault = _make_vault(Path(d) / "realvault")
            plain = Path(d) / "work"
            plain.mkdir()
            env = {"LIVING_WIKI_VAULT": str(vault)}
            self.run_engine("store", "q1", "a1", cwd=plain, env_extra=env)
            found = self.run_engine("match", "q1", cwd=plain, env_extra=env)
            self.assertEqual(found.returncode, 0, found.stderr)
            self.assertEqual(list(plain.rglob("memory.db")), [])
            self.assertEqual(len(list(vault.rglob("memory.db"))), 1)


class TestHarvestSubagent(CliTestCase):
    def test_technical_anchor_markdown_is_stripped(self):
        """Регрессия: в тело урока попадало `**: timeout=10`."""
        with tempfile.TemporaryDirectory() as d:
            vault = _make_vault(Path(d))
            report = (
                "### Находки для Живой Вики\n"
                "- **Что сделали**: timeout=10 и отключение WAL\n"
                "- **Первопричина**: Windows держит дескрипторы дольше\n"
                "- **Грабли на будущее**: не включать параллельный запуск\n"
            )
            res = self.run_engine("harvest-subagent", report, cwd=vault)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertNotIn("**:", res.stdout)
            self.assertIn("timeout=10", res.stdout)

            lessons = (vault / "self" / "Lessons-Learned.md").read_text(encoding="utf-8")
            # Проверяем ИМЕННО значение после двоеточия: остатков разметки
            # (`**: `) в нём быть не должно. Формат `- **Что сделали**: ` —
            # легитимная markdown-подпись, поэтому ищем `Заголовок**: мусор`.
            for label in ("Что сделали", "Первопричина", "Грабли на будущее"):
                with self.subTest(label=label):
                    self.assertNotRegex(
                        lessons,
                        rf"\*\*{label}\*\*:\s*\*\*",
                        f"в значение «{label}» попала markdown-разметка",
                    )
            self.assertIn("- **Что сделали**: timeout=10", lessons)
            self.assertIn("- **Первопричина**: Windows", lessons)


class TestOtherCommands(CliTestCase):
    def test_smoke_all_read_commands(self):
        with tempfile.TemporaryDirectory() as d:
            vault = _make_vault(Path(d))
            (vault / "wiki" / "rag").mkdir()
            (vault / "wiki" / "rag" / "index.md").write_text(
                "---\ntitle: RAG\n---\n\ntext", encoding="utf-8"
            )
            for cmd in ("status", "audit", "subjects", "sync", "digest-today", "clean"):
                with self.subTest(cmd=cmd):
                    res = self.run_engine(cmd, cwd=vault)
                    self.assertNotIn("Traceback", res.stderr, f"{cmd} упал")
                    self.assertEqual(res.returncode, 0, f"{cmd}: {res.stderr}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
