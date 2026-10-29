from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


PATCH = Path(__file__).resolve().parents[1] / "cmake/OpenMVSWeightedPatch.cmake"
RECIPE = Path(__file__).resolve().parents[1] / "cmake/External-OpenMVS.cmake"


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


@unittest.skipUnless(PATCH.exists(), "OpenMVS build inputs are needed")
class TestOpenMVSPatchStep(unittest.TestCase):
    def patch_step(self):
        recipe = RECIPE.read_text()
        self.assertEqual(recipe.count("PATCH_COMMAND"), 1,
                         "a second PATCH_COMMAND joins both into one command line")
        match = re.search(r"PATCH_COMMAND(.*?)#--Configure step", recipe, re.DOTALL)
        self.assertIsNotNone(match)
        return match.group(1)

    def test_patch_step_runs_width_and_weighted_patch(self):
        step = self.patch_step()
        width = step.find("docker/patch_openmvs.py")
        chained = step.find("COMMAND ${CMAKE_COMMAND}")
        weighted = step.find("OpenMVSWeightedPatch.cmake")
        self.assertNotEqual(width, -1, "arm64 width patch is missing")
        self.assertNotEqual(weighted, -1, "weighted patch kernel is missing")
        self.assertTrue(width < chained < weighted,
                        "the weighted patch must be a second COMMAND of the patch step")


if __name__ == "__main__":
    unittest.main()
