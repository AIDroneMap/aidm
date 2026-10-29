"""Build the CPU image from scratch on a boat.dev sandbox and run the smoke test on it.

Used by .github/workflows/boat-build.yml. The runner only drives boat's API:
it creates one sandbox, which clones this repository at the given commit,
builds Dockerfile for linux/amd64 with --no-cache, runs
scripts/smoke-test.sh on the image, and prints its progress once a minute.
The build and smoke logs are downloaded to --out, and the sandbox is deleted
at the end, also on failure.

boat.dev sandboxes are x86_64 only. A change that touches the arm64 build or
the GPU image still needs the release workflow's dry run.

The API key comes from BOAT_API_KEY. This script never prints it, never
writes it to a file, and prints no sandbox identifier.
"""

import argparse
import json
import os
import shlex
import sys
import time
import urllib.request
from pathlib import Path

# Runs inside the sandbox as its user, who has docker and passwordless sudo.
# The swap mirrors the release workflow's, so a build that fits there fits here.
SCRIPT = r"""
set -u
cd /home/user
echo "[build] $(nproc) vCPU, $(free -g | awk '/Mem:/{print $2}') GiB RAM,$(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2)"
sudo fallocate -l 16G /swapfile && sudo chmod 600 /swapfile && sudo mkswap -q /swapfile && sudo swapon /swapfile
git clone --quiet --filter=blob:none @REPO_URL@ src && cd src && git checkout --quiet @COMMIT@
echo "[build] commit $(git rev-parse HEAD)"
export SOURCE_DATE_EPOCH=$(git log -1 --format=%ct)
start=$(date +%s)
( docker buildx build --no-cache --progress=plain --platform linux/amd64 -f Dockerfile --load -t aidm-build:test . > /home/user/build.log 2>&1; echo $? > /home/user/build.exit ) &
while [ ! -f /home/user/build.exit ]; do
  sleep 60
  echo "[build] $(( $(date +%s) - start )) s, load $(cut -d' ' -f1 /proc/loadavg), memory $(free -g | awk '/Mem:/{print $3}') GiB, swap $(free -g | awk '/Swap:/{print $3}') GiB: $(tail -1 /home/user/build.log | cut -c1-120)"
done
build_exit=$(cat /home/user/build.exit)
echo "[build] build exit $build_exit after $(( $(date +%s) - start )) s"
if [ "$build_exit" != 0 ]; then tail -60 /home/user/build.log; exit "$build_exit"; fi
smoke_start=$(date +%s)
bash scripts/smoke-test.sh engine aidm-build:test > /home/user/smoke.log 2>&1
smoke_exit=$?
echo "[build] smoke test exit $smoke_exit after $(( $(date +%s) - smoke_start )) s"
tail -20 /home/user/smoke.log
exit "$smoke_exit"
"""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repository", required=True, help="owner/name, cloned over https")
    parser.add_argument("--commit", required=True, help="the commit to build, a full SHA")
    parser.add_argument("--machine-type", default="large", choices=["large", "xlarge"])
    parser.add_argument("--lifetime-hours", type=int, default=2, help="the sandbox stops by itself after this")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if len(args.commit) != 40 or any(c not in "0123456789abcdef" for c in args.commit):
        parser.error("--commit must be a full lower-case SHA")

    from boat_sdk import ApiClient, Configuration, run_command, wait_until_ready
    from boat_sdk.api.boat_api import BoatApi
    from boat_sdk.models import CreateSandboxRequest, FileUrlRequest

    script = SCRIPT.replace("@REPO_URL@", shlex.quote(f"https://github.com/{args.repository}.git")).replace("@COMMIT@", args.commit)
    args.out.mkdir(parents=True, exist_ok=True)
    config = Configuration(host="https://boat.dev/api/v1", access_token=os.environ["BOAT_API_KEY"])
    exit_code = None
    with ApiClient(config) as client:
        boat = BoatApi(client)
        # The lifetime is set at creation: running a command does not extend it
        created = boat.create(
            create_sandbox_request=CreateSandboxRequest(type=args.machine_type, ttl_seconds=args.lifetime_hours * 3600)
        )
        sandbox_id = created.sandbox.id
        started = time.monotonic()
        try:
            wait_until_ready(boat, sandbox_id, timeout_seconds=900)
            print(f"[build] boat.dev {args.machine_type} sandbox ready", flush=True)
            for frame in run_command(boat, sandbox_id, "bash -c " + shlex.quote(script)):
                if frame.get("type") in ("stdout", "stderr"):
                    print(frame["data"], end="", flush=True)
                elif frame.get("type") == "exit":
                    exit_code = frame.get("exitCode")
                elif frame.get("type") == "error":
                    print(f"[build] boat.dev command error: {frame.get('message')}", file=sys.stderr, flush=True)
            for name in ("build.log", "smoke.log"):
                try:
                    url = boat.create_download_url(sandbox_id, FileUrlRequest(path=f"/home/user/{name}")).url
                    with urllib.request.urlopen(url, timeout=300) as response:
                        (args.out / name).write_bytes(response.read())
                except Exception as error:
                    print(f"[build] {name} not downloaded: {type(error).__name__}", file=sys.stderr)
        finally:
            seconds = round(time.monotonic() - started)
            try:
                boat.delete_sandbox(sandbox_id=sandbox_id, x_ascii_confirm_delete=sandbox_id)
            except Exception as error:
                print(f"[build] sandbox not deleted ({type(error).__name__}); it stops at its lifetime", file=sys.stderr)
    (args.out / "summary.json").write_text(
        json.dumps({"machine_type": args.machine_type, "commit": args.commit, "sandbox_seconds": seconds, "exit_code": exit_code}, indent=2)
    )
    print(f"[build] sandbox used {seconds} s, exit {exit_code}")
    return 0 if exit_code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
