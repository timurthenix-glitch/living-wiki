#!/usr/bin/env python3
"""
test_memory.py — Модульные тесты для семантической памяти (text.py и storage.py).
"""

import sys
import tempfile
import unittest
from pathlib import Path

from text import (
    calculate_similarity,
    is_similar,
    normalize_string,
    stem_word,
    check_antonym_conflict,
    get_negations
)
from storage import MemoryStorage


class TestTextSimilarity(unittest.TestCase):
    def test_exact_match(self):
        score, det = calculate_similarity("Как настроить git?", "Как настроить git?")
        self.assertEqual(score, 1.0)
        self.assertTrue(det.get("exact"))

    def test_conversational_stop_words_removal(self):
        q1 = "Как настроить git config?"
        q2 = "Подскажи пожалуйста, как настроить git config"
        score, _ = calculate_similarity(q1, q2)
        self.assertAlmostEqual(score, 1.0, places=2)

    def test_negation_guardrail(self):
        q1 = "Можно ли давать собакам шоколад?"
        q2 = "Можно ли не давать собакам шоколад?"
        score, det = calculate_similarity(q1, q2)
        self.assertEqual(score, 0.0)
        self.assertEqual(det.get("conflict"), "negation_mismatch")

    def test_antonym_command_guardrail(self):
        q1 = "Как включить двухфакторную аутентификацию?"
        q2 = "Как выключить двухфакторную аутентификацию?"
        score, det = calculate_similarity(q1, q2)
        self.assertEqual(score, 0.0)
        self.assertEqual(det.get("conflict"), "antonym_command_mismatch")

    def test_russian_inflection_stemming(self):
        w1 = "регулярных"
        w2 = "регулярной"
        self.assertEqual(stem_word(w1), stem_word(w2))
        
        # Предложения с разными падежами
        s1 = "Как настроить расписание регулярных задач в cron"
        s2 = "Как настроить расписание регулярной задачи в cron"
        matched, score, det = is_similar(s1, s2, threshold=0.88)
        self.assertTrue(matched)
        self.assertGreaterEqual(score, 0.88)


    def test_english_negation_contractions(self):
        q1 = "How to run docker?"
        q2 = "Why don't run docker?"
        score, det = calculate_similarity(q1, q2)
        self.assertEqual(score, 0.0)
        self.assertEqual(det.get("conflict"), "negation_mismatch")

    def test_antonym_both_terms_no_false_conflict(self):
        q1 = "В чем разница между включить и выключить кэш?"
        q2 = "Какая разница между включить и выключить кэш?"
        score, det = calculate_similarity(q1, q2)
        self.assertGreaterEqual(score, 0.80)
        self.assertNotIn("conflict", det)

    def test_english_stemming_short_words(self):
        self.assertEqual(stem_word("king"), "king")
        self.assertEqual(stem_word("ring"), "ring")
        self.assertEqual(stem_word("pass"), "pass")
        self.assertEqual(stem_word("passes"), "pass")
        self.assertEqual(stem_word("classes"), "class")


class TestMemoryStorage(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_memory.db"
        self.storage = MemoryStorage(db_path=self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_store_and_match(self):
        self.storage.store(
            question="Как настроить ssh ключ для github?",
            answer="Используй ssh-keygen -t ed25519 -C 'email@example.com'",
            tags="ssh,git"
        )
        
        # Точный поиск
        res = self.storage.find_match("Как настроить ssh ключ для github?")
        self.assertIsNotNone(res)
        rec, score, _ = res
        self.assertIn("ssh-keygen", rec["answer"])
        self.assertGreaterEqual(score, 0.88)
        self.assertEqual(rec["hit_count"], 1)

        # Перефразированный поиск с разговорными стоп-словами
        res2 = self.storage.find_match("Подскажи пожалуйста как настроить ssh ключ github")
        self.assertIsNotNone(res2)
        rec2, score2, _ = res2
        self.assertGreaterEqual(score2, 0.88)
        self.assertEqual(rec2["hit_count"], 2)

    def test_miss_on_unrelated_query(self):
        self.storage.store(
            question="Как приготовить борщ?",
            answer="Свекла, капуста, мясо, бульон"
        )
        res = self.storage.find_match("Как настроить docker container?")
        self.assertIsNone(res)

    def test_update_on_duplicate(self):
        res1 = self.storage.store("Что такое DNS?", "Система доменных имен")
        self.assertEqual(res1["action"], "inserted")
        self.assertEqual(self.storage.list_entries()[0]["hit_count"], 0)
        
        res2 = self.storage.store("Что такое DNS?", "Domain Name System — система доменных имен")
        self.assertEqual(res2["action"], "updated")
        
        entries = self.storage.list_entries()
        self.assertEqual(len(entries), 1)
        self.assertIn("Domain Name System", entries[0]["answer"])
        # Проверяем, что обновление не накрутило hit_count
        self.assertEqual(entries[0]["hit_count"], 0)

    def test_sync_from_markdown_preserves_subsections(self):
        vault_dir = Path(self.temp_dir.name) / "vault"
        self_dir = vault_dir / "self"
        self_dir.mkdir(parents=True, exist_ok=True)
        lessons_file = self_dir / "Lessons-Learned.md"
        lessons_file.write_text("""---
title: Уроки
tags: [урок]
---

## Ошибка синхронизации хранилища
Основное описание ошибки.
### Контекст проблемы
Случилась перегрузка очередей.
### Решение
Добавить экспоненциальный бэкофф.

## Связано
- [[index]]
""", encoding="utf-8")

        count = self.storage.sync_from_markdown(vault_dir)
        self.assertEqual(count, 1)
        entries = self.storage.list_entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["question"], "Ошибка синхронизации хранилища")
        self.assertIn("### Контекст проблемы", entries[0]["answer"])
        self.assertIn("### Решение", entries[0]["answer"])


if __name__ == "__main__":
    unittest.main()
