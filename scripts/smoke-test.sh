#!/usr/bin/env bash
#
# Smoke test for AIDM images.
#
# Processes a public drone survey and checks that the orthophoto, DSM, point
# cloud and textured model exist and are not empty, and that the orthophoto
# is georeferenced where the images were taken
# (scripts/check-georeferencing.py). It does not check quality.
#
# Usage:
#   scripts/smoke-test.sh engine <image>   run the engine image directly
#   scripts/smoke-test.sh api <image>      run an AIDM API image and go through
#                                          its HTTP API: create a task, upload,
#                                          wait, download all.zip
#   scripts/smoke-test.sh golden-input <image>
#                                          run the engine image on the full set
#                                          up to and including openmvs and pack
#                                          the golden input (ADR 0004)
#
# Examples:
#   scripts/smoke-test.sh engine ghcr.io/aidronemap/aidm:0.1.0
#   scripts/smoke-test.sh api ghcr.io/aidronemap/aidm-api:2.5.0
#
# Environment:
#   SMOKE_DATASET   ci (default, 32 images) or full (73 images); see
#                   scripts/smoke-test.conf
#   SMOKE_CACHE     where the dataset zip is downloaded once
#                   (default: ~/.cache/aidm-smoke-test)
#   SMOKE_OPTIONS   engine options, as command-line flags (default: --dsm)
#   SMOKE_TIMEOUT   seconds to wait for an API task (default: 10800)
#   SMOKE_GPU       set to 1 to pass --gpus all to docker
#   SMOKE_KEEP      set to 1 to keep the work folder for inspection
#
# golden-input only:
#   GOLDEN_OUT      folder for golden-input.zip, golden-input.zip.sha256 and
#                   golden-input.manifest (default: ./golden-input)
#   GOLDEN_COMMIT   the engine commit the image was built from (required)
#   GOLDEN_IMAGE    the image by digest, for example
#                   ghcr.io/aidronemap/aidm-ci@sha256:... (required)
#   SMOKE_DATASET defaults to full and must be full: the golden input is a run
#   of the full set.
#
# Datasets: aidm-vineyard-ci v1 (32 images) and aidm-vineyard-small v1
#   (73 images), RGB images from a DJI Mavic 3M, pinned by URL and SHA-256 in
#   scripts/smoke-test.conf.
#   Licence: CC BY 4.0, Attribution: AIDroneMap, provided by ITALDRONI
# The images are downloaded at run time and never committed to this repository.
# Every run checks the SHA-256 of the zip and the image count before use.

set -euo pipefail

# Dataset pin, default options and expected outputs, shared with
# scripts/gpu-smoke-modal.py
SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source-path=SCRIPTDIR source=smoke-test.conf
source "$SCRIPTS/smoke-test.conf"

# Outputs that must exist and be non-empty, relative to the project folder
read -r -a OUTPUT_FILES <<< "$OUTPUTS"

# What the golden input keeps of the project folder, relative to it: what
# odm_filterpoints and the later stages read, and what the earlier stages
# look for before they skip their work on --rerun-from odm_filterpoints.
# A folder is kept with everything in it.
GOLDEN_PATHS=(
    images.json
    cameras.json
    odm_georeferencing
    opensfm/camera_mappings.npz
    opensfm/config.yaml
    opensfm/image_list.txt
    opensfm/reconstruction.json
    opensfm/reconstruction.topocentric.json
    opensfm/tracks.csv
    opensfm/updated_config.txt
    opensfm/stats
    opensfm/undistorted/nominal_done.txt
    opensfm/undistorted/reconstruction.nvm
    opensfm/undistorted/images
    opensfm/undistorted/openmvs/scene_dense_dense_filtered.ply
)
# Folders the opensfm stage only checks for, kept empty
GOLDEN_EMPTY_FOLDERS=(
    opensfm/exif
    opensfm/features
    opensfm/matches
)

CACHE="${SMOKE_CACHE:-$HOME/.cache/aidm-smoke-test}"
OPTIONS="${SMOKE_OPTIONS:-$DEFAULT_OPTIONS}"
TIMEOUT="${SMOKE_TIMEOUT:-10800}"

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
[ "$MODE" = "engine" ] || [ "$MODE" = "api" ] || [ "$MODE" = "golden-input" ] || usage

DEFAULT_DATASET=ci
if [ "$MODE" = "golden-input" ]; then
    DEFAULT_DATASET=full
    [ "${SMOKE_DATASET:-full}" = "full" ] || fail "golden-input runs the full set, SMOKE_DATASET is '$SMOKE_DATASET'"
    [ -n "${GOLDEN_COMMIT:-}" ] || fail "golden-input needs GOLDEN_COMMIT, the engine commit of the image"
    [ -n "${GOLDEN_IMAGE:-}" ] || fail "golden-input needs GOLDEN_IMAGE, the image by digest"
fi
GOLDEN_OUT="${GOLDEN_OUT:-$PWD/golden-input}"

case "${SMOKE_DATASET:-$DEFAULT_DATASET}" in
    ci)
        DATASET_URL="$CI_DATASET_URL"
        DATASET_SHA256="$CI_DATASET_SHA256"
        DATASET_IMAGES="$CI_DATASET_IMAGES"
        ;;
    full)
        DATASET_URL="$FULL_DATASET_URL"
        DATASET_SHA256="$FULL_DATASET_SHA256"
        DATASET_IMAGES="$FULL_DATASET_IMAGES"
        DATASET_TAG="$FULL_DATASET_TAG"
        DATASET_COMMIT="$FULL_DATASET_COMMIT"
        ;;
    *)
        fail "SMOKE_DATASET is '$SMOKE_DATASET', expected ci or full"
        ;;
esac
DATASET_ZIP="${DATASET_URL##*/}"

for cmd in docker curl sha256sum python3; do
    command -v "$cmd" > /dev/null || fail "$cmd is not installed"
done

GPU_FLAGS=()
if [ "${SMOKE_GPU:-0}" = "1" ]; then
    GPU_FLAGS=(--gpus all)
fi

WORK=$(mktemp -d "${TMPDIR:-/tmp}/aidm-smoke.XXXXXX")
CONTAINER=""
# Images of the project, unpacked from the dataset zip for this run
IMAGES_DIR="$WORK/project/images"

# Download the dataset zip once, into a folder named after its SHA-256. On
# every run, check its SHA-256, unpack it into the work folder and count the
# images.
fetch_dataset() {
    local zip="$CACHE/$DATASET_SHA256/$DATASET_ZIP"
    if [ ! -f "$zip" ]; then
        log "Downloading $DATASET_ZIP to $(dirname "$zip")"
        mkdir -p "$(dirname "$zip")"
        curl -fsSL --retry 3 -o "$zip.tmp" "$DATASET_URL"
        mv "$zip.tmp" "$zip"
    fi

    local sum
    sum=$(sha256sum "$zip" | cut -d ' ' -f 1)
    if [ "$sum" != "$DATASET_SHA256" ]; then
        rm -f "$zip"
        fail "$zip has SHA-256 $sum, expected $DATASET_SHA256; removed it, run again to download it again"
    fi

    mkdir -p "$IMAGES_DIR"
    python3 -I -m zipfile -e "$zip" "$IMAGES_DIR"
    local count
    count=$(find "$IMAGES_DIR" -type f -iname '*.jpg' | wc -l)
    [ "$count" -eq "$DATASET_IMAGES" ] || fail "expected $DATASET_IMAGES images in $DATASET_ZIP, found $count"
    log "Dataset $DATASET_ZIP: SHA-256 ok, $count images"
}

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

# Checks the outputs of the project in $WORK/project
check_outputs() {
    local project="$WORK/project" missing=0 size
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

    # Runs in the image under test, which has the Python packages it needs
    docker run --rm -v "$WORK:/datasets" -v "$SCRIPTS:/smoke:ro" --entrypoint python3 "$IMAGE" \
        -I /smoke/check-georeferencing.py /datasets/project \
        || fail "the orthophoto is not georeferenced where the images were taken"
}

# Fails if the engine log in $1 shows a PoissonRecon run that aborted and
# was retried. The retry still produces a mesh, so the outputs alone do not
# show it (#24).
check_no_retry() {
    if grep -q "PoissonRecon failed with" "$1"; then
        fail "PoissonRecon aborted and odm_meshing had to retry it"
    fi
    log "ok    no PoissonRecon retry in the engine log"
}

json_field() {
    python3 -I -c 'import json, sys; d = json.load(sys.stdin)
for k in sys.argv[1].split("."): d = d[k]
print(d)' "$1"
}

run_engine() {
    # Record how OpenCV was built, including the CPU features it dispatches
    # on, so every smoke test log shows them
    log "OpenCV build information"
    docker run --rm --entrypoint python3 "$IMAGE" -c 'import cv2; print(cv2.getBuildInformation())'

    log "Running $IMAGE $OPTIONS"
    # OPTIONS is a list of flags, so word splitting is wanted here
    # shellcheck disable=SC2086
    docker run --rm "${GPU_FLAGS[@]}" -v "$WORK:/datasets" "$IMAGE" \
        --project-path /datasets project $OPTIONS 2>&1 | tee "$WORK/engine.log"

    check_outputs
    check_no_retry "$WORK/engine.log"
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
    for f in "$IMAGES_DIR"/*; do
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

    python3 -I -m zipfile -e "$WORK/all.zip" "$WORK/project"
    check_outputs

    curl -fsS "$url/task/$uuid/output" > "$WORK/engine.log"
    check_no_retry "$WORK/engine.log"
}

# Runs the engine up to and including openmvs and packs the golden input
# (docs/adr/0004-golden-outputs-start-from-the-stored-dense-point-cloud.md)
# into $GOLDEN_OUT: the zip, its SHA-256 and a manifest.
run_golden_input() {
    local options="$OPTIONS --end-with openmvs" project="$WORK/project"

    log "Running $IMAGE $options"
    # shellcheck disable=SC2086
    docker run --rm -v "$WORK:/datasets" "$IMAGE" \
        --project-path /datasets project $options 2>&1 | tee "$WORK/engine.log"

    [ -s "$project/opensfm/undistorted/openmvs/scene_dense_dense_filtered.ply" ] \
        || fail "openmvs wrote no dense point cloud"

    mkdir -p "$GOLDEN_OUT"
    rm -f "$GOLDEN_OUT/golden-input.zip"
    log "Packing ${GOLDEN_PATHS[*]}, and ${GOLDEN_EMPTY_FOLDERS[*]} empty"
    python3 -I - "$project" "$GOLDEN_OUT/golden-input.zip" \
        "${GOLDEN_PATHS[@]}" --empty "${GOLDEN_EMPTY_FOLDERS[@]}" <<'EOF' \
        || fail "cannot pack the golden input"
import os, sys, zipfile
root, archive, args = sys.argv[1], sys.argv[2], sys.argv[3:]
split = args.index("--empty")
paths, empty = args[:split], args[split + 1:]
with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
    for path in paths + empty:
        full = os.path.join(root, path)
        if not os.path.exists(full):
            sys.exit("%s is not in the project folder" % path)
        zf.write(full, path)
        if path in empty:
            continue
        for folder, dirs, files in os.walk(full):
            dirs.sort()
            for name in dirs + sorted(files):
                zf.write(os.path.join(folder, name),
                         os.path.relpath(os.path.join(folder, name), root))
EOF

    local sum
    sum=$(cd "$GOLDEN_OUT" && sha256sum golden-input.zip | tee golden-input.zip.sha256 | cut -d ' ' -f 1)
    cat > "$GOLDEN_OUT/golden-input.manifest" << EOF
# Golden input of the AIDM golden comparison, ADR 0004. Unpack
# golden-input.zip into a folder named project and mount its parent at
# /datasets, as scripts/smoke-test.sh does: the files name their paths
# under /datasets/project.
ENGINE_COMMIT="$GOLDEN_COMMIT"
IMAGE="$GOLDEN_IMAGE"
DATASET_URL="$DATASET_URL"
DATASET_SHA256="$DATASET_SHA256"
DATASET_TAG="$DATASET_TAG"
DATASET_COMMIT="$DATASET_COMMIT"
OPTIONS="$options"
ARCHIVE_SHA256="$sum"
EOF
    log "Golden input in $GOLDEN_OUT: golden-input.zip ($(stat -c %s "$GOLDEN_OUT/golden-input.zip") bytes), SHA-256 $sum"
    cat "$GOLDEN_OUT/golden-input.manifest"
}

fetch_dataset
started=$(date +%s)
"run_${MODE//-/_}"
log "PASS: $MODE smoke test of $IMAGE in $(( $(date +%s) - started )) seconds"
