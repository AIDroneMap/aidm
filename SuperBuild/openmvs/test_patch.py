from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


PATCH = Path(__file__).resolve().parents[1] / "cmake/OpenMVSWeightedPatch.cmake"


@unittest.skipUnless(PATCH.exists() and shutil.which("cmake"),
                     "OpenMVS build inputs and CMake are needed")
class TestOpenMVSWeightedPatch(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.source = Path(self.directory.name)
        self.depth_source = self.source / "libs/MVS/DepthMap.cpp"
        self.depth_source.parent.mkdir(parents=True)
        self.original = ('#include "DepthMap.h"\n'
                         '\tH *= float(nSizeStep);\n'
                         '\tASSERT(n == nTexels);\n'
                         '\t// score similarity\n')
        self.depth_source.write_text(self.original)
        (self.source / "CMakeLists.txt").write_text("project(OpenMVS)\n")

    def patch(self):
        return subprocess.run(
            ["cmake", f"-DOPENMVS_SOURCE_DIR={self.source}", "-P", str(PATCH)],
            capture_output=True, text=True)

    def test_patch_installs_kernel_and_is_idempotent(self):
        first = self.patch()
        self.assertEqual(first.returncode, 0, first.stderr)
        patched = self.depth_source.read_bytes()
        build = (self.source / "CMakeLists.txt").read_bytes()
        self.assertIn(b"aidm::sampleWeightedPatch", patched)
        self.assertIn(b"#if DENSE_NCC == DENSE_NCC_WEIGHTED", patched)
        self.assertIn(b"#else", patched)
        self.assertIn(b"#endif", patched)
        self.assertTrue((self.depth_source.parent / "AIDMWeightedPatch.h").exists())
        second = self.patch()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.depth_source.read_bytes(), patched)
        self.assertEqual((self.source / "CMakeLists.txt").read_bytes(), build)

    def test_changed_anchor_fails_without_writing_source(self):
        changed = self.original.replace("ASSERT(n == nTexels)", "ASSERT(other)")
        self.depth_source.write_text(changed)
        result = self.patch()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("source anchor changed", result.stderr)
        self.assertEqual(self.depth_source.read_text(), changed)

    def test_duplicate_anchor_fails_without_writing_source(self):
        changed = self.original + '\tH *= float(nSizeStep);\n'
        self.depth_source.write_text(changed)
        result = self.patch()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("source anchor is not unique", result.stderr)
        self.assertEqual(self.depth_source.read_text(), changed)


if __name__ == "__main__":
    unittest.main()
