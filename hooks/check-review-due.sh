#!/usr/bin/env bash
# Optional accelerator for AGENTS.md section 6.4/6.5 ("review dates") — a
# best-effort SessionStart nudge, not a replacement. It reads the vault's own
# self/Reviews/ files directly (no separate state file), so there is no
# second source of truth to drift from the real data. If this hook fails to
# run or a date fails to parse, the agent is still required to do the same
# calendar check itself every session (see AGENTS.md section 0).
set -u

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-.}"
REVIEWS_DIR="$PROJECT_DIR/self/Reviews"

[[ -d "$REVIEWS_DIR" ]] || exit 0

to_epoch() {
  date -u -d "$1" +%s 2>/dev/null || date -u -j -f "%Y-%m-%d" "$1" +%s 2>/dev/null
}

today_epoch=$(date -u +%s)
overdue=""

latest_weekly=$(ls "$REVIEWS_DIR"/Weekly-*.md 2>/dev/null | sort | tail -n 1)
if [[ -n "$latest_weekly" ]]; then
  weekly_date=$(basename "$latest_weekly" .md | sed -E 's/^Weekly-//')
  weekly_epoch=$(to_epoch "$weekly_date")
  if [[ -n "$weekly_epoch" ]]; then
    days=$(( (today_epoch - weekly_epoch) / 86400 ))
    if (( days >= 7 )); then
      overdue="Weekly не обновлялся $days дн. (последний: $(basename "$latest_weekly"))."
    fi
  fi
fi

latest_monthly=$(ls "$REVIEWS_DIR"/Monthly-*.md 2>/dev/null | sort | tail -n 1)
if [[ -n "$latest_monthly" ]]; then
  monthly_period=$(basename "$latest_monthly" .md | sed -E 's/^Monthly-//')
  this_month=$(date -u +%Y-%m)
  if [[ "$monthly_period" != "$this_month" ]]; then
    overdue="${overdue:+$overdue }Monthly последний раз собирался за $monthly_period, сейчас $this_month."
  fi
fi

if [[ -n "$overdue" ]]; then
  msg="[living-wiki] Обзор, похоже, просрочен: $overdue Сверь self/Reviews/ и, если обзор реально настал (references/reviews.md), собери его в этой сессии."
  msg_escaped=$(printf '%s' "$msg" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')
  printf '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"%s"}}\n' "$msg_escaped"
fi

exit 0
