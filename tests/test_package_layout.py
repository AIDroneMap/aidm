"""The package step of configure.py, on a fake built tree on Linux.

configure.py package lays out the engine folder of a native package
(ADR 0007) from the tree that configure.py build leaves. These tests build
such a tree with fake programs and DLLs, small PE files that import what
each test needs, and check the layout the step writes.
"""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("aidm_configure_package", ROOT / "configure.py")
configure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(configure)

# The ._pth file of the Python 3.12 embeddable package as configure.py build
# leaves it: the publisher's file, with CRLF line endings, edited to import
# site and to list Lib\site-packages.
BUILT_PTH = "python312.zip\r\n.\r\nLib\\site-packages\r\n\r\n# Uncomment to run site.main() automatically\r\nimport site\r\n"


def make_pe(imports=(), delay_imports=()):
    """Return the bytes of a minimal 64-bit PE file that imports the DLLs
    in imports and delay-loads the ones in delay_imports."""
    section_rva, section_offset = 0x1000, 0x400
    body = bytearray()
    # Names first, then the two tables, all in one section.
    name_rvas = {}
    for name in list(imports) + list(delay_imports):
        if name not in name_rvas:
            name_rvas[name] = section_rva + len(body)
            body += name.encode("ascii") + b"\0"
    while len(body) % 8:
        body += b"\0"
    import_rva = section_rva + len(body)
    for name in imports:
        body += struct.pack("<IIIII", 0, 0, 0, name_rvas[name], 0)
    body += b"\0" * 20
    delay_rva = section_rva + len(body)
    for name in delay_imports:
        body += struct.pack("<IIIIIIII", 1, name_rvas[name], 0, 0, 0, 0, 0, 0)
    body += b"\0" * 32

    optional = bytearray(240)
    struct.pack_into("<H", optional, 0, 0x20B)
    struct.pack_into("<I", optional, 108, 16)
    if imports:
        struct.pack_into("<II", optional, 112 + 8 * 1, import_rva, 20 * (len(imports) + 1))
    if delay_imports:
        struct.pack_into("<II", optional, 112 + 8 * 13, delay_rva, 32 * (len(delay_imports) + 1))
    coff = struct.pack("<HHIIIHH", 0x8664, 1, 0, 0, 0, len(optional), 0x22)
    section = struct.pack("<8sIIIIIIHHI", b".idata", len(body), section_rva, len(body), section_offset,
                          0, 0, 0, 0, 0xC0000040)
    header = bytearray(b"MZ" + b"\0" * 62)
    struct.pack_into("<I", header, 0x3C, 64)
    data = bytes(header) + b"PE\0\0" + coff + bytes(optional) + section
    return data + b"\0" * (section_offset - len(data)) + bytes(body)


def write(path, data=b""):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    return path


class FakeBuild:
    """A built tree as configure.py build leaves it, and a Visual Studio
    redistributable folder, in a temporary folder."""

    def __init__(self, tmp):
        self.root = Path(tmp, "aidm")
        self.dest = Path(tmp, "dist")
        self.redist = Path(tmp, "redist", "14.51.36014")
        self.licenses = write(Path(tmp, "downloads", "LICENSES.md"), "# Licences of the deps release\n")
        root = self.root
        for name in ("run.bat", "win32env.bat", "run.py", "ENGINE", "settings.yaml", "LICENSE"):
            write(root / name, name)
        write(root / "VERSION", "0.3.0\n")
        write(root / "licenses" / "license.md", "licence summary")
        write(root / "modules" / "__init__.py")
        write(root / "modules" / "context.py", "context")
        write(root / "modules" / "__pycache__" / "context.cpython-312.pyc", b"bytecode")
        write(root / "stages" / "odm_app.py", "app")
        write(root / "tests" / "test_x.py", "not in the package")

        install = root / "SuperBuild" / "install"
        self.bin = install / "bin"
        write(self.bin / "odm_orthophoto.exe", make_pe(["gdal.dll", "msvcp140.dll", "kernel32.dll"]))
        write(self.bin / "gdal.dll", make_pe(["vcruntime140.dll"]))
        write(self.bin / "OpenMVS" / "DensifyPointCloud.exe", make_pe(["msvcp140.dll"], ["vcomp140.dll"]))
        write(install / "share" / "gdal" / "gdalvrt.xsd", "xsd")
        write(install / "bin" / "opensfm" / "LICENSE", "opensfm licence")
        write(root / "SuperBuild" / "src" / "pdal" / "LICENSE.txt", "pdal licence")
        write(root / "SuperBuild" / "src" / "untwine" / "README.md", "no licence here")

        python = root / "python"
        write(python / "python.exe", make_pe(["python312.dll"]))
        write(python / "python312.dll", make_pe(["vcruntime140.dll"]))
        write(python / "vcruntime140.dll", b"the embeddable package's own")
        write(python / "python312._pth", BUILT_PTH)
        write(python / configure.PYTHON_MARKER, "marker")
        write(python / "Lib" / "site-packages" / "numpy" / "_core.pyd", make_pe(["vcruntime140_1.dll"]))
        write(python / "Lib" / "site-packages" / "numpy" / "__pycache__" / "x.pyc", b"bytecode")

        crt = self.redist / "x64" / "Microsoft.VC145.CRT"
        write(crt / "msvcp140.dll", make_pe(["vcruntime140.dll"]))
        write(crt / "vcruntime140.dll", make_pe())
        write(crt / "vcruntime140_1.dll", make_pe(["vcruntime140.dll"]))
        write(crt / "concrt140.dll", make_pe())
        write(self.redist / "x64" / "Microsoft.VC145.OpenMP" / "vcomp140.dll", make_pe())
        write(self.redist / "x64" / "Microsoft.VC145.MFC" / "mfc140.dll", make_pe())

    def lay_out(self, variant="cpu", info=()):
        self.output = io.StringIO()
        with contextlib.redirect_stdout(self.output):
            return Path(configure.lay_out_package(str(self.root), str(self.dest), variant, str(self.redist),
                                                  str(self.licenses), list(info)))

    def stops(self, test, variant, *lines):
        """Lay out variant, expect DepsError, and expect each line in the output."""
        with test.assertRaises(configure.DepsError):
            self.lay_out(variant)
        for line in lines:
            test.assertIn(line, self.output.getvalue())


class TestNames(unittest.TestCase):
    def test_package_and_archive_name_per_variant(self):
        self.assertEqual(configure.package_name("0.3.0", "cpu"), "aidm-0.3.0-windows-x64")
        self.assertEqual(configure.archive_name("0.3.0", "cpu"), "aidm-0.3.0-windows-x64.tar.gz")
        self.assertEqual(configure.archive_name("0.3.0", "cuda"), "aidm-0.3.0-windows-x64-cuda.tar.gz")

    def test_unknown_variant_stops(self):
        with self.assertRaises(configure.DepsError):
            configure.package_name("0.3.0", "rocm")

    def test_version_of_the_repository(self):
        self.assertEqual(configure.read_version(str(ROOT)), (ROOT / "VERSION").read_text().strip())


class TestEngineFolderInPth(unittest.TestCase):
    def test_lists_the_engine_folder_after_the_other_paths(self):
        text = configure.add_engine_folder(BUILT_PTH)
        lines = text.split("\r\n")
        self.assertEqual(lines[:4], ["python312.zip", ".", "Lib\\site-packages", ".."])
        self.assertIn("import site", lines)
        self.assertNotIn("\n", text.replace("\r\n", ""))

    def test_second_edit_changes_nothing(self):
        once = configure.add_engine_folder(BUILT_PTH)
        self.assertEqual(configure.add_engine_folder(once), once)


class TestFakePe(unittest.TestCase):
    def test_the_reader_of_configure_py_reads_the_fake_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(Path(tmp, "a.exe"), make_pe(["MSVCP140.dll", "gdal.dll"], ["vcomp140.dll"]))
            self.assertEqual(configure.pe_imports(str(path)), ["MSVCP140.dll", "gdal.dll", "vcomp140.dll"])


class TestRuntime(unittest.TestCase):
    def test_takes_the_crt_and_openmp_folders_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            build = FakeBuild(tmp)
            found = configure.vc_runtime_dlls(str(build.redist))
            self.assertEqual(sorted(found), ["concrt140.dll", "msvcp140.dll", "vcomp140.dll",
                                             "vcruntime140.dll", "vcruntime140_1.dll"])

    def test_no_redistributable_folder_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(configure.DepsError):
                configure.vc_runtime_dlls(tmp)

    def test_needed_runtime_follows_imports_of_the_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = configure.vc_runtime_dlls(str(FakeBuild(tmp).redist))
            needed = configure.needed_runtime({"tool.exe": ["msvcp140.dll", "gdal.dll"]}, runtime)
            self.assertEqual(needed, ["msvcp140.dll", "vcruntime140.dll"])


class TestLayOutPackage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.build = FakeBuild(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_files_that_must_exist(self):
        top = self.build.lay_out()
        self.assertEqual(top.name, "aidm-0.3.0-windows-x64")
        self.assertEqual([p.name for p in top.iterdir()], ["engine"])
        engine = top / "engine"
        for name in ("run.bat", "win32env.bat", "run.py", "VERSION", "ENGINE", "settings.yaml", "BUILD-INFO.txt",
                     "modules/context.py", "stages/odm_app.py",
                     "SuperBuild/install/bin/odm_orthophoto.exe", "SuperBuild/install/share/gdal/gdalvrt.xsd",
                     "python/python.exe", "python/python312._pth", "python/Lib/site-packages/sitecustomize.py",
                     "licenses/LICENSE", "licenses/README.md", "licenses/aidm-windows-deps-LICENSES.md"):
            self.assertTrue((engine / name).is_file(), name)
        self.assertFalse((engine / "tests").exists())
        self.assertFalse((engine / "python" / configure.PYTHON_MARKER).exists())
        self.assertEqual([p for p in engine.rglob("*") if p.name == "__pycache__" or p.suffix == ".pyc"], [])

    def test_pth_lists_the_engine_folder(self):
        engine = self.build.lay_out() / "engine"
        lines = (engine / "python" / "python312._pth").read_bytes().decode().split("\r\n")
        self.assertEqual(lines[:4], ["python312.zip", ".", "Lib\\site-packages", ".."])
        # The built tree keeps its own ._pth: only the package's Python lists the engine folder.
        self.assertEqual((self.build.root / "python" / "python312._pth").read_bytes().decode(), BUILT_PTH)

    def test_runtime_next_to_python_in_bin_and_next_to_programs(self):
        engine = self.build.lay_out() / "engine"
        expected = ["msvcp140.dll", "vcomp140.dll", "vcruntime140.dll", "vcruntime140_1.dll"]
        crt = self.build.redist / "x64" / "Microsoft.VC145.CRT"
        for folder in ("python", "SuperBuild/install/bin", "SuperBuild/install/bin/OpenMVS"):
            for name in expected:
                self.assertTrue((engine / folder / name).is_file(), "%s/%s" % (folder, name))
        # The runner's runtime replaces the embeddable package's own copy.
        self.assertEqual((engine / "python" / "vcruntime140.dll").read_bytes(),
                         (crt / "vcruntime140.dll").read_bytes())
        self.assertFalse((engine / "python" / "concrt140.dll").exists())
        self.assertFalse((engine / "python" / "mfc140.dll").exists())
        info = (engine / "BUILD-INFO.txt").read_text()
        for name in expected:
            self.assertIn("visual c++ runtime: %s sha256 " % name, info)

    def test_build_info_names_variant_and_toolset(self):
        env = {"VSCMD_VER": "18.0.1", "VCToolsVersion": "14.51.36014", "VCToolsRedistDir": str(self.build.redist)}
        engine = self.build.lay_out("cpu", configure.toolset_info(env)) / "engine"
        info = (engine / "BUILD-INFO.txt").read_text().splitlines()
        self.assertEqual(info[:2], ["variant: windows-x64", "version: 0.3.0"])
        self.assertIn("VSCMD_VER: 18.0.1", info)
        self.assertIn("VisualStudioVersion: (not set)", info)
        self.assertIn("VCToolsRedistDir: %s" % self.build.redist, info)

    def test_no_cuda_runtime_dll_in_a_cpu_layout(self):
        write(self.build.bin / "cudart64_12.dll", make_pe())
        self.build.stops(self, "cpu", "cudart64_12.dll is a CUDA DLL in the cpu variant")

    def test_cpu_layout_with_a_tool_that_imports_cuda_stops(self):
        write(self.build.bin / "OpenMVS" / "ReconstructMesh.exe", make_pe(["cublas64_12.dll"]))
        self.build.stops(self, "cpu", "ReconstructMesh.exe imports cublas64_12.dll in the cpu variant")

    def test_no_driver_dll_in_either_layout(self):
        write(self.build.bin / "nvcuda.dll", make_pe())
        self.build.stops(self, "cpu", "nvcuda.dll is the NVIDIA driver's DLL")

    def test_cuda_layout_holds_the_runtime_its_tools_import(self):
        write(self.build.bin / "OpenMVS" / "DensifyPointCloud.exe", make_pe(["msvcp140.dll", "cudart64_12.dll"]))
        write(self.build.bin / "cudart64_12.dll", make_pe())
        top = self.build.lay_out("cuda")
        self.assertEqual(top.name, "aidm-0.3.0-windows-x64-cuda")
        self.assertIn("cuda runtime imported: cudart64_12.dll",
                      (top / "engine" / "BUILD-INFO.txt").read_text().splitlines())

    def test_cuda_layout_without_the_runtime_stops(self):
        write(self.build.bin / "OpenMVS" / "DensifyPointCloud.exe", make_pe(["cudart64_12.dll"]))
        self.build.stops(self, "cuda", "DensifyPointCloud.exe imports cudart64_12.dll, which is not in")

    def test_cuda_layout_without_a_cuda_tool_stops(self):
        self.build.stops(self, "cuda", "the cuda variant was built without CUDA")

    def test_licenses_folder(self):
        licenses = self.build.lay_out() / "engine" / "licenses"
        self.assertEqual((licenses / "LICENSE").read_text(), "LICENSE")
        self.assertEqual((licenses / "license.md").read_text(), "licence summary")
        self.assertEqual((licenses / "aidm-windows-deps-LICENSES.md").read_text(), "# Licences of the deps release\n")
        self.assertEqual((licenses / "superbuild" / "pdal" / "LICENSE.txt").read_text(), "pdal licence")
        self.assertEqual((licenses / "superbuild" / "opensfm" / "LICENSE").read_text(), "opensfm licence")
        index = (licenses / "README.md").read_text()
        self.assertIn("No licence file at the top of the sources of untwine.", index)
        self.assertIn("Microsoft Software License Terms for Visual Studio", index)
        self.assertIn("msvcp140.dll", index)

    def test_sitecustomize_of_a_wheel_stops(self):
        write(self.build.root / "python" / "Lib" / "site-packages" / "sitecustomize.py", "a wheel's own")
        with self.assertRaisesRegex(configure.DepsError, "sitecustomize"):
            self.build.lay_out()

    def test_tree_without_a_build_stops(self):
        configure.safe_remove(str(self.build.root / "SuperBuild" / "install"))
        with self.assertRaisesRegex(configure.DepsError, "configure.py build"):
            self.build.lay_out()

    def test_relative_cv2_config_passes(self):
        # What OpenCV writes when External-OpenCV.cmake gives it install paths
        # relative to the install prefix on Windows.
        cv2 = self.build.root / "python" / "Lib" / "site-packages" / "cv2"
        write(cv2 / "config.py", "import os\n\nBINARIES_PATHS = [\n    os.path.join(os.path.join("
              "LOADER_DIR, '../../../../SuperBuild/install'), 'bin')\n] + BINARIES_PATHS\n")
        write(cv2 / "config-3.12.py", "PYTHON_EXTENSIONS_PATHS = [\n    os.path.join(LOADER_DIR, 'python-3.12')\n]"
              " + PYTHON_EXTENSIONS_PATHS\n")
        self.build.lay_out()

    def test_a_file_naming_the_build_folder_stops(self):
        root = str(self.build.root)
        cv2 = self.build.root / "python" / "Lib" / "site-packages" / "cv2"
        for name, text in (("config.py", "BINARIES_PATHS = [os.path.join('%s/SuperBuild/install', 'bin')]"
                            % root.replace("\\", "/")),
                           ("config-3.12.py", "PYTHON_EXTENSIONS_PATHS = ['%s']"
                            % (root + "/python/lib/site-packages/cv2").replace("/", "\\\\")),
                           ("extra.pth", (root + "/SuperBuild/install/bin").replace("/", "\\").upper())):
            with self.subTest(name=name):
                path = write(cv2 / name, text)
                with self.assertRaisesRegex(configure.DepsError, "name the build folder"):
                    self.build.lay_out()
                path.unlink()

    def test_a_new_layout_replaces_the_old_one(self):
        top = self.build.lay_out()
        write(top / "engine" / "stale.txt", "left from before")
        self.assertFalse((self.build.lay_out() / "engine" / "stale.txt").exists())


class TestPathSpellings(unittest.TestCase):
    def test_windows_folder_in_every_spelling(self):
        self.assertEqual(configure.path_spellings("D:\\a\\aidm\\aidm"),
                         ["d:/a/aidm/aidm", "d:\\\\a\\\\aidm\\\\aidm", "d:\\a\\aidm\\aidm"])

    def test_only_run_time_text_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(Path(tmp, "a.py"), "x = 'D:/a/aidm/aidm/python'")
            write(Path(tmp, "b.pyi"), "x = 'D:/a/aidm/aidm/python'")
            write(Path(tmp, "c.pth"), "D:\\A\\AIDM\\AIDM\\python")
            write(Path(tmp, "d.py"), "x = 'D:/a/aidm/aidm2'")
            self.assertEqual([os.path.basename(p) for p in configure.files_naming(tmp, "D:\\a\\aidm\\aidm")],
                             ["a.py", "c.pth"])


class TestLoadedFromOutside(unittest.TestCase):
    def test_build_folder_and_shipped_names(self):
        build = "/w/aidm"
        engine = "/w/aidm/dist/aidm-0.3.0-windows-x64/engine"
        loaded = [engine + "/python/python312.dll",
                  engine + "/SuperBuild/install/bin/opencv_core4120.dll",
                  "/w/aidm/SuperBuild/install/bin/opencv_core4120.dll",
                  "/w/aidm/python/Lib/site-packages/cv2/python-3.12/cv2.cp312-win_amd64.pyd",
                  "/w/aidm/vcpkg/installed/x64-windows-avx2/bin/zlib1.dll",
                  "/Windows/System32/kernel32.dll",
                  "/Program Files/Other/msvcp140.dll"]
        shipped = {"python312.dll", "opencv_core4120.dll", "msvcp140.dll", "cv2.cp312-win_amd64.pyd"}
        self.assertEqual(configure.loaded_from_outside(loaded, engine, build, shipped), loaded[2:5] + loaded[6:])

    def test_the_check_script_carries_it(self):
        self.assertIn(configure.inspect.getsource(configure.loaded_from_outside), configure.PACKAGE_CHECK)
        self.assertIn('loaded_from_outside(loaded, engine, args["build"], shipped)', configure.PACKAGE_CHECK)


class TestReinstallFindsCv2(unittest.TestCase):
    """configure.py build reinstalls the projects whose install wrote into
    python/. With the relative install path, OpenCV's manifest names python/
    through the install prefix and "..", and is still found."""

    def test_manifest_with_a_relative_python_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "SuperBuild" / "build" / "opencv" / "build" / "install_manifest.txt"
            write(root / "SuperBuild" / "build" / "opencv" / "stamp" / "Release" / "opencv-install", "")
            write(manifest, "%s/SuperBuild/install/../../python/lib/site-packages/cv2/__init__.py\n" % tmp)
            projects = configure.python_module_projects(str(root / "SuperBuild" / "build"), str(root / "python"))
            self.assertEqual(projects, [str(root / "SuperBuild" / "build" / "opencv")])


class TestSitecustomize(unittest.TestCase):
    def load(self):
        namespace = {"__name__": "sitecustomize"}
        exec(compile(configure.PACKAGE_SITECUSTOMIZE, "sitecustomize.py", "exec"), namespace)
        return namespace

    def test_does_nothing_outside_windows(self):
        namespace = self.load()
        self.assertEqual(namespace["DLL_DIRECTORIES"], [])
        self.assertEqual(namespace["setup"]("/x/python", "linux", {}, None, SimpleNamespace()), [])

    def test_adds_the_superbuild_bin_and_the_bytecode_cache(self):
        setup = self.load()["setup"]
        with tempfile.TemporaryDirectory() as tmp:
            engine = Path(tmp, "engine")
            (engine / "SuperBuild" / "install" / "bin").mkdir(parents=True)
            added = []
            fake_sys = SimpleNamespace(pycache_prefix=None)
            handles = setup(str(engine / "python"), "win32", {"PYTHONPYCACHEPREFIX": "C:\\cache"},
                            lambda folder: added.append(folder) or "handle", fake_sys)
            self.assertEqual(added, [str(engine / "SuperBuild" / "install" / "bin")])
            self.assertEqual(handles, ["handle"])
            self.assertEqual(fake_sys.pycache_prefix, "C:\\cache")

    def test_keeps_a_cache_given_on_the_command_line(self):
        setup = self.load()["setup"]
        fake_sys = SimpleNamespace(pycache_prefix="D:\\given")
        setup("/nowhere/python", "win32", {"PYTHONPYCACHEPREFIX": "C:\\cache"}, None, fake_sys)
        self.assertEqual(fake_sys.pycache_prefix, "D:\\given")


class TestArchive(unittest.TestCase):
    def test_tar_gz_with_one_top_folder_and_its_sha256(self):
        with tempfile.TemporaryDirectory() as tmp:
            build = FakeBuild(tmp)
            top = build.lay_out("cpu")
            with contextlib.redirect_stdout(io.StringIO()):
                archive = configure.write_archive(str(top), "tar")
            self.assertEqual(os.path.basename(archive), "aidm-0.3.0-windows-x64.tar.gz")
            with tarfile.open(archive) as t:
                names = t.getnames()
            self.assertEqual({name.split("/")[0] for name in names}, {"aidm-0.3.0-windows-x64"})
            self.assertIn("aidm-0.3.0-windows-x64/engine/run.bat", names)
            digest, name = Path(archive + ".sha256").read_text().split()
            self.assertEqual((digest, name), (configure.sha256_of(archive), "aidm-0.3.0-windows-x64.tar.gz"))

    def test_failed_tar_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            top = Path(tmp, "aidm-0.3.0-windows-x64")
            top.mkdir()
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(configure.DepsError):
                    configure.write_archive(str(top), "tar", runner=lambda cmd: SimpleNamespace(returncode=2))


class TestCheckPackage(unittest.TestCase):
    def test_runs_the_check_through_win32env_then_run_bat(self):
        calls = []

        def runner(cmd, env, cwd):
            calls.append(cmd)
            if "python" in cmd:
                args = json.loads(Path(cmd[-1]).read_text())
                self.assertEqual(args["windows"], "C:\\Windows")
                self.assertEqual(args["build"], configure.ROOT)
                self.assertEqual(Path(cmd[-2]).read_text(), configure.PACKAGE_CHECK)
            return SimpleNamespace(returncode=0)

        with tempfile.TemporaryDirectory() as tmp:
            engine = os.path.join(tmp, "engine")
            os.makedirs(engine)
            with contextlib.redirect_stdout(io.StringIO()):
                configure.check_package(tmp, {"SystemRoot": "C:\\Windows", "PATH": "C:\\elsewhere"}, runner=runner)
        self.assertEqual(calls[0][:5], ["cmd.exe", "/d", "/c", "call", os.path.join(engine, "win32env.bat")])
        self.assertEqual(calls[0][5:7], ["&&", "python"])
        self.assertEqual(calls[1], ["cmd.exe", "/d", "/c", "call", os.path.join(engine, "run.bat"), "--help"])

    def test_failed_check_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "engine"))
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(configure.DepsError):
                    configure.check_package(tmp, {}, runner=lambda cmd, env, cwd: SimpleNamespace(returncode=1))

    def test_bytecode_in_the_package_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(Path(tmp, "engine", "modules", "__pycache__", "x.pyc"), b"bytecode")
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(configure.DepsError, "bytecode"):
                    configure.check_package(tmp, {}, runner=lambda cmd, env, cwd: SimpleNamespace(returncode=0))

    def test_check_script_compiles(self):
        compile(configure.PACKAGE_CHECK, "check.py", "exec")


class TestMain(unittest.TestCase):
    def test_package_runs_on_windows_only(self):
        out = io.TextIOWrapper(io.BytesIO())
        with mock.patch.object(sys, "platform", "linux"), contextlib.redirect_stdout(out):
            self.assertEqual(configure.main(["package", "cuda"]), 1)

    def test_package_takes_cpu_by_default_or_cuda(self):
        called = []
        out = io.TextIOWrapper(io.BytesIO())
        with mock.patch.object(sys, "platform", "win32"), mock.patch.object(sys, "version_info", SimpleNamespace(major=3, minor=12)), \
                mock.patch.object(configure, "package", called.append), contextlib.redirect_stdout(out):
            self.assertEqual(configure.main(["package"]), 0)
            self.assertEqual(configure.main(["package", "cuda"]), 0)
        self.assertEqual(called, ["cpu", "cuda"])

    def test_wrong_variant_stops(self):
        with contextlib.redirect_stderr(io.StringIO()):
            for argv in (["package", "rocm"], ["build", "rocm"]):
                with self.assertRaises(SystemExit):
                    configure.main(argv)

    def test_clean_removes_the_package_folder(self):
        removed = []
        with mock.patch.object(configure, "safe_remove", removed.append):
            configure.clean()
        self.assertIn(configure.DIST_DIR, removed)


class TestBatchFiles(unittest.TestCase):
    """What the task API and run.bat rely on in the two batch files."""

    def test_win32env_builds_path_from_the_package_and_windows_only(self):
        text = (ROOT / "win32env.bat").read_text()
        path_lines = [line for line in text.splitlines() if line.lower().startswith('set "path=')]
        self.assertEqual(path_lines, ['set "PATH=%AIDM_ENGINE_DIR%\\python;%AIDM_INSTALL_DIR%\\bin;'
                                      '%SystemRoot%\\System32;%SystemRoot%;%SystemRoot%\\System32\\Wbem"'])
        self.assertNotIn("setlocal", [line.strip().lower() for line in text.splitlines()])
        for line in ('set "GDAL_DATA=%AIDM_INSTALL_DIR%\\share\\gdal"',
                     'set "PROJ_LIB=%AIDM_INSTALL_DIR%\\share\\proj"',
                     'set "PDAL_DRIVER_PATH=%AIDM_INSTALL_DIR%\\bin"',
                     'set "USE_PATH_FOR_GDAL_PYTHON=YES"',
                     'set "PYTHONPYCACHEPREFIX=%LOCALAPPDATA%\\AIDM\\pycache"',
                     "chcp 65001 > nul"):
            self.assertIn(line, text.splitlines())

    def test_run_bat_starts_run_py_in_utf8_mode(self):
        lines = (ROOT / "run.bat").read_text().splitlines()
        self.assertIn('call "%~dp0win32env.bat"', lines)
        self.assertIn('python -X utf8 "%~dp0run.py" %*', lines)

    def test_batch_files_check_out_with_crlf(self):
        self.assertIn("*.bat text eol=crlf", (ROOT / ".gitattributes").read_text().splitlines())


if __name__ == "__main__":
    unittest.main()
