import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIRROR = "https://github.com/AIDroneMap/aidm-eigen-mirror.git"
GITLAB = "https://gitlab.com/libeigen/eigen.git"


@unittest.skipUnless(shutil.which("cmake"), "CMake is required for SuperBuild tests")
class TestEigenMirror(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1",
                        GIT_CONFIG_GLOBAL=str(self.work / "gitconfig"),
                        GIT_ALLOW_PROTOCOL="file", GIT_TERMINAL_PROMPT="0")
        self.env.pop("GIT_CONFIG_COUNT", None)

    def run_cmd(self, *args, cwd=None, check=True):
        return subprocess.run(args, cwd=cwd or self.work, env=self.env,
                              text=True, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, check=check)

    def configure_superbuild(self):
        source = self.work / "source"
        source.mkdir()
        (source / "CMakeLists.txt").write_text(
            'cmake_minimum_required(VERSION 3.28)\n'
            'project(MirrorTest)\n'
            f'add_subdirectory("{ROOT / "SuperBuild"}" superbuild)\n'
            'foreach(name eigen34 draco mvstexturing openpointclass mve)\n'
            '  foreach(prop GIT_REPOSITORY GIT_TAG GIT_CONFIG CMAKE_ARGS INSTALL_COMMAND)\n'
            '    get_property(value TARGET ${name} PROPERTY _EP_${prop})\n'
            '    file(WRITE "${CMAKE_BINARY_DIR}/${name}-${prop}" "${value}")\n'
            '  endforeach()\n'
            'endforeach()\n'
            'get_property(value TARGET mve PROPERTY _EP_INSTALL_COMMAND SET)\n'
            'file(WRITE \"${CMAKE_BINARY_DIR}/mve-install-set\" \"${value}\")\n')
        build = self.work / "build"
        self.run_cmd("cmake", "-S", str(source), "-B", str(build),
                     f"-DSB_SOURCE_DIR={self.work / 'deps'}",
                     f"-DSB_BINARY_DIR={self.work / 'dep-build'}",
                     f"-DSB_DOWNLOAD_DIR={self.work / 'downloads'}",
                     f"-DSB_INSTALL_DIR={self.work / 'install'}",
                     "-DODM_BUILD_OpenCV=ON", "-DODM_BUILD_Ceres=ON",
                     "-DODM_BUILD_GFlags=ON")
        return build

    def test_real_superbuild_routes_direct_and_submodule_fetches(self):
        build = self.configure_superbuild()
        self.assertEqual((build / "eigen34-GIT_REPOSITORY").read_text(), MIRROR)
        self.assertEqual((build / "eigen34-GIT_TAG").read_text(),
                         "7176ae16238ded7fb5ed30a7f5215825b3abd134")
        self.assertEqual((build / "draco-GIT_TAG").read_text(), "383")
        self.assertEqual((build / "mve-install-set").read_text(), "1")
        self.assertEqual((build / "mve-INSTALL_COMMAND").read_text(), "")
        config = (build / "draco-GIT_CONFIG").read_text()
        self.assertEqual(config, "submodule.third_party/eigen.url=" + MIRROR)
        self.assertIn("-DCMAKE_PROJECT_Texturing_INCLUDE:FILEPATH=" +
                      str(ROOT / "SuperBuild/cmake/EigenMirror.cmake"),
                      (build / "mvstexturing-CMAKE_ARGS").read_text())

        self.assertIn("-DCMAKE_PROJECT_opc_INCLUDE:FILEPATH=" +
                      str(ROOT / "SuperBuild/cmake/EigenMirror.cmake"),
                      (build / "openpointclass-CMAKE_ARGS").read_text())
        self.check_submodule_clone(config, "third_party/eigen", "third_party/eigen")

    def test_nested_lightgbm_submodule_uses_mirror(self):
        source = self.work / "opc"
        source.mkdir()
        (source / "CMakeLists.txt").write_text(
            'cmake_minimum_required(VERSION 3.28)\n'
            'project(opc NONE)\n'
            'include(ExternalProject)\n'
            'ExternalProject_Add(lightgbm\n'
            '  GIT_REPOSITORY https://github.com/microsoft/LightGBM\n'
            '  GIT_TAG e4231205a3bac13662a81db9433ddaea8924fbce)\n'
            'foreach(prop GIT_CONFIG GIT_TAG)\n'
            '  get_property(value TARGET lightgbm PROPERTY _EP_${prop})\n'
            '  file(WRITE "${CMAKE_BINARY_DIR}/${prop}" "${value}")\n'
            'endforeach()\n')
        build = self.work / "opc-build"
        self.run_cmd("cmake", "-S", str(source), "-B", str(build),
                     "-DCMAKE_PROJECT_opc_INCLUDE:FILEPATH=" +
                     str(ROOT / "SuperBuild/cmake/EigenMirror.cmake"))
        config = (build / "GIT_CONFIG").read_text()
        self.assertEqual(config, "submodule.eigen.url=" + MIRROR)
        self.assertEqual((build / "GIT_TAG").read_text(),
                         "e4231205a3bac13662a81db9433ddaea8924fbce")
        self.check_submodule_clone(config, "eigen", "external_libs/eigen")

    def check_submodule_clone(self, config, submodule_name, submodule_path):
        eigen = self.work / "eigen"
        parent = self.work / "draco"
        for repo in (eigen, parent):
            self.run_cmd("git", "init", str(repo))
            (repo / "README").write_text("fixture\n")
            self.run_cmd("git", "add", ".", cwd=repo)
            self.run_cmd("git", "-c", "user.name=Test", "-c", "user.email=test@example.org",
                         "commit", "-m", "test fixture", cwd=repo)
        self.run_cmd("git", "submodule", "add", str(eigen), submodule_path, cwd=parent)
        self.run_cmd("git", "config", "-f", ".gitmodules",
                     "submodule." + submodule_path + ".url", GITLAB, cwd=parent)
        self.run_cmd("git", "config", "-f", ".gitmodules", "--rename-section",
                     "submodule." + submodule_path, "submodule." + submodule_name, cwd=parent)
        self.run_cmd("git", "add", ".", cwd=parent)
        self.run_cmd("git", "-c", "user.name=Test", "-c", "user.email=test@example.org",
                     "commit", "-m", "add Eigen submodule", cwd=parent)
        self.run_cmd("git", "config", "--global", f"url.{eigen.as_uri()}.insteadOf", MIRROR)

        # Reproduce ExternalProject's clone and separate submodule command.
        # A clone-local insteadOf rule does not reach the submodule clone.
        for name, setting, succeeds in (
            ("rewrite", f"url.{MIRROR}.insteadOf={GITLAB}", False),
            ("explicit", config, True),
        ):
            clone = self.work / name
            self.run_cmd("git", "clone", "--config", setting, str(parent), str(clone))
            result = self.run_cmd("git", "submodule", "update", "--init", "--recursive",
                                  cwd=clone, check=False)
            if succeeds:
                self.assertEqual(result.returncode, 0, result.stdout)
                expected = self.run_cmd("git", "rev-parse", "HEAD", cwd=eigen).stdout
                actual = self.run_cmd("git", "rev-parse", "HEAD",
                                      cwd=clone / submodule_path).stdout
                self.assertEqual(actual, expected)
            else:
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("transport 'https' not allowed", result.stdout)

    def configure_texturing(self, url):
        source = self.work / "texturing"
        source.mkdir(exist_ok=True)
        (source / "CMakeLists.txt").write_text(
            'cmake_minimum_required(VERSION 3.28)\n'
            'project(Texturing NONE)\n'
            'include(ExternalProject)\n'
            'externalproject_add(ext_eigen\n'
            f'  URL {url}\n'
            '  URL_MD5 02edfeec591ae09848223d622700a10b\n'
            '  SOURCE_DIR "${CMAKE_SOURCE_DIR}/elibs/eigen"\n'
            '  CONFIGURE_COMMAND "" BUILD_COMMAND "" INSTALL_COMMAND "")\n'
            'foreach(prop GIT_REPOSITORY GIT_TAG SOURCE_DIR URL UPDATE_COMMAND)\n'
            '  get_property(value TARGET ext_eigen PROPERTY _EP_${prop})\n'
            '  file(WRITE "${CMAKE_BINARY_DIR}/${prop}" "${value}")\n'
            'endforeach()\n'
            'externalproject_add(other URL https://example.org/other.tar.gz\n'
            '  CONFIGURE_COMMAND \"\" BUILD_COMMAND \"\" INSTALL_COMMAND \"\")\n'
            'foreach(name ext_eigen other)\n'
            '  foreach(prop CONFIGURE_COMMAND BUILD_COMMAND INSTALL_COMMAND)\n'
            '    get_property(is_set TARGET ${name} PROPERTY _EP_${prop} SET)\n'
            '    file(WRITE \"${CMAKE_BINARY_DIR}/${name}-${prop}-set\" \"${is_set}\")\n'
            '    get_property(value TARGET ${name} PROPERTY _EP_${prop})\n'
            '    file(WRITE \"${CMAKE_BINARY_DIR}/${name}-${prop}\" \"${value}\")\n'
            '  endforeach()\n'
            'endforeach()\n'
            'get_property(value TARGET other PROPERTY _EP_URL)\n'
            'file(WRITE "${CMAKE_BINARY_DIR}/other-URL" "${value}")\n')
        return self.run_cmd(
            "cmake", "-S", str(source), "-B", str(self.work / "texturing-build"),
            "-DCMAKE_PROJECT_Texturing_INCLUDE:FILEPATH=" +
            str(ROOT / "SuperBuild/cmake/EigenMirror.cmake"), check=False)

    def test_archive_uses_same_commit_and_source_directory(self):
        result = self.configure_texturing(
            "https://gitlab.com/libeigen/eigen/-/archive/3.3.2/eigen-3.3.2.tar.gz")
        self.assertEqual(result.returncode, 0, result.stdout)
        build = self.work / "texturing-build"
        self.assertEqual((build / "GIT_REPOSITORY").read_text(), MIRROR)
        self.assertEqual((build / "GIT_TAG").read_text(),
                         "477d1e81928a8514d2cca6a6e1e7e478eecd8634")
        self.assertEqual((build / "SOURCE_DIR").read_text(),
                         str(self.work / "texturing/elibs/eigen"))
        self.assertEqual((build / "URL").read_text(), "")
        self.assertEqual((build / "UPDATE_COMMAND").read_text(), "")
        self.assertEqual((build / "other-URL").read_text(), "https://example.org/other.tar.gz")

    def test_hook_preserves_explicit_empty_build_steps(self):
        result = self.configure_texturing(
            "https://gitlab.com/libeigen/eigen/-/archive/3.3.2/eigen-3.3.2.tar.gz")
        self.assertEqual(result.returncode, 0, result.stdout)
        build = self.work / "texturing-build"
        for name in ("ext_eigen", "other"):
            for prop in ("CONFIGURE_COMMAND", "BUILD_COMMAND", "INSTALL_COMMAND"):
                with self.subTest(name=name, prop=prop):
                    self.assertEqual((build / f"{name}-{prop}-set").read_text(), "1")
                    self.assertEqual((build / f"{name}-{prop}").read_text(), "")

    def test_changed_archive_pin_fails_instead_of_using_old_commit(self):
        result = self.configure_texturing("https://example.org/changed-pin.tar.gz")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("The mvs-texturing Eigen pin changed", result.stdout)


if __name__ == "__main__":
    unittest.main()
