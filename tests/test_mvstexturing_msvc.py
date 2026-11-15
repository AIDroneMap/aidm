from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
TEST_SOURCE = ROOT / "SuperBuild/mvstexturing/MaskmoveTest.cpp"
COMPILER = shutil.which("g++") or shutil.which("c++")


@unittest.skipUnless(TEST_SOURCE.exists(), "New mvstexturing build inputs are checked by the Windows build")
@unittest.skipUnless(platform.machine() in ("x86_64", "AMD64"), "MASKMOVQ exists on x86-64 only")
@unittest.skipUnless(COMPILER, "A C++ compiler is required")
class TestMsvcMapmapMaskmove(unittest.TestCase):
    """The replacement for _mm_maskmove_si64 that mvstexturing uses on MSVC
    writes the same bytes as the real MASKMOVQ, which GCC provides."""

    def test_replacement_matches_maskmovq(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "maskmove-test"
            subprocess.run([COMPILER, "-std=c++11", "-O2", "-Wall", "-Werror",
                            str(TEST_SOURCE), "-o", str(binary)], check=True)
            result = subprocess.run([str(binary)], text=True, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("4096 cases match MASKMOVQ", result.stdout)


if __name__ == "__main__":
    unittest.main()
