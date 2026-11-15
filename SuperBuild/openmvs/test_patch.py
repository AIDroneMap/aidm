import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


PATCH = Path(__file__).resolve().parents[1] / "cmake/OpenMVSWeightedPatch.cmake"
RECIPE = Path(__file__).resolve().parents[1] / "cmake/External-OpenMVS.cmake"
WIDTH_PATCH = Path(__file__).resolve().parents[2] / "docker/patch_openmvs.py"
EQUIVALENCE_TEST = Path(__file__).resolve().parent / "WeightedPatchTest.cpp"


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


GCC = shutil.which("g++") or shutil.which("c++")
MSVC = shutil.which("cl")


@unittest.skipUnless(EQUIVALENCE_TEST.exists() and (GCC or MSVC),
                     "the equivalence test source and a C++ preprocessor are needed")
class TestScalarReferenceDeclaration(unittest.TestCase):
    """The scalar reference of WeightedPatchTest.cpp is declared for the compiler at hand.

    Only the preprocessor runs. The includes are dropped, so the standard
    library of the host does not matter. GCC gets the MSVC branch by defining
    _MSC_VER; on a machine with cl the real preprocessor is used.
    """

    def preprocess(self, msvc):
        lines = [line for line in EQUIVALENCE_TEST.read_text().splitlines()
                 if not line.startswith("#include")]
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "reference.cpp"
            source.write_text("\n".join(lines) + "\n")
            if MSVC and msvc:
                command = [MSVC, "/nologo", "/EP", str(source)]
            elif GCC:
                command = [GCC, "-E", "-P", "-x", "c++"]
                command += ["-D_MSC_VER=1951"] if msvc else []
                command += [str(source)]
            else:
                self.skipTest("the GCC branch needs g++")
            result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return "".join(result.stdout.split())

    def test_msvc_gets_declspec_and_no_optimizer_around_the_reference(self):
        text = self.preprocess(msvc=True)
        self.assertNotIn("__attribute__", text, "MSVC has no __attribute__")
        declaration = text.find("__declspec(noinline)boolscalarPatch(")
        self.assertNotEqual(declaration, -1, "the reference is not declared noinline")
        off = text.find('#pragmaoptimize("",off)')
        on = text.find('#pragmaoptimize("",on)')
        self.assertTrue(-1 < off < declaration < on,
                        "the optimizer must be off from before the reference to after it")
        self.assertLess(on, text.find("voidcheck("), "the optimizer must be back on after it")
        self.assertEqual(text.count("#pragmaoptimize"), 2)

    def test_gcc_keeps_its_attributes_and_no_msvc_text(self):
        if MSVC and not GCC:
            self.skipTest("the GCC branch needs g++")
        text = self.preprocess(msvc=False)
        self.assertIn('__attribute__((noinline,optimize("no-tree-vectorize")))boolscalarPatch(',
                      text)
        self.assertNotIn("__declspec", text)
        self.assertNotIn("#pragma", text)


@unittest.skipUnless(RECIPE.exists() and WIDTH_PATCH.exists() and shutil.which("git"),
                     "the OpenMVS recipe, the width patch and git are needed")
class TestOpenMVSCheckout(unittest.TestCase):
    """The patch step reads LF bytes even where git checks out CRLF by default."""

    header = (b"// Define platform type\n#if __x86_64__ || __ppc64__\n"
              b"#define _ENVIRONMENT64\n#else\n#define _ENVIRONMENT32\n#endif\n")

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.no_config = self.root / "empty.gitconfig"
        self.no_config.write_text("")
        self.upstream = self.root / "upstream"
        (self.upstream / "libs/Common").mkdir(parents=True)
        (self.upstream / "libs/Common/Config.h").write_bytes(self.header)
        identity = ["-c", "user.name=test", "-c", "user.email=test@example.invalid"]
        self.git(["init", "-q", str(self.upstream)], self.root)
        self.git(["add", "."], self.upstream)
        self.git(identity + ["commit", "-q", "-m", "Config.h"], self.upstream)
        # Git for Windows writes core.autocrlf=true into the system config by
        # default. A global config with the same value stands in for it here.
        windows_config = self.root / "windows.gitconfig"
        windows_config.write_text("[core]\n\tautocrlf = true\n")
        self.windows_env = dict(self.env(), GIT_CONFIG_GLOBAL=str(windows_config))

    def env(self):
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith("GIT_")}
        environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=str(self.no_config))
        return environment

    def git(self, arguments, cwd, env=None):
        subprocess.run(["git"] + arguments, cwd=cwd, env=env or self.env(),
                       check=True, capture_output=True)

    def recipe_git_config(self):
        recipe = RECIPE.read_text()
        start = recipe.find("ExternalProject_Add(${_proj_name}")
        end = recipe.find("#--Configure step", start)
        self.assertNotEqual(start, -1)
        self.assertNotEqual(end, -1)
        values = []
        for line in recipe[start:end].splitlines():
            words = line.split("#")[0].split()
            if words and words[0] == "GIT_CONFIG":
                values.extend(words[1:])
        return values

    def clone(self, name, git_config):
        options = [part for value in git_config for part in ("--config", value)]
        target = self.root / name
        self.git(["clone", "-q"] + options + [str(self.upstream), str(target)],
                 self.root, self.windows_env)
        return target / "libs/Common/Config.h"

    def width_patch(self, config):
        return subprocess.run([sys.executable, str(WIDTH_PATCH), str(config)],
                              capture_output=True, text=True)

    def test_recipe_clone_keeps_lf_and_width_patch_applies(self):
        git_config = self.recipe_git_config()
        self.assertIn("core.autocrlf=false", git_config)
        config = self.clone("openmvs", git_config)
        self.assertEqual(config.read_bytes(), self.header)

        result = self.width_patch(config)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b"#if __x86_64__ || __ppc64__ || __aarch64__\n", config.read_bytes())

    def test_default_windows_clone_is_refused_by_width_patch(self):
        config = self.clone("default", [])
        crlf = self.header.replace(b"\n", b"\r\n")
        self.assertEqual(config.read_bytes(), crlf)

        result = self.width_patch(config)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CRLF line endings", result.stderr)
        self.assertEqual(config.read_bytes(), crlf)


if __name__ == "__main__":
    unittest.main()
