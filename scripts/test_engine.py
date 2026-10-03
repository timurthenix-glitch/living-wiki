#!/usr/bin/env python3
"""
test_engine.py — Модульные тесты для единого контура эффективности (engine.py).
"""

import re
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
        # несуществующий, но валидный по форме хэндл (12 hex)
        with self.assertRaises(FileNotFoundError):
            ObservationPack.recall("obs_abcdef123456")

    def test_recall_rejects_path_traversal(self):
        # Хэндл подставляется в путь — всё, что не 12 hex, должно отбиваться до обращения к ФС
        for bad in ("obs_../../../../etc/passwd", "../secrets", "obs_ZZZZ", "obs_abc"):
            with self.assertRaises(ValueError):
                ObservationPack.recall(bad)


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

    def test_verify_citation_rejects_shifted_line_number(self):
        # Инвариант обязан падать на сбитой нумерации — это единственный реальный
        # класс ошибок редуктора (прежняя проверка «строка есть в тексте» была тавтологией).
        lines = ["первая", "вторая", "третья"]
        EvidenceReducer.verify_citation(lines, 2, "вторая")  # корректная цитата — молча
        with self.assertRaises(ValueError):
            EvidenceReducer.verify_citation(lines, 3, "вторая")  # off-by-one
        with self.assertRaises(ValueError):
            EvidenceReducer.verify_citation(lines, 0, "первая")  # выход за границы
        with self.assertRaises(ValueError):
            EvidenceReducer.verify_citation(lines, 99, "третья")

    def test_receipt_line_numbers_match_source(self):
        # Сквозная проверка: каждая пронумерованная цитата в квитанции соответствует источнику
        res = EvidenceReducer.reduce(self.simulated_log)
        self.assertTrue(res["verified"])
        src = self.simulated_log.splitlines()
        quoted = 0
        for row in res["receipt"].splitlines():
            m = re.match(r"^\s*(\d+) \| (.*)$", row)
            if m:
                quoted += 1
                self.assertEqual(src[int(m.group(1)) - 1], m.group(2))
        self.assertGreater(quoted, 0)

    def test_reduce_status_with_zero_failures_is_completed(self):
        # Лог с успешным результатом, где слово fail встречается только как 0 failed
        successful_log = "Running tests...\nResults: 15 passed, 0 failed, 0 errors in 1.2s\nDone."
        res = EvidenceReducer.reduce(successful_log)
        self.assertEqual(res["status"], "COMPLETED")

    def test_reduce_status_with_zero_errors_colon_is_completed(self):
        # Регрессия: 'ERRORS: 0' раньше давало ложный FAILED на зелёной сборке
        successful_log = "build ok\nNo issues. ERRORS: 0\nDone."
        res = EvidenceReducer.reduce(successful_log)
        self.assertEqual(res["status"], "COMPLETED")
        self.assertEqual(res["status_source"], "heuristic")

    def test_exit_code_overrides_heuristic(self):
        # Код возврата — факт: лог со словом Error, но exit_code=0 → COMPLETED
        noisy_ok = "Инициализация...\nError: (это часть имени теста)\nвсё прошло"
        res = EvidenceReducer.reduce(noisy_ok, exit_code=0)
        self.assertEqual(res["status"], "COMPLETED")
        self.assertEqual(res["status_source"], "exit_code")

        # И наоборот: чистый лог, но ненулевой код → FAILED
        res2 = EvidenceReducer.reduce("всё тихо\nготово", exit_code=2)
        self.assertEqual(res2["status"], "FAILED")
        self.assertEqual(res2["status_source"], "exit_code")


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


class TestStoreDedup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = MemoryStorage(db_path=Path(self.tmp.name) / "dedup.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_near_duplicate_titles_stay_separate(self):
        # Регрессия: порог 0.95 по нечёткой метрике схлопывал разные уроки в один
        a = self.storage.store("Docker compose не поднимает порт", "Решение А")
        b = self.storage.store("Docker compose не поднимает порты", "Решение Б")
        self.assertEqual(a["action"], "inserted")
        self.assertEqual(b["action"], "inserted")
        self.assertNotEqual(a["id"], b["id"])
        self.assertEqual(len(self.storage.list_entries()), 2)

    def test_exact_question_updates_in_place(self):
        a = self.storage.store("Как собрать клиент", "Старый ответ")
        b = self.storage.store("как  собрать   клиент", "Новый ответ")  # та же нормализация
        self.assertEqual(b["action"], "updated")
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(len(self.storage.list_entries()), 1)


class TestVaultAtlas(unittest.TestCase):
    def test_get_status_on_empty_dir(self):
        from engine import VaultAtlas
        # Пустой каталог без wiki/ и self/ не должен ронять атлас
        with tempfile.TemporaryDirectory() as d:
            status_data = VaultAtlas.get_status(start_dir=Path(d))
            self.assertIn("[VAULT ATLAS]", status_data["report"])
            self.assertEqual(status_data["topics"], [])

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
