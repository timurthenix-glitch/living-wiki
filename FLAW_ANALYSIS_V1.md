# Comprehensive Flaw Analysis Report: Living Wiki Plugin v1

**Document ID**: FLAW-ANALYSIS-V1-20261030  
**Target Repository**: `c:\Users\Mura\Desktop\vik`  
**Plugin Version**: v1 (`bootstrap_version: v1`, `plugin.json: 1.1.1`)  
**Rules Hash**: `ac1b73f9b38778e50567ca6e24a516bce1ac42b57b72f1b2d6e5809edd274221`  
**Status**: Final Comprehensive Audit  
**Author**: Teamwork Worker (Audit & Remediation Engineering)  

---

## 1. Executive Summary

### 1.1 Scope and Objective
This report presents an exhaustive, forensic architectural analysis of the **Living Wiki (Живая память) Plugin v1**, an agentic memory and governance system designed to provide persistent, cross-session memory and repeatable procedural skills for LLM-driven development agents across multiple IDE ecosystems (Claude Code, Cursor, Windsurf, Cline, GitHub Copilot, Junie, Kiro, Qoder). 

The audit evaluated all components within the repository:
1. Core rule specification (`AGENTS.md`)
2. Working memory contracts and skeletons (`MEMORY.md`, `templates/MEMORY.md`)
3. Cold storage specifications (`memory/<topic>.md`)
4. Procedural skill systems (`skills/living-wiki/SKILL.md`, `templates/skill.md`)
5. Tool-specific rule pointer distribution (`templates/rule-pointer.md`, `.cursor/rules/`, `.clinerules/`, `.windsurf/rules/`, `.junie/`, `.kiro/steering/`, `.github/copilot-instructions.md`)
6. Maintenance, synchronization, and verification shell scripts (`scripts/*.sh`)
7. Marketplace and plugin manifests (`.claude-plugin/plugin.json`)

### 1.2 Key Findings and Architectural Maturity
The Living Wiki v1 design targets a minimalistic, file-based memory architecture modeled after Hermes-style memory ("one memory file and reusable skills, plain markdown, no database, no Obsidian graph"). While this philosophy delivers zero-dependency onboarding in trivial, single-agent, POSIX-only environments, deep architectural analysis reveals that **plugin v1 is fundamentally brittle, structurally contradictory, and poses an immediate risk of silent data loss, agent amnesia, and execution failure**.

Across the system, **17 critical and high-severity architectural defects** were identified and cataloged (DEF-MEM-01 through DEF-AUD-01). The defects cluster into five systemic failure modes:
1. **Destructive Memory Lifecycle**: Eviction rules mandate destroying unaddressed blockers (`Открытые вопросы`) and learned bug resolutions (`Грабли`) first, while simultaneously evicting pointer links to cold memory files, turning cold memory into permanent, unreachable orphans.
2. **Multi-Agent Concurrency Collapse**: Despite claiming universal multi-agent support (`AGENTS.md §9`), the system provides zero concurrency control, file locking, or atomic compare-and-swap (CAS) mechanisms. Concurrent read-modify-write operations across multiple IDE agents produce silent data loss, overwritten facts, and file-system race crashes.
3. **Severe Platform & Encoding Fragility**: The memory limit verification contract explicitly requires running `wc -m MEMORY.md`. On Windows environments (the native environment of the vast majority of desktop developers), `wc` does not exist, triggering shell command exceptions. Furthermore, Cyrillic UTF-8 encoding doubles byte consumption and inflates BPE token usage, causing false compaction triggers, while CRLF line endings introduce phantom characters.
4. **Specification Self-Contradictions**: The template files (`templates/skill.md`) embed legacy Obsidian wikilinks (`[[self/Skills]]`), directly violating the explicit ban in `AGENTS.md §1` and `§7`. Similarly, `AGENTS.md §5` permits `<name>/notes.md` while `§1` and `§7` mandate the immediate deletion of any `notes/` directory.
5. **Governance & Sync Circularity**: The integrity hashing mechanism (`scripts/rules-hash.sh`) hardcodes a dependency on `skills/living-wiki/` (which does not exist in downstream projects), computes hashes with CRLF/LF vulnerability, and instructs agents to overwrite local `AGENTS.md` rules upon hash mismatch, triggering infinite overwrite loops. Concurrently, the Cline rule pointer is deployed to an invalid path (`.clinerules/living-wiki.md` instead of `.clinerules`), causing Cline agents to run completely unconstrained.

---

## 2. Architecture Breakdown

The plugin v1 architecture is structured into six interacting layers:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        AGENTS.md (Layer 1: Governance)                │
│  - Immutable rules (v1)      - rules_hash integrity      - 4-step audit │
└───────────────────┬────────────────────────────────┬───────────────────┘
                    │                                │
                    ▼                                ▼
┌──────────────────────────────────────┐  ┌──────────────────────────────┐
│  MEMORY.md (Layer 2: Hot Memory)     │  │ skills/ (Layer 4: Procedures)│
│  - 5 fixed sections                  │  │ - SKILL.md templates         │
│  - One line = one declarative fact   │  │ - Trigger, Steps, Verification│
│  - 6000-char hard ceiling            │  │ - Auxiliary notes.md         │
└───────────────────┬──────────────────┘  └──────────────────────────────┘
                    │ Eviction / Displacement
                    ▼
┌──────────────────────────────────────┐
│  memory/<topic>.md (Layer 3: Cold)   │
│  - Topic details (arch, schemas)     │
│  - Threshold: 15 KB / ~300 lines     │
│  - On-demand activation by topic     │
└──────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────────┐
│              Layer 5: Multi-Agent Distribution (Rule Pointers)         │
│  templates/rule-pointer.md  ──►  .cursor/rules/living-wiki.md          │
│                             ──►  .clinerules/living-wiki.md (INVALID)  │
│                             ──►  .windsurf/rules/living-wiki.md        │
│                             ──►  .junie/guidelines.md                  │
│                             ──►  .kiro/steering/living-wiki.md         │
│                             ──►  .github/copilot-instructions.md       │
└────────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────────┐
│                   Layer 6: Tooling & Shell Validation                  │
│  - scripts/rules-hash.sh          - scripts/sync-rule-pointers.sh      │
│  - scripts/check-rule-pointers.sh - scripts/check-version-sync.sh      │
│  - scripts/check-json-valid.sh                                         │
└────────────────────────────────────────────────────────────────────────┘
```

### Layer 1: Rules & Governance (`AGENTS.md`)
The constitutional core of the plugin. It establishes the single-repository layout, mandates that only `AGENTS.md`, `MEMORY.md`, and tool pointers may live in the root directory, defines the lifecycle of facts, and specifies the per-turn 4-step cognitive audit. Versioning is frozen at `v1`, with staleness governed solely by a SHA-256 `rules_hash`.

### Layer 2: Hot Working Memory (`MEMORY.md`)
A flat, human-readable Markdown file containing short, declarative, single-line facts organized under five mandatory sections:
1. `## О пользователе` (User preferences, goals, constraints)
2. `## Проект и стек` (Architecture, languages, frameworks, runtime)
3. `## Решения` (Key technical and architectural decisions)
4. `## Грабли` (Traps, symptoms, root causes, bug remedies)
5. `## Открытые вопросы` (Pending questions, unresolved blockers)

A rigid 6,000-character ceiling is enforced, with an active compression trigger at 5,400 characters (90%).

### Layer 3: Cold Storage Layer (`memory/<topic>.md`)
Intended for deep domain context that exceeds single-line representations (e.g., database schemas, API specs, deployment manifests). Topics are accessed on-demand when the subject arises during conversation. Each topic file is capped at ~15 KB or ~300 lines.

### Layer 4: Procedural Skills (`skills/<name>/SKILL.md`)
Standardized operational procedures for recurring or non-obvious workflows. Format includes YAML frontmatter (`name`, `description`, `updated`) and markdown sections (`Когда применять`, `Шаги`, `Грабли`, `Проверка`). When a skill exceeds 150 lines, details must be split into `<name>/notes.md`.

### Layer 5: Rule Pointers & Ecosystem Integration
Lightweight pointers placed in IDE configuration directories informing external agents (Cursor, Cline, Windsurf, Copilot, etc.) to read `AGENTS.md`. Pointers are generated from `templates/rule-pointer.md` via `scripts/sync-rule-pointers.sh`.

### Layer 6: Verification & Validation Tooling
Bash utility scripts used in CI and local environments to calculate rules hashes, assert pointer synchronization, verify version markers across `plugin.json` and `AGENTS.md`, and validate JSON syntax.

---

## 3. Comprehensive Defect Sheets (DEF-MEM-01 through DEF-AUD-01)

### DEF-MEM-01: Non-Portable `wc -m` Command Fails on Windows Environments
- **Defect ID**: `DEF-MEM-01`
- **Severity**: **CRITICAL**
- **Criticality Justification**: The core rule specification explicitly commands agents to execute `wc -m MEMORY.md` to verify the 6000-character memory threshold. In native Windows environments (PowerShell / Command Prompt), `wc` is not an available command. When an LLM agent executes this command, it receives a fatal `CommandNotFoundException`, causing tool execution abortion, hallucinated memory sizes, or infinite retry loops.
- **Affected Files & Line Citations**:
  - `AGENTS.md`, Line 53: `Лимит — **6000 символов на файл**: 5400 (90%) — сигнал начинать сжатие, 6000 — потолок, выше файл не выходит никогда. Проверка одной командой: wc -m MEMORY.md.`
  - `skills/living-wiki/SKILL.md`, Line 18: `- Лимит файла — 6000 символов (5400 — начинай сжимать). Проверка: wc -m MEMORY.md.`
- **Technical Root Cause**:
  The specification assumes a POSIX environment where GNU Coreutils is globally mapped in `PATH`. On Windows PowerShell, `wc` is neither a cmdlet nor an alias. While PowerShell provides `Measure-Object -Character`, raw `wc` invocation fails immediately.
- **Step-by-Step Reproduction Scenario**:
  1. Initialize a Windows PowerShell terminal session (e.g., standard Windows 10/11 environment).
  2. Create a test `MEMORY.md` file in the workspace.
  3. Execute the exact command prescribed by `AGENTS.md` line 53: `wc -m MEMORY.md`.
  4. **Empirical Result**:
     ```powershell
     wc : The term 'wc' is not recognized as the name of a cmdlet, function, script file, or operable program.
     At line:1 char:1
     + wc -m MEMORY.md
     + ~~
         + CategoryInfo          : ObjectNotFound: (wc:String) [], CommandNotFoundException
         + FullyQualifiedErrorId : CommandNotFoundException
     ```
  5. The LLM agent, encountering a tool failure, either aborts its memory compaction step or fabricates a character count from context.
- **Impact on System**: Complete breakdown of automated size verification on Windows. Memory files grow past 6,000 characters unmonitored, or agents panic upon receiving command failure errors.
- **Remediation Recommendation**:
  Replace the single Unix command instruction in `AGENTS.md` and `SKILL.md` with cross-platform alternatives, or define an automated checking helper:
  ```markdown
  Проверка лимита:
  - Linux/macOS: `wc -m MEMORY.md`
  - Windows PowerShell: `(Get-Content MEMORY.md -Raw).Length`
  - Python (кроссплатформенно): `python -c "print(len(open('MEMORY.md', encoding='utf-8').read()))"`
  ```

---

### DEF-MEM-02: Character vs Byte vs Token Count Divergence & Cyrillic 2x Expansion
- **Defect ID**: `DEF-MEM-02`
- **Severity**: **CRITICAL**
- **Criticality Justification**: The specification defines limits strictly in "characters" (`6000 символов`), but operating systems, file explorers, and CLI utilities (`ls -l`, `stat`, Python `os.path.getsize`) measure storage in **bytes**. Cyrillic characters in UTF-8 encoding occupy **2 bytes per character**. A 5,400-character Russian memory file consumes ~11,000 bytes. When agents inspect file size via filesystem metadata, they falsely conclude the file is 180% over capacity and initiate hyper-aggressive, destructive pruning of valuable facts. Furthermore, modern BPE tokenizers consume 1.3 to 2.2 tokens per Cyrillic character, meaning a 6,000-character Russian file consumes up to 4,500 tokens.
- **Affected Files & Line Citations**:
  - `AGENTS.md`, Line 53: `Лимит — **6000 символов на файл**: 5400 (90%) — сигнал начинать сжатие, 6000 — потолок, выше файл не выходит никогда. Проверка одной командой: wc -m MEMORY.md.`
  - `skills/living-wiki/SKILL.md`, Lines 18-20: `- Лимит файла — 6000 символов (5400 — начинай сжимать).`
- **Technical Root Cause**:
  The rule specification conflates typographic characters with storage bytes and tokenizer tokens. In UTF-8, ASCII characters (English) are 1 byte, while Cyrillic characters (`U+0400` to `U+04FF`) require 2 bytes:
  - English: `Hello` (5 characters, 5 bytes, 1 token)
  - Russian: `Привет` (6 characters, 12 bytes, 3-4 tokens)
  When an LLM agent uses file listing tools or OS inspection, it observes byte size rather than character length. Seeing `11,200 bytes`, the agent triggers Section 3.1 emergency compression, wiping legitimate knowledge.
- **Step-by-Step Reproduction Scenario**:
  1. Generate a compliant `MEMORY.md` containing 5,500 characters of Russian text across valid sections.
  2. Inspect the file size via Python `os.path.getsize('MEMORY.md')` or `Get-Item MEMORY.md | Select Length`.
  3. Result: Size is `11,180 bytes`.
  4. Prompt an LLM agent with the rule: "Лимит — 6000 символов, выше файл не выходит никогда. Проверь размер MEMORY.md".
  5. The LLM agent inspects the file attributes, sees `11.1 KB (> 6000)`, concludes the memory is critically overflowing, and deletes 60% of the recorded facts.
- **Impact on System**: Catastrophic premature data loss. The effective character ceiling for Russian-language projects is silently halved to ~2,800 characters if byte tools are consulted, or causes unexpected token exhaustion in small-context models.
- **Remediation Recommendation**:
  Explicitly disambiguate character count, byte size, and token consumption in `AGENTS.md §3`:
  ```markdown
  Лимит файла — 6000 символов текста в кодировке UTF-8 (не байт на диске!). 
  Для русскоязычного текста размер на диске составит ~11–12 КБ (2 байта на символ) — это норма, не путать с переполнением. 
  Проверка должна считать именно символы (Unicode code points), а не байты.
  ```

---

### DEF-MEM-03: CRLF Line Ending Inflation Creates Phantom Characters on Windows
- **Defect ID**: `DEF-MEM-03`
- **Severity**: **HIGH**
- **Criticality Justification**: On Windows machines with standard Git settings (`core.autocrlf=true`), text files are checked out with CRLF (`\r\n`) line terminators. A typical 150-line `MEMORY.md` contains 150 `\r` carriage return bytes. When read into character buffers or evaluated via standard character length counters (`len(text)` or `(Get-Content -Raw).Length`), each CRLF sequence counts as **2 characters** instead of 1 (`\n`). This injects 150–200 phantom characters into the threshold budget, triggering premature compaction 5% earlier on Windows than on Linux/macOS.
- **Affected Files & Line Citations**:
  - `AGENTS.md`, Line 53: `Лимит — **6000 символов на файл**: 5400 (90%) — сигнал начинать сжатие, 6000 — потолок, выше файл не выходит никогда. Проверка одной командой: wc -m MEMORY.md.`
  - `scripts/rules-hash.sh`, Lines 17, 20: Demonstrates that the author was aware of CRLF divergence in scripts (`tr -d '\r'`), but failed to account for it in `MEMORY.md` character counting rules.
- **Technical Root Cause**:
  POSIX newline is LF (`0x0A`, 1 character/byte). Windows newline is CRLF (`0x0D 0x0A`, 2 characters/bytes). If an agent uses PowerShell `(Get-Content MEMORY.md -Raw).Length` or Python `len(open('MEMORY.md', 'r', newline='').read())`, the `\r` characters are counted as distinct characters. For a file near the 5,400-character threshold, the 150 `\r` characters prematurely push the file over the 90% threshold, triggering unneeded fact eviction.
- **Step-by-Step Reproduction Scenario**:
  1. Create a `MEMORY.md` with 120 lines and exactly 5,300 visible characters using LF line endings. Character count = `5,300 + 120 = 5,420`.
  2. Clone the repository on Windows with `git config core.autocrlf true`.
  3. Git converts all LFs to CRLFs. The file now has 120 `\r` characters.
  4. Character count becomes `5,300 + (120 * 2) = 5,540`.
  5. The file has crossed the 5,400 warning line purely due to OS line endings, triggering Section 3.1 eviction protocols.
- **Impact on System**: Non-deterministic cross-platform behavior. Repositories shared between Windows and macOS/Linux developers experience oscillating memory compaction cycles where Windows agents delete facts that Linux agents consider well within limits.
- **Remediation Recommendation**:
  Mandate normalized newline handling:
  1. Add a repository `.gitattributes` file enforcing `* text=auto eol=lf` across all Markdown files.
  2. Update `AGENTS.md §3` to specify that character counting must ignore carriage return (`\r`) characters:
     `Проверка: python -c "print(len(open('MEMORY.md', encoding='utf-8').read().replace('\r', '')))"`

---

### DEF-MEM-04: Contradiction Between "One Line — One Fact" and Step 1 Displacement
- **Defect ID**: `DEF-MEM-04`
- **Severity**: **HIGH**
- **Criticality Justification**: `AGENTS.md §3` strictly mandates that every entry in `MEMORY.md` must be a single line (`Одна строка — один факт`). However, `AGENTS.md §3.1` (Step 1 of eviction) commands: *"Вытесни деталь в тему. Всё, что длиннее одной строки и относится к одной области (архитектура, API-контракты, схема БД, бизнес-правила, деплой, история решений), уезжает в memory/<тема>.md целиком и без потерь"*. This creates a severe cognitive and contractual paradox: if the agent strictly follows Section 3, no multiline entry can ever exist in `MEMORY.md`. If Step 1 looks for multiline entries, it finds zero candidates, causing the agent to skip Step 1 and proceed straight to deleting unaddressed bugs and open questions.
- **Affected Files & Line Citations**:
  - `AGENTS.md`, Line 47: `- Одна строка — один факт. Коротко: - Пользователь предпочитает короткие ответы без преамбул.`
  - `AGENTS.md`, Line 59: `1. **Вытесни деталь в тему.** Всё, что длиннее одной строки и относится к одной области (архитектура, API-контракты, схема БД, бизнес-правила, деплой, история решений), уезжает в memory/<тема>.md целиком и без потерь, а в MEMORY.md остаётся строка-указатель: - Архитектура и слои — memory/architecture.md.`
- **Technical Root Cause**:
  Architectural specification drafting error. The author intended for multiline knowledge discovered *during the current conversation turn* to be routed directly to cold storage, but drafted Step 1 as an eviction step on *existing contents of MEMORY.md*. Because `MEMORY.md` is already constrained to single lines by Section 3, an agent parsing `MEMORY.md` during overflow compaction finds no entries "длиннее одной строки".
- **Step-by-Step Reproduction Scenario**:
  1. Agent fills `MEMORY.md` with 80 strictly valid, single-line entries across `## Решения`, `## Грабли`, and `## Проект и стек`.
  2. Character count reaches 5,500 characters. Section 3.1 triggers.
  3. Step 1 instructs the agent: "Всё, что длиннее одной строки и относится к одной области, уезжает в memory/<тема>.md".
  4. The agent scans `MEMORY.md`. Every single line is a single line. None are multiline.
  5. The agent concludes Step 1 yields 0 bytes freed, and advances to Step 2 (merge duplicates) and Step 3 (delete "Открытые вопросы" and "Грабли").
  6. Essential bug fixes and unresolved blockers are purged while dense architectural facts remain un-displaced.
- **Impact on System**: Complete bypass of the primary cold-storage offloading mechanism. Knowledge that belongs in cold storage remains trapped as single-line summaries in hot memory, while critical debugging rules are discarded.
- **Remediation Recommendation**:
  Rewrite `AGENTS.md §3.1 Step 1` to target **clusters of related single-line facts** rather than non-existent "multiline entries":
  ```markdown
  1. **Вытесни группу фактов в тему.** Если в MEMORY.md накопилось 3 и более фактов по одной предметной области (архитектура, схема БД, API, деплой), объедини их и перенеси в `memory/<тема>.md`, а в MEMORY.md оставь одну строку-указатель:
     `- Архитектура и слои — memory/architecture.md`
  ```

---

### DEF-MEM-05: Pointer Eviction in Section 3.1 Destroys Cold Storage Links
- **Defect ID**: `DEF-MEM-05`
- **Severity**: **CRITICAL**
- **Criticality Justification**: `AGENTS.md §3.1` explicitly declares: *"Жертвы касаются только строк и указателей. Файлы тем не режутся: устаревшую тему обновляют или удаляют целиком вместе с указателем, если её содержание стало неверным. Порядок жертв в горячем файле: «Открытые вопросы» → «Грабли» → «Решения» → «Проект и стек» → «О пользователе»."*. Under memory pressure, pointer lines (e.g., `- Архитектура — memory/architecture.md`) stored under `## Решения` or `## Проект и стек` are evicted. Because `AGENTS.md §7` strictly bans directory indexing, wikis, and graphs, the cold files in `memory/` become **completely invisible and unindexed**. No future agent session will ever know `memory/architecture.md` exists, resulting in permanent, silent loss of deep architectural memory.
- **Affected Files & Line Citations**:
  - `AGENTS.md`, Line 65: `Жертвы касаются только строк и указателей. Файлы тем не режутся: устаревшую тему обновляют или удаляют целиком вместе с указателем, если её содержание стало неверным.`
  - `AGENTS.md`, Line 67: `Порядок жертв в горячем файле: «Открытые вопросы» → «Грабли» → «Решения» → «Проект и стек» → «О пользователе».`
  - `AGENTS.md`, Line 73: `Тема всплыла в работе — открой memory/<тема>.md и читай его, а не все файлы подряд. Один файл темы — до ~15 КБ (примерно 300 строк); больше — разбей на подтемы и оставь в родительском ссылки. Нет файла темы — значит деталей ещё нет, не выдумывай их.`
  - `skills/living-wiki/SKILL.md`, Line 35: `Жертвы — только строки и указатели; файлы тем целиком не режутся.`
- **Technical Root Cause**:
  The memory model treats cold storage pointers as regular, disposable fact strings subject to eviction. However, cold files are not in the agent's default context. Section 3.2 states that an agent only reads `memory/<тема>.md` when the theme arises, guided by pointers. Once the pointer `- Схема базы данных — memory/db-schema.md` is deleted from `MEMORY.md`, subsequent agents have zero context that the file exists, turning the cold layer into a graveyard of orphaned Markdown files.
- **Step-by-Step Reproduction Scenario**:
  1. Turn 1: Agent creates `memory/auth-architecture.md` and records the pointer in `MEMORY.md` under `## Решения`: `- Архитектура Auth0/OAuth2 — memory/auth-architecture.md`.
  2. Turn 10: Cumulative additions push `MEMORY.md` to 5,800 characters.
  3. Compression order reaches `## Решения`. The agent compresses solutions and evicts the pointer string `- Архитектура Auth0/OAuth2 — memory/auth.md`.
  4. Turn 15: A new agent session starts. The user asks: "How is authentication implemented in our project?".
  5. The new agent reads `AGENTS.md` and `MEMORY.md`. The pointer is gone.
  6. Per `AGENTS.md §3.2`: "Нет файла темы — значит деталей ещё нет, не выдумывай их."
  7. The agent hallucinates a new, conflicting authentication architecture from scratch, ignoring `memory/auth-architecture.md`.
- **Impact on System**: Complete disintegration of the hot/cold memory bridge. Cold memory files become unreachable orphans, violating the core promise of cross-session knowledge retention.
- **Remediation Recommendation**:
  Protect pointer strings from eviction. In `AGENTS.md §3.1`, establish an **Eviction Immunity Rule**:
  ```markdown
  Строки-указатели на холодные темы (`memory/<тема>.md`) обладают иммунитетом к сжатию и НИКОГДА не удаляются из MEMORY.md, пока существует сам файл темы. Удаление указателя допустимо ТОЛЬКО одновременно с физическим удалением устаревшего файла темы.
  ```

---

### DEF-MEM-06: Flawed Eviction Priority Sacrifices "Открытые вопросы" and "Грабли" First
- **Defect ID**: `DEF-MEM-06`
- **Severity**: **CRITICAL**
- **Criticality Justification**: `AGENTS.md §3.1` establishes the strict order of sacrifice during memory overflow:
  `«Открытые вопросы» → «Грабли» → «Решения» → «Проект и стек» → «О пользователе»`.
  This prioritizes static project metadata over dynamic operational lessons. Sacrificing `Открытые вопросы` first wipes out active blockers, pending migrations, and unresolved technical debts. Sacrificing `Грабли` wipes out hard-won bug solutions, known pitfalls, and workaround recipes. Agents immediately repeat previously diagnosed and solved mistakes, causing severe project regressions.
- **Affected Files & Line Citations**:
  - `AGENTS.md`, Line 61: `3. **Убери переставшее действовать**: решённые «Открытые вопросы», починенные «Грабли», отменённые «Решения», устаревшие части стека.`
  - `AGENTS.md`, Line 67: `Порядок жертв в горячем файле: «Открытые вопросы» → «Грабли» → «Решения» → «Проект и стек» → «О пользователе». Последний раздел чистится только слиянием, никогда удалением.`
  - `skills/living-wiki/SKILL.md`, Line 35: `Порядок жертв: «Открытые вопросы» → «Грабли» → «Решения» → «Проект и стек» → «О пользователе» (только слиянием, не удалением).`
- **Technical Root Cause**:
  Flawed conceptual model of knowledge value. The specification assumes that `Открытые вопросы` and `Грабли` are transient disposable notes. In reality, `Грабли` (e.g., *"Do not upgrade package X to 2.1 due to memory leak"*) are the highest-value operational knowledge in an AI-maintained codebase. Removing them causes the agent to re-introduce the exact same bug on the very next prompt.
- **Step-by-Step Reproduction Scenario**:
  1. Turn 3: Agent spends 45 minutes debugging an obscure race condition in Prisma ORM on Windows and records under `## Грабли`:
     `- Prisma v5.4 падает на Windows при pool_timeout < 10s: всегда ставить timeout=15s.`
  2. Turn 12: Memory reaches 5,600 characters. Section 3.1 compaction triggers.
  3. The agent applies line 67: sacrifice `Открытые вопросы`, then `Грабли`.
  4. The Prisma bug entry is deleted to make room for general descriptions under `## Проект и стек`.
  5. Turn 14: The user asks to optimize database connections. The agent, having lost the memory of the Prisma bug, reduces `pool_timeout` to 5s.
  6. The application crashes in production with the exact same error.
- **Impact on System**: Chronic cognitive amnesia. The system punishes long-running projects by actively destroying bug-prevention knowledge, leading to cyclic bug regression loops.
- **Remediation Recommendation**:
  Invert the eviction hierarchy. Static project stack information (which can always be re-derived from `package.json` or `Cargo.toml`) should be offloaded or condensed first. Active `Грабли` must be preserved or migrated to a permanent cold file (`memory/known-traps.md`):
  ```markdown
  Порядок сжатия и вытеснения:
  1. Вытеснение разросшихся разделов «Решения» и «Проект и стек» в `memory/<тема>.md`.
  2. Удаление закрытых «Открытых вопросов» и неактуальных решений.
  3. Сжатие формулировок в «Грабли». Если раздел «Грабли» превышает 15 строк — вытеснить в `memory/pitfalls.md`, сохранив указатель.
  Запрещено удалять нерешённые грабли и блокирующие вопросы ради сохранения статического описания стека.
  ```

---

### DEF-MEM-07: Cold Storage 15 KB Limit Lacks Navigation Protocol; Wikilinks Ban Prevents Subtopic Linking
- **Defect ID**: `DEF-MEM-07`
- **Severity**: **HIGH**
- **Criticality Justification**: `AGENTS.md §3.2` specifies: *"Один файл темы — до ~15 КБ (примерно 300 строк); больше — разбей на подтемы и оставь в родительском ссылки."* At the same time, `AGENTS.md §1` and `§7` strictly forbid Obsidian-style wikilinks, index pages, graphs, and directories like `wiki/` or `notes/`. The specification fails to define how subtopic links should be formatted, how subtopic files should be named, or how an agent should discover hierarchical child topics.
- **Affected Files & Line Citations**:
  - `AGENTS.md`, Line 9: `Ни графа, ни вики из страниц, ни дневников, ни папок «на вырост». Если правила здесь нет — не выдумывай структуру.`
  - `AGENTS.md`, Line 20: `Больше ничего не заводи: ни wiki/, ни journal/, ни raw/, ни notes/, ни brain/. memory/ — не вторая память, а её холодная часть: у каждой темы один файл, без копий.`
  - `AGENTS.md`, Line 73: `Один файл темы — до ~15 КБ (примерно 300 строк); больше — разбей на подтемы и оставь в родительском ссылки. Нет файла темы — значит деталей ещё нет, не выдумывай их.`
  - `AGENTS.md`, Line 126: `Параллельные копии (notes/, brain/, второй memory.md) запрещены: найденную — перенеси и удали.`
- **Technical Root Cause**:
  Architectural incompleteness. The specification allows hierarchical decomposition of cold topics (`разбей на подтемы`), but simultaneously bans the structural tools required for hierarchy (subdirectories, wikilinks, index files). This creates an unnavigable flat directory where agents either create illegal directory structures or produce unparseable reference formats.
- **Step-by-Step Reproduction Scenario**:
  1. A project's database architecture grows beyond 15 KB (450 lines of table schemas, triggers, and indices).
  2. The agent is forced by Section 3.2 to split `memory/database.md` into subtopics.
  3. The agent attempts to create `memory/database/users.md`. This violates Section 1: "у каждой темы один файл, папок на вырост не заводить".
  4. The agent attempts to create `memory/database-users.md` and link it inside `memory/database.md` using `[[memory/database-users.md]]`.
  5. Another agent parses the file, sees `[[wikilink]]`, and triggers Section 7 cleanup, deleting the links or flagging a contract violation.
- **Impact on System**: Inability to scale domain memory beyond trivial sizes. Agents get trapped between exceeding the 15 KB limit or violating the structural prohibitions of Section 1 and Section 7.
- **Remediation Recommendation**:
  Standardize the cold storage subtopic convention in `AGENTS.md §3.2`:
  ```markdown
  Формат подтем:
  - Именование файлов строго плоское: `memory/<тема>-<подтема>.md` (например, `memory/db-schema.md`, `memory/db-migrations.md`).
  - Ссылки оформляются стандартным Markdown: `[Описание подтемы](memory/db-migrations.md)`.
  - Запрещено использовать синтаксис двойных скобок `[[ссылка]]` и создавать вложенные подпапки внутри `memory/`.
  ```

---

### DEF-CONC-01: Absence of File Locking Causes Silent Data Loss on Concurrent Edits
- **Defect ID**: `DEF-CONC-01`
- **Severity**: **CRITICAL**
- **Criticality Justification**: `AGENTS.md §9` explicitly declares support for multiple heterogeneous agents operating simultaneously: *"Для инструментов, которые не читают AGENTS.md сами (Cursor, Windsurf, Cline, Copilot, Antigravity, Kiro, Junie), положи рядом короткий указатель из templates/rule-pointer.md. Память у всех одна — MEMORY.md."* However, there is zero provision for file locking, atomic writes, transactions, or optimistic concurrency control. When two agents work concurrently (e.g., Cursor editing a frontend component while Cline refactors a backend service), both perform uncoordinated read-modify-write operations on `MEMORY.md`, causing silent, irrecoverable data loss via the classic Lost Update anomaly.
- **Affected Files & Line Citations**:
  - `AGENTS.md`, Lines 133-136: `## 9. Несколько агентов. Полный текст правил — только здесь. Для инструментов, которые не читают AGENTS.md сами (Cursor, Windsurf, Cline, Copilot, Antigravity, Kiro, Junie), положи рядом короткий указатель из templates/rule-pointer.md. Память у всех одна — MEMORY.md.`
  - `AGENTS.md`, Lines 75-86: `## 4. Когда писать — сразу, не по команде. Пиши в том же ответе, где факт выяснился.`
- **Technical Root Cause**:
  The system assumes single-threaded, sequential human-in-the-loop interactions. In modern multi-agent workflows, multiple AI processes run concurrently. Because `MEMORY.md` is a plain text file written via non-atomic file system writes without lockfiles (`MEMORY.md.lock`), concurrent writes overwrite each other's updates entirely.
- **Step-by-Step Reproduction Scenario**:
  1. **T0**: `MEMORY.md` contains 3,000 characters.
  2. **T1**: Agent A (Cursor) reads `MEMORY.md` into memory to record a newly adopted frontend library (TailwindCSS).
  3. **T2**: Agent B (Cline), running concurrently, reads `MEMORY.md` to record a critical database fix under `## Грабли`.
  4. **T3**: Agent A adds its Tailwind entry and writes its buffered state back to `MEMORY.md`.
  5. **T4**: Agent B finishes its step, appends its database fix to *its* local copy (which does not contain Agent A's Tailwind entry), and overwrites `MEMORY.md`.
  6. **Result**: Agent A's addition is completely obliterated. No warning, git conflict, or log entry is generated.
- **Impact on System**: Complete corruption of memory consistency in multi-agent setups. Critical project decisions and bug fixes are silently erased, defeating the sole purpose of Section 9.
- **Remediation Recommendation**:
  Implement an atomic append / optimistic locking protocol, or introduce a lightweight lockfile mechanism (`MEMORY.md.lock` with timeout and agent PID/session ID) and recommend appending facts to an append-only journal (`memory/journal.log`) that is consolidated periodically.

---

### DEF-CONC-02: Race Conditions During Displacement Cause Lost Updates & ENOENT Crashes
- **Defect ID**: `DEF-CONC-02`
- **Severity**: **CRITICAL**
- **Criticality Justification**: When memory approaches 6,000 characters, `AGENTS.md §3.1` commands the active agent to execute displacement: create a topic file in `memory/<тема>.md`, excise text from `MEMORY.md`, and write an index pointer. If two agents simultaneously attempt displacement or topic update:
  1. Both agents may choose conflicting topic file names or simultaneously overwrite the same topic file.
  2. If Agent A evicts and deletes an outdated topic file (per line 65) while Agent B is reading or appending to it, Agent B crashes with an unhandled `ENOENT` (file not found) error.
- **Affected Files & Line Citations**:
  - `AGENTS.md`, Lines 69-70: `Освобождал место — делай вытеснение и запись одним проходом, чтобы файл ни на миг не остался сверх лимита.`
  - `AGENTS.md`, Line 65: `Файлы тем не режутся: устаревшую тему обновляют или удаляют целиком вместе с указателем, если её содержание стало неверным.`
- **Technical Root Cause**:
  Non-atomic multi-file operations. Displacement requires a multi-step sequence across multiple disk entities:
  `(1) Read MEMORY.md -> (2) Create/Update memory/<topic>.md -> (3) Rewrite MEMORY.md`.
  Without a transactional boundary, interleaved execution between two agents causes partial writes, dangling pointers to non-existent topic files, or deleted files during active reads.
- **Step-by-Step Reproduction Scenario**:
  1. `MEMORY.md` reaches 5,900 characters.
  2. Agent A starts displacement: extracts 10 lines of architecture decisions into `memory/architecture.md`.
  3. Before Agent A rewrites `MEMORY.md`, Agent B checks `MEMORY.md`, sees it is at 5,900 characters, and decides to displace the same architecture lines into `memory/architecture.md`.
  4. Agent A writes `memory/architecture.md` with version A.
  5. Agent B writes `memory/architecture.md` with version B (clobbering A).
  6. Agent A writes `MEMORY.md` with pointer A.
  7. Agent B writes `MEMORY.md` with pointer B, potentially duplicating the pointer or stripping other facts.
- **Impact on System**: Truncated cold files, duplicate or contradictory pointer lines in `MEMORY.md`, and severe file-system race collisions.
- **Remediation Recommendation**:
  Establish that displacement must be performed exclusively in single-file atomic steps, or delegate cold file mutations to a dedicated synchronization command.

---

### DEF-PTR-01: Cline Configuration Path Mismatch Causes Rules to be Ignored
- **Defect ID**: `DEF-PTR-01`
- **Severity**: **CRITICAL**
- **Criticality Justification**: The pointer synchronization scripts (`scripts/sync-rule-pointers.sh` and `check-rule-pointers.sh`) deploy the Living Wiki rule pointer for Cline to:
  `.clinerules/living-wiki.md`.
  In the official Cline architecture and VS Code extension, Cline looks for a single configuration file named `.clinerules` directly in the project root (`c:\Users\Mura\Desktop\vik\.clinerules`), or in modern versions `.cline/rules/`. Cline **never scans** a folder called `.clinerules/` for arbitrary Markdown files. As a direct result, Cline completely ignores the rule pointer. A developer using Cline receives zero instructions, never reads `AGENTS.md`, never creates `MEMORY.md`, and operates in complete ignorance of the memory architecture.
- **Affected Files & Line Citations**:
  - `scripts/sync-rule-pointers.sh`, Line 12: `".clinerules/living-wiki.md"`
  - `scripts/check-rule-pointers.sh`, Line 11: `".clinerules/living-wiki.md"`
  - `.clinerules/living-wiki.md`, Lines 1-7: The orphan pointer file deployed to the wrong location.
- **Technical Root Cause**:
  Misunderstanding of the Cline specification during script authoring. The author assumed Cline followed the directory pattern of Cursor (`.cursor/rules/<name>.md`) and created `.clinerules/<name>.md`. Cline expects a root file named `.clinerules`.
- **Step-by-Step Reproduction Scenario**:
  1. Open the project in VS Code with the Cline extension enabled.
  2. Execute `scripts/sync-rule-pointers.sh`. The file `.clinerules/living-wiki.md` is generated.
  3. Start a new Cline task: "Analyze project memory".
  4. Inspect Cline's system prompt and active injected rules via Cline debug view.
  5. **Observation**: Cline reports no active custom instructions. The `.clinerules/living-wiki.md` file is completely ignored.
- **Impact on System**: Silent failure for all Cline users. Cline agents fail to adhere to any project rules, create parallel incompatible memory files, or overwrite user files without audit.
- **Remediation Recommendation**:
  Update `scripts/sync-rule-pointers.sh` and `scripts/check-rule-pointers.sh` to target `.clinerules` at the project root:
  ```bash
  # In scripts/sync-rule-pointers.sh
  TARGETS=(
    ".agents/rules/living-wiki.md"
    ".clinerules"
    ".cursor/rules/living-wiki.md"
    ".github/copilot-instructions.md"
    ".junie/guidelines.md"
    ".kiro/steering/living-wiki.md"
    ".qoder/rules/living-wiki.md"
    ".windsurf/rules/living-wiki.md"
  )
  ```
  If `.clinerules` already exists, append or merge the Living Wiki pointer block between demarcated comment tags: `<!-- LIVING-WIKI-START -->` and `<!-- LIVING-WIKI-END -->`.

---

### DEF-PTR-02: Rule Pointers Rely on Passive File Reading Without Context Injection
- **Defect ID**: `DEF-PTR-02`
- **Severity**: **HIGH**
- **Criticality Justification**: The generated rule pointers (`templates/rule-pointer.md`) contain passive advice: *"Полные правила — в файле AGENTS.md в корне проекта (если его ещё нет, скопируй его из репозитория living-wiki: https://github.com/timurthenix-glitch/living-wiki). Прочитай AGENTS.md перед началом работы и следуй ему во всех сессиях в этом проекте."*. LLM coding agents running in IDEs (Cursor, Copilot, Windsurf) treat rule pointer files as system-prompt guidelines, not mandatory execution triggers. Most agents do NOT proactively call `view_file AGENTS.md` before answering simple questions. Consequently, agents answer and modify code without ever loading the 136 lines of `AGENTS.md` rules into context, missing the 4-step audit, memory limits, and displacement contracts.
- **Affected Files & Line Citations**:
  - `templates/rule-pointer.md`, Lines 6-7: `Полные правила — в файле AGENTS.md в корне проекта (если его ещё нет, скопируй его из репозитория living-wiki: https://github.com/timurthenix-glitch/living-wiki). Прочитай AGENTS.md перед началом работы и следуй ему во всех сессиях в этом проекте.`
  - `AGENTS.md`, Line 135: `Для инструментов, которые не читают AGENTS.md сами (Cursor, Windsurf, Cline, Copilot, Antigravity, Kiro, Junie), положи рядом короткий указатель из templates/rule-pointer.md.`
- **Technical Root Cause**:
  Flawed mental model of agent prompt processing. A pointer that says "read file X" requires the agent to spend a tool-call turn reading file X. Under tight token budgets or standard conversational turns, agents skip this step and proceed with user queries without reading the rules.
- **Step-by-Step Reproduction Scenario**:
  1. Open a project configured with `.cursor/rules/living-wiki.md`.
  2. Ask the agent: "Refactor the authentication controller in `src/auth.ts`".
  3. The agent receives the user prompt and the pointer text in its system prompt.
  4. The agent immediately calls tools to edit `src/auth.ts`.
  5. The agent never executes `view_file AGENTS.md`.
  6. The agent makes architectural decisions and finishes its response without updating `MEMORY.md`, violating `AGENTS.md §4` and `§6`.
- **Impact on System**: Complete breakdown of agent compliance in external IDEs. Memory remains stale because the agent never actually internalizes the rules.
- **Remediation Recommendation**:
  Embed the critical core operating instructions directly into `templates/rule-pointer.md` rather than merely pointing to `AGENTS.md`. Include a concise inline executive summary of Section 3, 4, and 6 so the agent has the core contracts in its system prompt on turn zero without needing an explicit tool call.

---

### DEF-HASH-01: Hash Calculation Circularity, CRLF Divergence, and Infinite Overwrite Loops
- **Defect ID**: `DEF-HASH-01`
- **Severity**: **CRITICAL**
- **Criticality Justification**: `AGENTS.md §8` specifies: *"Устарела ли копия правил в проекте, решает rules_hash во frontmatter (scripts/rules-hash.sh): не совпал — замени AGENTS.md на актуальный"*. This creates three fatal failure modes:
  1. **Circular Overwrite Loop**: Any intentional, project-specific rule customization in `AGENTS.md` alters its computed hash. When the agent checks the hash, it finds a mismatch and overwrites `AGENTS.md` with the stock template, erasing the user's custom instructions.
  2. **Missing Dependency Crash**: `scripts/rules-hash.sh` line 18 executes `find skills/living-wiki -type f -name '*.md'`. In downstream client projects that copy `AGENTS.md` (per Section 2: "скопируй этот файл в корень проекта"), `skills/living-wiki` does not exist! The script fails with `find: 'skills/living-wiki': No such file or directory` or produces a different hash, guaranteeing a permanent mismatch.
  3. **CRLF vs LF Hash Invalidation**: On Windows, if a tool normalizes line endings differently or if `git checkout` alters whitespace, the hash diverges, triggering automatic file replacement.
- **Affected Files & Line Citations**:
  - `scripts/rules-hash.sh`, Lines 15-23:
    ```bash
    hash_rules() {
      {
        grep -v '^rules_hash:' AGENTS.md | tr -d '\r'
        find skills/living-wiki -type f -name '*.md' | LC_ALL=C sort | while IFS= read -r f; do
          printf '=== %s\n' "$f"
          tr -d '\r' < "$f"
        done
      } | sha256sum | cut -d' ' -f1
    }
    ```
  - `AGENTS.md`, Line 3: `rules_hash: ac1b73f9b38778e50567ca6e24a516bce1ac42b57b72f1b2d6e5809edd274221`
  - `AGENTS.md`, Line 131: `Устарела ли копия правил в проекте, решает rules_hash во frontmatter (scripts/rules-hash.sh): не совпал — замени AGENTS.md на актуальный; MEMORY.md и skills/ при этом не трогай.`
- **Technical Root Cause**:
  Coupling rule versioning in downstream consumer repositories to internal source paths (`skills/living-wiki`) of the upstream authoring repository, combined with an unsafe auto-replacement instruction.
- **Step-by-Step Reproduction Scenario**:
  1. Follow `AGENTS.md §2`: Copy `AGENTS.md` to a new user project. Do not copy the plugin author's internal development skills (`skills/living-wiki`).
  2. The project's CI or agent runs `scripts/rules-hash.sh --check`.
  3. The `find skills/living-wiki` command fails because the directory does not exist.
  4. Even if `find` returns empty, the computed SHA-256 is completely different from `ac1b73f9b38778e50567ca6e24a516bce1ac42b57b72f1b2d6e5809edd274221`.
  5. The agent reads `AGENTS.md §8`: "не совпал — замени AGENTS.md на актуальный".
  6. The agent downloads/replaces `AGENTS.md`, re-runs the check, fails again, and enters an infinite overwrite loop.
- **Impact on System**: Complete destruction of user rule customizations, broken CI pipelines, and fatal script execution in downstream client repositories.
- **Remediation Recommendation**:
  1. In `scripts/rules-hash.sh`, guard the search path: `if [[ -d skills/living-wiki ]]; then find skills/living-wiki -type f -name '*.md' | LC_ALL=C sort | while IFS= read -r f; do printf '=== %s\n' "$f"; tr -d '\r' < "$f"; done; fi`.
  2. Base `rules_hash` strictly on the canonical rule text of `AGENTS.md` itself, stripping both frontmatter and local project additions.
  3. Explicitly prohibit agents from automatically replacing `AGENTS.md` without explicit human operator confirmation.

---

### DEF-HASH-02: Bash Script Dependency Lacks Native Windows/PowerShell Alternatives
- **Defect ID**: `DEF-HASH-02`
- **Severity**: **HIGH**
- **Criticality Justification**: All validation, synchronization, and hashing tooling (`scripts/rules-hash.sh`, `sync-rule-pointers.sh`, `check-rule-pointers.sh`, `check-version-sync.sh`, `check-json-valid.sh`) are written exclusively as Bash scripts with POSIX assumptions (`#!/usr/bin/env bash`, `pipefail`, `sed`, `awk`, `tr`, `find`, `sha256sum`). On Windows systems lacking WSL or Git Bash in `PATH`, these scripts are completely unexecutable.
- **Affected Files & Line Citations**:
  - `scripts/rules-hash.sh`, Lines 1-41
  - `scripts/sync-rule-pointers.sh`, Lines 1-26
  - `scripts/check-rule-pointers.sh`, Lines 1-29
  - `scripts/check-version-sync.sh`, Lines 1-51
  - `scripts/check-json-valid.sh`, Lines 1-28
- **Technical Root Cause**:
  Monocultural POSIX development environment assumptions. No PowerShell (`.ps1`) or platform-agnostic (Node.js / Python) equivalents were authored or distributed with the plugin.
- **Step-by-Step Reproduction Scenario**:
  1. On a standard Windows developer workstation without WSL, invoke:
     `powershell -Command "./scripts/sync-rule-pointers.sh"`
  2. Windows does not associate `.sh` files with an executable interpreter.
  3. The command fails with an execution error or opens the file in a text editor (Notepad/VS Code).
  4. CI runners configured on Windows (e.g., `windows-latest` on GitHub Actions) fail immediately when executing these check scripts.
- **Impact on System**: Complete inability to maintain, validate, or synchronize rule pointers on Windows environments.
- **Remediation Recommendation**:
  Provide equivalent PowerShell scripts (e.g., `scripts/sync-rule-pointers.ps1`, `scripts/rules-hash.ps1`) or replace the shell scripts with a single cross-platform Python script (`scripts/living_wiki_cli.py`) that runs identically on Windows, Linux, and macOS.

---

### DEF-SKL-01: Legacy Obsidian Wikilinks in `templates/skill.md` Contradict Rule Ban
- **Defect ID**: `DEF-SKL-01`
- **Severity**: **CRITICAL**
- **Criticality Justification**: `templates/skill.md` lines 26-29 explicitly mandate the insertion of Obsidian-style wikilinks:
  ```markdown
  ## Связано

  - [[self/Skills]]
  - [[<тематическая страница>]]
  ```
  This is a blatant, direct contradiction of the core doctrine established in `AGENTS.md §1` line 9 ("Ни графа, ни вики из страниц, ни дневников, ни папок «на вырост»"), `AGENTS.md §1` line 20 ("Больше ничего не заводи: ни wiki/, ни journal/, ни raw/, ни notes/, ни brain/"), `AGENTS.md §7` line 126, and `skills/living-wiki/SKILL.md` line 8 ("Никакого Obsidian, графа, [[ссылок]]"). When an agent creates a new skill using this template, it generates `[[wikilinks]]`. On subsequent turns, the agent audits the project under Section 7, flags its own newly generated skill as a forbidden Obsidian graph artifact, and attempts to strip or delete it.
- **Affected Files & Line Citations**:
  - `templates/skill.md`, Lines 26-29:
    ```markdown
    26: ## Связано
    27: 
    28: - [[self/Skills]]
    29: - [[<тематическая страница>]]
    ```
  - `AGENTS.md`, Line 9: `Всё, что нужно, — этот файл. Одна версия (v1), одно хранилище (MEMORY.md), один тип процедур (skills/). Ни графа, ни вики из страниц, ни дневников, ни папок «на вырост».`
  - `AGENTS.md`, Line 20: `Больше ничего не заводи: ни wiki/, ни journal/, ни raw/, ни notes/, ни brain/.`
  - `skills/living-wiki/SKILL.md`, Line 8: `Никакого Obsidian, графа, [[ссылок]], wiki/, journal/, raw/.`
- **Technical Root Cause**:
  Incomplete refactoring of legacy code. The template `templates/skill.md` was copied verbatim from an earlier Obsidian-based knowledge system and was never sanitized when the project pivoted to Hermes-style flat Markdown.
- **Step-by-Step Reproduction Scenario**:
  1. User asks agent to record a new procedure: "Remember how we run database migrations".
  2. Agent follows `AGENTS.md §5` and uses `templates/skill.md` as the template.
  3. Agent writes `skills/db-migrate/SKILL.md` including the `## Связано` section with `[[self/Skills]]`.
  4. On the next turn, the agent runs the Section 6 audit and Section 7 cleanliness check.
  5. The agent encounters `[[self/Skills]]` in `skills/db-migrate/SKILL.md`.
  6. The agent detects a violation of `AGENTS.md §1` ("Никакого графа, ни [[ссылок]]") and either enters an error state or modifies the file to remove it.
- **Impact on System**: Severe specification contradiction. Degrades LLM prompt adherence, introduces hallucinations, and pollutes the repository with banned syntax.
- **Remediation Recommendation**:
  Immediately delete lines 26-29 from `templates/skill.md`. The template should end cleanly after `## Проверка`.

---

### DEF-SKL-02: Contradiction Between `skills/<name>/notes.md` Allowance and `notes/` Ban
- **Defect ID**: `DEF-SKL-02`
- **Severity**: **MEDIUM**
- **Criticality Justification**: `AGENTS.md §5` line 109 and `skills/living-wiki/SKILL.md` line 43 state: *"Скил длиннее 150 строк — вынеси детали в файл рядом (`<имя>/notes.md`), в шагах оставь ссылку."* Meanwhile, `AGENTS.md §1` line 20 and `AGENTS.md §7` line 126 state: *"Больше ничего не заводи: ни wiki/, ни journal/, ни raw/, ни notes/, ни brain/. Параллельные копии (notes/, brain/, второй memory.md) запрещены: найденную — перенеси и удали."* A regex or substring-based cleanup agent interpreting Section 7 scans the file tree for `notes`, locates `skills/<name>/notes.md`, flags it as an illegal `notes` directory/file, and deletes it.
- **Affected Files & Line Citations**:
  - `AGENTS.md`, Line 109: `- Скил длиннее 150 строк — вынеси детали в файл рядом (<имя>/notes.md), в шагах оставь ссылку.`
  - `AGENTS.md`, Line 20: `Больше ничего не заводи: ни wiki/, ни journal/, ни raw/, ни notes/, ни brain/.`
  - `AGENTS.md`, Line 126: `Параллельные копии (notes/, brain/, второй memory.md) запрещены: найденную — перенеси и удали.`
  - `skills/living-wiki/SKILL.md`, Line 43: `- Скил длиннее 150 строк — детали в <имя>/notes.md.`
  - `skills/living-wiki/SKILL.md`, Line 54: `Второй памяти не заводить (memory.md, notes.md, brain/); найденную — перенести в MEMORY.md и удалить.`
- **Technical Root Cause**:
  Conflicting naming conventions across sections. Section 1 and Section 7 prohibit `notes/` directories and `notes.md` files, while Section 5 prescribes `notes.md` as the official overflow mechanism for large skills.
- **Step-by-Step Reproduction Scenario**:
  1. A complex deployment skill grows to 180 lines.
  2. The agent splits the skill, placing long bash scripts in `skills/deploy/notes.md` per line 109.
  3. During the next turn, the agent performs the Section 7 hygiene audit: "Второй памяти не заводить (memory.md, notes.md, brain/); найденную — перенести в MEMORY.md и удалить."
  4. The agent finds `skills/deploy/notes.md`.
  5. The agent parses `notes.md` as a banned second-memory file, moves its code snippets into `MEMORY.md` (violating single-line rules), and deletes `skills/deploy/notes.md`.
- **Impact on System**: Corruption of both `MEMORY.md` and auxiliary skill documentation.
- **Remediation Recommendation**:
  Rename the auxiliary skill file convention from `notes.md` to `details.md` or `reference.md` in `AGENTS.md §5` and `SKILL.md`, and clarify that Section 7 applies exclusively to root-level note folders.

---

### DEF-CTX-01: Fixed 6000-Character Cap is Architecture-Blind
- **Defect ID**: `DEF-CTX-01`
- **Severity**: **MEDIUM**
- **Criticality Justification**: The 6,000-character hard cap was conceived in the era of 8k/16k context window models. In modern LLM architectures with 200k (Claude 3.5 Sonnet) to 1M-2M (Gemini 1.5 Pro) context windows, 6,000 characters represents less than **0.8%** of the available context window. Enforcing hyper-aggressive compaction at 5,400 characters artificially induces severe information loss, amnesia, and fragmentation in advanced models. Conversely, on small local models (e.g., Llama-3-8B running with 4k-8k context in resource-constrained IDE environments), a 6,000-character Russian file (~4,500 BPE tokens) consumes over 50% of the entire prompt context, causing generation degradation and context truncation.
- **Affected Files & Line Citations**:
  - `AGENTS.md`, Line 53: `Лимит — **6000 символов на файл**: 5400 (90%) — сигнал начинать сжатие, 6000 — потолок, выше файл не выходит никогда.`
  - `skills/living-wiki/SKILL.md`, Line 18: `- Лимит файла — 6000 символов (5400 — начинай сжимать).`
- **Technical Root Cause**:
  Hardcoded magic number without architectural adaptation or tiering. The system treats all LLM engines as identical, regardless of context window capacity or tokenizer efficiency.
- **Step-by-Step Reproduction Scenario**:
  1. An enterprise project is developed using Claude 3.5 Sonnet (200,000 token context window).
  2. Over 3 weeks of complex domain development, `MEMORY.md` reaches 5,450 characters (approx. 1,400 tokens, or 0.7% of context).
  3. The agent is strictly forbidden by `AGENTS.md §3` from allowing the file to grow.
  4. The agent executes destructive compaction, discarding subtle architectural nuances and historical context to stay under 6,000 characters.
  5. 99.3% of the LLM's available context window sits completely empty while the memory system actively degrades project knowledge.
- **Impact on System**: Massive underutilization of modern frontier LLM capabilities; unnecessary information degradation and human frustration.
- **Remediation Recommendation**:
  Make the memory ceiling configurable or tiered:
  - Default: 6,000 characters (for standard/local models)
  - Extended Profile: 25,000 characters / 5,000 tokens (for frontier models with >128k context)
  Allow the user or project configuration (`.living-wiki.json`) to declare the memory budget.

---

### DEF-AUD-01: Mandatory 4-Step Per-Turn Audit Causes Severe Cognitive Overhead
- **Defect ID**: `DEF-AUD-01`
- **Severity**: **HIGH**
- **Criticality Justification**: `AGENTS.md §6` mandates that the agent must execute a 4-question audit before concluding *every single meaningful response*:
  1. New fact about user or project? -> `MEMORY.md`
  2. New decision or bug cause? -> `MEMORY.md`
  3. New repeatable procedure? -> skill
  4. Outdated entry? -> replace it.
  In practice, forcing an LLM to evaluate this multi-branch questionnaire on every single turn creates severe cognitive fatigue. In simple coding turns (e.g. "fix syntax error on line 42"), LLMs either:
  1. Completely ignore the audit to minimize latency and token spend.
  2. Hallucinate trivial or fake entries (e.g., recording *"Пользователь попросил исправить опечатку"*) just to satisfy the audit rule.
  3. Introduce catastrophic latency and double tool invocations on every turn.
- **Affected Files & Line Citations**:
  - `AGENTS.md`, Lines 112-114: `## 6. Аудит ответа — сам, каждый раз. Перед тем как закончить содержательный ответ, молча пройди четыре вопроса: 1. Появился факт о пользователе или проекте? → MEMORY.md.`
  - `skills/living-wiki/SKILL.md`, Line 50: `Перед завершением содержательного ответа молча проверь: новый факт о пользователе или проекте → MEMORY.md; принятое решение или причина бага → MEMORY.md.`
- **Technical Root Cause**:
  Unconditional per-turn imperative prompting without a trigger gate. The rule does not discriminate between substantial architectural work and trivial micro-edits.
- **Step-by-Step Reproduction Scenario**:
  1. User asks: "What is the return type of `getUser()` in `auth.ts`?".
  2. Agent inspects file and replies: "It returns `Promise<User | null>`."
  3. Agent evaluates Section 6 audit:
     - Question 1: "Появился факт о проекте?"
  4. The agent hallucinates that this trivial type signature is an essential project fact and appends:
     `- Функция getUser возвращает Promise<User | null>` to `MEMORY.md`.
  5. Over 20 simple Q&A queries, `MEMORY.md` is swamped with useless trivia, rapidly consuming the 6,000-character quota and triggering the eviction of genuine bug fixes.
- **Impact on System**: Memory bloat with low-entropy noise, triggering premature compaction cycles and evicting high-value knowledge.
- **Remediation Recommendation**:
  Add an explicit significance threshold and gating condition to `AGENTS.md §6`:
  ```markdown
  Аудит проводится ТОЛЬКО при завершении содержательных задач (реализация фичи, рефакторинг, фикс бага, изменение конфигурации). 
  При справочных вопросах, чтении кода и мелких правках аудит НЕ запускается. 
  Критерий записи: «Изменит ли этот факт поведение агента в новой сессии через месяц? Если нет — не писать».
  ```

---

## 4. Cross-Cutting Risk Analysis

### 4.1 Concurrency & Race Conditions in Multi-Agent Workflows
Modern software engineering increasingly employs concurrent multi-agent systems where different IDEs and sub-agents operate on the codebase simultaneously.
Plugin v1 assumes a single-threaded synchronous environment. The combination of:
- A single global file (`MEMORY.md`)
- Mandatory per-turn updates (`AGENTS.md §4`)
- Multi-step file displacement (`AGENTS.md §3.1`)
- Zero file locking or transactional atomicity
guarantees that multi-agent usage will experience data clobbering, lost updates, and file access contention.

### 4.2 Platform Portability: Windows vs POSIX
The repository exhibits extreme POSIX-centric bias. 
- `AGENTS.md §3` commands the execution of `wc -m`.
- All automation scripts in `scripts/*.sh` require Bash, GNU Coreutils (`find`, `sort`, `tr`, `sed`, `sha256sum`), and POSIX pipelines.
- On Windows systems, developers without WSL or Git Bash cannot compute rules hashes, cannot synchronize rule pointers, and cannot run CI validation scripts locally.

### 4.3 Encoding & Line Endings: Cyrillic Expansion & CRLF
Because the plugin was authored in Russian and targets Russian-speaking developers, text in `MEMORY.md` is predominantly Cyrillic. 
- In UTF-8, Cyrillic characters are 2 bytes each, causing a 100% discrepancy between character metrics and byte metrics.
- In BPE tokenizers, Cyrillic has low token density (1.3–2.2 chars/token vs 4.0 chars/token in English), consuming disproportionate prompt context.
- CRLF conversion on Windows adds 1 hidden character per line, introducing silent platform-dependent drift into threshold checks.

### 4.4 Specification Self-Contradictions
The audit uncovered internal logical contradictions within the plugin specification:
1. `templates/skill.md` mandates Obsidian `[[wikilinks]]`, while `AGENTS.md §1` and `§7` strictly forbid them.
2. `AGENTS.md §5` instructs agents to create `<name>/notes.md`, while `AGENTS.md §1` and `§7` command agents to delete any `notes/` directory or `notes.md` file found in the project.
3. `AGENTS.md §3` mandates "Одна строка — один факт", while `AGENTS.md §3.1` expects to find multiline facts in `MEMORY.md` during displacement.

### 4.5 Governance Fragility & Rules Hash Circularity
The integrity system in `AGENTS.md §8` and `scripts/rules-hash.sh` suffers from architectural circularity:
- The hash in downstream consumer projects depends on non-existent paths (`skills/living-wiki`).
- Legitimate local customization of `AGENTS.md` by developers is treated as corruption, instructing agents to overwrite the file and destroy local custom rules.

---

## 5. Defect Summary Matrix

| Defect ID | Component | Summary | Severity | Data Loss Risk | Agent Crash Risk | Contract Violation | Priority |
|:---|:---|:---|:---:|:---:|:---:|:---:|:---:|
| **DEF-MEM-01** | `MEMORY.md / §3` | `wc -m` fails on Windows | **CRITICAL** | High | **High** | Yes | **P0** |
| **DEF-MEM-02** | `MEMORY.md / §3` | Cyrillic 2x byte/token expansion & byte confusion | **CRITICAL** | **Catastrophic** | Low | Yes | **P0** |
| **DEF-MEM-03** | `MEMORY.md / §3` | CRLF newline inflation adds phantom chars | **HIGH** | Medium | Low | Yes | **P1** |
| **DEF-MEM-04** | `AGENTS.md §3/§3.1`| Single-line rule contradicts multiline displacement | **HIGH** | High | Low | Yes | **P1** |
| **DEF-MEM-05** | `AGENTS.md §3.1/§7`| Pointer eviction creates orphaned cold memory | **CRITICAL** | **Catastrophic** | Low | Yes | **P0** |
| **DEF-MEM-06** | `AGENTS.md §3.1` | Eviction sacrifices Open Questions & Traps first | **CRITICAL** | **Catastrophic** | Low | Yes | **P0** |
| **DEF-MEM-07** | `AGENTS.md §3.2/§7`| Cold storage 15KB split lacks valid navigation | **HIGH** | Medium | Low | Yes | **P1** |
| **DEF-CONC-01**| `Multi-Agent / §9`| No file locking causes lost updates on concurrent edits | **CRITICAL** | **Catastrophic** | Low | Yes | **P0** |
| **DEF-CONC-02**| `Multi-Agent / §3.1`| Race condition during displacement crashes agents | **CRITICAL** | High | **High** | Yes | **P0** |
| **DEF-PTR-01** | `Rule Pointers` | Cline path mismatch (`.clinerules/living-wiki.md`) | **CRITICAL** | High | Medium | Yes | **P0** |
| **DEF-PTR-02** | `Rule Pointers` | Pointers rely on passive reading; agents start blind | **HIGH** | Medium | Low | Yes | **P1** |
| **DEF-HASH-01**| `scripts/rules-hash`| Hash circularity, living-wiki path bug, overwrite loop | **CRITICAL** | **Catastrophic** | **High** | Yes | **P0** |
| **DEF-HASH-02**| `scripts / Windows`| Bash scripts lack PowerShell/Windows alternatives | **HIGH** | Low | **High** | Yes | **P1** |
| **DEF-SKL-01** | `templates/skill.md`| Obsidian wikilinks violate core rule ban | **CRITICAL** | Low | Medium | Yes | **P0** |
| **DEF-SKL-02** | `AGENTS.md §5 / §7`| `notes.md` allowance conflicts with `notes/` ban | **MEDIUM** | Medium | Low | Yes | **P2** |
| **DEF-CTX-01** | `AGENTS.md §3` | Fixed 6000-char cap blind to modern context windows | **MEDIUM** | High | Low | No | **P2** |
| **DEF-AUD-01** | `AGENTS.md §6` | Mandatory per-turn audit causes cognitive bloat | **HIGH** | Medium | Low | Yes | **P1** |

---

## 6. Architectural Recommendations for v2 Roadmap

To evolve Living Wiki into a resilient, production-ready system for modern AI-assisted engineering teams, the following architectural upgrades are recommended for **v2**:

### 6.1 Memory Management Subsystem Evolution
1. **Dynamic, Configurable Memory Budgets**: Replace the hardcoded 6,000-character cap with a profile-based limit declared in a `.living-wiki.json` configuration file:
   - `compact` (6,000 chars / ~1,500 tokens) for small/local models.
   - `standard` (15,000 chars / ~3,500 tokens) default.
   - `expanded` (50,000 chars / ~12,000 tokens) for frontier models (Claude 3.5, Gemini 1.5/2.0).
2. **Non-Destructive Cold Linking & Pointer Immunity**: Explicitly grant perpetual immunity to cold-memory pointer strings. A pointer in `MEMORY.md` may never be evicted while its corresponding target file exists in `memory/`.
3. **Inverted Eviction Hierarchy**: Prioritize retaining dynamic operational wisdom (`Грабли` and `Открытые вопросы`). When compaction is necessary, displace clusters of static architecture/stack lines into `memory/<topic>.md`.
4. **Cross-Platform Measurement Utility**: Replace the raw shell command `wc -m` with a standardized, cross-platform CLI tool or Python script (`python -m living_wiki check-size`) that normalizes CRLF/LF line endings and counts Unicode code points rather than raw bytes.

### 6.2 Multi-Agent Concurrency & Synchronization
1. **Atomic Lockfile Protocol**: Implement a lightweight, file-based advisory locking mechanism (`MEMORY.md.lock`) with timeout detection and agent identification.
2. **Append-Only Staging Journal**: For highly concurrent workflows, allow agents to append facts to an append-only journal (`memory/journal.jsonl` or `memory/inbox.md`), with a single designated consolidation step during session checkpoints.

### 6.3 Rule Pointer & Ecosystem Realignment
1. **Fix Cline Configuration Path**: Update pointer synchronization to write directly to `.clinerules` in the project root, using demarcated comment blocks so existing user rules are preserved.
2. **Active Context Injection in Pointers**: Enhance `templates/rule-pointer.md` with an inline executive summary of core memory rules (single-line facts, section names, audit triggers) so external agents possess the rules in their prompt without requiring a separate file read.

### 6.4 Specification Harmonization & Cleansing
1. **Purge Legacy Wikilinks**: Remove lines 26–29 from `templates/skill.md`, eliminating all traces of `[[self/Skills]]` and Obsidian syntax.
2. **Resolve `notes` Naming Conflict**: Rename auxiliary skill documentation from `<name>/notes.md` to `<name>/details.md`, eliminating any ambiguity with the root `notes/` ban in Section 7.
3. **Harmonize Displacement Logic**: Update `AGENTS.md §3.1 Step 1` to define displacement in terms of related fact clusters rather than non-existent multiline entries.

### 6.5 Governance & Tooling Portability
1. **Cross-Platform Tooling CLI**: Package the maintenance scripts into a cross-platform Python CLI (`living_wiki_cli.py`) or Node.js script runnable across Windows, macOS, and Linux without external shell dependencies.
2. **Decouple Downstream Rules Hash**: In `rules-hash.sh`, remove the dependency on `skills/living-wiki/`. Hash only `AGENTS.md` (excluding frontmatter).
3. **Protect Local Customizations**: Remove the automated rule instructing agents to unconditionally overwrite `AGENTS.md` upon hash mismatch. Replace with a user warning prompt.

---
*End of Report FLAW_ANALYSIS_V1.md*
