#!/usr/bin/env python3
#
# The merge step of the nightly (.github/workflows/release.yml, job
# nightly-merge). Merges the open pull requests on master that change build
# inputs and are ready, oldest first, then prints the master commit to build.
#
# A pull request is merged only when all of these hold, all read from one
# snapshot of the pull request:
#   - it is not a draft;
#   - it changes a build input (SuperBuild/, docker/, Dockerfile,
#     gpu.Dockerfile, configure.sh, requirements.txt);
#   - it changes no CI code (.github/, scripts/);
#   - a code owner approved it (reviewDecision APPROVED);
#   - every check on its head commit is green (success, skipped or neutral);
#   - a release dry run on that same head commit succeeded and actually ran
#     the three image builds and the GPU test, not only skipped them.
# The merge names that head commit (--match-head-commit), so a push after the
# snapshot makes the merge fail instead of merging unchecked code. Nothing is
# merged while an issue labelled nightly-failed is open.
#
# Usage: scripts/nightly-merge.py
# Environment:
#   GH_TOKEN, GH_REPO   for the gh command line tool (required)
#   GITHUB_OUTPUT       file that receives sha=<master commit> and
#                       merged=<pull request numbers> (optional)
#   GITHUB_SERVER_URL, GITHUB_REPOSITORY, GITHUB_RUN_ID
#                       to link the run in comments (optional)

import json
import os
import re
import subprocess
import sys

BUILD_INPUTS = re.compile(r"^(SuperBuild/|docker/|Dockerfile$|gpu\.Dockerfile$|configure\.sh$|requirements\.txt$)")
CI_CODE = re.compile(r"^(\.github/|scripts/)")
GREEN = {"SUCCESS", "SKIPPED", "NEUTRAL"}
# The jobs a dry run must have run and passed to count
DRY_RUN_JOBS = ("cpu-amd64", "cpu-arm64", "gpu-amd64", "GPU test on Modal")
# gh returns at most this many files for a pull request
MAX_FILES = 100


def gh(*args):
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def gh_json(*args):
    out = gh(*args).strip()
    return json.loads(out) if out else None


def changes_build_inputs(files):
    return any(BUILD_INPUTS.match(f) for f in files)


def changes_ci_code(files):
    return any(CI_CODE.match(f) for f in files)


def checks_not_green(rollup):
    """Return the names of the checks in a statusCheckRollup that are not green."""
    bad = []
    for check in rollup or []:
        state = check.get("conclusion") or check.get("state") or "PENDING"
        if state not in GREEN:
            bad.append(check.get("name") or check.get("context") or "unnamed check")
    return bad


def not_ready(snapshot, dry_run_ok):
    """Return the reasons a build input pull request is not ready; empty means ready."""
    reasons = []
    if snapshot.get("reviewDecision") != "APPROVED":
        reasons.append("not approved by a code owner")
    bad = checks_not_green(snapshot.get("statusCheckRollup"))
    if bad:
        reasons.append("checks not green: " + ", ".join(sorted(set(bad))))
    if not dry_run_ok:
        reasons.append("no passing dry run on %s" % snapshot["headRefOid"])
    return reasons


def dry_run_passed(head):
    """True when a release dry run on head succeeded with every DRY_RUN_JOBS job run and passed."""
    runs = gh_json("run", "list", "--workflow", "release.yml", "--commit", head,
                   "--event", "workflow_dispatch", "--status", "success",
                   "--json", "databaseId") or []
    for run in runs:
        jobs = (gh_json("run", "view", str(run["databaseId"]), "--json", "jobs") or {}).get("jobs", [])
        conclusions = {job.get("name"): job.get("conclusion") for job in jobs}
        if all(conclusions.get(name) == "success" for name in DRY_RUN_JOBS):
            return True
    return False


def run_link():
    parts = [os.environ.get(k, "") for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID")]
    return "%s/%s/actions/runs/%s" % tuple(parts) if all(parts) else "the nightly run"


def merge_ready():
    """Merge the ready pull requests and return their numbers."""
    merged = []
    if gh_json("issue", "list", "--state", "open", "--label", "nightly-failed", "--json", "number"):
        print("::warning::An open nightly-failed issue stops merging. Building master as it is.")
        return merged

    prs = gh_json("pr", "list", "--base", "master", "--state", "open",
                  "--json", "number,isDraft", "--limit", "100") or []
    for number in sorted(p["number"] for p in prs if not p.get("isDraft")):
        # One snapshot: the files, the head commit, the review and the checks
        # all describe the same commit.
        snapshot = gh_json("pr", "view", str(number),
                           "--json", "headRefOid,reviewDecision,statusCheckRollup,files")
        files = [f["path"] for f in snapshot.get("files") or []]
        head = snapshot["headRefOid"]
        if len(files) >= MAX_FILES:
            print("#%d changes %d files or more: left for a maintainer" % (number, MAX_FILES))
            continue
        if not changes_build_inputs(files):
            continue
        if changes_ci_code(files):
            print("#%d changes CI code: left for a maintainer" % number)
            continue

        ready_so_far = not not_ready(snapshot, dry_run_ok=True)
        reasons = not_ready(snapshot, dry_run_passed(head) if ready_so_far else False)
        if reasons:
            print("#%d not ready: %s" % (number, "; ".join(reasons)))
            continue

        try:
            gh("pr", "merge", str(number), "--merge", "--match-head-commit", head)
        except subprocess.CalledProcessError as e:
            print("#%d could not be merged: %s" % (number, (e.stderr or "").strip()))
            gh("pr", "comment", str(number), "--body",
               "The nightly could not merge this pull request (%s). If it is behind master "
               "or in conflict, bring it up to date; it is tried again the next night." % run_link())
            continue
        print("#%d merged at %s" % (number, head))
        merged.append(number)
    return merged


def main():
    for name in ("GH_TOKEN", "GH_REPO"):
        if not os.environ.get(name):
            print("%s is not set" % name, file=sys.stderr)
            return 2

    merged = merge_ready()
    repo = os.environ["GH_REPO"]
    sha = gh("api", "repos/%s/git/ref/heads/master" % repo, "--jq", ".object.sha").strip()
    listed = " ".join("#%d" % n for n in merged)
    print("Building master at %s. Merged: %s" % (sha, listed or "none"))

    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a") as f:
            f.write("sha=%s\nmerged=%s\n" % (sha, listed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
