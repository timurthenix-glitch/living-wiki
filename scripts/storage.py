#!/usr/bin/env python3
"""
storage.py — Локальная база пар «вопрос → ответ» (SQLite) с семантическим кэшем.

Универсальный инструмент для любого агента (Gemini, Claude, Cursor, Windsurf, Copilot, Antigravity и др.):
  - Хранит проверенные решения, ответы и уроки в локальной SQLite-базе.
  - Ищет ответ по алгоритму схожести (text.py: 3-граммы + Жаккар + гардрайлы отрицаний).
  - При схожести >= 0.88 моментально отдает готовый ответ, экономя токены и время.
  - Умеет синхронизировать уроки из markdown-файлов vault (self/Lessons-Learned.md).
"""

import os
import re
import sys
import json
import sqlite3
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Tuple, Any

# Импортируем алгоритм схожести из того же каталога
try:
    from text import calculate_similarity, normalize_string
except ImportError:
    from scripts.text import calculate_similarity, normalize_string


from contextlib import contextmanager


DEFAULT_THRESHOLD = 0.88


def get_default_db_path() -> Path:
    """Определяет путь к базе SQLite (через переменную окружения или .cache/memory.db)."""
    env_path = os.environ.get("LIVING_WIKI_MEMORY_DB")
    if env_path:
        return Path(env_path)
    
    # Ищем корень репозитория/проекта (где лежит AGENTS.md или .git)
    current = Path.cwd().resolve()
    for parent in [current] + list(current.parents):
        if (parent / "AGENTS.md").exists() or (parent / ".git").exists():
            cache_dir = parent / ".cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            return cache_dir / "memory.db"
            
    # Fallback: текущая папка
    cache_dir = current / ".cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / "memory.db"


class MemoryStorage:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or get_default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Создает схему таблицы кэша памяти, если её нет."""
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memory_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    question TEXT NOT NULL,
                    question_clean TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    tags TEXT DEFAULT '',
                    agent TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    hit_count INTEGER DEFAULT 0,
                    last_hit_at TEXT
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_clean ON memory_cache(question_clean);")
            conn.commit()

    def store(self, question: str, answer: str, tags: str = "", agent: str = "") -> Dict[str, Any]:
        """
        Сохраняет пару «вопрос → ответ».
        Если уже есть запись с очень высокой схожестью (>= 0.95), обновляет её ответ.
        """
        now = datetime.now(timezone.utc).isoformat()
        q_clean = normalize_string(question)
        
        # Проверяем, нет ли уже дубликата с высокой степенью совпадения
        existing = self.find_match(question, threshold=0.95)
        with self._get_connection() as conn:
            if existing:
                rec, score, _ = existing
                conn.execute("""
                    UPDATE memory_cache
                    SET answer = ?, tags = ?, agent = ?, updated_at = ?
                    WHERE id = ?
                """, (answer, tags or rec["tags"], agent or rec["agent"], now, rec["id"]))
                conn.commit()
                return {
                    "id": rec["id"],
                    "action": "updated",
                    "question": question,
                    "similarity": score
                }
            else:
                cursor = conn.execute("""
                    INSERT INTO memory_cache (
                        question, question_clean, answer, tags, agent, created_at, updated_at, hit_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                """, (question, q_clean, answer, tags, agent, now, now))
                conn.commit()
                return {
                    "id": cursor.lastrowid,
                    "action": "inserted",
                    "question": question
                }

    def search(self, query: str) -> Tuple[Optional[Dict[str, Any]], float, Dict[str, Any]]:
        """
        Ищет наиболее похожий вопрос в базе среди всех записей.
        Возвращает (best_row_dict, best_score, best_details).
        """
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM memory_cache").fetchall()
            
        if not rows:
            return None, 0.0, {"reason": "database_empty"}

        best_row = None
        best_score = -1.0
        best_details = {}

        for row in rows:
            score, details = calculate_similarity(query, row["question"])
            if score > best_score:
                best_score = score
                best_row = row
                best_details = details

        if best_row:
            return dict(best_row), best_score, best_details
        return None, 0.0, {}

    def find_match(self, query: str, threshold: float = DEFAULT_THRESHOLD) -> Optional[Tuple[Dict[str, Any], float, Dict[str, Any]]]:
        """
        Ищет наиболее похожий вопрос в базе.
        Если сходство >= threshold, возвращает (запись, score, details) и инкрементирует hit_count.
        """
        best_dict, best_score, best_details = self.search(query)
        if best_dict and best_score >= threshold:
            # Обновляем статистику попадания
            now = datetime.now(timezone.utc).isoformat()
            with self._get_connection() as conn:
                conn.execute("""
                    UPDATE memory_cache
                    SET hit_count = hit_count + 1, last_hit_at = ?
                    WHERE id = ?
                """, (now, best_dict["id"]))
                conn.commit()

            best_dict["hit_count"] += 1
            best_dict["last_hit_at"] = now
            return best_dict, best_score, best_details

        return None

    def list_entries(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Возвращает список сохраненных записей памяти."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT id, question, answer, tags, agent, hit_count, updated_at FROM memory_cache ORDER BY id DESC LIMIT ?",
                (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def delete(self, entry_id: int) -> bool:
        """Удаляет запись по ID."""
        with self._get_connection() as conn:
            cur = conn.execute("DELETE FROM memory_cache WHERE id = ?", (entry_id,))
            conn.commit()
            return cur.rowcount > 0

    def stats(self) -> Dict[str, Any]:
        """Возвращает статистику кэша памяти."""
        with self._get_connection() as conn:
            total = conn.execute("SELECT COUNT(*) FROM memory_cache").fetchone()[0]
            total_hits = conn.execute("SELECT SUM(hit_count) FROM memory_cache").fetchone()[0] or 0
            
        size_bytes = self.db_path.stat().st_size if self.db_path.exists() else 0
        return {
            "db_path": str(self.db_path),
            "total_entries": total,
            "total_hits": total_hits,
            "size_kb": round(size_bytes / 1024, 2)
        }

    def sync_from_markdown(self, vault_path: Path) -> int:
        """
        Импортирует проверенные уроки из self/Lessons-Learned.md в кэш SQLite.
        Ищет разделы уроков и сохраняет заголовок как вопрос/ситуацию, а тело как решение.
        """
        lessons_file = vault_path / "self" / "Lessons-Learned.md"
        if not lessons_file.exists():
            return 0

        content = lessons_file.read_text(encoding="utf-8")
        imported = 0
        # Разделяем по заголовкам ## или ###
        blocks = re.split(r"\n(?=#{2,4}\s+)", content)
        for block in blocks:
            lines = [l.strip() for l in block.strip().split("\n") if l.strip()]
            if not lines:
                continue
            title = lines[0].lstrip("#- ").strip()
            # Пропускаем служебные заголовки вроде "Связано"
            if title.lower() in {"связано", "уроки", "lessons learned", "related"}:
                continue
            body = "\n".join(lines[1:])
            if body and len(title) > 5:
                self.store(question=title, answer=body, tags="lesson,markdown_sync", agent="sync")
                imported += 1

        return imported


def main():
    parser = argparse.ArgumentParser(description="Семантическая память и кэш ответов (SQLite)")
    subparsers = parser.add_subparsers(dest="command", help="Команды")

    # match
    match_parser = subparsers.add_parser("match", help="Поиск ответа по вопросу")
    match_parser.add_argument("query", type=str, help="Текст вопроса или задачи")
    match_parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD, help=f"Порог схожести (дефолт {DEFAULT_THRESHOLD})")
    match_parser.add_argument("--json", action="store_true", help="Вывод в JSON")
    match_parser.add_argument("--verbose", "-v", action="store_true", help="Подробный вывод")

    # store
    store_parser = subparsers.add_parser("store", help="Сохранение пары вопрос -> ответ")
    store_parser.add_argument("question", type=str, help="Вопрос или ситуация")
    store_parser.add_argument("answer", type=str, help="Ответ или решение")
    store_parser.add_argument("--tags", type=str, default="", help="Теги через запятую")
    store_parser.add_argument("--agent", type=str, default="", help="Имя агента")

    # list
    list_parser = subparsers.add_parser("list", help="Список записей памяти")
    list_parser.add_argument("--limit", type=int, default=20, help="Максимальное число записей")

    # sync
    sync_parser = subparsers.add_parser("sync", help="Синхронизация из self/Lessons-Learned.md")
    sync_parser.add_argument("--vault", type=str, default=".", help="Путь к vault")

    # stats
    subparsers.add_parser("stats", help="Статистика кэша")

    # delete
    del_parser = subparsers.add_parser("delete", help="Удалить запись по ID")
    del_parser.add_argument("id", type=int, help="ID записи")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    storage = MemoryStorage()

    if args.command == "match":
        best_rec, best_score, best_details = storage.search(args.query)
        if best_rec and best_score >= args.threshold:
            # Инкрементируем счетчик попаданий
            matched_res = storage.find_match(args.query, threshold=args.threshold)
            rec, score, details = matched_res if matched_res else (best_rec, best_score, best_details)
            if args.json:
                print(json.dumps({
                    "matched": True,
                    "similarity": score,
                    "id": rec["id"],
                    "question": rec["question"],
                    "answer": rec["answer"],
                    "tags": rec["tags"],
                    "hit_count": rec["hit_count"],
                    "details": details
                }, ensure_ascii=False, indent=2))
            elif args.verbose:
                print(f"[HIT] Схожесть: {score:.4f} >= {args.threshold}")
                print(f"Исходный вопрос в памяти: {rec['question']}")
                print(f"Метрики: {details}")
                print(f"Ответ:\n{rec['answer']}")
            else:
                # Стандартный режим для агентов: чистый ответ в stdout
                print(rec["answer"])
            sys.exit(0)
        else:
            if args.json:
                print(json.dumps({
                    "matched": False,
                    "query": args.query,
                    "threshold": args.threshold,
                    "best_similarity": best_score,
                    "best_candidate": best_rec["question"] if best_rec else None,
                    "details": best_details
                }, ensure_ascii=False, indent=2))
            elif args.verbose:
                print(f"[MISS] Нет совпадений с порогом >= {args.threshold}")
                if best_rec:
                    print(f"Ближайший кандидат (скор {best_score:.4f}): '{best_rec['question']}'")
                    print(f"Детали: {best_details}")
            sys.exit(1)

    elif args.command == "store":
        res = storage.store(args.question, args.answer, tags=args.tags, agent=args.agent)
        print(f"[STORED] ID: {res['id']}, Действие: {res['action']}")
        sys.exit(0)

    elif args.command == "list":
        entries = storage.list_entries(limit=args.limit)
        if not entries:
            print("База памяти пуста.")
        else:
            for e in entries:
                print(f"[{e['id']}] Q: {e['question']} | Hits: {e['hit_count']} | Updated: {e['updated_at']}")
                print(f"     A: {e['answer'][:100]}...")
        sys.exit(0)

    elif args.command == "sync":
        count = storage.sync_from_markdown(Path(args.vault).resolve())
        print(f"[SYNC] Импортировано уроков из markdown: {count}")
        sys.exit(0)

    elif args.command == "stats":
        stats = storage.stats()
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        sys.exit(0)

    elif args.command == "delete":
        ok = storage.delete(args.id)
        if ok:
            print(f"[DELETED] Запись {args.id} удалена.")
            sys.exit(0)
        else:
            print(f"[ERROR] Запись {args.id} не найдена.")
            sys.exit(1)


if __name__ == "__main__":
    main()
