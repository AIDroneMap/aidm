import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PATCH = ROOT / "docker" / "patch_openmvs.py"
BEFORE = b"#if __x86_64__ || __ppc64__\n"
AFTER = b"#if __x86_64__ || __ppc64__ || __aarch64__\n"
HEADER = (b"#define KEEP_THIS 1\n" + BEFORE +
          b"#define _ENVIRONMENT64\n#else\n#define _ENVIRONMENT32\n#endif\n"
          b"#define _PLATFORM_ARM 1\n#undef _USE_SSE\n")


class TestOpenMvsWidth(unittest.TestCase):
    def run_patch(self, config):
        return subprocess.run([sys.executable, str(PATCH), str(config)],
                              capture_output=True, text=True)

    def test_adds_arm64_without_changing_other_bytes(self):
        with tempfile.TemporaryDirectory(prefix="openmvs width ") as directory:
            config = Path(directory) / "Config.h"
            config.write_bytes(HEADER)

            result = self.run_patch(config)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(config.read_bytes(), HEADER.replace(BEFORE, AFTER))

    def test_repeated_patch_leaves_header_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "Config.h"
            config.write_bytes(HEADER)
            self.assertEqual(self.run_patch(config).returncode, 0)
            patched = config.read_bytes()
            timestamp = config.stat().st_mtime_ns

            result = self.run_patch(config)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(config.read_bytes(), patched)
            self.assertEqual(config.stat().st_mtime_ns, timestamp)

    def test_unexpected_detection_fails_without_changing_header(self):
        headers = [
            b"",
            HEADER.replace(BEFORE, b"#if __LP64__\n"),
            HEADER + BEFORE,
            HEADER + AFTER,
            HEADER.replace(BEFORE, AFTER) + AFTER,
        ]
        for header in headers:
            with self.subTest(header=header), tempfile.TemporaryDirectory() as directory:
                config = Path(directory) / "Config.h"
                config.write_bytes(header)

                result = self.run_patch(config)

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("OpenMVS width detection changed", result.stderr)
                self.assertEqual(config.read_bytes(), header)


if __name__ == "__main__":
    unittest.main()
