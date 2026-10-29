import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "nightly-merge.py")

spec = importlib.util.spec_from_file_location("nightly_merge", SCRIPT)
nightly = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nightly)

HEAD = "a" * 40
MASTER = "b" * 40
GOOD_JOBS = [{"name": n, "conclusion": "success"} for n in nightly.DRY_RUN_JOBS]

# A stand-in for the gh command line tool. It answers from a JSON world file
# and appends every call to a log, one JSON array per line.
FAKE_GH = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json, os, sys
    args = sys.argv[1:]
    with open(os.environ["FAKE_GH_LOG"], "a") as f:
        f.write(json.dumps(args) + "\\n")
    with open(os.environ["FAKE_GH_WORLD"]) as f:
        world = json.load(f)
    def out(value):
        print(json.dumps(value))
        sys.exit(0)
    if args[:2] == ["issue", "list"]:
        out(world.get("issues", []))
    if args[:2] == ["pr", "list"]:
        out(world.get("prs", []))
    if args[:2] == ["pr", "view"]:
        out(world["pr"][args[2]])
    if args[:2] == ["run", "list"]:
        out(world.get("runs", {}).get(args[args.index("--commit") + 1], []))
    if args[:2] == ["run", "view"]:
        out({"jobs": world.get("jobs", {}).get(args[2], [])})
    if args[:2] == ["pr", "merge"]:
        if args[2] in world.get("merge_fails", []):
            print("merge refused", file=sys.stderr)
            sys.exit(1)
        sys.exit(0)
    if args[:2] == ["pr", "comment"]:
        sys.exit(0)
    if args[0] == "api":
        print(world["master"])
        sys.exit(0)
    print("unexpected call: %s" % args, file=sys.stderr)
    sys.exit(3)
    """)


def snapshot(files, decision="APPROVED", rollup=None, head=HEAD):
    return {
        "headRefOid": head,
        "reviewDecision": decision,
        "statusCheckRollup": rollup if rollup is not None else [{"name": "DCO", "conclusion": "SUCCESS"}],
        "files": [{"path": f} for f in files],
    }


class TestRules(unittest.TestCase):
    def test_build_inputs(self):
        for f in ("SuperBuild/CMakeLists.txt", "docker/gcc", "Dockerfile", "gpu.Dockerfile",
                  "configure.sh", "requirements.txt"):
            self.assertTrue(nightly.changes_build_inputs([f]), f)
        for f in ("modules/mesh.py", "docs/Dockerfile.md", "tests/test_mesh.py"):
            self.assertFalse(nightly.changes_build_inputs([f]), f)

    def test_ci_code(self):
        self.assertTrue(nightly.changes_ci_code([".github/workflows/release.yml"]))
        self.assertTrue(nightly.changes_ci_code(["scripts/smoke-test.sh"]))
        self.assertFalse(nightly.changes_ci_code(["SuperBuild/CMakeLists.txt"]))

    def test_checks_not_green(self):
        rollup = [
            {"name": "a", "conclusion": "SUCCESS"},
            {"name": "b", "conclusion": "SKIPPED"},
            {"context": "c", "state": "SUCCESS"},
            {"name": "d", "conclusion": None, "status": "IN_PROGRESS"},
            {"name": "e", "conclusion": "FAILURE"},
        ]
        self.assertEqual(nightly.checks_not_green(rollup), ["d", "e"])

    def test_not_ready(self):
        self.assertEqual(nightly.not_ready(snapshot(["Dockerfile"]), True), [])
        reasons = nightly.not_ready(snapshot(["Dockerfile"], decision="REVIEW_REQUIRED",
                                             rollup=[{"name": "x", "conclusion": "FAILURE"}]), False)
        self.assertEqual(len(reasons), 3)


class TestMergeReady(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        bindir = os.path.join(self.tmp.name, "bin")
        os.makedirs(bindir)
        gh = os.path.join(bindir, "gh")
        with open(gh, "w") as f:
            f.write(FAKE_GH)
        os.chmod(gh, os.stat(gh).st_mode | stat.S_IEXEC)
        self.world = os.path.join(self.tmp.name, "world.json")
        self.log = os.path.join(self.tmp.name, "calls.log")
        self.output = os.path.join(self.tmp.name, "output")
        self.env = dict(os.environ, PATH=bindir + os.pathsep + os.environ["PATH"],
                        FAKE_GH_WORLD=self.world, FAKE_GH_LOG=self.log,
                        GH_TOKEN="x", GH_REPO="AIDroneMap/aidm", GITHUB_OUTPUT=self.output)

    def run_script(self, world):
        base = {"master": MASTER, "runs": {HEAD: [{"databaseId": 7}]}, "jobs": {"7": GOOD_JOBS}}
        base.update(world)
        with open(self.world, "w") as f:
            json.dump(base, f)
        p = subprocess.run([sys.executable, "-I", SCRIPT], env=self.env, capture_output=True, text=True)
        calls = []
        if os.path.exists(self.log):
            with open(self.log) as f:
                calls = [json.loads(line) for line in f]
        output = ""
        if os.path.exists(self.output):
            with open(self.output) as f:
                output = f.read()
        return p, calls, output

    def merges(self, calls):
        return [c for c in calls if c[:2] == ["pr", "merge"]]

    def test_ready_pull_request_is_merged_at_its_snapshot_head(self):
        p, calls, output = self.run_script({
            "prs": [{"number": 5, "isDraft": False}],
            "pr": {"5": snapshot(["SuperBuild/CMakeLists.txt", "CHANGELOG.md"])},
        })
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.merges(calls), [["pr", "merge", "5", "--merge", "--match-head-commit", HEAD]])
        self.assertIn("sha=%s\nmerged=#5\n" % MASTER, output)

    def test_one_snapshot_per_pull_request(self):
        # The files and the head come from the same request, so a push between
        # two requests cannot pair new code with old files (review of #66).
        _, calls, _ = self.run_script({
            "prs": [{"number": 5, "isDraft": False}],
            "pr": {"5": snapshot(["Dockerfile"])},
        })
        views = [c for c in calls if c[:2] == ["pr", "view"]]
        self.assertEqual(len(views), 1)
        self.assertIn("headRefOid,reviewDecision,statusCheckRollup,files", views[0])

    def test_ci_code_is_never_merged(self):
        _, calls, output = self.run_script({
            "prs": [{"number": 5, "isDraft": False}],
            "pr": {"5": snapshot(["Dockerfile", ".github/workflows/release.yml"])},
        })
        self.assertEqual(self.merges(calls), [])
        self.assertIn("merged=\n", output)

    def test_other_changes_are_left_alone(self):
        _, calls, _ = self.run_script({
            "prs": [{"number": 5, "isDraft": False}, {"number": 6, "isDraft": True}],
            "pr": {"5": snapshot(["modules/mesh.py"])},
        })
        self.assertEqual(self.merges(calls), [])
        self.assertNotIn(["pr", "view", "6"], [c[:3] for c in calls])

    def test_not_approved_or_red_is_not_merged(self):
        _, calls, _ = self.run_script({
            "prs": [{"number": 5, "isDraft": False}, {"number": 6, "isDraft": False}],
            "pr": {
                "5": snapshot(["Dockerfile"], decision="REVIEW_REQUIRED"),
                "6": snapshot(["Dockerfile"], rollup=[{"name": "Unit tests", "conclusion": "FAILURE"}]),
            },
        })
        self.assertEqual(self.merges(calls), [])

    def test_dry_run_that_skipped_its_builds_does_not_count(self):
        skipped = [{"name": n, "conclusion": "skipped"} for n in nightly.DRY_RUN_JOBS]
        _, calls, _ = self.run_script({
            "prs": [{"number": 5, "isDraft": False}],
            "pr": {"5": snapshot(["Dockerfile"])},
            "jobs": {"7": skipped},
        })
        self.assertEqual(self.merges(calls), [])

    def test_no_dry_run_on_the_head(self):
        _, calls, _ = self.run_script({
            "prs": [{"number": 5, "isDraft": False}],
            "pr": {"5": snapshot(["Dockerfile"])},
            "runs": {},
        })
        self.assertEqual(self.merges(calls), [])

    def test_open_nightly_failed_issue_stops_merging(self):
        p, calls, output = self.run_script({
            "issues": [{"number": 99}],
            "prs": [{"number": 5, "isDraft": False}],
            "pr": {"5": snapshot(["Dockerfile"])},
        })
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual([c for c in calls if c[0] == "pr"], [])
        self.assertIn("sha=%s" % MASTER, output)

    def test_refused_merge_gets_a_comment_and_the_run_goes_on(self):
        p, calls, output = self.run_script({
            "prs": [{"number": 5, "isDraft": False}, {"number": 6, "isDraft": False}],
            "pr": {"5": snapshot(["Dockerfile"]), "6": snapshot(["docker/gcc"])},
            "merge_fails": ["5"],
        })
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn(["pr", "comment", "5"], [c[:3] for c in calls])
        self.assertIn("merged=#6\n", output)

    def test_too_many_files_is_left_for_a_maintainer(self):
        files = ["SuperBuild/f%d" % i for i in range(nightly.MAX_FILES)]
        _, calls, _ = self.run_script({
            "prs": [{"number": 5, "isDraft": False}],
            "pr": {"5": snapshot(files)},
        })
        self.assertEqual(self.merges(calls), [])


if __name__ == "__main__":
    unittest.main()
