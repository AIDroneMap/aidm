"""Tests for the script boat_build.py runs in the sandbox.

Run with: python3 -m unittest discover -s .github/scripts -p 'test_*.py'

The sandbox script runs here with stand-ins for git, docker and sudo first
on PATH: the build must never start unless the requested commit is checked
out.
"""

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

import boat_build

COMMIT = "0123456789abcdef0123456789abcdef01234567"

# git: clone makes a work tree with a passing smoke test; checkout and rev-parse
# follow the CHECKOUT_EXIT and HEAD variables
FAKE_GIT = """#!/bin/bash
case "$1" in
  clone) mkdir -p "${@: -1}/scripts" && printf 'exit 0\\n' > "${@: -1}/scripts/smoke-test.sh" ;;
  checkout) exit "${CHECKOUT_EXIT:-0}" ;;
  rev-parse) echo "${HEAD:-unset}" ;;
  log) echo 1700000000 ;;
esac
"""
FAKE_DOCKER = """#!/bin/bash
touch "$DOCKER_MARKER"
"""
FAKE_SUDO = """#!/bin/bash
exit 0
"""


class SandboxScript(unittest.TestCase):
    def run_script(self, checkout_exit: int, head: str) -> tuple[int, bool]:
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            for name, body in (("git", FAKE_GIT), ("docker", FAKE_DOCKER), ("sudo", FAKE_SUDO)):
                path = bin_dir / name
                path.write_text(body)
                path.chmod(path.stat().st_mode | stat.S_IEXEC)
            work = root / "work"
            work.mkdir()
            marker = root / "docker-ran"
            env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", CHECKOUT_EXIT=str(checkout_exit), HEAD=head, DOCKER_MARKER=str(marker))
            script = boat_build.render_script("AIDroneMap/aidm", COMMIT, work=str(work), poll_seconds=0)
            completed = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=60)
            return completed.returncode, marker.exists()

    def test_a_failed_checkout_never_starts_the_build(self):
        code, built = self.run_script(checkout_exit=1, head=COMMIT)
        self.assertNotEqual(code, 0)
        self.assertFalse(built)

    def test_another_commit_checked_out_never_starts_the_build(self):
        code, built = self.run_script(checkout_exit=0, head="f" * 40)
        self.assertNotEqual(code, 0)
        self.assertFalse(built)

    def test_the_requested_commit_is_built_and_smoke_tested(self):
        code, built = self.run_script(checkout_exit=0, head=COMMIT)
        self.assertEqual(code, 0)
        self.assertTrue(built)


if __name__ == "__main__":
    unittest.main()
