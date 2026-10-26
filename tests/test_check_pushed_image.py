import os
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "check-pushed-image.sh")

CONFIG = "sha256:" + "c" * 64
MANIFEST = "sha256:" + "m" * 64
OTHER = "sha256:" + "0" * 64


def check(tested, pushed, digest):
    p = subprocess.run(["bash", SCRIPT, tested, pushed, digest],
                       capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


class TestCheckPushedImage(unittest.TestCase):
    def test_classic_store_config_digest(self):
        code, out = check(CONFIG, CONFIG, MANIFEST)
        self.assertEqual(code, 0, out)
        self.assertIn("config digest", out)

    def test_containerd_store_manifest_digest(self):
        # Docker 29 on the GitHub runners: the build step reports the
        # manifest digest, the push step's imageid is the config digest
        code, out = check(MANIFEST, CONFIG, MANIFEST)
        self.assertEqual(code, 0, out)
        self.assertIn("manifest digest", out)

    def test_different_image(self):
        code, out = check(OTHER, CONFIG, MANIFEST)
        self.assertEqual(code, 1)
        self.assertIn("::error::", out)

    def test_empty_tested_id(self):
        self.assertEqual(check("", CONFIG, MANIFEST)[0], 1)

    def test_all_empty(self):
        self.assertEqual(check("", "", "")[0], 1)

    def test_empty_push_outputs(self):
        self.assertEqual(check(CONFIG, "", "")[0], 1)

    def test_wrong_arguments(self):
        p = subprocess.run(["bash", SCRIPT, CONFIG], capture_output=True, text=True)
        self.assertEqual(p.returncode, 2)


if __name__ == "__main__":
    unittest.main()
