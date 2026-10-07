#!/usr/bin/env bash
# Continue an `opencode run` session that stopped because a response hit the output-token limit.
#
# OpenCode 1.18.31 ends its loop after any step that finishes with a reason other than
# "tool-calls"/"unknown". When a single response spends the whole output budget on reasoning
# (`"reason":"length"`, no tool call), the run exits 0 and nothing was done. This wrapper runs
# `opencode run` once, and while the last event is such a length stop, sends one fixed, task-neutral
# nudge to the same session (at most OPENCODE_CONTINUE_ON_LENGTH times). It is harness-generic:
# usable by PatchEval's adapter and by any other `opencode run` invocation.
#
# Usage: opencode_continue.sh [opencode run arguments...]
#   The first call is `opencode run "$@"`; its stdin (the prompt, if not given positionally) passes
#   through. `--format json` is required so events can be read. Continuations run
#   `opencode run -s <session> $OPENCODE_CONTINUE_RUN_ARGS` with the nudge on stdin (a positional
#   message would reach the model wrapped in quotes).
# Environment:
#   OPENCODE_CONTINUE_ON_LENGTH        maximum continuations (default 0: run once, no continuation)
#   OPENCODE_CONTINUE_RUN_ARGS         continuation flags (default: --format json --auto)
#   OPENCODE_CONTINUE_STARTUP_TIMEOUT  seconds a continuation may take to start a step (default 300)
#   OPENCODE_CONTINUE_STARTUP_RETRIES  restarts of a stalled continuation (default 2)
#   OPENCODE_CONTINUE_RECORD_DIR       if set, write continuations.json and length_stop_<n>.patch there
#   OPENCODE_CONTINUE_SNAPSHOT_FILE    patch file to snapshot at each length stop when non-empty
#                                      (otherwise `git diff HEAD` of the working directory)
# Exit status: that of the last opencode run (a continuation that never starts keeps the earlier status).
set -u

NUDGE='Your previous response hit the output token limit before calling a tool, so nothing was done. Continue from where you are: keep your reasoning short and make your next tool call now (for example, apply the edit you planned or run the next check).'

max=${OPENCODE_CONTINUE_ON_LENGTH:-0}
case $max in ''|*[!0-9]*) echo "opencode_continue: OPENCODE_CONTINUE_ON_LENGTH must be a non-negative integer" >&2; exit 2;; esac
cont_args=${OPENCODE_CONTINUE_RUN_ARGS:---format json --auto}
startup=${OPENCODE_CONTINUE_STARTUP_TIMEOUT:-300}
retries=${OPENCODE_CONTINUE_STARTUP_RETRIES:-2}
record_dir=${OPENCODE_CONTINUE_RECORD_DIR:-}
snapshot_file=${OPENCODE_CONTINUE_SNAPSHOT_FILE:-}

tmp=$(mktemp -d "${TMPDIR:-/tmp}/opencode-continue.XXXXXX")
log=$tmp/events.jsonl
: > "$log"
printf '%s\n' "$NUDGE" > "$tmp/nudge.txt"
nudge_sha=$(sha256sum "$tmp/nudge.txt" | cut -d' ' -f1)

# First run: the prompt arrives on stdin (or as arguments) exactly as without the wrapper.
opencode run "$@" | tee -a "$log"
status=${PIPESTATUS[0]}
first_exit=$status
n=0
stalls=0
final_reason=""

last_event() { grep -a '^{"type":"' "$log" | tail -n 1; }

snapshot() {  # snapshot <n>: what would have been submitted had the run ended at this length stop
  [ -n "$record_dir" ] || return 0
  local out="$record_dir/length_stop_$1.patch"
  if [ -n "$snapshot_file" ] && [ -s "$snapshot_file" ]; then
    cp "$snapshot_file" "$out" 2>/dev/null || : > "$out"
  elif git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git diff HEAD -U3 > "$out" 2>/dev/null || : > "$out"
  else
    : > "$out"
  fi
}

started_since() {  # started_since <file>: a new step began in that continuation's output
  grep -aq '^{"type":"step_start"' "$1" 2>/dev/null
}

continue_once() {  # continue_once <session>; sets status; returns 1 if it never started
  local sid=$1 attempt=0 out opid tpid waited
  while [ "$attempt" -le "$retries" ]; do
    attempt=$((attempt + 1))
    out=$tmp/continuation_${n}_${attempt}.jsonl
    : > "$out"
    # shellcheck disable=SC2086
    opencode run -s "$sid" $cont_args < "$tmp/nudge.txt" > "$out" &
    opid=$!
    tail -n +1 -f --pid="$opid" "$out" 2>/dev/null | tee -a "$log" &
    tpid=$!
    waited=0
    while ! started_since "$out" && kill -0 "$opid" 2>/dev/null && [ "$waited" -lt "$((startup * 2))" ]; do
      sleep 0.5
      waited=$((waited + 1))
    done
    if started_since "$out"; then
      wait "$opid"
      status=$?
      wait "$tpid" 2>/dev/null
      return 0
    fi
    if kill -0 "$opid" 2>/dev/null; then
      kill "$opid" 2>/dev/null
      sleep 1
      kill -9 "$opid" 2>/dev/null
    fi
    wait "$opid" 2>/dev/null
    wait "$tpid" 2>/dev/null
    stalls=$((stalls + 1))
    echo "[continue-on-length] continuation $n did not start (attempt $attempt)" >&2
  done
  return 1
}

while [ "$status" -eq 0 ] && [ "$n" -lt "$max" ]; do
  last=$(last_event)
  case $last in
    '{"type":"step_finish",'*'"reason":"length"'*) ;;
    *) break ;;
  esac
  sid=$(printf '%s\n' "$last" | sed -n 's/^{"type":"step_finish","timestamp":[0-9]*,"sessionID":"\([^"]*\)".*/\1/p')
  [ -n "$sid" ] || break
  n=$((n + 1))
  snapshot "$n"
  echo "[continue-on-length] $n/$max session=$sid" >&2
  continue_once "$sid" || break
done

last=$(last_event)
case $last in
  '{"type":"step_finish",'*) final_reason=$(printf '%s\n' "$last" | sed -n 's/.*"reason":"\([^"]*\)".*/\1/p') ;;
esac

if [ -n "$record_dir" ]; then
  printf '{"max": %s, "continuations": %s, "startup_stalls": %s, "first_exit": %s, "exit": %s, "final_reason": "%s", "nudge_sha256": "%s"}\n' \
    "$max" "$n" "$stalls" "$first_exit" "$status" "$final_reason" "$nudge_sha" > "$record_dir/.continuations.json.tmp" \
    && mv -f "$record_dir/.continuations.json.tmp" "$record_dir/continuations.json"
fi
rm -rf "$tmp"
exit "$status"
