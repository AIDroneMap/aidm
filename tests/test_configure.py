import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
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
