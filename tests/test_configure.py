import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("aidm_configure", ROOT / "configure.py")
configure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(configure)

# Unit CI copies the branch over the last published image without
# SuperBuild/, so there this is the last release's pin, or no pin at all.
PIN_FILE = ROOT / "SuperBuild" / "windows-deps.json"


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
