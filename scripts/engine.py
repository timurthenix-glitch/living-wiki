#!/usr/bin/env python3
"""
engine.py — Единый контур эффективности Живой Вики (Unified Efficiency Engine).

Объединяет принципы SoL-Pi (NVIDIA Research) и Живой Вики:
  1. Action Fusion (fuse): слияние выполнения команд, авто-фильтрации логов и синхронизации памяти.
  2. ObservationPack (pack / recall): упаковка больших наблюдений в легковесные хэндлы с постраничным чтением.
  3. Evidence-Preserving Reducer (reduce): сжатие диагностических логов с гарантией точных цитат (zero-hallucination).
  4. Semantic Memory (match / store / sync): быстрый поиск решений (>=0.88), гибридный fallback (>=0.35) и уроков в SQLite.
  5. VaultAtlas (status): сверхбыстрый LOD 0 дашборд (~150 токенов) для защиты контекста агента.
  6. SubjectExtractor (subjects): детерминированное извлечение тем и кандидатов в wiki/ (Zero-LLM Topic Indexing).
  7. DailyRollup (digest-today): детерминированная сборка дневной активности в журнал без расхода токенов.
  8. MemoryAuditor (audit): панель аудита контекста, бюджета токенов и prompt-кэша памяти.
"""

import os
import sys
import re
import json
import time
import hashlib
import argparse
import subprocess
from datetime import datetime, timezone, date
from pathlib import Path
from collections import Counter
from typing import Optional, List, Dict, Tuple, Any, Set

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
# 0. Session Recorder
# =====================================================================

class SessionRecorder:
    """Компактный идемпотентный буфер рабочей сессии.

    В .cache хранится только незавершенное состояние. В vault попадает одна
    итоговая запись за день и одна строка хронологии, без записи каждого tool call.
    """

    MAX_EVENTS = 80
    MAX_EVENT_CHARS = 1200
    MAX_JOURNAL_CHARS = 10000

    def __init__(self, root: Optional[Path] = None):
        self.root = (root or VaultAtlas.find_vault_root()).resolve()
        self.cache_path = self.root / ".cache" / "session.json"

    def _load(self) -> Dict[str, Any]:
        if not self.cache_path.exists():
            return {}
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"Не удалось прочитать состояние сессии: {exc}") from exc
        if not isinstance(data, dict):
            raise RuntimeError("Состояние сессии повреждено: ожидался JSON-объект")
        return data

    def start(self) -> Dict[str, Any]:
        state = self._load()
        if state.get("started_at") and not state.get("closed_at"):
            return state
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "cwd": str(Path.cwd().resolve()),
            "root": str(self.root),
            "events": [],
        }
        self.cache_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        return state

    def record(self, kind: str, summary: str, detail: str = "", files: Optional[List[str]] = None) -> Dict[str, Any]:
        state = self.start()
        summary = " ".join(summary.split()).strip()
        detail = detail.strip()
        if not summary:
            raise ValueError("Нельзя записать пустое событие")
        event = {
            "kind": kind.strip() or "work",
            "summary": summary[:self.MAX_EVENT_CHARS],
            "detail": detail[:self.MAX_EVENT_CHARS],
            "files": sorted(set(files or []))[:12],
            "at": datetime.now(timezone.utc).isoformat(),
        }
        events = state.setdefault("events", [])
        fingerprint = (event["kind"], event["summary"], event["detail"], tuple(event["files"]))
        if any((e.get("kind"), e.get("summary"), e.get("detail"), tuple(e.get("files", []))) == fingerprint for e in events):
            return {"action": "duplicate", "event": event}
        events.append(event)
        state["events"] = events[-self.MAX_EVENTS:]
        self.cache_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"action": "recorded", "event": event}

    def close(self, lesson: Optional[str] = None) -> Dict[str, Any]:
        state = self._load()
        events = state.get("events", [])
        if not events:
            return {"action": "empty", "message": "Содержательных событий нет; запись не создана."}
        today = datetime.now().date().isoformat()
        journal_dir = self.root / "journal"
        journal_dir.mkdir(parents=True, exist_ok=True)
        journal_path = journal_dir / f"{today}.md"
        summaries = []
        for event in events:
            line = f"- **{event.get('kind', 'work')}**: {event.get('summary', '')}"
            if event.get("detail"):
                line += f" — {event['detail']}"
            if event.get("files"):
                line += f" (`{', '.join(event['files'])}`)"
            summaries.append(line)
        block = (
            f"\n## Сессия {datetime.now().strftime('%H:%M')} UTC\n"
            + "\n".join(summaries)
            + "\n"
        )
        if journal_path.exists():
            existing = journal_path.read_text(encoding="utf-8")
            if block.strip() not in existing:
                journal_path.write_text((existing.rstrip() + "\n" + block).strip() + "\n", encoding="utf-8")
        else:
            journal_path.write_text(
                f"---\ntitle: Журнал — {today}\ntags: [журнал]\ncreated: {today}\nupdated: {today}\nstatus: факт\n---\n"
                + block + "\n## Связано\n- [[../index]]\n",
                encoding="utf-8",
            )
        log_path = self.root / "log.md"
        log_line = f"{today} — сессия: {len(events)} событий зафиксировано в [[journal/{today}]].\n"
        if log_path.exists():
            log_text = log_path.read_text(encoding="utf-8")
            if log_line not in log_text:
                log_path.write_text(log_text.rstrip() + "\n" + log_line, encoding="utf-8")
        else:
            log_path.write_text(log_line, encoding="utf-8")
        lesson_path = None
        if lesson and lesson.strip():
            lesson_path = self.root / "self" / "Lessons-Learned.md"
            lesson_path.parent.mkdir(parents=True, exist_ok=True)
            lesson_line = f"\n## {today}: урок\n- {lesson.strip()}\n- Источник: [[../journal/{today}]]\n"
            existing = lesson_path.read_text(encoding="utf-8") if lesson_path.exists() else (
                f"---\ntitle: Lessons Learned\ntags: [урок]\ncreated: {today}\nupdated: {today}\nstatus: факт\n---\n"
            )
            if lesson.strip() not in existing:
                lesson_path.write_text(existing.rstrip() + lesson_line, encoding="utf-8")
        state["closed_at"] = datetime.now(timezone.utc).isoformat()
        state["events"] = []
        self.cache_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"action": "closed", "journal": str(journal_path), "lesson": str(lesson_path) if lesson_path else None}


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

    @staticmethod
    def configured_vault_root() -> Optional[Path]:
        candidates = []
        env_path = os.environ.get("LIVING_WIKI_VAULT")
        if env_path:
            candidates.append(Path(env_path).expanduser())
        candidates.extend([
            Path.home() / ".living-wiki-path",
            Path.home() / ".living-wiki" / "vault-path",
        ])
        for candidate in candidates:
            if candidate.is_file():
                try:
                    candidate = Path(candidate.read_text(encoding="utf-8").strip()).expanduser()
                except OSError as exc:
                    raise RuntimeError(f"Не удалось прочитать путь центрального vault: {exc}") from exc
            if candidate.is_dir():
                return candidate.resolve()
        return None

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
        lessons_count = 0
        lessons_chars = 0
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
                lessons_chars = len(text)
                for line in text.splitlines():
                    if line.startswith("## ") and not line.startswith("## Связано"):
                        candidate = line[3:].strip()
                        if "архив" in candidate.lower():
                            continue
                        lessons_count += 1
                        if latest_lesson == "Нет данных":
                            latest_lesson = candidate

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
            db_path = os.environ.get("LIVING_WIKI_MEMORY_DB")
            storage = MemoryStorage(Path(db_path) if db_path else vault_root / ".cache" / "memory.db")
            mem_stats = storage.stats()
        except Exception:
            mem_stats = {"total_entries": 0, "total_hits": 0}

        # 5. Аномалии — дашборд обязан ловить дыру, а не только описывать состояние.
        # Молчаливый «Нет данных» там, где данных не может не быть, — это баг контура.
        anomalies: List[str] = []
        total_entries = mem_stats.get("total_entries", 0)
        total_hits = mem_stats.get("total_hits", 0)
        journal_count = 0
        if journal_dir.is_dir():
            date_re_j = re.compile(r"^\d{4}-\d{2}-\d{2}\.md$")
            journal_count = len([f for f in journal_dir.glob("*.md") if date_re_j.match(f.name)])

        if total_entries >= 50 and total_hits == 0:
            anomalies.append(
                f"память не читается: {total_entries} записей, hits = 0 — "
                f"контур пишется, но `engine.py match` ни разу не вызывался"
            )
        if journal_count >= 1 and (lessons_count == 0 or lessons_chars < 100):
            anomalies.append(
                f"дистилляция не идёт: журнал на {journal_count} записей, "
                f"а активных уроков {lessons_count} — уроки оседают в log.md/journal, а не в self/"
            )
        if total_entries == 0 and (lessons_count > 0 or total_wiki_notes > 0):
            anomalies.append(
                "память пуста при непустом vault — нужен `python scripts/engine.py sync`"
            )

        reviews_dir = vault_root / "self" / "Reviews"
        if reviews_dir.is_dir():
            review_files = sorted(f.name for f in reviews_dir.glob("*.md"))
            if not review_files:
                if journal_count >= 7:
                    anomalies.append("Review не делался ни разу при живом журнале — см. раздел 4.7")
            else:
                m = re.search(r"(\d{4}-\d{2}-\d{2})", review_files[-1])
                if m:
                    try:
                        last_review = date.fromisoformat(m.group(1))
                        age_days = (date.today() - last_review).days
                        if age_days >= 7:
                            anomalies.append(f"просрочен Review: последний {m.group(1)} ({age_days} дн. назад)")
                    except ValueError:
                        pass

        anomaly_block = ""
        if anomalies:
            anomaly_block = "\n\n*** ВНИМАНИЕ — АНОМАЛИИ КОНТУРА ***\n" + "\n".join(f"!!! {a}" for a in anomalies)

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
=================================================={anomaly_block}
-> Правило агента: не сканируй файлы целиком. Уточни фокус (цели / уроки / тему) перед углублением."""

        return {
            "report": report,
            "vault_root": str(vault_root),
            "topics": topics,
            "total_wiki_notes": total_wiki_notes,
            "active_goals": active_goals,
            "latest_lesson": latest_lesson,
            "lessons_count": lessons_count,
            "latest_journal": latest_journal,
            "journal_count": journal_count,
            "anomalies": anomalies,
            "memory_stats": mem_stats
        }



# =====================================================================
# 5. SubjectExtractor (Zero-LLM Topic Indexing)
# =====================================================================

class SubjectExtractor:
    """
    Детерминированное извлечение тем и сущностей (Zero-LLM Topic Indexing)
    по принципу hermes-memory-wiki.
    Парсит файлы vault регулярными выражениями:
      1. CamelCase термины: ModelContextProtocol, ObservationPack
      2. Термины в кавычках: «...», "...", '...'
      3. Wikilinks: [[тема]]
      4. Пути и slash-команды: scripts/engine.py, /plugin
    Выявляет часто встречающиеся сущности и кандидатов на создание статей в wiki/.
    """
    STOP_WORDS: Set[str] = {
        "true", "false", "none", "self", "class", "def", "import", "from",
        "return", "print", "open", "read", "write", "path", "file", "test",
        "date", "title", "tags", "created", "updated", "status", "факт",
        "связано", "введение", "заключение", "активные", "цели", "уроки",
        "журнал", "правило", "база", "контур", "память", "архив", "статус"
    }

    @classmethod
    def extract_from_text(cls, text: str) -> Dict[str, int]:
        counts: Counter = Counter()

        # 1. CamelCase
        camel_matches = re.findall(r"\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+\b", text)
        for m in camel_matches:
            if m.lower() not in cls.STOP_WORDS and len(m) >= 4:
                counts[m] += 1

        # 2. Wikilinks [[target|label]] or [[target]]
        wikilink_matches = re.findall(r"\[\[([^\]|#]+)(?:\|[^\]]+)?\]\]", text)
        for w in wikilink_matches:
            clean = Path(w.strip()).stem
            if clean and clean.lower() not in cls.STOP_WORDS and len(clean) >= 2:
                counts[clean] += 1

        # 3. Quoted phrases: «term», "term", 'term'
        # Используем ord codes для кавычек-ёлочек во избежание проблем с кодировкой
        quote_chars = chr(171) + chr(187) + "\"\'`"
        quoted_matches = re.findall(r"[" + re.escape(quote_chars) + r"]([A-Za-zА-Яа-я0-9_\-\.\s]{3,35})[" + re.escape(quote_chars) + r"]", text)
        for q in quoted_matches:
            q_clean = q.strip()
            if not re.match(r"^\d+[\d\-\.\s]*$", q_clean) and q_clean.lower() not in cls.STOP_WORDS:
                if len(q_clean.split()) <= 4:
                    counts[q_clean] += 1

        # 4. Slash-commands
        slash_matches = re.findall(r"(?<!\w)/([a-z0-9\-]{3,25})\b", text)
        for s in slash_matches:
            if s.lower() not in cls.STOP_WORDS:
                counts[f"/{s}"] += 1

        return dict(counts)

    @classmethod
    def scan_vault(cls, vault_root: Optional[Path] = None, min_count: int = 2) -> Dict[str, Any]:
        from storage import get_default_db_path
        # Определение корня хранилища
        current = (vault_root or Path.cwd()).resolve()
        for p in [current] + list(current.parents):
            if (p / "AGENTS.md").exists() or (p / "self").is_dir():
                root = p
                break
        else:
            root = current

        total_counts: Counter = Counter()
        entity_sources: Dict[str, Set[str]] = {}

        existing_wiki_topics: Set[str] = set()
        wiki_dir = root / "wiki"
        if wiki_dir.is_dir():
            for t in wiki_dir.iterdir():
                if t.is_dir() and not t.name.startswith("."):
                    existing_wiki_topics.add(t.name.lower())
                elif t.is_file() and t.suffix == ".md":
                    existing_wiki_topics.add(t.stem.lower())

        scan_files: List[Path] = []
        log_file = root / "log.md"
        if log_file.is_file():
            scan_files.append(log_file)

        for folder_name in ("journal", "self", "raw"):
            folder = root / folder_name
            if folder.is_dir():
                for f in folder.glob("*.md"):
                    scan_files.append(f)

        for sf in scan_files:
            try:
                rel_name = sf.relative_to(root).as_posix()
            except ValueError:
                rel_name = sf.name
            try:
                content = sf.read_text(encoding="utf-8", errors="replace")
                counts = cls.extract_from_text(content)
                for ent, cnt in counts.items():
                    total_counts[ent] += cnt
                    if ent not in entity_sources:
                        entity_sources[ent] = set()
                    entity_sources[ent].add(rel_name)
            except Exception:
                continue

        tracked = []
        candidates = []

        for ent, cnt in total_counts.most_common():
            ent_key = ent.lower().replace(" ", "-")
            is_tracked = (ent_key in existing_wiki_topics or 
                          ent.lower() in existing_wiki_topics or 
                          (root / "wiki" / ent).is_dir() or
                          (root / "wiki" / ent_key).is_dir())

            sources = sorted(list(entity_sources.get(ent, [])))
            item = {
                "entity": ent,
                "count": cnt,
                "sources_count": len(sources),
                "sources": sources[:5],
                "is_tracked": is_tracked
            }

            if is_tracked:
                tracked.append(item)
            elif cnt >= min_count:
                candidates.append(item)

        return {
            "vault_root": str(root),
            "total_entities_found": len(total_counts),
            "tracked_count": len(tracked),
            "candidates_count": len(candidates),
            "tracked": tracked[:15],
            "candidates": candidates[:20]
        }

    @classmethod
    def format_report(cls, data: Dict[str, Any]) -> str:
        lines = [
            "=" * 50,
            "[SUBJECT ATLAS] Детерминированный индекс тем и сущностей (LOD 0+)",
            f"Путь: {data['vault_root']}",
            "-" * 50,
            f"* Всего выявлено сущностей: {data['total_entities_found']}",
            f"* Уже отслеживаются в wiki/:  {data['tracked_count']}",
            f"* Кандидаты в новые заметки:  {data['candidates_count']}",
            ""
        ]

        if data["candidates"]:
            lines.append("--- [Кандидаты на создание заметок в wiki/ (Zero-LLM)] ---")
            for c in data["candidates"][:10]:
                src_str = ", ".join(c["sources"][:3])
                if len(c["sources"]) > 3:
                    src_str += f" (+ещё {len(c['sources']) - 3})"
                lines.append(f"  • {c['entity']} (вхождений: {c['count']}, файлов: {c['sources_count']}) <- {src_str}")
        else:
            lines.append("--- [Кандидатов на новые заметки нет (все ключевые термины уже связаны)] ---")

        if data["tracked"]:
            lines.append("\n--- [Активные связанные темы] ---")
            for t in data["tracked"][:8]:
                lines.append(f"  ✓ {t['entity']} (вхождений: {t['count']})")

        lines.append("=" * 50)
        lines.append("-> Создай страницу wiki/<тема>/index.md для кандидатов с высоким числом упоминаний.")
        return "\n".join(lines)


# =====================================================================
# 6. DailyRollup (Deterministic Daily Digest)
# =====================================================================

class DailyRollup:
    """
    Детерминированная сборка дневной активности (Daily Activity Rollup)
    по принципу hermes-memory-wiki.
    Агрегирует действия за день из log.md и git status без вызова LLM.
    """
    @classmethod
    def get_git_changes(cls, vault_root: Path) -> List[str]:
        try:
            res = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(vault_root),
                capture_output=True,
                text=True,
                timeout=3
            )
            if res.returncode == 0:
                lines = [l.strip() for l in res.stdout.splitlines() if l.strip()]
                return lines
        except Exception:
            pass
        return []

    @classmethod
    def get_daily_digest(cls, vault_root: Optional[Path] = None, target_date: Optional[str] = None) -> Dict[str, Any]:
        # Определение корня хранилища
        current = (vault_root or Path.cwd()).resolve()
        for p in [current] + list(current.parents):
            if (p / "AGENTS.md").exists() or (p / "self").is_dir():
                root = p
                break
        else:
            root = current

        if not target_date:
            target_date = datetime.now().strftime("%Y-%m-%d")

        log_file = root / "log.md"
        actions = []
        if log_file.is_file():
            text = log_file.read_text(encoding="utf-8", errors="replace")
            for line in text.splitlines():
                line_s = line.strip()
                if line_s.startswith(target_date):
                    m = re.match(r"^\d{4}-\d{2}-\d{2}(?:\s+(\d{2}:\d{2}))?\s*—\s*(.+)$", line_s)
                    if m:
                        time_part = m.group(1) or ""
                        action_text = m.group(2).strip()
                        actions.append({"time": time_part, "text": action_text, "raw": line_s})
                    else:
                        actions.append({"time": "", "text": line_s, "raw": line_s})

        git_changes = cls.get_git_changes(root)

        touched_files = set()
        for act in actions:
            paths = re.findall(r"(?:wiki|self|journal|scripts|raw)/[A-Za-z0-9_\-\.\/]+", act["text"])
            for p in paths:
                touched_files.add(p)

        md_lines = [
            f"## Активность за день ({target_date})",
            ""
        ]
        if actions:
            md_lines.append("### Хроника операций")
            for act in actions:
                prefix = f"**{act['time']}** — " if act["time"] else "- "
                md_lines.append(f"{prefix}{act['text']}")
            md_lines.append("")
        else:
            md_lines.append(f"*Записей в log.md за {target_date} не обнаружено.*")
            md_lines.append("")

        if touched_files:
            md_lines.append("### Затронутые области базы")
            for tf in sorted(touched_files):
                md_lines.append(f"- `{tf}`")
            md_lines.append("")

        if git_changes:
            md_lines.append("### Несохраненные / рабочие изменения (Git)")
            for gc in git_changes[:10]:
                md_lines.append(f"- `{gc}`")
            if len(git_changes) > 10:
                md_lines.append(f"- *...и ещё {len(git_changes) - 10} файлов*")
            md_lines.append("")

        markdown_block = "\n".join(md_lines)

        return {
            "vault_root": str(root),
            "target_date": target_date,
            "actions_count": len(actions),
            "actions": actions,
            "touched_files": sorted(list(touched_files)),
            "git_changes_count": len(git_changes),
            "markdown_block": markdown_block
        }

    @classmethod
    def apply_to_journal(cls, vault_root: Optional[Path] = None, target_date: Optional[str] = None) -> Tuple[Path, bool]:
        current = (vault_root or Path.cwd()).resolve()
        for p in [current] + list(current.parents):
            if (p / "AGENTS.md").exists() or (p / "self").is_dir():
                root = p
                break
        else:
            root = current

        digest = cls.get_daily_digest(root, target_date)
        dt = digest["target_date"]
        journal_dir = root / "journal"
        journal_dir.mkdir(parents=True, exist_ok=True)
        journal_file = journal_dir / f"{dt}.md"

        block = digest["markdown_block"]
        is_new = not journal_file.exists()

        if is_new:
            content = f"""---
title: Журнал ({dt})
tags: [журнал]
created: {dt}
updated: {dt}
status: факт
---

{block}
## Связано
- [[../self/Lessons-Learned]]
- [[../index]]
"""
            journal_file.write_text(content, encoding="utf-8")
        else:
            existing = journal_file.read_text(encoding="utf-8", errors="replace")
            sec_header = f"## Активность за день ({dt})"
            if sec_header in existing:
                pattern = rf"## Активность за день \({re.escape(dt)}\)[\s\S]*?(?=\n## |\Z)"
                updated = re.sub(pattern, block.strip(), existing)
            elif "## Связано" in existing:
                parts = existing.split("## Связано", 1)
                updated = parts[0].rstrip() + "\n\n" + block + "\n## Связано" + parts[1]
            else:
                updated = existing.rstrip() + "\n\n" + block + "\n"

            journal_file.write_text(updated, encoding="utf-8")

        return journal_file, is_new


# =====================================================================
# 7. MemoryAuditor (Memory Audit & Prompt Cache Health)
# =====================================================================

class MemoryAuditor:
    """
    Панель аудита памяти (Memory Audit Panel) по принципу hermes-memory-wiki.
    Аудирует состояние активного контекста, размер файлов в токенах и
    гардрайлы prompt-кэша.
    """
    @classmethod
    def audit(cls, vault_root: Optional[Path] = None) -> Dict[str, Any]:
        from storage import MemoryStorage
        current = (vault_root or Path.cwd()).resolve()
        for p in [current] + list(current.parents):
            if (p / "AGENTS.md").exists() or (p / "self").is_dir():
                root = p
                break
        else:
            root = current

        items = []
        total_chars = 0
        total_lines = 0

        target_files = [
            ("Инструкции агента", root / "AGENTS.md", True),
            ("Активные уроки (LOD 1)", root / "self" / "Lessons-Learned.md", True),
            ("Активные цели", root / "self" / "Goals.md", True),
            ("Привычки", root / "self" / "Habits.md", False),
            ("Навыки", root / "self" / "Skills.md", False),
            ("Профиль пользователя", root / "self" / "Profile.md", False),
            ("Индекс хранилища", root / "index.md", False),
        ]

        warnings = []

        for label, p, is_critical in target_files:
            if p.is_file():
                try:
                    text = p.read_text(encoding="utf-8", errors="replace")
                    chars = len(text)
                    lines = len(text.splitlines())
                    total_chars += chars
                    total_lines += lines
                    est_tokens = int(chars / 3.5)

                    if p.name == "Lessons-Learned.md":
                        if lines > 200 or chars > 25000:
                            warnings.append(
                                f"LOD 1 Lessons-Learned.md превышает лимит ({lines} строк / {chars} байт). "
                                f"Необходим перенос старых блоков в self/Lessons/ архива."
                            )
                    elif p.name == "AGENTS.md":
                        if lines > 600:
                            warnings.append(
                                f"AGENTS.md разросся до {lines} строк. Рекомендуется оптимизация через /writing-for-agents."
                            )

                    items.append({
                        "label": label,
                        "file": p.name,
                        "path": str(p),
                        "exists": True,
                        "lines": lines,
                        "chars": chars,
                        "est_tokens": est_tokens,
                        "is_critical": is_critical
                    })
                except Exception as e:
                    items.append({
                        "label": label,
                        "file": p.name,
                        "path": str(p),
                        "exists": True,
                        "error": str(e),
                        "is_critical": is_critical
                    })
            else:
                items.append({
                    "label": label,
                    "file": p.name,
                    "path": str(p),
                    "exists": False,
                    "is_critical": is_critical
                })

        try:
            storage = MemoryStorage()
            mem_stats = storage.stats()
        except Exception:
            mem_stats = {"total_entries": 0, "total_hits": 0}

        total_est_tokens = int(total_chars / 3.5)

        return {
            "vault_root": str(root),
            "files": items,
            "total_chars": total_chars,
            "total_lines": total_lines,
            "total_est_tokens": total_est_tokens,
            "memory_stats": mem_stats,
            "warnings": warnings,
            "cache_status": "OPTIMAL" if not warnings else "NEEDS_ATTENTION"
        }

    @classmethod
    def format_report(cls, data: Dict[str, Any]) -> str:
        lines = [
            "=" * 50,
            "[MEMORY AUDIT] Панель аудита контекста и кэша (Hermes-Style)",
            f"Путь: {data['vault_root']}",
            f"Статус кэша памяти: {data['cache_status']}",
            "-" * 50,
            "Компоненты памяти агента:"
        ]

        for item in data["files"]:
            if item["exists"]:
                lines.append(
                    f"  • {item['label']:<24} [{item['file']}] — {item['lines']} строк, "
                    f"~{item['est_tokens']} токенов ({item['chars']} симв.)"
                )
            else:
                crit = " (Опционально)" if not item.get("is_critical") else " (ОТСУТСТВУЕТ!)"
                lines.append(f"  • {item['label']:<24} [{item['file']}] — не создан{crit}")

        lines.append("-" * 50)
        lines.append(f"Суммарный контекстный вес: ~{data['total_est_tokens']} токенов ({data['total_lines']} строк)")
        lines.append(f"Семантический кэш SQLite: {data['memory_stats'].get('total_entries', 0)} записей, {data['memory_stats'].get('total_hits', 0)} hits")

        if data["warnings"]:
            lines.append("\n*** ПРЕДУПРЕЖДЕНИЯ АУДИТА ПАМЯТИ ***")
            for w in data["warnings"]:
                lines.append(f"  ⚠️  {w}")
        else:
            lines.append("\n✓ Гардрайлы в норме: LOD 1 компактен, кэш промптов защищён от деградации.")

        lines.append("=" * 50)
        return "\n".join(lines)


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
    p_status.add_argument("--central", action="store_true", help="Показать настроенный центральный vault")

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

# 9. session recorder
    p_session_start = subparsers.add_parser("session-start", help="Открыть компактный буфер рабочей сессии")
    p_session_start.add_argument("--root", type=Path, help="Корень vault (по умолчанию определяется автоматически)")
    p_record = subparsers.add_parser("record", help="Добавить одно содержательное событие в буфер сессии")
    p_record.add_argument("summary", help="Краткое описание результата")
    p_record.add_argument("--kind", default="work", help="Тип: decision, change, test, error, lesson, work")
    p_record.add_argument("--detail", default="", help="Краткая дополнительная деталь")
    p_record.add_argument("--file", action="append", default=[], help="Измененный файл (можно повторять)")
    p_record.add_argument("--root", type=Path, help="Корень vault")
    p_close = subparsers.add_parser("session-close", help="Собрать итог сессии в journal/log и выполнить sync")
    p_close.add_argument("--lesson", help="Переносимый урок одной строкой; не указывать для обычного итога")
    p_close.add_argument("--root", type=Path, help="Корень vault")

# 10. subjects (Zero-LLM Topic Indexing)
    p_subj = subparsers.add_parser("subjects", help="Детерминированный поиск тем и кандидатов в wiki (Zero-LLM)")
    p_subj.add_argument("--min-count", type=int, default=2, help="Минимальное число упоминаний (дефолт 2)")
    p_subj.add_argument("--json", action="store_true", help="Вывод в формате JSON")

    # 11. digest-today (Daily Activity Rollup)
    p_digest = subparsers.add_parser("digest-today", help="Детерминированная сборка активности дня из log.md и git")
    p_digest.add_argument("--date", help="Целевая дата YYYY-MM-DD (по умолчанию сегодня)")
    p_digest.add_argument("--write", action="store_true", help="Записать сводку напрямую в journal/YYYY-MM-DD.md")
    p_digest.add_argument("--json", action="store_true", help="Вывод в формате JSON")

    # 12. audit (Memory Audit Panel)
    p_audit = subparsers.add_parser("audit", help="Панель аудита памяти, бюджета токенов и prompt-кэша")
    p_audit.add_argument("--json", action="store_true", help="Вывод в формате JSON")


    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    # --- status / atlas ---
    if args.command == "status":
        root = VaultAtlas.configured_vault_root() if args.central else None
        if args.central and root is None:
            parser.error("Центральный vault не настроен: задайте LIVING_WIKI_VAULT или ~/.living-wiki-path")
        status_data = VaultAtlas.get_status(root)
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
        db_path = os.environ.get("LIVING_WIKI_MEMORY_DB")
        storage = MemoryStorage(Path(db_path) if db_path else recorder.root / ".cache" / "memory.db")
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

# --- session recorder ---
    elif args.command == "session-start":
        state = SessionRecorder(args.root).start()
        print(f"[SESSION] Открыта сессия: {state['started_at']}")
        sys.exit(0)
    elif args.command == "record":
        result = SessionRecorder(args.root).record(args.kind, args.summary, args.detail, args.file)
        print(f"[SESSION] {result['action']}: {result['event']['summary']}")
        sys.exit(0)
    elif args.command == "session-close":
        recorder = SessionRecorder(args.root)
        result = recorder.close(args.lesson)
        if result["action"] == "empty":
            print(f"[SESSION] {result['message']}")
            sys.exit(0)
        storage = MemoryStorage()
        count = storage.sync_from_markdown(recorder.root)
        if recorder.root == VaultAtlas.find_vault_root():
            mark_lessons_synced()
        print(f"[SESSION] Закрыта: {result['journal']}; память синхронизирована: {count}")

# --- subjects (Zero-LLM Topic Indexing) ---
    elif args.command == "subjects":
        data = SubjectExtractor.scan_vault(min_count=args.min_count)
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            print(SubjectExtractor.format_report(data))
        sys.exit(0)

    # --- digest-today (Daily Activity Rollup) ---
    elif args.command == "digest-today":
        if args.write:
            j_file, is_new = DailyRollup.apply_to_journal(target_date=args.date)
            status_str = "создан" if is_new else "обновлен"
            print(f"[DIGEST] Журнал {j_file.name} успешно {status_str}: {j_file}")
            sys.exit(0)
        else:
            digest = DailyRollup.get_daily_digest(target_date=args.date)
            if args.json:
                print(json.dumps(digest, ensure_ascii=False, indent=2))
            else:
                print(digest["markdown_block"])
            sys.exit(0)

    # --- audit (Memory Audit Panel) ---
    elif args.command == "audit":
        audit_data = MemoryAuditor.audit()
        if args.json:
            print(json.dumps(audit_data, ensure_ascii=False, indent=2))
        else:
            print(MemoryAuditor.format_report(audit_data))
        sys.exit(0)


if __name__ == "__main__":
    main()
