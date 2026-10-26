#!/usr/bin/env bash
#
# Checks that the image the release workflow pushed is the image it tested.
#
# Usage:
#   scripts/check-pushed-image.sh <tested id> <pushed id> <pushed digest>
#
# <tested id> is the imageid output of the build step that loaded the image
# for the smoke test. <pushed id> and <pushed digest> are the imageid and
# digest outputs of the push step. All three are content hashes.
#
# Docker reports the tested id in one of two forms, depending on its image
# store:
#   - classic store (overlay2): the config digest, equal to <pushed id>;
#   - containerd image store (the GitHub runners use it since Docker 29,
#     October 2026): the manifest digest, equal to <pushed digest>.
# Either match proves that the pushed image is the tested one. Anything else,
# including an empty value, fails.

set -euo pipefail

if [ $# -ne 3 ]; then
    echo "Usage: $0 <tested id> <pushed id> <pushed digest>" >&2
    exit 2
fi
TESTED="$1"
PUSHED="$2"
DIGEST="$3"

if [ -z "$TESTED" ]; then
    echo "::error::The build step reported no image id, so the pushed image cannot be checked"
    exit 1
fi
if [ -n "$PUSHED" ] && [ "$TESTED" = "$PUSHED" ]; then
    echo "The pushed image is the tested one: same config digest $TESTED"
    exit 0
fi
if [ -n "$DIGEST" ] && [ "$TESTED" = "$DIGEST" ]; then
    echo "The pushed image is the tested one: same manifest digest $TESTED"
    exit 0
fi
echo "::error::The pushed image differs from the tested image: tested $TESTED, pushed config $PUSHED, pushed manifest $DIGEST"
exit 1
