#!/usr/bin/env python3
"""
storage.py — Локальная база пар «вопрос → ответ» (SQLite) с семантическим кэшем.

Универсальный инструмент для любого агента (Gemini, Claude, Cursor, Windsurf, Copilot, Antigravity и др.):
  - Хранит проверенные решения, ответы и уроки в локальной SQLite-базе.
  - Ищет ответ по алгоритму схожести (text.py: 3-граммы + Жаккар + гардрайлы отрицаний + Query Coverage).
  - При схожести >= 0.88 моментально отдает готовый ответ, экономя токены и время.
  - Поддерживает гибридный поиск search_top(query, top_k=3, min_score=0.35) с бонусом ключевых слов ответа.
  - Синхронизирует уроки из self/Lessons-Learned.md, архивов self/Lessons/*.md и предметных статей wiki/**/*.md.
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

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


# Импортируем алгоритм схожести из того же каталога
try:
    from text import calculate_similarity, normalize_string, extract_words, CONVERSATIONAL_STOP_WORDS
except ImportError:
    from scripts.text import calculate_similarity, normalize_string, extract_words, CONVERSATIONAL_STOP_WORDS


from contextlib import contextmanager


DEFAULT_THRESHOLD = 0.88


def get_default_db_path() -> Path:
    """
    Определяет путь к базе SQLite (через LIVING_WIKI_MEMORY_DB или .cache/memory.db).
    Чистый геттер: директорию создаёт вызывающий (MemoryStorage.__init__).
    """
    env_path = os.environ.get("LIVING_WIKI_MEMORY_DB")
    if env_path:
        return Path(env_path)

    # Ищем корень репозитория/проекта (где лежит AGENTS.md или .git)
    current = Path.cwd().resolve()
    for parent in [current] + list(current.parents):
        if (parent / "AGENTS.md").exists() or (parent / ".git").exists():
            return parent / ".cache" / "memory.db"

    # Fallback: текущая папка
    return current / ".cache" / "memory.db"


class MemoryStorage:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or get_default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA busy_timeout=10000;")
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
        Перезаписывает существующую запись ТОЛЬКО при точном совпадении нормализованного
        вопроса. Прежний порог 0.95 по нечёткой метрике схлопывал разные уроки с похожими
        заголовками («не поднимает порт» / «не поднимает порты») и молча терял один из них.
        """
        with self._get_connection() as conn:
            return self._store_conn(conn, question, answer, tags=tags, agent=agent)

    def _store_conn(self, conn: sqlite3.Connection, question: str, answer: str,
                    tags: str = "", agent: str = "") -> Dict[str, Any]:
        """Тело store в рамках уже открытого соединения (для батчевых вставок)."""
        now = datetime.now(timezone.utc).isoformat()
        clean_text = re.sub(r"^\d{4}-\d{2}-\d{2}\s*[:\-—]?\s*", "", question)
        q_clean = normalize_string(clean_text)

        existing = conn.execute(
            "SELECT id, tags, agent FROM memory_cache WHERE question_clean = ? LIMIT 1",
            (q_clean,)
        ).fetchone() if q_clean else None

        if existing:
            conn.execute("""
                UPDATE memory_cache
                SET answer = ?, tags = ?, agent = ?, updated_at = ?
                WHERE id = ?
            """, (answer, tags or existing["tags"], agent or existing["agent"], now, existing["id"]))
            conn.commit()
            return {
                "id": existing["id"],
                "action": "updated",
                "question": question,
                "similarity": 1.0
            }

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
        Сначала проверяет индекс точного совпадения, затем выполняет перебор.
        Возвращает (best_row_dict, best_score, best_details).
        """
        clean_query = re.sub(r"^\d{4}-\d{2}-\d{2}\s*[:\-—]?\s*", "", query)
        q_clean = normalize_string(clean_query)
        with self._get_connection() as conn:
            if q_clean:
                exact_row = conn.execute(
                    "SELECT * FROM memory_cache WHERE question_clean = ? LIMIT 1",
                    (q_clean,)
                ).fetchone()
                if exact_row:
                    return dict(exact_row), 1.0, {"exact": True, "cosine_3gram": 1.0, "jaccard_words": 1.0, "query_coverage": 1.0, "combined": 1.0}

            candidates = conn.execute("SELECT id, question FROM memory_cache").fetchall()

            if not candidates:
                return None, 0.0, {"reason": "database_empty"}

            best_id = None
            best_score = -1.0
            best_details: Dict[str, Any] = {}

            for row in candidates:
                cand_clean = re.sub(r"^\d{4}-\d{2}-\d{2}\s*[:\-—]?\s*", "", row["question"])
                score, details = calculate_similarity(clean_query, cand_clean)
                if score > best_score:
                    best_score = score
                    best_id = row["id"]
                    best_details = details

            if best_id is None:
                return None, 0.0, {}

            best_row = conn.execute(
                "SELECT * FROM memory_cache WHERE id = ?", (best_id,)
            ).fetchone()

        return (dict(best_row), best_score, best_details) if best_row else (None, 0.0, {})

    def search_top(self, query: str, top_k: int = 3, min_score: float = 0.35) -> List[Tuple[Dict[str, Any], float, Dict[str, Any]]]:
        """
        Возвращает топ-K релевантных совпадений с оценкой не ниже min_score.
        Учитывает заголовок, теги и ключевые токены в теле ответа.
        """
        clean_query = re.sub(r"^\d{4}-\d{2}-\d{2}\s*[:\-—]?\s*", "", query)
        q_words = [w for w in extract_words(clean_query) if w not in CONVERSATIONAL_STOP_WORDS and w != "не"]
        with self._get_connection() as conn:
            candidates = conn.execute("SELECT id, question, tags, answer FROM memory_cache").fetchall()
            if not candidates:
                return []

            scored = []
            for row in candidates:
                cand_clean = re.sub(r"^\d{4}-\d{2}-\d{2}\s*[:\-—]?\s*", "", row["question"])
                full_cand = f"{cand_clean} {row['tags']}"
                score, details = calculate_similarity(clean_query, full_cand)

                if q_words and row["answer"]:
                    ans_lower = row["answer"].lower()
                    matched_ans = sum(1 for qw in q_words if qw in ans_lower)
                    if matched_ans > 0:
                        ans_bonus = min(0.15, 0.05 * matched_ans)
                        score = min(1.0, score + ans_bonus)
                        details["answer_keyword_bonus"] = ans_bonus
                        details["combined"] = round(score, 4)

                if score >= min_score:
                    scored.append((score, row, details))

            scored.sort(key=lambda x: x[0], reverse=True)
            results = []
            for score, row, details in scored[:top_k]:
                results.append((dict(row), score, details))
            return results

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
        Импортирует проверенные уроки из self/Lessons-Learned.md, архивов self/Lessons/*.md
        и предметных статей wiki/**/*.md в кэш SQLite.
        """
        imported = 0
        with self._get_connection() as conn:
            # 1. self/Lessons-Learned.md и self/Lessons/*.md
            lesson_files = [vault_path / "self" / "Lessons-Learned.md"]
            lessons_dir = vault_path / "self" / "Lessons"
            if lessons_dir.is_dir():
                lesson_files.extend(sorted(lessons_dir.glob("*.md")))

            for lf in lesson_files:
                if not lf.exists():
                    continue
                content = lf.read_text(encoding="utf-8", errors="replace")
                clean_content = re.sub(r"^---\n.*?\n---\n?", "", content, flags=re.DOTALL)
                blocks = re.split(r"(?:\n|^)(?=##\s+)", clean_content)
                for block in blocks:
                    lines = [l.strip() for l in block.strip().split("\n") if l.strip()]
                    if not lines or not lines[0].startswith("## "):
                        continue
                    title = lines[0][3:].strip()
                    if title.lower() in {"связано", "уроки", "lessons learned", "related", "архив уроков"}:
                        continue
                    body = "\n".join(lines[1:])
                    if body and len(title) > 3:
                        self._store_conn(conn, question=title, answer=body,
                                         tags="lesson,markdown_sync", agent="sync")
                        imported += 1

            # 2. wiki/**/*.md (предметная база знаний)
            wiki_dir = vault_path / "wiki"
            if wiki_dir.is_dir():
                for wf in sorted(wiki_dir.glob("**/*.md")):
                    if wf.name.endswith(".local.md"):
                        continue
                    content = wf.read_text(encoding="utf-8", errors="replace")
                    clean_content = re.sub(r"^---\n.*?\n---\n?", "", content, flags=re.DOTALL)
                    title_m = re.search(r"^#\s+(.+)$", clean_content, re.MULTILINE)
                    note_title = title_m.group(1).strip() if title_m else wf.stem
                    topic = wf.parent.name
                    blocks = re.split(r"(?:\n|^)(?=##\s+)", clean_content)
                    for block in blocks:
                        lines = [l.strip() for l in block.strip().split("\n") if l.strip()]
                        if not lines or not lines[0].startswith("## "):
                            continue
                        sec_title = lines[0][3:].strip()
                        if sec_title.lower() in {"связано", "related"}:
                            continue
                        body = "\n".join(lines[1:])
                        if body and len(sec_title) > 3:
                            full_q = f"{note_title}: {sec_title}"
                            self._store_conn(conn, question=full_q, answer=body,
                                             tags=f"wiki,{topic},{wf.stem}", agent="wiki_sync")
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
        matched_res = storage.find_match(args.query, threshold=args.threshold)
        if matched_res:
            rec, score, details = matched_res
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
            best_rec, best_score, best_details = storage.search(args.query)
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
                if best_rec and best_score > 0.0:
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
