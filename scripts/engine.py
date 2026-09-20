#!/usr/bin/env python3
"""
engine.py — Единый контур эффективности Живой Вики (Unified Efficiency Engine).

Объединяет принципы SoL-Pi (NVIDIA Research) и Живой Вики:
  1. Action Fusion (fuse): слияние выполнения команд, авто-фильтрации логов и синхронизации памяти.
  2. ObservationPack (pack / recall): упаковка больших наблюдений в легковесные хэндлы с постраничным чтением.
  3. Evidence-Preserving Reducer (reduce): сжатие диагностических логов с гарантией точных цитат (zero-hallucination).
  4. Semantic Memory (match / store / sync): быстрый поиск решений (>=0.88) и уроков в SQLite.
"""

import os
import sys
import re
import json
import time
import hashlib
import argparse
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Tuple, Any

# Подключаем модули памяти
try:
    from storage import MemoryStorage, get_default_db_path
    from text import calculate_similarity, normalize_string
except ImportError:
    from scripts.storage import MemoryStorage, get_default_db_path
    from scripts.text import calculate_similarity, normalize_string


def get_cache_root() -> Path:
    """Возвращает путь к директории .cache проекта."""
    db_path = get_default_db_path()
    cache_dir = db_path.parent
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def get_obs_dir() -> Path:
    """Директория хранения упакованных наблюдений."""
    d = get_cache_root() / "obs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_logs_dir() -> Path:
    """Директория хранения сырых архивированных логов."""
    d = get_cache_root() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


# =====================================================================
# 1. ObservationPack (pack / recall)
# =====================================================================

class ObservationPack:
    @staticmethod
    def pack(content: str, source_name: str = "observation") -> Dict[str, Any]:
        """
        Упаковывает объемный текст в .cache/obs/<hash>.raw,
        формируя компактный дескриптор со структурой и смещениями.
        """
        raw_bytes = content.encode("utf-8")
        full_hash = hashlib.sha256(raw_bytes).hexdigest()
        short_hash = full_hash[:12]
        handle = f"obs_{short_hash}"

        obs_dir = get_obs_dir()
        raw_file = obs_dir / f"{short_hash}.raw"
        meta_file = obs_dir / f"{short_hash}.meta.json"

        # Сохраняем сырой файл
        with open(raw_file, "w", encoding="utf-8") as f:
            f.write(content)

        lines = content.splitlines()
        total_lines = len(lines)
        size_bytes = len(raw_bytes)

        # Извлечение структуры/оглавления (заголовки, функции, ключевые секции)
        outline: List[Dict[str, Any]] = []
        heading_re = re.compile(r"^(#{1,6}\s+.+|def\s+\w+|class\s+\w+|===+\s+.+|---+\s+.+)")
        for idx, line in enumerate(lines, 1):
            m = heading_re.match(line.strip())
            if m:
                outline.append({"line": idx, "title": line.strip()[:100]})
            if len(outline) >= 20:
                break

        preview_lines = lines[:8]
        preview = "\n".join(preview_lines)

        meta = {
            "handle": handle,
            "hash": short_hash,
            "source_name": source_name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "total_lines": total_lines,
            "size_bytes": size_bytes,
            "raw_file": str(raw_file),
            "outline": outline
        }

        with open(meta_file, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        return {
            "handle": handle,
            "hash": short_hash,
            "total_lines": total_lines,
            "size_bytes": size_bytes,
            "raw_file": str(raw_file),
            "outline": outline,
            "preview": preview
        }

    @staticmethod
    def recall(handle_or_hash: str, start: int = 1, count: int = 50) -> Dict[str, Any]:
        """
        Возвращает срез строк из упакованного наблюдения по 1-индексированному смещению.
        """
        clean_hash = handle_or_hash.replace("obs_", "").strip()
        obs_dir = get_obs_dir()
        raw_file = obs_dir / f"{clean_hash}.raw"

        if not raw_file.exists():
            raise FileNotFoundError(f"Наблюдение с хэндлом {handle_or_hash} не найдено в кэше.")

        with open(raw_file, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()

        total_lines = len(lines)
        start_idx = max(1, start)
        end_idx = min(total_lines, start_idx + count - 1)

        if start_idx > total_lines:
            slice_lines = []
        else:
            slice_lines = lines[start_idx - 1:end_idx]

        numbered = [f"{start_idx + i:5d} | {line}" for i, line in enumerate(slice_lines)]

        return {
            "handle": f"obs_{clean_hash}",
            "start": start_idx,
            "end": end_idx,
            "total_lines": total_lines,
            "has_more": end_idx < total_lines,
            "content": "\n".join(numbered)
        }


# =====================================================================
# 2. Evidence-Preserving Reducer (reduce)
# =====================================================================

class EvidenceReducer:
    """
    Редуктор логов с гарантией точных цитат (Zero-Hallucination).
    Выделяет ошибки, трассировки и сводки тестов, проверяет точное совпадение
    каждой строки с оригиналом и сохраняет полный сырой лог.
    """
    ERROR_PATTERNS = [
        re.compile(r"Traceback \(most recent call last\):", re.IGNORECASE),
        re.compile(r"^\s*File \".+\", line \d+", re.IGNORECASE),
        re.compile(r"\b(Error|Exception|AssertionError|TypeError|ValueError|SyntaxError|Fatal|Panic):", re.IGNORECASE),
        re.compile(r"^\s*(FAILED|ERROR)\b", re.IGNORECASE),
        re.compile(r"={3,}\s*(FAILURES|ERRORS)\s*={3,}", re.IGNORECASE),
        re.compile(r"FAILED \(.*failures=\d+.*\)", re.IGNORECASE),
        re.compile(r"npm ERR!", re.IGNORECASE),
        re.compile(r"exit status \d+", re.IGNORECASE),
    ]

    @classmethod
    def reduce(cls, log_text: str, max_blocks: int = 4, context_lines: int = 2) -> Dict[str, Any]:
        raw_bytes = log_text.encode("utf-8")
        full_hash = hashlib.sha256(raw_bytes).hexdigest()
        short_hash = full_hash[:10]
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_filename = f"{ts}_{short_hash}.log"
        log_path = get_logs_dir() / log_filename

        # Архивация сырого лога
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(log_text)

        lines = log_text.splitlines()
        total_lines = len(lines)

        # Поиск ключевых строк
        matched_line_indices = set()
        for idx, line in enumerate(lines):
            for pat in cls.ERROR_PATTERNS:
                if pat.search(line):
                    # Добавляем строку и контекст вокруг неё
                    start_c = max(0, idx - context_lines)
                    end_c = min(total_lines, idx + context_lines + 1)
                    for c_idx in range(start_c, end_c):
                        matched_line_indices.add(c_idx)
                    break

        # Если ошибок не найдено по паттернам, но лог длинный — берем хвост (обычно там результат)
        if not matched_line_indices:
            tail_count = min(15, total_lines)
            start_tail = max(0, total_lines - tail_count)
            for c_idx in range(start_tail, total_lines):
                matched_line_indices.add(c_idx)

        # Объединяем смежные строки в блоки
        sorted_indices = sorted(matched_line_indices)
        blocks: List[List[Tuple[int, str]]] = []
        current_block: List[Tuple[int, str]] = []

        for idx in sorted_indices:
            line_content = lines[idx]
            if not current_block:
                current_block.append((idx + 1, line_content))
            else:
                last_idx = current_block[-1][0] - 1
                if idx == last_idx + 1:
                    current_block.append((idx + 1, line_content))
                else:
                    blocks.append(current_block)
                    current_block = [(idx + 1, line_content)]
        if current_block:
            blocks.append(current_block)

        # Ограничиваем количество блоков
        selected_blocks = blocks[:max_blocks]

        # ПРОВЕРКА ГАРАНТИИ ТОЧНЫХ ЦИТАТ (Zero-Hallucination Guardrail)
        verified_evidence: List[str] = []
        for b in selected_blocks:
            verified_evidence.append(f"--- [Фрагмент строк {b[0][0]}–{b[-1][0]}] ---")
            for line_no, content_str in b:
                # Строгая проверка: цитата обязана побайтово существовать в исходном тексте
                if content_str not in log_text:
                    raise ValueError(f"Нарушение инварианта Evidence Reducer: цитата '{content_str}' отсутствует в источнике!")
                verified_evidence.append(f"{line_no:5d} | {content_str}")

        reduced_evidence_str = "\n".join(verified_evidence)
        status = "FAILED" if any(cls.ERROR_PATTERNS[0].search(log_text) or "FAIL" in log_text.upper() or "ERROR" in log_text.upper() for _ in [1]) else "COMPLETED"

        receipt = f"""[EVIDENCE RECEIPT] Log ID: log_{short_hash}
Статус: {status}
Исходный размер: {total_lines} строк ({len(raw_bytes)} байт) -> Выжимка: {len(verified_evidence)} строк
Архив сырого лога: {log_path}

{reduced_evidence_str}
"""
        return {
            "receipt": receipt,
            "log_id": f"log_{short_hash}",
            "status": status,
            "log_path": str(log_path),
            "total_lines": total_lines,
            "reduced_lines": len(verified_evidence),
            "verified": True
        }


# =====================================================================
# 3. Action Fusion (fuse)
# =====================================================================

class ActionFusion:
    @staticmethod
    def run_command(command: str, sync_on_success: bool = True, log_threshold: int = 35) -> int:
        """
        Выполняет команду в шелле, автоматически сокращает длинный вывод
        через Evidence Reducer и при успехе синхронизирует уроки в память.
        """
        print(f"[FUSE] Запуск: {command}")
        start_time = time.time()

        proc = subprocess.run(command, shell=True, capture_output=True, text=True, errors="replace")
        duration = time.time() - start_time

        combined_output = (proc.stdout + "\n" + proc.stderr).strip()
        lines = combined_output.splitlines()

        if len(lines) > log_threshold:
            print(f"[FUSE] Вывод велик ({len(lines)} строк). Применяется Evidence Reducer:")
            res = EvidenceReducer.reduce(combined_output)
            print(res["receipt"])
        else:
            if combined_output:
                print(combined_output)

        print(f"[FUSE] Завершено с кодом {proc.returncode} за {duration:.2f} сек.")

        if proc.returncode == 0:
            if sync_on_success:
                try:
                    storage = MemoryStorage()
                    root_dir = get_cache_root().parent
                    count = storage.sync_from_markdown(root_dir)
                    if count > 0:
                        print(f"[FUSE:SYNC] Семантическая память обновлена: +{count} уроков.")
                except Exception as e:
                    print(f"[FUSE:SYNC WARN] Не удалось синхронизировать уроки: {e}")
        return proc.returncode


# =====================================================================
# 4. VaultAtlas / Skeleton Map (LOD 0 Status)
# =====================================================================

class VaultAtlas:
    """
    Формирует ультра-компактный Атлас хранилища (LOD 0).
    Предотвращает раздувание контекста от запросов 'чекни хранилище',
    заменяя слепое сканирование файлов компактным дашбордом на ~150 токенов.
    """
    @staticmethod
    def find_vault_root(start_dir: Optional[Path] = None) -> Path:
        current = (start_dir or Path.cwd()).resolve()
        for p in [current] + list(current.parents):
            if (p / "AGENTS.md").exists() or (p / "self").is_dir():
                return p
        return current

    @classmethod
    def get_status(cls, start_dir: Optional[Path] = None) -> Dict[str, Any]:
        vault_root = cls.find_vault_root(start_dir)

        # 1. Wiki stats
        wiki_dir = vault_root / "wiki"
        topics = []
        total_wiki_notes = 0
        if wiki_dir.is_dir():
            for item in wiki_dir.iterdir():
                if item.is_dir() and not item.name.startswith("."):
                    topics.append(item.name)
                    total_wiki_notes += len(list(item.glob("*.md")))

        # 2. Self stats
        self_dir = vault_root / "self"
        active_goals = []
        latest_lesson = "Нет данных"
        if self_dir.is_dir():
            goals_file = self_dir / "Goals.md"
            if goals_file.exists():
                text = goals_file.read_text(encoding="utf-8", errors="replace")
                in_active = False
                for line in text.splitlines():
                    if "## Активные" in line:
                        in_active = True
                        continue
                    if in_active:
                        if line.startswith("## "):
                            break
                        line_s = line.strip()
                        if line_s.startswith("- "):
                            clean = re.sub(r"\[\[.*?\|(.*?)\]\]", r"\1", line_s[2:])
                            clean = re.sub(r"\[\[(.*?)\]\]", r"\1", clean)
                            active_goals.append(clean[:80])
                            if len(active_goals) >= 2:
                                break

            lessons_file = self_dir / "Lessons-Learned.md"
            if lessons_file.exists():
                text = lessons_file.read_text(encoding="utf-8", errors="replace")
                for line in text.splitlines():
                    if line.startswith("## ") and not line.startswith("## Связано"):
                        latest_lesson = line[3:].strip()
                        break

        # 3. Journal stats
        journal_dir = vault_root / "journal"
        latest_journal = "нет записей"
        if journal_dir.is_dir():
            journals = sorted([f.name for f in journal_dir.glob("????-??-??.md")])
            if journals:
                latest_journal = journals[-1]

        # 4. Memory cache stats
        try:
            storage = MemoryStorage()
            mem_stats = storage.stats()
        except Exception:
            mem_stats = {"total_entries": 0, "total_hits": 0}

        report = f"""==================================================
[VAULT ATLAS] Статус хранилища Живой Вики (LOD 0)
Путь: {vault_root}
--------------------------------------------------
* База знаний (wiki/):   {len(topics)} тем ({', '.join(topics) if topics else 'пока нет'}), {total_wiki_notes} заметок
* Контур саморазвития:
  - Активные цели:       {active_goals[0] if active_goals else 'нет активных целей'}
  - Последний урок:      {latest_lesson}
  - Журнал:              {latest_journal} (последняя запись)
* Семантическая память: {mem_stats.get('total_entries', 0)} записей, hits: {mem_stats.get('total_hits', 0)} (порог >= 0.88)
* Оптимизация SoL-Pi:    ObservationPack (.cache/obs), Evidence Reducer (.cache/logs)
==================================================
-> Правило агента: не сканируй файлы целиком. Уточни фокус (цели / уроки / тему) перед углублением."""

        return {
            "report": report,
            "vault_root": str(vault_root),
            "topics": topics,
            "total_wiki_notes": total_wiki_notes,
            "active_goals": active_goals,
            "latest_lesson": latest_lesson,
            "latest_journal": latest_journal,
            "memory_stats": mem_stats
        }


# =====================================================================
# CLI Entrypoint
# =====================================================================

def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Unified Efficiency Engine для Живой Вики (SoL-Pi + Karpathy Wiki)")
    subparsers = parser.add_subparsers(dest="command", help="Команды контура эффективности")

    # 0. status / atlas
    p_status = subparsers.add_parser("status", help="Компактный Атлас хранилища (LOD 0) без расхода контекста")
    p_status.add_argument("--json", action="store_true", help="Вывод в формате JSON")

    # 1. pack
    p_pack = subparsers.add_parser("pack", help="Упаковать большой текст/файл в ObservationPack")
    p_pack.add_argument("file", nargs="?", help="Путь к файлу (если не задан — читается stdin)")
    p_pack.add_argument("--source", default="raw_data", help="Имя источника")

    # 2. recall
    p_recall = subparsers.add_parser("recall", help="Прочитать срез строк из ObservationPack")
    p_recall.add_argument("handle", help="Хэндл наблюдения (obs_<hash> или <hash>)")
    p_recall.add_argument("--start", type=int, default=1, help="Начальная строка (1-индексация)")
    p_recall.add_argument("--lines", type=int, default=50, help="Количество строк")

    # 3. reduce
    p_reduce = subparsers.add_parser("reduce", help="Сжать диагностический лог с верификацией цитат")
    p_reduce.add_argument("file", nargs="?", help="Файл лога (если не задан — читается stdin)")

    # 4. fuse
    p_fuse = subparsers.add_parser("fuse", help="Запустить команду со сжатием логов и авто-синком памяти")
    p_fuse.add_argument("cmd", help="Команда для выполнения")
    p_fuse.add_argument("--no-sync", action="store_true", help="Не синхронизировать память после успешного выполнения")

    # 5. match (семантическая память)
    p_match = subparsers.add_parser("match", help="Поиск ответа в семантической памяти (threshold >= 0.88)")
    p_match.add_argument("query", help="Вопрос или ситуация")
    p_match.add_argument("--threshold", type=float, default=0.88, help="Порог схожести")

    # 6. store (сохранение в память)
    p_store = subparsers.add_parser("store", help="Сохранить решение в память")
    p_store.add_argument("question", help="Вопрос или проблема")
    p_store.add_argument("answer", help="Проверенное решение")
    p_store.add_argument("--tags", default="", help="Теги через запятую")

    # 7. sync (синхронизация уроков)
    p_sync = subparsers.add_parser("sync", help="Синхронизировать уроки из vault в память SQLite")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    # --- status / atlas ---
    if args.command == "status":
        status_data = VaultAtlas.get_status()
        if args.json:
            print(json.dumps(status_data, ensure_ascii=False, indent=2))
        else:
            print(status_data["report"])
        sys.exit(0)

    # --- pack ---
    if args.command == "pack":
        if args.file and os.path.exists(args.file):
            with open(args.file, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            source = Path(args.file).name
        else:
            content = sys.stdin.read()
            source = args.source

        res = ObservationPack.pack(content, source_name=source)
        print(f"[OBSERVATION PACK] Хэндл: {res['handle']}")
        print(f"Размер: {res['size_bytes']} байт | Всего строк: {res['total_lines']}")
        print(f"Файл кэша: {res['raw_file']}")
        if res["outline"]:
            print("Структура / Заголовки:")
            for item in res["outline"][:8]:
                print(f"  {item['line']:5d} | {item['title']}")
        print(f"\nВызов среза: python scripts/engine.py recall {res['handle']} --start 1 --lines 50")
        sys.exit(0)

    # --- recall ---
    elif args.command == "recall":
        try:
            res = ObservationPack.recall(args.handle, start=args.start, count=args.lines)
            print(f"=== {res['handle']} (строки {res['start']}–{res['end']} из {res['total_lines']}) ===")
            print(res["content"])
            if res["has_more"]:
                next_start = res["end"] + 1
                print(f"--- [Есть продолжение: recall {res['handle']} --start {next_start} --lines {args.lines}] ---")
            sys.exit(0)
        except Exception as e:
            print(f"[ERROR] {e}", file=sys.stderr)
            sys.exit(1)

    # --- reduce ---
    elif args.command == "reduce":
        if args.file and os.path.exists(args.file):
            with open(args.file, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        else:
            content = sys.stdin.read()

        if not content.strip():
            print("[WARN] Пустой входной поток для reduce.")
            sys.exit(0)

        res = EvidenceReducer.reduce(content)
        print(res["receipt"])
        sys.exit(0)

    # --- fuse ---
    elif args.command == "fuse":
        code = ActionFusion.run_command(args.cmd, sync_on_success=not args.no_sync)
        sys.exit(code)

    # --- match ---
    elif args.command == "match":
        storage = MemoryStorage()
        match_res = storage.find_match(args.query, threshold=args.threshold)
        if match_res:
            rec, score, details = match_res
            print(rec["answer"])
            sys.exit(0)
        else:
            sys.exit(1)

    # --- store ---
    elif args.command == "store":
        storage = MemoryStorage()
        res = storage.store(args.question, args.answer, tags=args.tags, agent="engine")
        print(f"[STORED] ID: {res['id']}, Действие: {res['action']}")
        sys.exit(0)

    # --- sync ---
    elif args.command == "sync":
        storage = MemoryStorage()
        root_dir = get_cache_root().parent
        count = storage.sync_from_markdown(root_dir)
        print(f"[SYNC] Импортировано уроков из markdown: {count}")
        sys.exit(0)


if __name__ == "__main__":
    main()
