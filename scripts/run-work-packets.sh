#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(git -C "$SCRIPT_DIR/.." rev-parse --show-toplevel)
PACKET_DIR="$ROOT/docs/work-packets"
PACKET_INDEX="$PACKET_DIR/README.md"
RESULT_SCHEMA="$ROOT/scripts/work-packet-result.schema.json"
RUN_ROOT="$ROOT/.work-packet-runs"
CODEX_BIN=${CODEX_BIN:-codex}

FROM_PACKET=001
TO_PACKET=999
MAX_REPAIRS=2
DRY_RUN=false
IGNORE_USER_CONFIG=true

usage() {
  cat <<'EOF'
Usage: scripts/run-work-packets.sh [options]

Run implementation work packets serially in fresh Codex contexts.

Options:
  --from NNN          Start at packet NNN (default: 001).
  --to NNN            Stop after packet NNN (default: last packet).
  --max-repairs N     Maximum repair attempts per packet (default: 2).
  --use-user-config   Load ~/.codex/config.toml instead of isolated defaults.
  --dry-run           Print the packets that would run without changing anything.
  -h, --help          Show this help.

Completed packets are recognized by a Git commit trailer of the form:

  Work-Packet: NNN

The runner requires a clean working tree and creates one commit per verified
packet. Re-running it skips completed packet commits and resumes at the next one.
EOF
}

log() {
  printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
}

die() {
  log "ERROR: $*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

validate_packet_id() {
  [[ "$1" =~ ^[0-9]{3}$ ]] || die "Packet IDs must contain exactly three digits: $1"
}

while (($# > 0)); do
  case "$1" in
    --from)
      (($# >= 2)) || die "--from requires a packet ID"
      FROM_PACKET=$2
      shift 2
      ;;
    --to)
      (($# >= 2)) || die "--to requires a packet ID"
      TO_PACKET=$2
      shift 2
      ;;
    --max-repairs)
      (($# >= 2)) || die "--max-repairs requires a number"
      MAX_REPAIRS=$2
      shift 2
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    --use-user-config)
      IGNORE_USER_CONFIG=false
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "Unknown option: $1"
      ;;
  esac
done

validate_packet_id "$FROM_PACKET"
validate_packet_id "$TO_PACKET"
[[ "$MAX_REPAIRS" =~ ^[0-9]+$ ]] || die "--max-repairs must be a non-negative integer"
((10#$FROM_PACKET <= 10#$TO_PACKET)) || die "--from must not be greater than --to"

require_command git
require_command jq
require_command flock
require_command "$CODEX_BIN"

[[ -f "$PACKET_INDEX" ]] || die "Missing packet index: $PACKET_INDEX"
[[ -f "$RESULT_SCHEMA" ]] || die "Missing result schema: $RESULT_SCHEMA"
git -C "$ROOT" config user.name >/dev/null \
  || die "Git user.name is not configured; packet checkpoint commits would fail."
git -C "$ROOT" config user.email >/dev/null \
  || die "Git user.email is not configured; packet checkpoint commits would fail."

mapfile -d '' PACKETS < <(
  find "$PACKET_DIR" -maxdepth 1 -type f -name '[0-9][0-9][0-9]-*.md' -print0 | sort -z
)
((${#PACKETS[@]} > 0)) || die "No numbered work packets found in $PACKET_DIR"

packet_is_in_range() {
  local id=$1
  ((10#$id >= 10#$FROM_PACKET && 10#$id <= 10#$TO_PACKET))
}

packet_is_completed() {
  local id=$1
  local trailers
  trailers=$(git -C "$ROOT" log --format='%(trailers:key=Work-Packet,valueonly)')
  grep -Fxq "$id" <<<"$trailers"
}

if $DRY_RUN; then
  for packet in "${PACKETS[@]}"; do
    id=$(basename "$packet" | cut -c1-3)
    packet_is_in_range "$id" || continue
    if packet_is_completed "$id"; then
      printf 'skip %s (completed) %s\n' "$id" "$packet"
    else
      printf 'run  %s             %s\n' "$id" "$packet"
    fi
  done
  exit 0
fi

[[ -z "$(git -C "$ROOT" status --porcelain=v1 --untracked-files=all)" ]] \
  || die "Working tree is not clean. Commit or otherwise resolve existing changes before starting."

mkdir -p "$RUN_ROOT"
exec 9>"$RUN_ROOT/runner.lock"
flock -n 9 || die "Another work-packet runner is already active."

INITIAL_BRANCH=$(git -C "$ROOT" symbolic-ref --quiet --short HEAD) \
  || die "Run the packet loop from a branch, not detached HEAD."
RUN_ID=$(date -u +%Y%m%dT%H%M%SZ)
RUN_DIR="$RUN_ROOT/$RUN_ID"
mkdir -p "$RUN_DIR"

log "Serial run $RUN_ID started on branch $INITIAL_BRANCH"
log "Logs: $RUN_DIR"
if $IGNORE_USER_CONFIG; then
  log "Personal Codex config: ignored (saved authentication remains available)"
else
  log "Personal Codex config: enabled"
fi

assert_repository_control_unchanged() {
  local expected_head=$1
  local current_branch current_head
  current_branch=$(git -C "$ROOT" symbolic-ref --quiet --short HEAD) \
    || die "An agent detached HEAD. Stop and inspect the repository."
  current_head=$(git -C "$ROOT" rev-parse HEAD)
  [[ "$current_branch" == "$INITIAL_BRANCH" ]] \
    || die "An agent changed branches from $INITIAL_BRANCH to $current_branch."
  [[ "$current_head" == "$expected_head" ]] \
    || die "An agent created or changed commits. Expected HEAD $expected_head, found $current_head."
  [[ -f "$RESULT_SCHEMA" && -f "$ROOT/scripts/run-work-packets.sh" ]] \
    || die "An agent removed or renamed packet-runner control files."
  [[ -z "$(git -C "$ROOT" status --porcelain=v1 --untracked-files=all -- scripts/run-work-packets.sh scripts/work-packet-result.schema.json)" ]] \
    || die "An agent modified packet-runner control files."
  [[ -z "$(git -C "$ROOT" status --porcelain=v1 --untracked-files=all -- docs/work-packets)" ]] \
    || die "An agent modified the authoritative work-packet definitions."
}

validate_result() {
  local result_file=$1
  [[ -s "$result_file" ]] || die "Codex did not produce a result: $result_file"
  jq -e '
    type == "object" and
    (.status == "completed" or .status == "needs_changes" or .status == "blocked") and
    (.summary | type == "string") and
    (.verification | type == "array") and
    (.issues | type == "array")
  ' "$result_file" >/dev/null \
    || die "Codex returned a malformed result: $result_file"
}

run_agent() {
  local phase=$1
  local packet_id=$2
  local sandbox=$3
  local prompt=$4
  local result_file=$5
  local phase_dir
  local -a codex_command
  phase_dir=$(dirname "$result_file")
  mkdir -p "$phase_dir"

  codex_command=(
    "$CODEX_BIN" exec
    --ephemeral
    --sandbox "$sandbox"
    -C "$ROOT"
    --output-schema "$RESULT_SCHEMA"
    --output-last-message "$result_file"
  )
  if $IGNORE_USER_CONFIG; then
    codex_command+=(--ignore-user-config)
  fi
  codex_command+=("$prompt")

  log "Packet $packet_id: starting fresh $phase context"
  if ! "${codex_command[@]}" \
    >"$phase_dir/$phase.stdout.log" \
    2>"$phase_dir/$phase.stderr.log"; then
    die "Packet $packet_id $phase process failed. See $phase_dir/$phase.stderr.log"
  fi
  validate_result "$result_file"
  log "Packet $packet_id: $phase returned $(jq -r '.status' "$result_file")"
}

stop_for_result() {
  local packet_id=$1
  local phase=$2
  local result_file=$3
  log "Packet $packet_id stopped during $phase: $(jq -r '.summary' "$result_file")" >&2
  jq -r '.issues[] | "  - " + .' "$result_file" >&2
  die "Resolve the packet manually, restore a clean tree, then rerun the serial runner."
}

completed_count=0
skipped_count=0

for packet in "${PACKETS[@]}"; do
  packet_name=$(basename "$packet")
  packet_id=${packet_name:0:3}
  packet_is_in_range "$packet_id" || continue

  if packet_is_completed "$packet_id"; then
    log "Packet $packet_id: already completed; skipping"
    ((skipped_count += 1))
    continue
  fi

  [[ -z "$(git -C "$ROOT" status --porcelain=v1 --untracked-files=all)" ]] \
    || die "Packet $packet_id cannot start because the working tree is not clean."

  packet_dir="$RUN_DIR/$packet_id"
  mkdir -p "$packet_dir"
  head_before=$(git -C "$ROOT" rev-parse HEAD)

  implementation_prompt=$(printf '%s\n' \
    "Implement only $packet." \
    "Read $PACKET_INDEX and only the design sections referenced by packet $packet_id." \
    "This is one step in a serial unattended run. Do not spawn subagents, start another packet, commit, amend, switch branches, or modify scripts/run-work-packets.sh, scripts/work-packet-result.schema.json, or .work-packet-runs/." \
    "Inspect the repository before editing. Keep the packet's in-scope and out-of-scope boundaries authoritative. Preserve existing behavior and satisfy every acceptance criterion." \
    "Run every verification command required by the packet. Return status completed only when all acceptance criteria pass. Return blocked when infrastructure, missing authority, ambiguity, or a failed check prevents completion. Do not conceal or bypass failures." \
    "Put exact commands and concise results in verification, and all unresolved concerns in issues. Stop after the packet handoff.")

  implementation_result="$packet_dir/implementation.json"
  run_agent implementation "$packet_id" workspace-write "$implementation_prompt" "$implementation_result"
  assert_repository_control_unchanged "$head_before"

  implementation_status=$(jq -r '.status' "$implementation_result")
  [[ "$implementation_status" == "completed" ]] \
    || stop_for_result "$packet_id" implementation "$implementation_result"

  repair_attempt=0
  verification_round=1
  while true; do
    status_before_verification=$(git -C "$ROOT" status --porcelain=v1 --untracked-files=all)
    verifier_prompt=$(printf '%s\n' \
      "Independently verify the uncommitted implementation of $packet against the packet's complete contract." \
      "Read $PACKET_INDEX and only the design sections referenced by packet $packet_id." \
      "You are a verifier, not the producer. Do not edit, format, stage, commit, amend, switch branches, spawn subagents, begin another packet, or modify .work-packet-runs/." \
      "Inspect the complete diff and run the packet's relevant acceptance and verification commands. Use non-mutating test options when available." \
      "Return completed only if the implementation stays within scope and every acceptance criterion is supported by code and verification evidence." \
      "Return needs_changes for concrete, actionable implementation defects. Return blocked for missing infrastructure, authority, or design decisions that a repair agent cannot safely resolve." \
      "List exact checks in verification and every finding in issues.")

    verification_result="$packet_dir/verification-$verification_round.json"
    run_agent "verification-$verification_round" "$packet_id" workspace-write "$verifier_prompt" "$verification_result"
    assert_repository_control_unchanged "$head_before"
    status_after_verification=$(git -C "$ROOT" status --porcelain=v1 --untracked-files=all)
    [[ "$status_after_verification" == "$status_before_verification" ]] \
      || die "Packet $packet_id verifier changed repository files. Inspect $verification_result and the working tree."

    verification_status=$(jq -r '.status' "$verification_result")
    case "$verification_status" in
      completed)
        break
        ;;
      blocked)
        stop_for_result "$packet_id" "verification-$verification_round" "$verification_result"
        ;;
      needs_changes)
        if ((repair_attempt >= MAX_REPAIRS)); then
          stop_for_result "$packet_id" "verification-$verification_round (repair limit reached)" "$verification_result"
        fi
        ((repair_attempt += 1))
        repair_prompt=$(printf '%s\n' \
          "Repair only the implementation of $packet." \
          "Read the independent verifier result at $verification_result and address every actionable issue." \
          "Read $PACKET_INDEX and only the design sections referenced by packet $packet_id." \
          "Do not spawn subagents, start another packet, commit, amend, switch branches, or modify scripts/run-work-packets.sh, scripts/work-packet-result.schema.json, or .work-packet-runs/." \
          "Keep changes within packet scope and rerun all relevant verification commands." \
          "Return completed only when the repair and checks succeed; otherwise return blocked with exact unresolved issues.")
        repair_result="$packet_dir/repair-$repair_attempt.json"
        run_agent "repair-$repair_attempt" "$packet_id" workspace-write "$repair_prompt" "$repair_result"
        assert_repository_control_unchanged "$head_before"
        repair_status=$(jq -r '.status' "$repair_result")
        [[ "$repair_status" == "completed" ]] \
          || stop_for_result "$packet_id" "repair-$repair_attempt" "$repair_result"
        ((verification_round += 1))
        ;;
    esac
  done

  packet_title=$(sed -n "1s/^# $packet_id: //p" "$packet")
  [[ -n "$packet_title" ]] || packet_title=$packet_name
  git -C "$ROOT" check-ignore -q "$RUN_ROOT/runner.lock" \
    || die "Packet run logs are no longer ignored; refusing to stage repository changes."
  git -C "$ROOT" add -A
  git -C "$ROOT" commit --allow-empty \
    -m "feat(packet-$packet_id): $packet_title" \
    -m "Work-Packet: $packet_id"

  [[ -z "$(git -C "$ROOT" status --porcelain=v1 --untracked-files=all)" ]] \
    || die "Packet $packet_id commit left tracked or untracked changes behind."

  ((completed_count += 1))
  log "Packet $packet_id: verified and committed"
done

log "Serial run complete: $completed_count packet(s) committed, $skipped_count already completed."
