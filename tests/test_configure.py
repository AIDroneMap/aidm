import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import platform
import re
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("aidm_configure", ROOT / "configure.py")
configure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(configure)

# Unit CI copies the branch over the last published image without
# SuperBuild/, so there this is the last release's pin, or no pin at all.
PIN_FILE = ROOT / "SuperBuild" / "windows-deps.json"
CMAKE_PIN_FILE = ROOT / "SuperBuild" / "windows-cmake.txt"
SOURCE_PINS_FILE = ROOT / "SuperBuild" / "windows-python-source.txt"

GOOD_HASH = "d5521b4a9e864505c550abae99ff593d501bd08225583e69b3a9a4ca7f36a03f"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


class FakeRelease:
    """Stands in for urlopen: serves bytes by URL and records each request."""

    def __init__(self, files):
        self.files = files
        self.requests = []

    def __call__(self, url):
        self.requests.append(url)
        return io.BytesIO(self.files[url])


def make_pin(**assets):
    pin = {"tag": "v9.9.9", "triplet": "x64-windows-avx2", "assets": {}}
    for role in configure.PINNED_ASSETS:
        data = assets.get(role, role.encode())
        pin["assets"][role] = {"file": "%s.bin" % role, "sha256": sha256(data)}
    return pin


class TestFetchAsset(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dest = os.path.join(self.tmp.name, "v9.9.9", "asset.zip")
        self.url = "https://example.invalid/asset.zip"

    def tearDown(self):
        self.tmp.cleanup()

    def test_matching_download_is_kept(self):
        release = FakeRelease({self.url: b"payload"})
        configure.fetch_asset(self.url, self.dest, sha256(b"payload"), opener=release)
        self.assertEqual(Path(self.dest).read_bytes(), b"payload")
        self.assertEqual(release.requests, [self.url])

    def test_wrong_hash_stops_and_leaves_nothing(self):
        release = FakeRelease({self.url: b"tampered"})
        with self.assertRaises(configure.DepsError) as raised:
            configure.fetch_asset(self.url, self.dest, sha256(b"payload"), opener=release)
        message = str(raised.exception)
        self.assertIn(sha256(b"payload"), message)
        self.assertIn(sha256(b"tampered"), message)
        self.assertIn(self.url, message)
        self.assertEqual(os.listdir(os.path.dirname(self.dest)), [])

    def test_file_with_pinned_hash_is_not_downloaded_again(self):
        os.makedirs(os.path.dirname(self.dest))
        Path(self.dest).write_bytes(b"payload")
        release = FakeRelease({})
        configure.fetch_asset(self.url, self.dest, sha256(b"payload"), opener=release)
        self.assertEqual(release.requests, [])

    def test_stale_file_is_replaced(self):
        os.makedirs(os.path.dirname(self.dest))
        Path(self.dest).write_bytes(b"older release")
        release = FakeRelease({self.url: b"payload"})
        configure.fetch_asset(self.url, self.dest, sha256(b"payload"), opener=release)
        self.assertEqual(Path(self.dest).read_bytes(), b"payload")
        self.assertEqual(release.requests, [self.url])

    def test_failed_transfer_leaves_no_partial_file(self):
        def broken(url):
            raise OSError("connection reset")
        with self.assertRaises(OSError):
            configure.fetch_asset(self.url, self.dest, sha256(b"payload"), opener=broken)
        self.assertEqual(os.listdir(os.path.dirname(self.dest)), [])


class TestFetchPinnedAssets(unittest.TestCase):
    def test_every_asset_comes_from_the_deps_release_by_tag(self):
        pin = make_pin()
        files = {configure.asset_url(pin, role): role.encode() for role in configure.PINNED_ASSETS}
        release = FakeRelease(files)
        with tempfile.TemporaryDirectory() as tmp:
            paths = configure.fetch_pinned_assets(pin, download_dir=tmp, opener=release)
            for role in configure.PINNED_ASSETS:
                self.assertEqual(Path(paths[role]).read_bytes(), role.encode())
        self.assertEqual(sorted(release.requests), sorted(
            "https://github.com/AIDroneMap/aidm-windows-deps/releases/download/v9.9.9/%s.bin" % role
            for role in configure.PINNED_ASSETS))

    def test_one_wrong_asset_stops_the_build(self):
        pin = make_pin()
        files = {configure.asset_url(pin, role): role.encode() for role in configure.PINNED_ASSETS}
        files[configure.asset_url(pin, "python")] = b"not the pinned python"
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(configure.DepsError):
                configure.fetch_pinned_assets(pin, download_dir=tmp, opener=FakeRelease(files))


class TestLoadPin(unittest.TestCase):
    def load(self, pin):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "windows-deps.json")
            with open(path, "w") as f:
                json.dump(pin, f)
            return configure.load_pin(path)

    def assertRejected(self, pin, text):
        with self.assertRaises(configure.DepsError) as raised:
            self.load(pin)
        self.assertIn("windows-deps.json", str(raised.exception))
        self.assertIn(text, str(raised.exception))

    def test_valid_pin_loads(self):
        self.assertEqual(self.load(make_pin())["tag"], "v9.9.9")

    def test_missing_asset_is_rejected(self):
        pin = make_pin()
        del pin["assets"]["exiftool"]
        self.assertRejected(pin, "exiftool")

    def test_unknown_asset_is_rejected(self):
        pin = make_pin()
        pin["assets"]["extra"] = {"file": "extra.zip", "sha256": "0" * 64}
        self.assertRejected(pin, "extra")

    def test_short_or_uppercase_hash_is_rejected(self):
        for digest in ("abc", "A" * 64, None):
            pin = make_pin()
            pin["assets"]["export"]["sha256"] = digest
            self.assertRejected(pin, "export")

    def test_file_name_with_a_path_is_rejected(self):
        for name in ("../export.zip", "sub/export.zip", "sub\\export.zip", ""):
            pin = make_pin()
            pin["assets"]["export"]["file"] = name
            self.assertRejected(pin, "export")

    def test_tag_with_a_path_is_rejected(self):
        pin = make_pin()
        pin["tag"] = "../v1.0.0"
        self.assertRejected(pin, "tag")

    def test_unreadable_pin_is_rejected(self):
        with self.assertRaises(configure.DepsError):
            configure.load_pin(os.path.join(tempfile.gettempdir(), "no-such-pin.json"))


@unittest.skipUnless(PIN_FILE.exists(), "the pin is a SuperBuild file; unit CI has the last release's copy")
class TestCommittedPin(unittest.TestCase):
    def test_pin_loads_with_the_avx2_triplet(self):
        pin = configure.load_pin(str(PIN_FILE))
        self.assertEqual(pin["triplet"], "x64-windows-avx2")

    def test_pin_names_the_embeddable_python(self):
        pin = configure.load_pin(str(PIN_FILE))
        self.assertEqual(configure.embeddable_python_version(pin), "3.12.10")

    def test_gdal_wheel_is_the_gdal_of_the_linux_images(self):
        pin = configure.load_pin(str(PIN_FILE))
        self.assertEqual(configure.gdal_wheel_version(pin, str(ROOT / "requirements.txt")), "3.8.4")


GDAL_WHEEL = "gdal-3.8.4-cp312-cp312-win_amd64.whl"


class TestGdalWheelVersion(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.requirements = Path(self.tmp.name) / "requirements.txt"
        self.requirements.write_text("numpy==2.3.2\ngdal[numpy]==3.8.4 ; sys_platform == 'linux'\n")

    def tearDown(self):
        self.tmp.cleanup()

    def version(self, wheel, python="python-3.12.10-embed-amd64.zip"):
        pin = make_pin()
        pin["assets"]["python"]["file"] = python
        pin["assets"]["gdal-wheel"]["file"] = wheel
        return configure.gdal_wheel_version(pin, str(self.requirements))

    def test_wheel_of_the_linux_version_for_the_package_python_passes(self):
        self.assertEqual(self.version(GDAL_WHEEL), "3.8.4")
        self.assertEqual(self.version("GDAL-3.8.4-cp312-cp312-win_amd64.whl"), "3.8.4")

    def test_wheel_of_another_name_is_rejected(self):
        for wheel in ("gdal.whl", "gdal-3.8.4-cp312-cp312-linux_x86_64.whl", "GDAL-3.8.4.tar.gz"):
            with self.assertRaisesRegex(configure.DepsError, "gdal-X.Y.Z-cp312-cp312-win_amd64.whl"):
                self.version(wheel)

    def test_wheel_for_another_python_is_rejected(self):
        with self.assertRaisesRegex(configure.DepsError, "gdal-X.Y.Z-cp312-cp312-win_amd64.whl"):
            self.version("gdal-3.8.4-cp311-cp311-win_amd64.whl")
        with self.assertRaisesRegex(configure.DepsError, "gdal-X.Y.Z-cp313-cp313-win_amd64.whl"):
            self.version(GDAL_WHEEL, python="python-3.13.1-embed-amd64.zip")

    def test_wheel_of_another_version_than_linux_is_rejected(self):
        with self.assertRaisesRegex(configure.DepsError, "GDAL wheel 3.9.0, but .* pins gdal==3.8.4 for Linux"):
            self.version("gdal-3.9.0-cp312-cp312-win_amd64.whl")
        self.requirements.write_text("numpy==2.3.2\n")
        with self.assertRaisesRegex(configure.DepsError, "pins no gdal for Linux"):
            self.version(GDAL_WHEEL)


def make_export(path, top="aidm-windows-deps", triplet="x64-windows-avx2", toolchain=True):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("%s/.vcpkg-root" % top, "")
        z.writestr("%s/installed/%s/bin/gdal.dll" % (top, triplet), "dll")
        if toolchain:
            z.writestr("%s/scripts/buildsystems/vcpkg.cmake" % top, "# toolchain")


class TestUnpackExport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.archive = os.path.join(self.tmp.name, "aidm-windows-deps.zip")
        self.export_dir = os.path.join(self.tmp.name, "vcpkg")
        self.pin = make_pin()

    def tearDown(self):
        self.tmp.cleanup()

    def test_export_unpacks_into_the_export_folder(self):
        make_export(self.archive)
        configure.unpack_export(self.pin, self.archive, self.export_dir)
        self.assertTrue(os.path.isfile(os.path.join(self.export_dir, "scripts/buildsystems/vcpkg.cmake")))
        self.assertTrue(os.path.isfile(os.path.join(self.export_dir, "installed/x64-windows-avx2/bin/gdal.dll")))
        self.assertFalse(os.path.exists(self.export_dir + ".unpack"))

    def test_same_pin_reuses_the_unpacked_export(self):
        make_export(self.archive)
        configure.unpack_export(self.pin, self.archive, self.export_dir)
        kept = os.path.join(self.export_dir, "kept")
        Path(kept).write_text("")
        configure.unpack_export(self.pin, self.archive, self.export_dir)
        self.assertTrue(os.path.exists(kept))

    def test_new_pin_replaces_the_unpacked_export(self):
        make_export(self.archive)
        configure.unpack_export(self.pin, self.archive, self.export_dir)
        stale = os.path.join(self.export_dir, "stale")
        Path(stale).write_text("")
        self.pin["assets"]["export"]["sha256"] = "1" * 64
        configure.unpack_export(self.pin, self.archive, self.export_dir)
        self.assertFalse(os.path.exists(stale))

    def test_export_without_the_pinned_triplet_is_rejected(self):
        make_export(self.archive, triplet="x64-windows")
        with self.assertRaises(configure.DepsError) as raised:
            configure.unpack_export(self.pin, self.archive, self.export_dir)
        self.assertIn("x64-windows-avx2", str(raised.exception))

    def test_export_without_the_toolchain_is_rejected(self):
        make_export(self.archive, toolchain=False)
        with self.assertRaises(configure.DepsError) as raised:
            configure.unpack_export(self.pin, self.archive, self.export_dir)
        self.assertIn("vcpkg.cmake", str(raised.exception))

    def test_export_with_two_top_folders_is_rejected(self):
        make_export(self.archive)
        with zipfile.ZipFile(self.archive, "a") as z:
            z.writestr("other/file.txt", "")
        with self.assertRaises(configure.DepsError):
            configure.unpack_export(self.pin, self.archive, self.export_dir)


class TestMsvcEnvironment(unittest.TestCase):
    def test_avx2_flag_comes_first(self):
        self.assertEqual(configure.msvc_environment({})["CL"], "/arch:AVX2")
        self.assertEqual(configure.msvc_environment({"CL": "/MP"})["CL"], "/arch:AVX2 /MP")

    def test_flag_is_not_added_twice(self):
        self.assertEqual(configure.msvc_environment({"CL": "/arch:AVX2 /MP"})["CL"], "/arch:AVX2 /MP")

    def test_caller_environment_is_not_changed(self):
        env = {"CL": "/MP"}
        configure.msvc_environment(env)
        self.assertEqual(env, {"CL": "/MP"})


def write_cmake_pin(folder, text):
    path = os.path.join(folder, "windows-cmake.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


class TestLoadCmakePin(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def load(self, text):
        return configure.load_cmake_pin(write_cmake_pin(self.tmp.name, text))

    def rejected(self, text, message):
        with self.assertRaisesRegex(configure.DepsError, message):
            self.load(text)

    def test_valid_pin_loads_with_comments(self):
        self.assertEqual(self.load("# CMake for the SuperBuild\ncmake==4.4.5 --hash=sha256:%s\n" % GOOD_HASH), "4.4.5")

    def test_several_hashes_and_a_rebuild_number_load(self):
        line = "cmake==4.2.0.1 --hash=sha256:%s --hash=sha256:%s\n" % (GOOD_HASH, "0" * 64)
        self.assertEqual(self.load(line), "4.2.0.1")

    def test_line_without_a_hash_is_rejected(self):
        self.rejected("cmake==4.4.5\n", "--hash=sha256")

    def test_short_or_uppercase_hash_is_rejected(self):
        self.rejected("cmake==4.4.5 --hash=sha256:%s\n" % GOOD_HASH[:63], "--hash=sha256")
        self.rejected("cmake==4.4.5 --hash=sha256:%s\n" % GOOD_HASH.upper(), "--hash=sha256")

    def test_range_instead_of_one_version_is_rejected(self):
        self.rejected("cmake>=4.4.5 --hash=sha256:%s\n" % GOOD_HASH, "cmake==X.Y.Z")

    def test_cmake_without_a_visual_studio_18_generator_is_rejected(self):
        self.rejected("cmake==3.31.10 --hash=sha256:%s\n" % GOOD_HASH, "Pin 4.2 or later")
        self.rejected("cmake==4.1.3 --hash=sha256:%s\n" % GOOD_HASH, "Pin 4.2 or later")

    def test_two_requirements_are_rejected(self):
        self.rejected("cmake==4.4.5 --hash=sha256:%s\nninja==1.13.0 --hash=sha256:%s\n" % (GOOD_HASH, GOOD_HASH),
                      "one requirement line, found 2")

    def test_unreadable_pin_is_rejected(self):
        with self.assertRaisesRegex(configure.DepsError, "cannot read the CMake pin"):
            configure.load_cmake_pin(os.path.join(self.tmp.name, "missing.txt"))


@unittest.skipUnless(CMAKE_PIN_FILE.exists(), "the CMake pin is a SuperBuild file; unit CI has the last release's copy")
class TestCommittedCmakePin(unittest.TestCase):
    def test_pin_names_one_cmake_with_a_visual_studio_18_generator(self):
        version = configure.load_cmake_pin(str(CMAKE_PIN_FILE))
        self.assertGreaterEqual(tuple(int(n) for n in version.split(".")[:2]), configure.CMAKE_MINIMUM)


class FakeTools:
    """Stands in for subprocess.run: plays pip and the cmake it installs.

    pip "installs" by writing the cmake program into its --target folder,
    unless pip_exit is not 0, which is how pip ends on a hash mismatch.
    """

    def __init__(self, pip_exit=0, version="4.4.5", default="Visual Studio 18 2026"):
        self.pip_exit = pip_exit
        self.version = version
        self.default = default
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append((list(cmd), kwargs))
        if cmd[1:4] == ["-m", "pip", "install"]:
            if self.pip_exit == 0:
                program = configure.cmake_executable(cmd[cmd.index("--target") + 1])
                os.makedirs(os.path.dirname(program))
                Path(program).write_text("cmake")
            return SimpleNamespace(returncode=self.pip_exit)
        if cmd[1:] == ["--version"]:
            return SimpleNamespace(returncode=0, stdout="cmake version %s\n\nCMake suite maintained and supported by Kitware.\n" % self.version)
        if cmd[1:] == ["--help"]:
            generators = ["Visual Studio 18 2026", "Visual Studio 17 2022", "NMake Makefiles", "Ninja"]
            lines = ["Generators", "", "The following generators are available on this platform (* marks default):"]
            for name in generators:
                lines.append("%s %-27s= Generates files." % ("*" if name == self.default else " ", name))
            return SimpleNamespace(returncode=0, stdout="\n".join(lines) + "\n")
        return SimpleNamespace(returncode=0)


class TestInstallCmake(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pin = write_cmake_pin(self.tmp.name, "cmake==4.4.5 --hash=sha256:%s\n" % GOOD_HASH)
        self.cmake_dir = os.path.join(self.tmp.name, "cmake")

    def install(self, tools):
        with contextlib.redirect_stdout(io.StringIO()):
            return configure.install_cmake(self.pin, self.cmake_dir, python="python", runner=tools)

    def test_pip_installs_the_pin_with_its_hashes_only(self):
        tools = FakeTools()
        cmake = self.install(tools)
        target = os.path.join(self.cmake_dir, "4.4.5")
        self.assertEqual(tools.calls[0][0], ["python", "-m", "pip", "install", "--disable-pip-version-check",
                                             "--require-hashes", "--only-binary", ":all:", "--no-deps",
                                             "--target", target, "-r", self.pin])
        self.assertEqual(cmake, configure.cmake_executable(target))
        self.assertTrue(os.path.isfile(cmake))

    def test_wrong_hash_stops_and_leaves_no_cmake(self):
        with self.assertRaisesRegex(configure.DepsError, "pinned SHA-256"):
            self.install(FakeTools(pip_exit=1))
        self.assertFalse(os.path.exists(os.path.join(self.cmake_dir, "4.4.5")))

    def test_cmake_of_an_earlier_build_is_removed_before_pip_runs(self):
        stale = configure.cmake_executable(os.path.join(self.cmake_dir, "4.4.5"))
        os.makedirs(os.path.dirname(stale))
        Path(stale).write_text("old cmake")
        with self.assertRaises(configure.DepsError):
            self.install(FakeTools(pip_exit=1))
        self.assertFalse(os.path.exists(stale))


def pip_python():
    """A Python with pip: this one, or the one PIP_PYTHON names."""
    for python in (os.environ.get("PIP_PYTHON"), sys.executable):
        if python and subprocess.run([python, "-m", "pip", "--version"], capture_output=True).returncode == 0:
            return python
    return None


PIP_PYTHON = pip_python()


def make_wheel(folder, version="4.4.5"):
    """A wheel named like the PyPI one that holds only a cmake program."""
    name = "cmake-%s-py3-none-any.whl" % version
    dist_info = "cmake-%s.dist-info" % version
    files = {
        "cmake/__init__.py": "",
        "cmake/data/bin/cmake": "fake cmake",
        "cmake/data/bin/cmake.exe": "fake cmake",
        dist_info + "/METADATA": "Metadata-Version: 2.1\nName: cmake\nVersion: %s\n" % version,
        dist_info + "/WHEEL": "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    record = ["%s,," % path for path in files] + ["%s/RECORD,," % dist_info]
    files[dist_info + "/RECORD"] = "\n".join(record) + "\n"
    path = os.path.join(folder, name)
    with zipfile.ZipFile(path, "w") as z:
        for member, text in files.items():
            z.writestr(member, text)
    return path


@unittest.skipUnless(PIP_PYTHON, "needs a Python with pip; set PIP_PYTHON to one")
class TestInstallCmakeWithPip(unittest.TestCase):
    """Runs the real pip on a local wheel, with no index, to show that
    --require-hashes is what stops a wheel that differs from the pin."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.wheels = os.path.join(self.tmp.name, "wheels")
        os.makedirs(self.wheels)
        with open(make_wheel(self.wheels), "rb") as f:
            self.wheel_hash = sha256(f.read())
        self.cmake_dir = os.path.join(self.tmp.name, "cmake")

    def install(self, digest):
        pin = write_cmake_pin(self.tmp.name, "cmake==4.4.5 --hash=sha256:%s\n" % digest)
        env = dict(os.environ, PIP_NO_INDEX="1", PIP_FIND_LINKS=self.wheels, PIP_NO_CACHE_DIR="1")
        runner = lambda cmd: subprocess.run(cmd, env=env, capture_output=True)
        with contextlib.redirect_stdout(io.StringIO()):
            return configure.install_cmake(pin, self.cmake_dir, python=PIP_PYTHON, runner=runner)

    def test_wheel_with_the_pinned_hash_installs_cmake(self):
        self.assertTrue(os.path.isfile(self.install(self.wheel_hash)))

    def test_wheel_with_another_hash_stops_the_build(self):
        with self.assertRaisesRegex(configure.DepsError, "pinned SHA-256"):
            self.install("0" * 64)
        self.assertFalse(os.path.exists(os.path.join(self.cmake_dir, "4.4.5")))


class TestSuperbuildEnvironment(unittest.TestCase):
    cmake = os.path.join("pinned", "cmake", "data", "bin", "cmake")

    def test_policy_minimum_avx2_and_pinned_cmake_first_on_path(self):
        env = configure.superbuild_environment({"PATH": "system"}, self.cmake)
        self.assertEqual(env["CMAKE_POLICY_VERSION_MINIMUM"], "3.5")
        self.assertEqual(env["CL"], "/arch:AVX2")
        self.assertEqual(env["PATH"].split(os.pathsep), [os.path.dirname(self.cmake), "system"])

    def test_windows_spelling_of_path_is_kept(self):
        env = configure.superbuild_environment({"Path": "system"}, self.cmake)
        self.assertNotIn("PATH", env)
        self.assertEqual(env["Path"].split(os.pathsep)[0], os.path.dirname(self.cmake))

    def test_caller_environment_is_not_changed(self):
        env = {"PATH": "system"}
        configure.superbuild_environment(env, self.cmake)
        self.assertEqual(env, {"PATH": "system"})


    def test_python_for_the_tools_is_named(self):
        env = configure.superbuild_environment({"PATH": "system"}, self.cmake, r"C:\py312\python.exe")
        self.assertEqual(env["AIDM_PYTHON_EXECUTABLE"], r"C:\py312\python.exe")
        self.assertNotIn("AIDM_PYTHON_EXECUTABLE", configure.superbuild_environment({}, self.cmake))

    def test_build_python_sees_only_the_package_site_packages(self):
        env = configure.superbuild_environment({"PATH": "system", "PythonPath": r"C:\elsewhere"}, self.cmake,
                                               r"C:\py312\python.exe", r"C:\aidm\python\Lib\site-packages")
        self.assertEqual(env["PYTHONPATH"], r"C:\aidm\python\Lib\site-packages")
        self.assertNotIn("PythonPath", env)
        self.assertNotIn("PYTHONPATH", configure.superbuild_environment({}, self.cmake, r"C:\py312\python.exe"))

class TestBuildSuperbuild(unittest.TestCase):
    cmake = os.path.join("pinned", "cmake", "data", "bin", "cmake.exe")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.build_dir = os.path.join(self.tmp.name, "build")
        self.env = configure.superbuild_environment({"PATH": "system"}, self.cmake)

    def build(self, tools, pinned="4.4.5"):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            configure.build_superbuild(self.cmake, pinned, self.build_dir, self.env, runner=tools)
        return out.getvalue().splitlines()

    def test_configure_and_build_run_the_pinned_cmake_with_the_policy_minimum(self):
        tools = FakeTools()
        self.build(tools)
        configure_call, build_call = tools.calls[-2:]
        self.assertEqual(configure_call[0], [self.cmake, ".."])
        self.assertEqual(build_call[0][:5], [self.cmake, "--build", ".", "--config", "Release"])
        for cmd, kwargs in (configure_call, build_call):
            self.assertEqual(kwargs["cwd"], self.build_dir)
            self.assertEqual(kwargs["env"]["CMAKE_POLICY_VERSION_MINIMUM"], "3.5")
        self.assertTrue(all(cmd[0] == self.cmake for cmd, _ in tools.calls))

    def test_path_version_and_generator_are_printed_before_the_superbuild(self):
        lines = self.build(FakeTools())
        start = lines.index("Compiling SuperBuild")
        self.assertEqual(lines[:start], ["CMake: %s" % self.cmake, "CMake version: 4.4.5",
                                         "CMake generator: Visual Studio 18 2026",
                                         "CMAKE_POLICY_VERSION_MINIMUM=3.5"])

    def test_rebuild_number_of_the_wheel_matches_the_cmake_version(self):
        self.build(FakeTools(version="4.2.0"), pinned="4.2.0.1")

    def test_cmake_of_another_version_stops_before_configure(self):
        tools = FakeTools(version="4.4.4")
        with self.assertRaisesRegex(configure.DepsError, "reports version 4.4.4"):
            self.build(tools)
        self.assertNotIn([self.cmake, ".."], [cmd for cmd, _ in tools.calls])

    def test_nmake_default_stops_before_configure(self):
        tools = FakeTools(default="NMake Makefiles")
        with self.assertRaisesRegex(configure.DepsError, "would use the generator NMake Makefiles"):
            self.build(tools)
        self.assertNotIn([self.cmake, ".."], [cmd for cmd, _ in tools.calls])

    def test_failed_configure_stops_before_the_build(self):
        tools = FakeTools()
        failing = lambda cmd, **kwargs: SimpleNamespace(returncode=1) if cmd[1:] == [".."] else tools(cmd, **kwargs)
        with self.assertRaisesRegex(configure.DepsError, "returned 1"):
            self.build(failing)
        self.assertFalse(any("--build" in cmd for cmd, _ in tools.calls))


class TestPolicyMinimumOnWindowsOnly(unittest.TestCase):
    def test_build_does_not_run_outside_windows(self):
        with mock.patch.object(configure.sys, "platform", "linux"), \
                mock.patch.object(configure, "build") as build, \
                contextlib.redirect_stdout(io.TextIOWrapper(io.BytesIO())):
            self.assertEqual(configure.main(["build"]), 1)
        build.assert_not_called()

    def test_no_linux_build_input_sets_the_policy_minimum(self):
        files = [ROOT / "configure.sh", ROOT / "Dockerfile", ROOT / "gpu.Dockerfile"]
        files += sorted((ROOT / "docker").rglob("*"))
        files += sorted((ROOT / "SuperBuild").glob("*.txt")) + sorted((ROOT / "SuperBuild" / "cmake").glob("*.cmake"))
        for path in files:
            if path.is_file():
                self.assertNotIn("CMAKE_POLICY_VERSION_MINIMUM", path.read_text(errors="replace"), str(path))


class TestReportToolset(unittest.TestCase):
    def report(self, env):
        def fake_cl(cmd, **kwargs):
            return SimpleNamespace(stderr="Microsoft (R) C/C++ Optimizing Compiler Version 19.99 for x64\nusage\n")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            configure.report_toolset(env, runner=fake_cl)
        return out.getvalue().splitlines()

    def test_upper_case_names_of_a_windows_environment_are_read(self):
        lines = self.report({"VSCMD_VER": "18.11.0", "VISUALSTUDIOVERSION": "18.0", "VCTOOLSVERSION": "14.50.1"})
        self.assertEqual(lines, ["VSCMD_VER=18.11.0", "VisualStudioVersion=18.0", "VCToolsVersion=14.50.1",
                                 "Microsoft (R) C/C++ Optimizing Compiler Version 19.99 for x64"])

    def test_missing_names_are_reported(self):
        self.assertIn("VCToolsVersion=(not set)", self.report({}))


# The ._pth file of python-3.12.10-embed-amd64.zip in the deps release v1.0.0,
# byte for byte, as its publisher ships it.
PUBLISHER_PTH = ROOT / "tests" / "assets" / "python312._pth"
PUBLISHER_PTH_SHA256 = "2820f241bc9d6810d4db21c21cca3845799367fbdf0199620fb37c86a74b945c"


class TestEnableSitePackages(unittest.TestCase):
    def test_asset_is_the_file_its_publisher_ships(self):
        self.assertEqual(sha256(PUBLISHER_PTH.read_bytes()), PUBLISHER_PTH_SHA256)

    def test_publisher_file_imports_site_and_lists_site_packages(self):
        text = PUBLISHER_PTH.read_bytes().decode("utf-8")
        self.assertEqual(configure.enable_site_packages(text),
                         "python312.zip\r\n.\r\nLib\\site-packages\r\n\r\n"
                         "# Uncomment to run site.main() automatically\r\nimport site\r\n")

    def test_edited_file_is_not_changed_again(self):
        once = configure.enable_site_packages(PUBLISHER_PTH.read_bytes().decode("utf-8"))
        self.assertEqual(configure.enable_site_packages(once), once)

    def test_file_with_unix_line_endings_keeps_them(self):
        self.assertEqual(configure.enable_site_packages("python312.zip\n.\n#import site\n"),
                         "python312.zip\n.\nLib\\site-packages\nimport site\n")

    def test_file_without_the_commented_line_gets_import_site(self):
        self.assertEqual(configure.enable_site_packages("python312.zip\n.\n"),
                         "python312.zip\n.\nLib\\site-packages\nimport site\n")

    def test_edit_of_the_folder_keeps_the_bytes_of_the_line_endings(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "python312._pth"
            target.write_bytes(PUBLISHER_PTH.read_bytes())
            with contextlib.redirect_stdout(io.StringIO()):
                configure.edit_pth_file(folder)
            self.assertEqual(target.read_bytes(), b"python312.zip\r\n.\r\nLib\\site-packages\r\n\r\n"
                             b"# Uncomment to run site.main() automatically\r\nimport site\r\n")

    def test_folder_without_one_pth_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(configure.DepsError, "expected one ._pth file, found none"):
                configure.edit_pth_file(folder)
            (Path(folder) / "a._pth").write_text(".")
            (Path(folder) / "b._pth").write_text(".")
            with self.assertRaisesRegex(configure.DepsError, "expected one ._pth file"):
                configure.edit_pth_file(folder)


class TestBuildPython(unittest.TestCase):
    def test_asset_of_another_name_is_rejected(self):
        pin = make_pin()
        with self.assertRaisesRegex(configure.DepsError, "python-X.Y.Z-embed-amd64.zip"):
            configure.embeddable_python_version(pin)

    def check(self, version="3.12.10", machine="AMD64", bits=64):
        pin = make_pin()
        pin["assets"]["python"]["file"] = "python-3.12.10-embed-amd64.zip"
        configure.check_build_python(pin, version=version, machine=machine, pointer_bits=bits)

    def test_same_version_and_architecture_passes(self):
        self.check()

    def test_other_patch_version_is_rejected(self):
        with self.assertRaisesRegex(configure.DepsError, "Python 3.12.9 for AMD64, 64-bit"):
            self.check(version="3.12.9")

    def test_arm64_or_32_bit_python_is_rejected(self):
        with self.assertRaisesRegex(configure.DepsError, "ARM64"):
            self.check(machine="ARM64")
        with self.assertRaisesRegex(configure.DepsError, "32-bit"):
            self.check(bits=32)


class TestReadRequirements(unittest.TestCase):
    def read(self, text, platform_name="win32"):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "requirements.txt"
            path.write_text(text)
            return configure.read_requirements(str(path), platform_name=platform_name)

    def test_markers_select_the_platform(self):
        text = "numpy==2.3.2\ngdal[numpy]==3.8.4 ; sys_platform == 'linux'\n# comment\n\nsix==1.17.0  # why\n"
        self.assertEqual(self.read(text), [("numpy", "2.3.2"), ("six", "1.17.0")])
        self.assertIn(("gdal", "3.8.4"), self.read(text, "linux"))

    def test_committed_requirements_on_windows(self):
        pins = dict(configure.read_requirements(str(ROOT / "requirements.txt"), platform_name="win32"))
        self.assertNotIn("gdal", pins)
        self.assertEqual(pins["numpy"], "2.3.2")
        self.assertEqual(pins["onnxruntime"], "1.22.1")

    def test_setuptools_on_windows_only(self):
        # codem imports distutils, which Python 3.12 lacks: setuptools
        # provides it on Windows, python3-setuptools of configure.sh on Linux.
        requirements = str(ROOT / "requirements.txt")
        self.assertEqual(dict(configure.read_requirements(requirements, platform_name="win32"))["setuptools"],
                         "68.1.2")
        self.assertIn("codem", dict(configure.read_requirements(requirements, platform_name="win32")))
        self.assertNotIn("setuptools", dict(configure.read_requirements(requirements, platform_name="linux")))
        self.assertIn("python3-setuptools", (ROOT / "configure.sh").read_text())

    def test_line_the_check_cannot_read_stops_the_build(self):
        for line in ("numpy>=2", "numpy==2.3.2 ; python_version < '3.13'", "https://example.invalid/x.whl"):
            with self.assertRaisesRegex(configure.DepsError, "line 2"):
                self.read("six==1.17.0\n%s\n" % line)


VMEM_SDIST_SHA256 = "95fe9ba737edea8e5737fb9ddda7031d7ca62fd53f53c1c4e95195f3c7d801fd"


def write_source_pins(folder, text):
    path = Path(folder) / "windows-python-source.txt"
    path.write_text(text)
    return str(path)


class TestSourcePins(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_pins_load_with_their_hashes(self):
        path = write_source_pins(self.tmp.name, "# why\nvmem==1.0.2 --hash=sha256:%s\n" % VMEM_SDIST_SHA256)
        self.assertEqual(configure.load_source_pins(path), [("vmem", "1.0.2")])

    def test_pin_without_a_hash_is_rejected(self):
        for line in ("vmem==1.0.2", "vmem>=1 --hash=sha256:%s" % VMEM_SDIST_SHA256,
                     "vmem==1.0.2 --hash=sha256:%s" % VMEM_SDIST_SHA256.upper()):
            path = write_source_pins(self.tmp.name, "%s\n" % line)
            with self.assertRaisesRegex(configure.DepsError, "line 1: expected name==version"):
                configure.load_source_pins(path)

    def test_version_must_be_the_one_requirements_pin(self):
        configure.check_source_pins([("vmem", "1.0.2")], [("numpy", "2.3.2"), ("vmem", "1.0.2")])
        with self.assertRaisesRegex(configure.DepsError, "pins vmem==1.0.3, but .* pins vmem==1.0.2"):
            configure.check_source_pins([("vmem", "1.0.3")], [("vmem", "1.0.2")])
        with self.assertRaisesRegex(configure.DepsError, "no vmem for Windows"):
            configure.check_source_pins([("vmem", "1.0.2")], [("numpy", "2.3.2")])

    def test_wheel_requirements_leave_out_the_source_pins(self):
        requirements = Path(self.tmp.name) / "requirements.txt"
        requirements.write_text("numpy==2.3.2\nVMem==1.0.2  # source\nsix==1.17.0\n")
        dest = configure.write_wheel_requirements(str(requirements), [("vmem", "1.0.2")],
                                                  str(Path(self.tmp.name) / "wheels.txt"))
        self.assertEqual(Path(dest).read_text(), "numpy==2.3.2\nsix==1.17.0\n")


@unittest.skipUnless(SOURCE_PINS_FILE.exists(), "the source pins are a SuperBuild file; unit CI has the last release's copy")
class TestCommittedSourcePins(unittest.TestCase):
    def test_vmem_is_pinned_by_its_sdist_at_the_version_of_requirements(self):
        self.assertEqual(configure.load_source_pins(str(SOURCE_PINS_FILE)), [("vmem", "1.0.2")])
        self.assertIn("--hash=sha256:%s" % VMEM_SDIST_SHA256, SOURCE_PINS_FILE.read_text())
        configure.check_source_pins(configure.load_source_pins(str(SOURCE_PINS_FILE)),
                                    configure.read_requirements(str(ROOT / "requirements.txt"), "win32"))


class TestPipCommands(unittest.TestCase):
    def test_wheels_only_into_the_package_site_packages(self):
        cmd = configure.pip_wheels_command("py.exe", "site", "req.txt")
        self.assertEqual(cmd[:4], ["py.exe", "-m", "pip", "install"])
        self.assertEqual(cmd[cmd.index("--only-binary") + 1], ":all:")
        self.assertNotIn("--no-binary", cmd)
        self.assertEqual(cmd[cmd.index("--target") + 1], "site")
        self.assertEqual(cmd[-2:], ["-r", "req.txt"])
        self.assertIn("--no-compile", cmd)

    def test_gdal_wheel_alone_from_its_checked_download(self):
        cmd = configure.pip_gdal_wheel_command("py.exe", "site", r"C:\dl\%s" % GDAL_WHEEL)
        self.assertEqual(cmd[:4], ["py.exe", "-m", "pip", "install"])
        self.assertEqual(cmd[cmd.index("--target") + 1], "site")
        self.assertIn("--no-deps", cmd)
        self.assertIn("--no-index", cmd)
        self.assertNotIn("-r", cmd)
        self.assertEqual(cmd[-1], r"C:\dl\%s" % GDAL_WHEEL)

    def test_source_pins_are_built_from_their_hashed_sdist_only(self):
        cmd = configure.pip_source_command("py.exe", "site", [("vmem", "1.0.2")], "source.txt")
        self.assertEqual(cmd[:4], ["py.exe", "-m", "pip", "install"])
        self.assertIn("--require-hashes", cmd)
        self.assertIn("--no-deps", cmd)
        self.assertEqual(cmd[cmd.index("--no-binary") + 1], "vmem")
        self.assertNotIn("--only-binary", cmd)
        self.assertEqual(cmd[cmd.index("--target") + 1], "site")
        self.assertEqual(cmd[-2:], ["-r", "source.txt"])


class TestMinimalPath(unittest.TestCase):
    def test_path_is_the_folders_then_windows(self):
        env = configure.minimal_path_environment(
            {"Path": r"C:\Emulator;C:\Windows\System32", "SystemRoot": r"D:\Win", "TEMP": "t"},
            [r"C:\pkg\python", r"C:\pkg\bin"])
        self.assertEqual(env["PATH"].split(";"), [r"C:\pkg\python", r"C:\pkg\bin", r"D:\Win\System32",
                                                  r"D:\Win", r"D:\Win\System32\Wbem"])
        self.assertNotIn("Path", env)
        self.assertEqual(env["TEMP"], "t")

    def test_windows_folder_defaults_when_system_root_is_unset(self):
        env = configure.minimal_path_environment({}, [])
        self.assertEqual(env["PATH"].split(";")[0], r"C:\Windows\System32")


class TestReplaceSqlite(unittest.TestCase):
    def test_export_sqlite_replaces_the_embedded_one(self):
        with tempfile.TemporaryDirectory() as folder:
            python_dir, export_bin = Path(folder) / "python", Path(folder) / "bin"
            python_dir.mkdir()
            export_bin.mkdir()
            (python_dir / "sqlite3.dll").write_bytes(b"embedded")
            (export_bin / "SQLite3.dll").write_bytes(b"export")
            with contextlib.redirect_stdout(io.StringIO()):
                configure.replace_sqlite(str(python_dir), str(export_bin))
            self.assertEqual((python_dir / "sqlite3.dll").read_bytes(), b"export")

    def test_missing_sqlite_stops_the_build(self):
        with tempfile.TemporaryDirectory() as folder:
            python_dir = Path(folder)
            (python_dir / "sqlite3.dll").write_bytes(b"embedded")
            with self.assertRaisesRegex(configure.DepsError, "has no sqlite3.dll"):
                configure.replace_sqlite(str(python_dir), str(python_dir / "bin"))


def make_dist(site_packages, name, version):
    info = Path(site_packages) / ("%s-%s.dist-info" % (name, version))
    info.mkdir(parents=True)
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: %s\nVersion: %s\n" % (name, version))


class TestEmbeddedPythonCheck(unittest.TestCase):
    """Runs the check this script hands to the package's Python, here in the
    Python that runs the tests, against a fake site-packages."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.site = os.path.join(self.tmp.name, "Lib", "site-packages")
        make_dist(self.site, "alpha", "1.0")

    def tearDown(self):
        self.tmp.cleanup()

    def run_check(self, pins, version=None, extra_path=(), imports=("json",), env=None):
        args = {"version": version or platform.python_version(), "site_packages": self.site,
                "pins": pins, "imports": list(imports), "sqlite_dll": "unused"}
        env = dict(os.environ if env is None else env,
                   PYTHONPATH=os.pathsep.join([self.site] + list(extra_path)))
        return subprocess.run([sys.executable, "-c", configure.EMBEDDED_PYTHON_CHECK, json.dumps(args)],
                              env=env, capture_output=True, text=True)

    def test_installed_pins_pass_and_are_listed(self):
        result = self.run_check([["alpha", "1.0"]])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Installed alpha==1.0 in %s" % self.site, result.stdout)
        self.assertIn("Imported json", result.stdout)
        self.assertRegex(result.stdout, r"SQLite \d+\.\d+\.\d+ opened a database")

    def test_other_version_fails(self):
        result = self.run_check([["alpha", "2.0"]])
        self.assertEqual(result.returncode, 1)
        self.assertIn("Error: alpha is 1.0, but requirements.txt pins 2.0", result.stdout)

    def test_missing_pin_fails(self):
        result = self.run_check([["alpha", "1.0"], ["beta", "3.0"]])
        self.assertEqual(result.returncode, 1)
        self.assertIn("Error: beta==3.0 is not installed", result.stdout)

    def test_pin_outside_the_package_site_packages_fails(self):
        other = os.path.join(self.tmp.name, "elsewhere")
        make_dist(other, "beta", "3.0")
        result = self.run_check([["beta", "3.0"]], extra_path=[other])
        self.assertEqual(result.returncode, 1)
        self.assertIn("Error: beta==3.0 is in %s, not in %s" % (other, self.site), result.stdout)

    def test_other_python_version_fails(self):
        result = self.run_check([["alpha", "1.0"]], version="3.0.0")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not 3.0.0", result.stdout)

    def test_failed_import_fails(self):
        result = self.run_check([["alpha", "1.0"]], imports=["json", "aidm_no_such_module"])
        self.assertEqual(result.returncode, 1)
        self.assertIn("Error: import aidm_no_such_module failed: ModuleNotFoundError", result.stdout)

    def gdal_check(self, gdal_sqlite):
        """Run the check with osgeo imported from a fake GDAL whose SQLite
        driver reports gdal_sqlite, or the stdlib's version when None."""
        make_fake_osgeo(self.site)
        env = dict(os.environ)
        env.pop("FAKE_GDAL_SQLITE", None)
        if gdal_sqlite is not None:
            env["FAKE_GDAL_SQLITE"] = gdal_sqlite
        return self.run_check([["alpha", "1.0"]], imports=["json", "osgeo"], env=env)

    def test_gdal_reporting_the_stdlib_sqlite_passes(self):
        result = self.gdal_check(None)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Imported osgeo", result.stdout)
        self.assertIn("GDAL 3.8.4 reports SQLite %s" % sqlite3.sqlite_version, result.stdout)

    def test_gdal_reporting_another_sqlite_fails(self):
        result = self.gdal_check("3.0.1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("GDAL 3.8.4 reports SQLite 3.0.1", result.stdout)
        self.assertIn("Error: GDAL reports SQLite 3.0.1, the stdlib sqlite3 runs %s" % sqlite3.sqlite_version,
                      result.stdout)

    def test_gdal_without_its_sqlite_driver_fails(self):
        result = self.gdal_check("none")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Error: GDAL cannot report its SQLite version: AttributeError", result.stdout)

    def test_no_comparison_without_osgeo(self):
        result = self.run_check([["alpha", "1.0"]])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("GDAL", result.stdout)


def make_fake_osgeo(site_packages):
    """A stand-in for the osgeo package of the GDAL wheel: its SQLite driver
    answers sqlite_version() with FAKE_GDAL_SQLITE, by default the stdlib's
    version, and does not exist when FAKE_GDAL_SQLITE is none."""
    package = Path(site_packages) / "osgeo"
    package.mkdir()
    (package / "__init__.py").write_text("__version__ = '3.8.4'\n")
    (package / "gdal.py").write_text("__version__ = '3.8.4'\n")
    (package / "ogr.py").write_text(
        "import os\n"
        "import sqlite3\n"
        "VERSION = os.environ.get('FAKE_GDAL_SQLITE', sqlite3.sqlite_version)\n"
        "class Feature:\n"
        "    def GetField(self, index):\n"
        "        return VERSION\n"
        "class Result:\n"
        "    def GetNextFeature(self):\n"
        "        return Feature()\n"
        "class DataSource:\n"
        "    def ExecuteSQL(self, sql):\n"
        "        assert sql == 'SELECT sqlite_version()', sql\n"
        "        return Result()\n"
        "    def ReleaseResultSet(self, result):\n"
        "        pass\n"
        "class Driver:\n"
        "    def CreateDataSource(self, name):\n"
        "        assert name == ':memory:', name\n"
        "        return DataSource()\n"
        "def GetDriverByName(name):\n"
        "    return Driver() if name == 'SQLite' and VERSION != 'none' else None\n")


class TestCheckDataFolders(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.install = Path(self.tmp.name) / "install"
        for folder, required in (("gdal", "gdalvrt.xsd"), ("proj", "proj.db")):
            (self.install / "share" / folder).mkdir(parents=True)
            (self.install / "share" / folder / required).write_text("data")

    def tearDown(self):
        self.tmp.cleanup()

    def test_both_folders_pass_and_are_printed(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            configure.check_data_folders(str(self.install))
        self.assertIn("Data folder %s present, with 1 files, gdalvrt.xsd among them"
                      % (self.install / "share" / "gdal"), out.getvalue())
        self.assertIn("Data folder %s present, with 1 files, proj.db among them"
                      % (self.install / "share" / "proj"), out.getvalue())

    def test_missing_proj_db_stops(self):
        (self.install / "share" / "proj" / "proj.db").unlink()
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(configure.DepsError, "share.proj has no proj.db"):
                configure.check_data_folders(str(self.install))

    def test_missing_gdal_folder_stops(self):
        (self.install / "share" / "gdal" / "gdalvrt.xsd").unlink()
        (self.install / "share" / "gdal").rmdir()
        with self.assertRaisesRegex(configure.DepsError, "share.gdal has no gdalvrt.xsd"):
            configure.check_data_folders(str(self.install))


class FakePythonTools:
    """Stands in for subprocess.run: plays pip and the package's Python.

    pip "installs" by writing a dist-info for each pin into its --target
    folder. The package's Python records what sqlite3.dll holds when it runs.
    """

    def __init__(self, pip_exit=0, check_exit=0):
        self.pip_exit = pip_exit
        self.check_exit = check_exit
        self.calls = []
        self.requirements_seen = []
        self.sqlite_at_check = None

    def __call__(self, cmd, **kwargs):
        self.calls.append((list(cmd), kwargs))
        if cmd[1:4] == ["-m", "pip", "install"]:
            if cmd[-1].endswith(".whl"):
                if self.pip_exit == 0:
                    make_dist(cmd[cmd.index("--target") + 1], "gdal", Path(cmd[-1]).name.split("-")[1])
                return SimpleNamespace(returncode=self.pip_exit)
            self.requirements_seen.append(Path(cmd[-1]).read_text())
            if self.pip_exit == 0:
                target = cmd[cmd.index("--target") + 1]
                if "--require-hashes" in cmd:
                    pins = configure.load_source_pins(cmd[-1])
                else:
                    pins = configure.read_requirements(cmd[-1], platform_name="win32")
                for name, version in pins:
                    make_dist(target, name, version)
            return SimpleNamespace(returncode=self.pip_exit)
        self.sqlite_at_check = Path(kwargs["cwd"], "sqlite3.dll").read_bytes()
        return SimpleNamespace(returncode=self.check_exit)

    def pip_calls(self):
        return [cmd for cmd, _ in self.calls if cmd[1:4] == ["-m", "pip", "install"]]


class TestPreparePython(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.archive = root / "python-3.12.10-embed-amd64.zip"
        with zipfile.ZipFile(self.archive, "w") as z:
            z.writestr("python.exe", "exe")
            z.writestr("sqlite3.dll", "embedded sqlite")
            z.writestr("python312._pth", PUBLISHER_PTH.read_bytes())
        self.export_dir = root / "vcpkg"
        bin_dir = self.export_dir / "installed" / "x64-windows-avx2" / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "sqlite3.dll").write_bytes(b"export sqlite")
        self.export_bin = str(bin_dir)
        self.python_dir = root / "python"
        self.requirements = root / "requirements.txt"
        self.requirements.write_text("numpy==2.3.2\nvmem==1.0.2\ngdal==3.8.4 ; sys_platform == 'linux'\n")
        self.source_pins = write_source_pins(self.tmp.name, "vmem==1.0.2 --hash=sha256:%s\n" % VMEM_SDIST_SHA256)
        self.wheel = root / GDAL_WHEEL
        self.wheel.write_bytes(b"wheel")
        self.pin = make_pin()
        self.pin["assets"]["python"]["file"] = self.archive.name
        self.pin["assets"]["gdal-wheel"]["file"] = GDAL_WHEEL

    def tearDown(self):
        self.tmp.cleanup()

    def prepare(self, tools):
        with contextlib.redirect_stdout(io.StringIO()):
            configure.prepare_python(self.pin, str(self.archive), str(self.wheel), export_dir=str(self.export_dir),
                                     python_dir=str(self.python_dir), requirements=str(self.requirements),
                                     source_pins_file=self.source_pins,
                                     python="runner-python", env={"Path": r"C:\Emulator", "SystemRoot": r"C:\W"},
                                     runner=tools)

    def test_layout_wheels_sqlite_and_check(self):
        tools = FakePythonTools()
        self.prepare(tools)
        site = self.python_dir / "Lib" / "site-packages"
        self.assertTrue((site / "numpy-2.3.2.dist-info").is_dir())
        self.assertTrue((site / "vmem-1.0.2.dist-info").is_dir())
        self.assertTrue((site / "gdal-3.8.4.dist-info").is_dir())
        self.assertIn(b"\r\nLib\\site-packages\r\n", (self.python_dir / "python312._pth").read_bytes())
        self.assertIn(b"\r\nimport site\r\n", (self.python_dir / "python312._pth").read_bytes())
        wheels, source, gdal = tools.pip_calls()
        self.assertEqual(wheels[0], "runner-python")
        self.assertEqual(wheels[wheels.index("--target") + 1], str(site))
        self.assertEqual(source[source.index("--target") + 1], str(site))
        self.assertIn("--only-binary", wheels)
        self.assertEqual(tools.requirements_seen[0], "numpy==2.3.2\ngdal==3.8.4 ; sys_platform == 'linux'\n")
        self.assertIn("--require-hashes", source)
        self.assertEqual(source[-1], self.source_pins)
        self.assertEqual(gdal[gdal.index("--target") + 1], str(site))
        self.assertEqual(gdal[-1], str(self.wheel))
        self.assertIn("--no-deps", gdal)
        self.assertEqual(tools.sqlite_at_check, b"export sqlite")

        check, kwargs = tools.calls[-1]
        self.assertEqual(check[:3], [str(self.python_dir / "python.exe"), "-c", configure.EMBEDDED_PYTHON_CHECK])
        args = json.loads(check[3])
        self.assertEqual(args["version"], "3.12.10")
        self.assertEqual(args["site_packages"], str(site))
        self.assertEqual(args["pins"], [["numpy", "2.3.2"], ["vmem", "1.0.2"], ["gdal", "3.8.4"]])
        self.assertEqual(args["imports"], ["numpy", "osgeo"])
        self.assertEqual(kwargs["env"]["USE_PATH_FOR_GDAL_PYTHON"], "YES")
        self.assertEqual(kwargs["env"]["PATH"].split(";"), [str(self.python_dir), self.export_bin, r"C:\W\System32",
                                                            r"C:\W", r"C:\W\System32\Wbem"])

    def test_same_pin_and_requirements_reuse_the_layout(self):
        self.prepare(FakePythonTools())
        tools = FakePythonTools()
        self.prepare(tools)
        self.assertEqual(tools.pip_calls(), [])
        self.assertEqual(tools.sqlite_at_check, b"export sqlite")

    def test_changed_requirements_lay_out_python_again(self):
        self.prepare(FakePythonTools())
        stale = self.python_dir / "Lib" / "site-packages" / "stale.py"
        stale.write_text("")
        self.requirements.write_text("numpy==2.3.3\nvmem==1.0.2\ngdal==3.8.4 ; sys_platform == 'linux'\n")
        tools = FakePythonTools()
        self.prepare(tools)
        self.assertEqual(len(tools.pip_calls()), 3)
        self.assertFalse(stale.exists())

    def test_new_gdal_wheel_lays_out_python_again(self):
        self.prepare(FakePythonTools())
        self.pin["assets"]["gdal-wheel"]["sha256"] = sha256(b"another wheel")
        tools = FakePythonTools()
        self.prepare(tools)
        self.assertEqual(len(tools.pip_calls()), 3)

    def test_gdal_wheel_of_another_version_stops_before_pip(self):
        self.pin["assets"]["gdal-wheel"]["file"] = "gdal-3.9.0-cp312-cp312-win_amd64.whl"
        tools = FakePythonTools()
        with self.assertRaisesRegex(configure.DepsError, "GDAL wheel 3.9.0"):
            self.prepare(tools)
        self.assertEqual(tools.calls, [])

    def make_built_tree(self):
        """A SuperBuild tree where opencv and ceres have built and installed,
        with the stamps a multi-config generator writes. OpenCV's install
        manifest names cv2 in python/ the way CMake writes it on Windows,
        with forward slashes and the SuperBuild folder in the path."""
        root = Path(self.tmp.name) / "SuperBuild"
        stamps = {}
        for name, installed in (("opencv", "%s/SuperBuild/../python/lib/site-packages/cv2/__init__.py"
                                 % Path(self.tmp.name).as_posix()),
                                ("ceres", "%s/install/lib/ceres.lib" % root.as_posix())):
            stamps[name] = root / "build" / name / "stamp" / "Release"
            stamps[name].mkdir(parents=True)
            for step in ("download", "configure", "build", "install", "done"):
                (stamps[name] / ("%s-%s" % (name, step))).write_text("")
            (root / "build" / name / "install_manifest.txt").write_text(installed + "\n")
        (root / "install").mkdir()
        return root / "build", root / "install", stamps

    def decide(self, build_dir, install_dir):
        with contextlib.redirect_stdout(io.StringIO()):
            return configure.superbuild_needed(str(self.python_dir), str(build_dir), str(install_dir))

    def test_new_layout_after_a_superbuild_installs_its_python_modules_again(self):
        build_dir, install_dir, stamps = self.make_built_tree()
        self.prepare(FakePythonTools())
        self.assertTrue(self.decide(build_dir, install_dir))
        configure.superbuild_done(str(self.python_dir))
        (stamps["opencv"] / "opencv-install").write_text("")
        cv2 = self.python_dir / "Lib" / "site-packages" / "cv2"
        cv2.mkdir()

        self.prepare(FakePythonTools())
        self.assertTrue(cv2.exists())
        self.assertFalse(self.decide(build_dir, install_dir))

        self.requirements.write_text("numpy==2.3.2\nvmem==1.0.2\ngdal==3.8.4 ; sys_platform == 'linux'\n# a comment\n")
        self.prepare(FakePythonTools())
        self.assertFalse(cv2.exists())
        self.assertTrue(self.decide(build_dir, install_dir))
        self.assertFalse((stamps["opencv"] / "opencv-install").exists())
        self.assertTrue((stamps["opencv"] / "opencv-build").exists())
        self.assertTrue((stamps["opencv"] / "opencv-done").exists())
        self.assertTrue((stamps["ceres"] / "ceres-install").exists())

    def test_failed_superbuild_after_a_new_layout_is_run_again(self):
        build_dir, install_dir, stamps = self.make_built_tree()
        self.prepare(FakePythonTools())
        self.assertTrue(self.decide(build_dir, install_dir))
        (stamps["opencv"] / "opencv-install").write_text("")
        self.prepare(FakePythonTools())
        self.assertTrue(self.decide(build_dir, install_dir))
        self.assertFalse((stamps["opencv"] / "opencv-install").exists())

    def test_fresh_tree_builds_and_reused_layout_with_a_built_tree_does_not(self):
        self.prepare(FakePythonTools())
        configure.superbuild_done(str(self.python_dir))
        root = Path(self.tmp.name) / "SuperBuild"
        self.assertTrue(self.decide(root / "build", root / "install"))
        build_dir, install_dir, _ = self.make_built_tree()
        self.assertFalse(self.decide(build_dir, install_dir))

    def test_failed_pip_stops_and_leaves_no_marker(self):
        with self.assertRaisesRegex(configure.DepsError, "pip did not install"):
            self.prepare(FakePythonTools(pip_exit=1))
        self.assertFalse((self.python_dir / configure.PYTHON_MARKER).exists())

    def test_source_pin_of_another_version_stops_before_pip(self):
        self.requirements.write_text("numpy==2.3.2\nvmem==1.0.3\n")
        tools = FakePythonTools()
        with self.assertRaisesRegex(configure.DepsError, "pins vmem==1.0.2, but .* pins vmem==1.0.3"):
            self.prepare(tools)
        self.assertEqual(tools.calls, [])

    def test_check_after_the_superbuild_imports_cv2_with_its_dlls_on_path(self):
        tools = FakePythonTools()
        (self.python_dir).mkdir()
        (self.python_dir / "sqlite3.dll").write_bytes(b"export sqlite")
        configure.check_embedded_python(str(self.python_dir), self.export_bin, [("numpy", "2.3.2")], "3.12.10",
                                        {"SystemRoot": r"C:\W"}, runner=tools,
                                        imports=configure.SUPERBUILD_PYTHON_IMPORTS,
                                        dll_folders=[r"C:\aidm\SuperBuild\install\bin"])
        check, kwargs = tools.calls[-1]
        self.assertEqual(json.loads(check[3])["imports"], ["numpy", "cv2", "osgeo", "pdal", "codem"])
        self.assertEqual(kwargs["env"]["USE_PATH_FOR_GDAL_PYTHON"], "YES")
        self.assertEqual(kwargs["env"]["PATH"].split(";")[:3],
                         [str(self.python_dir), r"C:\aidm\SuperBuild\install\bin", self.export_bin])

    def test_failed_check_stops_the_build(self):
        with self.assertRaisesRegex(configure.DepsError, "failed its check, exit code 1"):
            self.prepare(FakePythonTools(check_exit=1))


class TestBuildThenCheck(unittest.TestCase):
    def run_steps(self, build_error=None, check_error=None):
        calls = []

        def step(name, error):
            def run():
                calls.append(name)
                if error:
                    raise configure.DepsError(error)
            return run

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            try:
                configure.build_then_check(step("build", build_error), step("check", check_error))
                raised = None
            except configure.DepsError as e:
                raised = str(e)
        return calls, raised, out.getvalue()

    def test_both_pass(self):
        self.assertEqual(self.run_steps(), (["build", "check"], None, ""))

    def test_check_runs_after_a_failed_build_and_the_build_failure_is_raised(self):
        calls, raised, out = self.run_steps(build_error="dem2points failed", check_error="no cv2")
        self.assertEqual(calls, ["build", "check"])
        self.assertEqual(raised, "dem2points failed")
        self.assertIn("Error: no cv2", out)

    def test_failed_check_after_a_good_build_is_raised(self):
        self.assertEqual(self.run_steps(check_error="no cv2")[1], "no cv2")


class TestDownloadSources(unittest.TestCase):
    def test_configure_downloads_only_from_the_project(self):
        urls = re.findall(r"https?://[^\s\"'<>)]+", (ROOT / "configure.py").read_text())
        self.assertTrue(urls)
        for url in urls:
            self.assertTrue(url.startswith("https://github.com/AIDroneMap/"), url)

    def test_requirements_name_no_download_url(self):
        for line in (ROOT / "requirements.txt").read_text().splitlines():
            self.assertNotRegex(line, r"https?://", line)


if __name__ == "__main__":
    unittest.main()
