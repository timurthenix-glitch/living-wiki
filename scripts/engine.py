#!/usr/bin/env python3
"""
engine.py — Единый контур эффективности Живой Вики (Unified Efficiency Engine).

Объединяет принципы SoL-Pi (NVIDIA Research) и Живой Вики:
  1. Action Fusion (fuse): слияние выполнения команд, авто-фильтрации логов и синхронизации памяти.
  2. ObservationPack (pack / recall): упаковка больших наблюдений в легковесные хэндлы с постраничным чтением.
  3. Evidence-Preserving Reducer (reduce): сжатие диагностических логов с гарантией точных цитат (zero-hallucination).
  4. Semantic Memory (match / store / sync): быстрый поиск решений (>=0.88), гибридный fallback (>=0.35) и уроков в SQLite.
  5. VaultAtlas (status): сверхбыстрый LOD 0 дашборд (~150 токенов) для защиты контекста агента.
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


def _sync_state_file() -> Path:
    return get_cache_root() / "sync_state.json"


def _lessons_path() -> Path:
    return get_cache_root().parent / "self" / "Lessons-Learned.md"


def lessons_changed() -> bool:
    """True, если self/Lessons-Learned.md менялся с момента последнего синка."""
    lessons = _lessons_path()
    if not lessons.exists():
        return False
    try:
        state = json.loads(_sync_state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    return state.get("lessons_mtime") != lessons.stat().st_mtime


def mark_lessons_synced() -> None:
    """Запоминает mtime уроков после успешной синхронизации."""
    lessons = _lessons_path()
    if not lessons.exists():
        return
    try:
        _sync_state_file().write_text(
            json.dumps({"lessons_mtime": lessons.stat().st_mtime}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass  # кэш-подсказка, не критично для работы


def cleanup_cache(max_age_days: int = 14) -> int:
    """
    Удаляет архивы логов и упакованные наблюдения старше max_age_days.
    Кэш иначе растёт бесконечно: reduce/pack пишут файл на каждый вызов.
    """
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    for d in (get_logs_dir(), get_obs_dir()):
        for f in d.iterdir():
            try:
                if f.is_file() and f.stat().st_mtime < cutoff:
                    f.unlink()
                    removed += 1
            except OSError:
                continue
    return removed


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
        # Хэндл подставляется в путь, поэтому принимаем только то, что выдаёт pack():
        # 12 hex-символов. Иначе 'obs_../../..' уводит чтение за пределы .cache/obs.
        if not re.fullmatch(r"[0-9a-f]{12}", clean_hash):
            raise ValueError(f"Некорректный хэндл наблюдения: {handle_or_hash!r}")
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
        re.compile(r"^\s*File \".+\", line \d+", re.IGNORECASE | re.MULTILINE),
        re.compile(r"\b(Error|Exception|AssertionError|TypeError|ValueError|SyntaxError|Fatal|Panic):", re.IGNORECASE),
        re.compile(r"^\s*(FAILED|ERROR)\b", re.IGNORECASE | re.MULTILINE),
        re.compile(r"={3,}\s*(FAILURES|ERRORS)\s*={3,}", re.IGNORECASE),
        re.compile(r"FAILED \(.*failures=\d+.*\)", re.IGNORECASE),
        re.compile(r"npm ERR!", re.IGNORECASE),
        re.compile(r"exit status \d+", re.IGNORECASE),
    ]

    # Нулевые счётчики в сводках («0 failed», «ERRORS: 0», «no errors») — не признак ошибки.
    # Вырезаются перед вторичной эвристикой, иначе зелёная сборка получает статус FAILED.
    ZERO_COUNT_RE = re.compile(
        r"\b(?:"
        r"0\s+(?:fail\w*|errors?|warnings?)"
        r"|(?:fail\w*|errors?|warnings?)\s*[:=]\s*0"
        r"|no\s+(?:errors?|failures?|warnings?)"
        r")\b",
        re.IGNORECASE,
    )

    @staticmethod
    def verify_citation(lines: List[str], line_no: int, content_str: str) -> None:
        """
        Инвариант выжимки: номер строки обязан указывать РОВНО на эту строку источника.
        """
        if not (1 <= line_no <= len(lines)) or lines[line_no - 1] != content_str:
            raise ValueError(
                f"Нарушение инварианта Evidence Reducer: цитата с номером {line_no} "
                f"не совпадает со строкой источника!"
            )

    @classmethod
    def reduce(cls, log_text: str, max_blocks: int = 4, context_lines: int = 2,
               exit_code: Optional[int] = None) -> Dict[str, Any]:
        """
        Сжимает лог до проверяемой выжимки.

        exit_code, если он известен вызывающему, — авторитетный источник статуса;
        текстовая эвристика применяется только когда кода возврата нет.
        """
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
                cls.verify_citation(lines, line_no, content_str)
                verified_evidence.append(f"{line_no:5d} | {content_str}")

        reduced_evidence_str = "\n".join(verified_evidence)

        # Определение статуса. Код возврата процесса — факт, текст лога — догадка,
        # поэтому при известном exit_code текстовая эвристика не применяется вовсе.
        if exit_code is not None:
            status = "FAILED" if exit_code != 0 else "COMPLETED"
            status_source = "exit_code"
        else:
            has_real_errors = any(pat.search(log_text) for pat in cls.ERROR_PATTERNS)
            if not has_real_errors:
                clean_text = cls.ZERO_COUNT_RE.sub("", log_text)
                if re.search(r"\b(FAILED|FAILURES|ERRORS?|CRITICAL)\b", clean_text, re.IGNORECASE):
                    has_real_errors = True
            status = "FAILED" if has_real_errors else "COMPLETED"
            status_source = "heuristic"

        receipt = f"""[EVIDENCE RECEIPT] Log ID: log_{short_hash}
Статус: {status} (источник: {status_source})
Исходный размер: {total_lines} строк ({len(raw_bytes)} байт) -> Выжимка: {len(verified_evidence)} строк
Архив сырого лога: {log_path}

{reduced_evidence_str}
"""
        return {
            "receipt": receipt,
            "log_id": f"log_{short_hash}",
            "status": status,
            "status_source": status_source,
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
            res = EvidenceReducer.reduce(combined_output, exit_code=proc.returncode)
            print(res["receipt"])
        else:
            if combined_output:
                print(combined_output)

        print(f"[FUSE] Завершено с кодом {proc.returncode} за {duration:.2f} сек.")

        if proc.returncode == 0:
            if sync_on_success and lessons_changed():
                try:
                    storage = MemoryStorage()
                    root_dir = get_cache_root().parent
                    count = storage.sync_from_markdown(root_dir)
                    mark_lessons_synced()
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
                    elif line.strip().startswith("- [ ] "):
                        # Поддержка стандартных markdown-чекбоксов активных задач
                        clean = line.strip()[6:].strip()
                        clean = re.sub(r"\[\[.*?\|(.*?)\]\]", r"\1", clean)
                        clean = re.sub(r"\[\[(.*?)\]\]", r"\1", clean)
                        active_goals.append(clean[:80])
                        if len(active_goals) >= 2:
                            break

            lessons_file = self_dir / "Lessons-Learned.md"
            if lessons_file.exists():
                text = lessons_file.read_text(encoding="utf-8", errors="replace")
                for line in text.splitlines():
                    if line.startswith("## ") and not line.startswith("## Связано"):
                        candidate = line[3:].strip()
                        if "архив" in candidate.lower():
                            continue
                        latest_lesson = candidate
                        break

        # 3. Journal stats
        journal_dir = vault_root / "journal"
        latest_journal = "нет записей"
        if journal_dir.is_dir():
            date_re = re.compile(r"^\d{4}-\d{2}-\d{2}\.md$")
            journals = sorted([f.name for f in journal_dir.glob("*.md") if date_re.match(f.name)])
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
    subparsers.add_parser("sync", help="Синхронизировать уроки из vault в память SQLite")

    # 8. clean (ротация кэша)
    p_clean = subparsers.add_parser("clean", help="Удалить старые архивы логов и наблюдений")
    p_clean.add_argument("--days", type=int, default=14, help="Возраст в днях (дефолт 14)")

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
        if args.file:
            if not os.path.exists(args.file):
                parser.error(f"Файл не найден: {args.file}")
            with open(args.file, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            source = Path(args.file).name
        else:
            if sys.stdin.isatty():
                parser.error("Не указан файл и отсутствует входной поток stdin.")
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
        if args.file:
            if not os.path.exists(args.file):
                parser.error(f"Файл не найден: {args.file}")
            with open(args.file, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        else:
            if sys.stdin.isatty():
                parser.error("Не указан файл лога и отсутствует входной поток stdin.")
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
            rec = match_res[0]
            print(rec["answer"])
            sys.exit(0)
        else:
            # Если точного совпадения >= threshold нет, ищем релевантные семантические совпадения
            top_matches = storage.search_top(args.query, top_k=3, min_score=0.35)
            if top_matches:
                best_rec, best_score, best_details = top_matches[0]
                print(f"[SEMANTIC MATCH: {best_score:.2f}] {best_rec['question']}\n")
                print(best_rec["answer"])
                if len(top_matches) > 1:
                    print("\n--- Другие релевантные статьи: ---")
                    for r, sc, _ in top_matches[1:]:
                        print(f"  • [{sc:.2f}] {r['question']}")
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
        mark_lessons_synced()
        print(f"[SYNC] Импортировано уроков из markdown: {count}")
        sys.exit(0)

    # --- clean ---
    elif args.command == "clean":
        removed = cleanup_cache(max_age_days=args.days)
        print(f"[CLEAN] Удалено устаревших файлов кэша (>{args.days} дн.): {removed}")
        sys.exit(0)


if __name__ == "__main__":
    main()
