#!/usr/bin/env bash
#
# Smoke test for AIDM images.
#
# Processes a small public dataset and checks that the orthophoto, DSM, point
# cloud and textured model exist and are not empty. It does not check quality.
#
# Usage:
#   scripts/smoke-test.sh engine <image>   run the engine image directly
#   scripts/smoke-test.sh api <image>      run an AIDM API image and go through
#                                          its HTTP API: create a task, upload,
#                                          wait, download all.zip
#
# Examples:
#   scripts/smoke-test.sh engine ghcr.io/aidronemap/aidm:0.1.0
#   scripts/smoke-test.sh api ghcr.io/aidronemap/aidm-api:2.5.0
#
# Environment:
#   SMOKE_CACHE     where the dataset is downloaded once
#                   (default: ~/.cache/aidm-smoke-test)
#   SMOKE_OPTIONS   engine options, as command-line flags (default: --dsm)
#   SMOKE_TIMEOUT   seconds to wait for an API task (default: 3600)
#   SMOKE_GPU       set to 1 to pass --gpus all to docker
#   SMOKE_KEEP      set to 1 to keep the work folder for inspection
#
# Dataset: Brighton Beach, 18 images taken with a DJI drone.
#   Source:  https://github.com/OpenDroneMap/drone_dataset_brighton_beach
#   Commit:  pinned in scripts/smoke-test.conf
#   Licence: BSD 2-Clause, Copyright (c) 2017, Piero Toffanin
# The images are downloaded at run time and never committed to this repository.

set -euo pipefail

# Dataset pin, default options and expected outputs, shared with
# scripts/gpu-smoke-modal.py
# shellcheck source=smoke-test.conf
source "$(dirname "${BASH_SOURCE[0]}")/smoke-test.conf"

# Outputs that must exist and be non-empty, relative to the project folder
read -r -a OUTPUT_FILES <<< "$OUTPUTS"

CACHE="${SMOKE_CACHE:-$HOME/.cache/aidm-smoke-test}"
OPTIONS="${SMOKE_OPTIONS:-$DEFAULT_OPTIONS}"
TIMEOUT="${SMOKE_TIMEOUT:-3600}"

usage() {
    sed -n '/^# Usage:/,/^# Environment:/p' "$0" | sed '$d; s/^# \{0,1\}//'
    exit 2
}

log() {
    echo "[smoke] $*"
}

fail() {
    echo "[smoke] FAIL: $*" >&2
    exit 1
}

[ $# -eq 2 ] || usage
MODE="$1"
IMAGE="$2"
[ "$MODE" = "engine" ] || [ "$MODE" = "api" ] || usage

for cmd in docker curl tar python3; do
    command -v "$cmd" > /dev/null || fail "$cmd is not installed"
done

GPU_FLAGS=()
if [ "${SMOKE_GPU:-0}" = "1" ]; then
    GPU_FLAGS=(--gpus all)
fi

# Download the dataset once, at a pinned commit
fetch_dataset() {
    local dir="$CACHE/brighton_beach-$DATASET_COMMIT"
    if [ ! -d "$dir/images" ]; then
        log "Downloading dataset to $dir"
        mkdir -p "$dir.tmp"
        curl -fsSL "$DATASET_REPO/archive/$DATASET_COMMIT.tar.gz" \
            | tar -xz -C "$dir.tmp" --strip-components=1 --wildcards '*/images/*'
        mv "$dir.tmp" "$dir"
    fi
    local count
    count=$(find "$dir/images" -type f -iname '*.jpg' | wc -l)
    [ "$count" -eq "$DATASET_IMAGES" ] || fail "expected $DATASET_IMAGES images in $dir/images, found $count"
    DATASET_DIR="$dir"
}

WORK=$(mktemp -d "${TMPDIR:-/tmp}/aidm-smoke.XXXXXX")
CONTAINER=""

cleanup() {
    if [ -n "$CONTAINER" ]; then
        docker rm -f "$CONTAINER" > /dev/null 2>&1 || true
    fi
    if [ "${SMOKE_KEEP:-0}" = "1" ]; then
        log "Work folder kept at $WORK"
    else
        # The engine runs as root, so remove its files from inside a container
        docker run --rm -v "$WORK:/work" --entrypoint rm "$IMAGE" -rf /work/project > /dev/null 2>&1 || true
        rm -rf "$WORK"
    fi
}
trap cleanup EXIT

check_outputs() {
    local project="$1" missing=0 size
    for out in "${OUTPUT_FILES[@]}"; do
        if [ -s "$project/$out" ]; then
            size=$(stat -c %s "$project/$out")
            log "ok    $out ($size bytes)"
        else
            log "MISSING OR EMPTY  $out"
            missing=1
        fi
    done
    [ "$missing" -eq 0 ] || fail "some outputs are missing or empty"
}

json_field() {
    python3 -I -c 'import json, sys; d = json.load(sys.stdin)
for k in sys.argv[1].split("."): d = d[k]
print(d)' "$1"
}

run_engine() {
    mkdir -p "$WORK/project"
    cp -r "$DATASET_DIR/images" "$WORK/project/images"

    log "Running $IMAGE $OPTIONS"
    # OPTIONS is a list of flags, so word splitting is wanted here
    # shellcheck disable=SC2086
    docker run --rm "${GPU_FLAGS[@]}" -v "$WORK:/datasets" "$IMAGE" \
        --project-path /datasets project $OPTIONS

    check_outputs "$WORK/project"
}

run_api() {
    log "Starting $IMAGE"
    CONTAINER=$(docker run -d "${GPU_FLAGS[@]}" -p 127.0.0.1::3000 "$IMAGE")
    local port url
    port=$(docker port "$CONTAINER" 3000/tcp | head -n 1 | sed 's/.*://')
    url="http://127.0.0.1:$port"

    local i
    for i in $(seq 1 120); do
        curl -fs "$url/info" > "$WORK/info.json" 2> /dev/null && break
        [ "$i" -lt 120 ] || fail "API did not answer at $url/info"
        sleep 1
    done
    log "API info: $(cat "$WORK/info.json")"

    # Turn "--dsm --foo 2" into [{"name": "dsm", "value": true}, {"name": "foo", "value": "2"}]
    local options_json
    # shellcheck disable=SC2086
    options_json=$(python3 -I -c 'import json, sys
opts, args = [], sys.argv[1:]
while args:
    name = args.pop(0).lstrip("-")
    value = True
    if args and not args[0].startswith("--"):
        value = args.pop(0)
    opts.append({"name": name, "value": value})
print(json.dumps(opts))' $OPTIONS)

    local uuid
    uuid=$(curl -fsS -X POST -F "name=smoke-test" -F "options=$options_json" "$url/task/new/init" | json_field uuid)
    log "Task $uuid created, uploading images"

    local files=() f
    for f in "$DATASET_DIR"/images/*; do
        files+=(-F "images=@$f")
    done
    curl -fsS -X POST "${files[@]}" "$url/task/new/upload/$uuid" > /dev/null
    curl -fsS -X POST "$url/task/new/commit/$uuid" > /dev/null

    local start info code
    start=$(date +%s)
    while true; do
        info=$(curl -fsS "$url/task/$uuid/info")
        code=$(json_field status.code <<< "$info")
        case "$code" in
            40) break ;;
            30|50)
                curl -fsS "$url/task/$uuid/output" | tail -n 30 >&2 || true
                fail "task ended with status $code"
                ;;
        esac
        [ $(( $(date +%s) - start )) -lt "$TIMEOUT" ] || fail "task did not finish within $TIMEOUT seconds"
        log "Status $code, progress $(json_field progress <<< "$info")%"
        sleep 15
    done
    log "Task finished in $(( $(date +%s) - start )) seconds"

    curl -fsS -o "$WORK/all.zip" "$url/task/$uuid/download/all.zip"
    [ -s "$WORK/all.zip" ] || fail "all.zip is empty"
    log "Downloaded all.zip ($(stat -c %s "$WORK/all.zip") bytes)"

    mkdir -p "$WORK/project"
    python3 -I -m zipfile -e "$WORK/all.zip" "$WORK/project"
    check_outputs "$WORK/project"
}

fetch_dataset
started=$(date +%s)
"run_$MODE"
log "PASS: $MODE smoke test of $IMAGE in $(( $(date +%s) - started )) seconds"
