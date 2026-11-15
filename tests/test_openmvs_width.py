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
# Lines 114 to 135 of libs/Common/Config.h at OpenMVS commit 988e582f, the
# commit External-OpenMVS.cmake pins (Boost Software License 1.0).
PINNED_BLOCK = (b"#else // _MSC_VER\n\n"
                b"#if !defined(_DEBUG) && !defined(NDEBUG)\n#define _DEBUG\n#endif\n\n"
                b"//----------------------------------------------------------------------\n"
                b"// DLL_API is ignored for all other systems\n"
                b"//----------------------------------------------------------------------\n"
                b"#define EXPORT_API\n#define IMPORT_API\n#define GENERAL_API\n"
                b"#define GENERAL_TPL\n\n"
                b"// Define platform type\n"
                b"#if __x86_64__ || __ppc64__\n"
                b"#define _ENVIRONMENT64\n#else\n#define _ENVIRONMENT32\n#endif\n\n"
                b"#endif // _MSC_VER\n")


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


    def test_pinned_block_gains_only_aarch64(self):
        # The Linux images patch this block. Their result must stay these bytes.
        expected = PINNED_BLOCK.replace(b"#if __x86_64__ || __ppc64__\n",
                                        b"#if __x86_64__ || __ppc64__ || __aarch64__\n")
        self.assertEqual(len(expected), len(PINNED_BLOCK) + len(b" || __aarch64__"))
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "Config.h"
            config.write_bytes(PINNED_BLOCK)

            result = self.run_patch(config)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(config.read_bytes(), expected)

    def test_crlf_header_fails_and_names_line_endings(self):
        # Git for Windows checks out CRLF unless the clone sets core.autocrlf=false.
        for header in (HEADER, PINNED_BLOCK):
            crlf = header.replace(b"\n", b"\r\n")
            with self.subTest(header=header), tempfile.TemporaryDirectory() as directory:
                config = Path(directory) / "Config.h"
                config.write_bytes(crlf)

                result = self.run_patch(config)

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("CRLF line endings", result.stderr)
                self.assertIn("core.autocrlf=false", result.stderr)
                self.assertEqual(config.read_bytes(), crlf)


if __name__ == "__main__":
    unittest.main()
