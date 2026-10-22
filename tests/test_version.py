import argparse
import contextlib
import io
import os
import re
import unittest

from modules import log

ROOT = os.path.join(os.path.dirname(__file__), "..")


def read(name):
    with open(os.path.join(ROOT, name)) as f:
        return f.read()


class TestVersion(unittest.TestCase):
    def test_version_files(self):
        self.assertRegex(log.get_version(), r"^\d+\.\d+\.\d+$")
        self.assertRegex(log.get_upstream_version(), r"^ODX \d+\.\d+\.\d+$")

    def test_version_string(self):
        self.assertEqual(log.get_version_string(),
                         "AIDM %s (%s)" % (log.get_version(), log.get_upstream_version()))

    def test_log_json_fields(self):
        logger = log.Logger()
        logger.init_json_output(["unused.json"], argparse.Namespace())
        self.assertEqual(logger.json["engine"], "AIDM")
        self.assertEqual(logger.json["version"], log.get_version())
        self.assertEqual(logger.json["odmVersion"], log.get_version())
        self.assertEqual(logger.json["upstreamVersion"], log.get_upstream_version())

    def test_version_option(self):
        from modules import config
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            config.config(["--version"])
        self.assertEqual(out.getvalue().strip(), log.get_version_string())

    def test_engine_file(self):
        # The task API reports this file as the engine name in /info
        self.assertEqual(read("ENGINE").strip(), "aidm")

    def test_docker_labels_match_version(self):
        for dockerfile in ["Dockerfile", "gpu.Dockerfile"]:
            labels = re.findall(r'org\.opencontainers\.image\.version="([^"]+)"', read(dockerfile))
            self.assertEqual(labels, [log.get_version()], dockerfile)


if __name__ == '__main__':
    unittest.main()
