import argparse
import contextlib
import io
import os
import re
import unittest

from modules import log

ROOT = os.path.join(os.path.dirname(__file__), "..")


RUNTIME_FILES = ["run.py", "Dockerfile", "gpu.Dockerfile", "VERSION", "ENGINE", "settings.yaml"]
RUNTIME_DIRS = ["modules", "stages"]
ODX_VERSION = re.compile(r"ODX\s*v?\d+\.\d+")


def read(name):
    with open(os.path.join(ROOT, name)) as f:
        return f.read()


def runtime_sources():
    for name in RUNTIME_FILES:
        yield name
    for directory in RUNTIME_DIRS:
        for folder, _, files in os.walk(os.path.join(ROOT, directory)):
            for file in files:
                if file.endswith((".py", ".yaml")):
                    yield os.path.relpath(os.path.join(folder, file), ROOT)


class TestVersion(unittest.TestCase):
    def test_version_files(self):
        self.assertRegex(log.get_version(), r"^\d+\.\d+\.\d+$")

    def test_version_string(self):
        self.assertEqual(log.get_version_string(), "AIDM %s" % log.get_version())

    def test_log_json_fields(self):
        logger = log.Logger()
        logger.init_json_output(["unused.json"], argparse.Namespace())
        self.assertEqual(logger.json["engine"], "AIDM")
        self.assertEqual(logger.json["version"], log.get_version())
        self.assertEqual(logger.json["odmVersion"], log.get_version())
        self.assertNotIn("upstreamVersion", logger.json)

    def test_version_option(self):
        from modules import config
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            config.config(["--version"])
        self.assertEqual(out.getvalue().strip(), log.get_version_string())

    def test_osfm_report_version(self):
        # The OpenSfM report line is built inside a long config function, so check its source
        self.assertIn('"report_version: %s" % log.get_version(),', read("modules/osfm.py"))

    def test_no_odx_version_in_runtime_sources(self):
        for name in runtime_sources():
            self.assertNotRegex(read(name), ODX_VERSION, name)

    def test_no_reader_of_upstream_version_file(self):
        # CI copies the branch over the last published image, which still holds the
        # deleted UPSTREAM_VERSION file, so check that no code reads it instead
        for name in runtime_sources():
            self.assertNotIn("UPSTREAM_VERSION", read(name), name)

    def test_engine_file(self):
        # The task API reports this file as the engine name in /info
        self.assertEqual(read("ENGINE").strip(), "aidm")

    def test_docker_labels_match_version(self):
        for dockerfile in ["Dockerfile", "gpu.Dockerfile"]:
            labels = re.findall(r'org\.opencontainers\.image\.version="([^"]+)"', read(dockerfile))
            self.assertEqual(labels, [log.get_version()], dockerfile)


if __name__ == '__main__':
    unittest.main()
