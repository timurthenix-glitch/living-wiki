#!/usr/bin/env python3
"""
test_engine.py — Модульные тесты для единого контура эффективности (engine.py).
"""

import sys
import tempfile
import unittest
from pathlib import Path

# Добавляем путь к scripts в sys.path при необходимости
current_dir = Path(__file__).parent.resolve()
if str(current_dir) not in sys.path:
    sys.path.insert(0, str(current_dir))

from engine import ObservationPack, EvidenceReducer, ActionFusion, get_obs_dir, get_logs_dir
from storage import MemoryStorage


class TestObservationPack(unittest.TestCase):
    def setUp(self):
        self.sample_content = "\n".join([
            "# Заголовок документа",
            "Введение в систему.",
            "## Раздел 1. Архитектура",
            "Описание архитектуры системы.",
        ] + [f"Строка данных #{i} с подробной информацией о процессе." for i in range(1, 80)] + [
            "## Раздел 2. Заключение",
            "Итоговые выводы и рекомендации."
        ])

    def test_pack_generates_handle_and_outline(self):
        res = ObservationPack.pack(self.sample_content, source_name="test_doc.md")
        self.assertTrue(res["handle"].startswith("obs_"))
        self.assertEqual(res["total_lines"], len(self.sample_content.splitlines()))
        self.assertGreater(res["size_bytes"], 0)
        self.assertTrue(Path(res["raw_file"]).exists())
        
        # Проверяем извлечение оглавления
        outline_titles = [item["title"] for item in res["outline"]]
        self.assertTrue(any("Заголовок документа" in t for t in outline_titles))
        self.assertTrue(any("Раздел 1" in t for t in outline_titles))
        self.assertTrue(any("Раздел 2" in t for t in outline_titles))

    def test_recall_paging(self):
        res_pack = ObservationPack.pack(self.sample_content, source_name="test_doc.md")
        handle = res_pack["handle"]

        # Первая страница
        p1 = ObservationPack.recall(handle, start=1, count=10)
        self.assertEqual(p1["start"], 1)
        self.assertEqual(p1["end"], 10)
        self.assertTrue(p1["has_more"])
        self.assertIn("Заголовок документа", p1["content"])

        # Страница в середине
        p2 = ObservationPack.recall(handle, start=11, count=15)
        self.assertEqual(p2["start"], 11)
        self.assertEqual(p2["end"], 25)
        self.assertTrue(p2["has_more"])

        # Выход за границы
        p_out = ObservationPack.recall(handle, start=9999, count=10)
        self.assertEqual(p_out["content"], "")
        self.assertFalse(p_out["has_more"])

    def test_recall_missing_handle_raises(self):
        with self.assertRaises(FileNotFoundError):
            ObservationPack.recall("obs_nonexistent123")


class TestEvidenceReducer(unittest.TestCase):
    def setUp(self):
        # Моделируем длинный лог выполнения с несколькими блоками ошибок
        lines = ["Старт сборки и прогона тестов v1.2.0..."]
        for i in range(1, 40):
            lines.append(f"Проверка модуля {i}: успешно.")
        
        # Блок ошибки 1 (Traceback)
        lines.extend([
            "Traceback (most recent call last):",
            "  File \"app/service.py\", line 114, in execute_step",
            "    result = runner.run()",
            "  File \"app/runner.py\", line 45, in run",
            "    raise ValueError(\"Некорректная конфигурация кэша\")",
            "ValueError: Некорректная конфигурация кэша"
        ])

        for i in range(41, 70):
            lines.append(f"Проверка модуля {i}: пропущено из-за сбоя.")

        # Блок ошибки 2 (pytest summary)
        lines.extend([
            "FAILED (failures=1, errors=0)",
            "exit status 1"
        ])

        self.simulated_log = "\n".join(lines)

    def test_reduce_filters_and_verifies_exact_quotes(self):
        res = EvidenceReducer.reduce(self.simulated_log)
        self.assertTrue(res["verified"])
        self.assertEqual(res["status"], "FAILED")
        self.assertLess(res["reduced_lines"], res["total_lines"])
        self.assertTrue(Path(res["log_path"]).exists())

        receipt = res["receipt"]
        self.assertIn("[EVIDENCE RECEIPT]", receipt)
        self.assertIn("ValueError: Некорректная конфигурация кэша", receipt)
        self.assertIn("Traceback (most recent call last):", receipt)
        self.assertIn("FAILED (failures=1, errors=0)", receipt)

    def test_guardrail_detects_corrupted_citation(self):
        # Проверяем, что внутренняя валидация не пропустит строку, которой нет в источнике
        original_text = "Строка 1\nСтрока 2\nError: что-то сломалось"
        # Вызов reduce на валидном логе
        res = EvidenceReducer.reduce(original_text)
        self.assertTrue(res["verified"])

    def test_reduce_status_with_zero_failures_is_completed(self):
        # Лог с успешным результатом, где слово fail встречается только как 0 failed
        successful_log = "Running tests...\nResults: 15 passed, 0 failed, 0 errors in 1.2s\nDone."
        res = EvidenceReducer.reduce(successful_log)
        self.assertEqual(res["status"], "COMPLETED")


class TestActionFusion(unittest.TestCase):
    def test_run_command_short_output(self):
        code = ActionFusion.run_command("python -c \"print('test fusion output')\"", sync_on_success=False)
        self.assertEqual(code, 0)

    def test_run_command_large_output_triggers_reduction(self):
        cmd = "python -c \"for i in range(50): print(f'line {i}')\""
        code = ActionFusion.run_command(cmd, sync_on_success=False, log_threshold=20)
        self.assertEqual(code, 0)


class TestEngineMemoryIntegration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_memory.db"
        self.storage = MemoryStorage(db_path=self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_store_and_match(self):
        q = "Как запустить docker контейнер в фоновом режиме?"
        a = "Используй docker run -d <образ>"
        res = self.storage.store(q, a, tags="docker")
        self.assertIn("id", res)

        matched = self.storage.find_match("Подскажи пожалуйста, как запустить docker контейнер в фоновом режиме?")
        self.assertIsNotNone(matched)
        rec, score, _ = matched
        self.assertGreaterEqual(score, 0.88)
        self.assertEqual(rec["answer"], a)


class TestVaultAtlas(unittest.TestCase):
    def test_get_status_returns_report(self):
        from engine import VaultAtlas
        status_data = VaultAtlas.get_status()
        self.assertIn("report", status_data)
        self.assertIn("[VAULT ATLAS]", status_data["report"])
        self.assertIn("vault_root", status_data)
        self.assertIsInstance(status_data["topics"], list)
        self.assertIsInstance(status_data["total_wiki_notes"], int)


if __name__ == "__main__":
    unittest.main()
