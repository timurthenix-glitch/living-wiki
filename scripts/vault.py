#!/usr/bin/env python3
"""
vault.py — Единственный источник правды о том, где находится vault.

До появления этого модуля `engine.py` и `storage.py` определяли корень
хранилища ДВУМЯ разными эвристиками (engine искал `AGENTS.md`/`self/`,
storage — `AGENTS.md`/`.git`). На одном и том же дереве они давали разные
ответы: `session-close` синхронизировал один корень, а database-файл
создавался в другом. Плюс обе эвристики поднимались до ЛЮБОГО совпадения
выше по дереву, поэтому посторонний `AGENTS.md` в родительском каталоге
уводил запись в чужой проект.

Правило определения корня (ровно одно, для всех вызовов):

  1. Явный путь (аргумент/переменная окружения) — если задан, он и корень.
  2. Ближайший каталог вверх по дереву, который САМ выглядит как vault:
     содержит `self/` или `wiki/`. `AGENTS.md` и `.git` намеренно НЕ
     считаются признаками — это признаки любого проекта, а не хранилища.
     Из нескольких кандидатов (вложенные vault) побеждает ближайший.
  3. Если ни один не найден — cwd объявляется «vault не настроен».
     Вызывающий код обязан явно сообщить об этом, а не изображать
     пустое хранилище.

ВАЖНО: отсутствие vault — это не пустой vault. Инструкции в AGENTS.md/
SKILL.md требуют не создавать структуру заново без нужды, но также и не
терять данные молча, поэтому различие между «не настроено» и «настроено,
но пусто» обязательно пробрасывается до пользователя.
"""

import os
import sys
from pathlib import Path
from typing import Optional, Tuple

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


# Каталоги, сами по себе доказывающие, что перед нами vault.
VAULT_MARKERS = ("self", "wiki")

ENV_VAULT = "LIVING_WIKI_VAULT"
CENTRAL_POINTERS = (Path.home() / ".living-wiki-path", Path.home() / ".living-wiki" / "vault-path")


def is_vault(path: Path) -> bool:
    """True, если каталог сам является корнем vault."""
    try:
        return any((path / marker).is_dir() for marker in VAULT_MARKERS)
    except OSError:
        return False


def _configured_vault_root() -> Optional[Path]:
    """Корень из LIVING_WIKI_VAULT или файлов-указателей на центральный vault."""
    env_path = os.environ.get(ENV_VAULT)
    candidates = []
    if env_path:
        candidates.append(Path(env_path).expanduser())
    candidates.extend(CENTRAL_POINTERS)

    for candidate in candidates:
        if candidate.is_file():
            try:
                candidate = Path(candidate.read_text(encoding="utf-8").strip()).expanduser()
            except OSError as exc:
                raise RuntimeError(f"Не удалось прочитать путь центрального vault: {exc}") from exc
        if candidate.is_dir():
            return candidate.resolve()
    return None


def configured_vault_root() -> Optional[Path]:
    """Публичный доступ к явно настроенному корню (для `status --central`)."""
    return _configured_vault_root()


def resolve_vault_root(start_dir: Optional[Path] = None, explicit: Optional[Path] = None) -> Tuple[Path, bool]:
    """
    Возвращает `(корень, найден_ли)`.

    `найден_ли is False` означает: vault не найден, и возвращённый корень —
    просто `start_dir` / cwd. Вызывающий обязан трактовать это как
    «хранилище не настроено», а не как «пустое хранилище».
    """
    if explicit is not None:
        return Path(explicit).expanduser().resolve(), True

    configured = _configured_vault_root()
    if configured is not None:
        return configured, True

    current = (start_dir or Path.cwd()).resolve()
    for parent in [current] + list(current.parents):
        if is_vault(parent):
            return parent, True

    return current, False


def find_vault_root(start_dir: Optional[Path] = None, explicit: Optional[Path] = None) -> Path:
    """Совместимость: только путь, без признака «найден»."""
    return resolve_vault_root(start_dir=start_dir, explicit=explicit)[0]


def require_vault_root(start_dir: Optional[Path] = None, explicit: Optional[Path] = None) -> Path:
    """
    Путь к vault или внятная ошибка. Используется командами, которые
    пишут в память: хуже упасть с объяснением, чем тихо записать в cwd
    пользователя посторонние файлы.
    """
    root, found = resolve_vault_root(start_dir=start_dir, explicit=explicit)
    if not found:
        raise VaultNotConfigured(root)
    return root


class VaultNotConfigured(RuntimeError):
    """Vault не найден ни по конфигурации, ни вверх по дереву каталогов."""

    def __init__(self, start_dir: Path):
        self.start_dir = start_dir
        super().__init__(
            f"Vault Живой Вики не найден (искали от {start_dir} вверх по дереву). "
            "Ожидался каталог с self/ или wiki/. Настройте хранилище "
            f"(`references/setup.md`) или укажите путь через ${ENV_VAULT} / ~/.living-wiki-path."
        )


def default_db_path(vault_root: Path) -> Path:
    """Путь к SQLite-памяти внутри конкретного vault."""
    return Path(vault_root) / ".cache" / "memory.db"
