#!/usr/bin/env bash
# Regression guard for cloud-sessions/reference/{billing,verified-facts}.md wording.
#
# G06/G07/G08/G09 (gap-closure-2026-08-16.md) were all the same failure shape: a doc made a
# claim its own evidence did not support, and nothing would notice if the correction were
# later reverted or overwritten by an agent that had not read this file. This script is that
# notice. It does not re-derive the underlying facts (the commands for that are quoted in
# the docs themselves) — it only guards the specific sentences that were wrong before.
set -euo pipefail
cd "$(dirname "$0")/.."

FAIL=0
BOLD=$'\033[1m'; RED=$'\033[31m'; GREEN=$'\033[32m'; OFF=$'\033[0m'

absent() {
  local file="$1" pattern="$2" label="$3"
  if grep -qF "$pattern" "$file"; then
    printf '%sFAIL%s  %s — stale wording is back: %s\n' "$RED" "$OFF" "$label" "$pattern"
    FAIL=1
  else
    printf '%sok%s    %s\n' "$GREEN" "$OFF" "$label"
  fi
}

present() {
  local file="$1" pattern="$2" label="$3"
  if grep -qF "$pattern" "$file"; then
    printf '%sok%s    %s\n' "$GREEN" "$OFF" "$label"
  else
    printf '%sFAIL%s  %s — expected correction is missing: %s\n' "$RED" "$OFF" "$label" "$pattern"
    FAIL=1
  fi
}

printf '%sdoc-drift: cloud-sessions/reference wording regressions%s\n\n' "$BOLD" "$OFF"

# G06 — billing.md's "no API key can bill you" must not overclaim what `cs doctor` checks.
absent  reference/billing.md \
        "\`cs doctor\` fails loudly if any of those variables appear, because each one would" \
        "G06/billing-doctor-sentence-not-overclaimed"
present reference/billing.md \
        "only fails loudly for the three" \
        "G06/billing-doctor-sentence-scoped"
present reference/billing.md "V-037" "G06/billing-names-V-037"

# G07 — the not-verified table's "last open item" claim must name what it actually closed,
# not claim to have closed the whole table.
absent  reference/verified-facts.md \
        "Probe 7 also closed the last open item in this file's own \"not verified\" table" \
        "G07/last-open-item-not-overclaimed"
present reference/verified-facts.md \
        "**one specific row**" \
        "G07/last-open-item-names-the-row"

# G08 — the append-only check failure must not be described as "correctly left alone as I10
# human-only": that framing says the check is fine, when it is a check that can only fail.
absent  reference/verified-facts.md \
        "correctly left alone as I10 human-only" \
        "G08/append-only-not-conflated-with-I10"
present reference/verified-facts.md "V-013" "G08/append-only-names-V-013"

# G09 — the ledger "gap worth naming" section must cross-reference that the gap it names
# was later closed, so a reader who stops at the top of the file is not misled.
present reference/verified-facts.md \
        "This gap was closed on 2026-08-14" \
        "G09/ledger-gap-cross-referenced"

printf '\n'
if [[ "$FAIL" -eq 0 ]]; then
  printf '%sgreen.%s\n' "$GREEN" "$OFF"
  exit 0
else
  printf '%sNOT GREEN.%s\n' "$RED" "$OFF"
  exit 1
fi
