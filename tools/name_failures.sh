#!/usr/bin/env bash
# name_failures.sh TITLE FILE
#
# Says why a CI lane failed, in GitHub annotations of its check. The API serves a check's
# annotations to anyone who can read the check, where the job's log and artifact sit behind a
# second download from a host a session may not reach (5.0.0's pull request, 2026-10-09), so
# what this prints is the only account of a failure some readers get. FILE is what the lane
# printed. For the symbolic gate's output: one annotation for each of the first few CrossHair
# processes that did not exit 0, with its error lines (its last lines when it printed none), and
# one that names the runner and every line of what the gate refused, every failed target's exit
# line and the count of verdicts among them. For any other lane, one annotation with the lines
# that say why and its last lines. Prints GitHub's workflow commands and nothing else; exits 0,
# since the step that runs it is already a failure's.
set -euo pipefail

title=$1
file=$2
# GitHub keeps ten error annotations a step; the report job names up to six lanes in one step,
# and the summary names every failed target whatever this leaves out.
most=4

escape() {
  # A workflow command ends with its line, so the text's own line breaks are escaped; a
  # property's value also escapes the colon and the comma that would end it.
  local text=$1
  text=${text//'%'/'%25'}
  text=${text//$'\r'/'%0D'}
  text=${text//$'\n'/'%0A'}
  if [ "${2:-}" = property ]; then
    text=${text//':'/'%3A'}
    text=${text//','/'%2C'}
  fi
  printf '%s' "$text"
}

annotate() {
  printf '::error title=%s::%s\n' "$(escape "$title" property)" "$(escape "$1")"
}

if [ ! -s "$file" ]; then
  annotate "the lane printed nothing ($file is missing or empty)"
  exit 0
fi
memory=$(awk '/^MemTotal:/ { printf "%.1f GB", $2 / 1048576 }' /proc/meminfo)
runner="the runner: $(nproc) cores, ${memory} of memory"
exit_line='^gate: [^ ]+: exit -?[0-9]+$'

if ! grep -qE "$exit_line" "$file"; then
  why=$(grep -E 'error|Error|FAIL|failed|refus|Unable|cannot|stopped' "$file" | tail -n 20 || true)
  annotate "${runner}"$'\n'"${why}"$'\n''--- the last lines:'$'\n'"$(tail -n 12 "$file")"
  exit 0
fi

# The gate prints, for each target, its exit line, its count of verdicts and CrossHair's own lines;
# then what it refused, one line each, and its count over every target.
blocks=$(mktemp -d)
trap 'rm -rf "$blocks"' EXIT
awk -v dir="$blocks" '
  /^gate: [^ ]+: exit -?[0-9]+$/ {
    if (out != "") close(out)
    out = ""
    if ($NF != "0") { n++; out = dir "/" n; print > out }
    next
  }
  /^gate: [^ ]+: [0-9]+ confirmed over all paths, / { next }
  /^gate: / { if (out != "") close(out); out = ""; next }
  out != "" { print substr($0, 1, 1000) > out }
' "$file"

failed=$(find "$blocks" -type f | wc -l)
for ((i = 1; i <= failed && i <= most; i++)); do
  block="$blocks/$i"
  errors=$(tail -n +2 "$block" | grep -F ': error: ' || true)
  if [ -n "$errors" ]; then
    count=$(printf '%s\n' "$errors" | wc -l)
    detail=$(printf '%s\n' "$errors" | head -n 20)
    [ "$count" -le 20 ] || detail+=$'\n'"and $((count - 20)) more error lines, in the job's log"
  else
    detail=$'(no error line; its last lines)\n'"$(tail -n +2 "$block" | tail -n 15)"
  fi
  annotate "$(head -n 1 "$block")"$'\n'"${detail}"
done

refused=$(grep -E '^(gate: |  - )' "$file" |
  grep -vE '^gate: [^ ]+: (exit 0$|[0-9]+ confirmed over all paths, )' |
  grep -vE '^gate: (not proven, |CrossHair \+ Z3 over |classes with no contracts)' || true)
summary="${runner}"
[ "$failed" -le "$most" ] ||
  summary+=$'\n'"the first ${most} of ${failed} failed targets have an annotation of their own; every one is named below"
annotate "${summary}"$'\n'"${refused}"
