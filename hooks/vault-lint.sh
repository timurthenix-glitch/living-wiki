#!/usr/bin/env bash
# Optional accelerator for AGENTS.md / SKILL.md section 4.6 ("Полная уборка") —
# a best-effort structural audit of the wikilink graph, not a replacement. It
# only detects (broken [[links]], orphan pages with <2 connections, pages over
# the size threshold, pages missing from their topic index.md, orphan attachments,
# and image embeds lacking text distillation) — every fix (merge a duplicate, pick
# which link to add, how to split a page, prune unused images) stays with the agent.
# If this script fails to run or is unavailable, the agent is still required to do
# the same check itself by opening files, per AGENTS.md section 4.6.
set -u

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-.}"
WIKI_DIR="$PROJECT_DIR/wiki"
SELF_DIR="$PROJECT_DIR/self"
JOURNAL_DIR="$PROJECT_DIR/journal"
ATTACHMENTS_DIR="$PROJECT_DIR/attachments"

MODE="summary"
[[ "${1:-}" == "--list" ]] && MODE="list"

[[ -d "$WIKI_DIR" || -d "$SELF_DIR" ]] || exit 0

TMP=$(mktemp -d 2>/dev/null) || exit 0
trap 'rm -rf "$TMP"' EXIT

# Every markdown file that can act as a link source or target.
{
  find "$WIKI_DIR" -type f -name '*.md' 2>/dev/null
  find "$SELF_DIR" -type f -name '*.md' 2>/dev/null
  find "$JOURNAL_DIR" -type f -name '*.md' 2>/dev/null
  find "$PROJECT_DIR" -maxdepth 1 -type f -name '*.md' 2>/dev/null
} | sort -u > "$TMP/all_files.txt"

# Files actually graded against the "2-3 links" / size / topic-index rules:
# only pages inside wiki/ and self/ (AGENTS.md sec. 1) — self/Reviews/ is a
# dated log like journal/, not a graph node, and root-level files (index.md,
# log.md, AGENTS.md) aren't wiki pages either.
{
  find "$WIKI_DIR" -type f -name '*.md' 2>/dev/null
  find "$SELF_DIR" -type f -name '*.md' 2>/dev/null | grep -v "^${SELF_DIR}/Reviews/"
} | sort -u > "$TMP/graded_files.txt"

: > "$TMP/broken.txt"
: > "$TMP/outbound.txt"
: > "$TMP/inbound.txt"
: > "$TMP/orphans.txt"
: > "$TMP/oversized.txt"
: > "$TMP/unindexed.txt"
: > "$TMP/referenced_attachments.txt"
: > "$TMP/orphan_attachments.txt"
: > "$TMP/unexplained_attachments.txt"

realpath_m() {
  if command -v realpath >/dev/null 2>&1; then
    realpath -m "$1" 2>/dev/null
  else
    local d b
    d=$(cd "$(dirname "$1")" 2>/dev/null && pwd) || return 1
    b=$(basename "$1")
    printf '%s/%s\n' "$d" "$b"
  fi
}

sanitize() {
  # Drop fenced code blocks and inline code spans first — a `[[wikilink]]`
  # mentioned as a literal syntax example in prose is not an actual link.
  awk '/^```/ { infence = !infence; next } infence { next } { print }' "$1" \
    | sed -E 's/`[^`]*`//g'
}

while IFS= read -r file; do
  [[ -z "$file" ]] && continue
  fdir=$(dirname "$file")
  s_content=$(sanitize "$file")

  # 1. Parse wikilinks [[...]]
  printf '%s\n' "$s_content" | grep -oE '\[\[[^]]+\]\]' | while IFS= read -r raw; do
    tgt="${raw#\[\[}"; tgt="${tgt%\]\]}"
    tgt="${tgt%%|*}"; tgt="${tgt%%#*}"; tgt="${tgt%%\^*}"
    tgt="$(printf '%s' "$tgt" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    [[ -z "$tgt" ]] && continue

    resolved=""
    # Check if target is a media/attachment file (e.g. .png, .jpg, .jpeg, .gif, .webp, .svg, .bmp, .avif)
    if [[ "$tgt" =~ \.(png|jpe?g|gif|webp|svg|bmp|avif)$ ]]; then
      cand=$(realpath_m "$fdir/$tgt")
      if [[ -n "$cand" && -f "$cand" ]]; then
        resolved="$cand"
      else
        cand_att=$(realpath_m "$ATTACHMENTS_DIR/$(basename "$tgt")")
        if [[ -n "$cand_att" && -f "$cand_att" ]]; then
          resolved="$cand_att"
        else
          cand_root=$(realpath_m "$PROJECT_DIR/$tgt")
          if [[ -n "$cand_root" && -f "$cand_root" ]]; then
            resolved="$cand_root"
          fi
        fi
      fi
      if [[ -z "$resolved" ]]; then
        printf '%s -> %s\n' "$file" "$raw" >> "$TMP/broken.txt"
      else
        printf '%s\n' "$resolved" >> "$TMP/referenced_attachments.txt"
      fi
      continue
    fi

    cand=$(realpath_m "$fdir/$tgt.md")
    if [[ -n "$cand" && -f "$cand" ]]; then
      resolved="$cand"
    else
      base=$(basename "$tgt")
      hit=$(grep -E "(^|/)${base}\.md\$" "$TMP/all_files.txt" 2>/dev/null | head -n1)
      [[ -n "$hit" ]] && resolved="$hit"
    fi

    if [[ -n "$resolved" ]]; then
      printf '%s\n' "$file" >> "$TMP/outbound.txt"
      printf '%s\n' "$resolved" >> "$TMP/inbound.txt"
    else
      printf '%s -> %s\n' "$file" "$raw" >> "$TMP/broken.txt"
    fi
  done

  # 2. Parse standard markdown image links ![alt](path)
  printf '%s\n' "$s_content" | grep -oE '!\[[^]]*\]\([^)]+\)' | while IFS= read -r md_img; do
    tgt=$(printf '%s' "$md_img" | sed -E 's/^!\[[^]]*\]\(([^)]+)\)$/\1/')
    tgt="$(printf '%s' "$tgt" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    # Strip optional title or angle brackets: ![alt](<path> "title") or ![alt](path "title")
    tgt=$(printf '%s' "$tgt" | sed -E 's/^<([^>]+)>.*/\1/; s/^([^[:space:]]+).*/\1/')
    [[ -z "$tgt" ]] && continue
    [[ "$tgt" =~ ^https?:// ]] && continue

    resolved=""
    cand=$(realpath_m "$fdir/$tgt")
    if [[ -n "$cand" && -f "$cand" ]]; then
      resolved="$cand"
    else
      cand_att=$(realpath_m "$ATTACHMENTS_DIR/$(basename "$tgt")")
      if [[ -n "$cand_att" && -f "$cand_att" ]]; then
        resolved="$cand_att"
      else
        cand_root=$(realpath_m "$PROJECT_DIR/$tgt")
        if [[ -n "$cand_root" && -f "$cand_root" ]]; then
          resolved="$cand_root"
        fi
      fi
    fi
    if [[ -z "$resolved" ]]; then
      printf '%s -> %s\n' "$file" "$md_img" >> "$TMP/broken.txt"
    else
      printf '%s\n' "$resolved" >> "$TMP/referenced_attachments.txt"
    fi
  done
done < "$TMP/all_files.txt"

# Check for orphan attachments in attachments/
if [[ -d "$ATTACHMENTS_DIR" ]]; then
  find "$ATTACHMENTS_DIR" -type f ! -name '.gitkeep' 2>/dev/null | while IFS= read -r att_file; do
    att_canon=$(realpath_m "$att_file")
    if ! grep -q -x -F "$att_canon" "$TMP/referenced_attachments.txt" 2>/dev/null; then
      printf '%s\n' "$att_file" >> "$TMP/orphan_attachments.txt"
    fi
  done
fi

while IFS= read -r file; do
  [[ -z "$file" ]] && continue

  out_n=$(grep -c -x -F "$file" "$TMP/outbound.txt" 2>/dev/null); out_n=${out_n:-0}
  in_n=$(grep -c -x -F "$file" "$TMP/inbound.txt" 2>/dev/null); in_n=${in_n:-0}
  total=$(( out_n + in_n ))
  if (( total < 2 )); then
    printf '%s (связей: %d)\n' "$file" "$total" >> "$TMP/orphans.txt"
  fi

  lines=$(wc -l < "$file" 2>/dev/null | tr -d '[:space:]'); lines=${lines:-0}
  words=$(wc -w < "$file" 2>/dev/null | tr -d '[:space:]'); words=${words:-0}
  if (( lines > 200 || words > 2000 )); then
    printf '%s (%d строк, %d слов)\n' "$file" "$lines" "$words" >> "$TMP/oversized.txt"
  fi

  # Guardrail: Check if note has an image embed but lacks meaningful explanatory text
  if grep -qE '(!\[\[|!\[[^]]*\]\()' "$file" 2>/dev/null; then
    prose_words=$(awk '
      /^---$/ { infm = !infm; next }
      infm { next }
      /^#/ { next }
      /^!/ { next }
      {
        gsub(/\[\[[^]]*\]\]/, "")
        print
      }
    ' "$file" | wc -w | tr -d '[:space:]')
    prose_words=${prose_words:-0}
    if (( prose_words < 10 )); then
      printf '%s (всего %d слов текста: требуется 1-3 строки пояснения)\n' "$file" "$prose_words" >> "$TMP/unexplained_attachments.txt"
    fi
  fi
done < "$TMP/graded_files.txt"

find "$WIKI_DIR" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | while IFS= read -r topic_dir; do
  idx="$topic_dir/index.md"
  [[ -f "$idx" ]] || continue
  find "$topic_dir" -maxdepth 1 -type f -name '*.md' ! -name 'index.md' 2>/dev/null | while IFS= read -r page; do
    base=$(basename "$page" .md)
    if ! grep -qE "\[\[([^]|#]*/)?${base}(\||#|\]\])" "$idx" 2>/dev/null; then
      printf '%s\n' "$page" >> "$TMP/unindexed.txt"
    fi
  done
done

count() { wc -l < "$1" 2>/dev/null | tr -d '[:space:]'; }
broken_n=$(count "$TMP/broken.txt"); broken_n=${broken_n:-0}
orphans_n=$(count "$TMP/orphans.txt"); orphans_n=${orphans_n:-0}
oversized_n=$(count "$TMP/oversized.txt"); oversized_n=${oversized_n:-0}
unindexed_n=$(count "$TMP/unindexed.txt"); unindexed_n=${unindexed_n:-0}
orphan_att_n=$(count "$TMP/orphan_attachments.txt"); orphan_att_n=${orphan_att_n:-0}
unexplained_att_n=$(count "$TMP/unexplained_attachments.txt"); unexplained_att_n=${unexplained_att_n:-0}
total=$(( broken_n + orphans_n + oversized_n + unindexed_n + orphan_att_n + unexplained_att_n ))

if [[ "$MODE" == "list" ]]; then
  if (( total == 0 )); then
    echo "[vault-lint] Проблем не найдено."
    exit 0
  fi
  echo "[vault-lint] Найдено: битых ссылок $broken_n, страниц-сирот $orphans_n, разросшихся страниц $oversized_n, вне индекса темы $unindexed_n, сирот-вложений $orphan_att_n, вложений без текста $unexplained_att_n."
  if (( broken_n > 0 )); then echo; echo "-- Битые ссылки --"; cat "$TMP/broken.txt"; fi
  if (( orphans_n > 0 )); then echo; echo "-- Страницы-сироты (<2 связей) --"; cat "$TMP/orphans.txt"; fi
  if (( oversized_n > 0 )); then echo; echo "-- Разросшиеся страницы (>200 строк / >2000 слов) --"; cat "$TMP/oversized.txt"; fi
  if (( unindexed_n > 0 )); then echo; echo "-- Не упомянуты в index.md своей темы --"; cat "$TMP/unindexed.txt"; fi
  if (( orphan_att_n > 0 )); then echo; echo "-- Неиспользуемые вложения в attachments/ (сироты) --"; cat "$TMP/orphan_attachments.txt"; fi
  if (( unexplained_att_n > 0 )); then echo; echo "-- Вложения без текстового описания (<10 слов пояснения) --"; cat "$TMP/unexplained_attachments.txt"; fi
  exit 0
fi

if (( total > 0 )); then
  msg="[living-wiki] vault-lint: битых $broken_n, сирот $orphans_n, разросшихся $oversized_n, вне индекса $unindexed_n, сирот-вложений $orphan_att_n, вложений без текста $unexplained_att_n. Запусти hooks/vault-lint.sh --list и разберись при полной уборке (AGENTS.md раздел 4.6)."
  msg_escaped=$(printf '%s' "$msg" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')
  printf '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"%s"}}\n' "$msg_escaped"
fi

exit 0
