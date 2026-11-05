import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import zipfile

from pyodm.types import NodeInfo, TaskInfo

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("windows_smoke", ROOT / "scripts/windows-smoke-test.py")
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


class TestConfig(unittest.TestCase):
    def test_shared_settings_match_shell(self):
        config = smoke.read_config()
        keys = ["DEFAULT_OPTIONS", "OUTPUTS", "CI_DATASET_URL", "CI_DATASET_SHA256",
                "CI_DATASET_IMAGES", "FULL_DATASET_URL", "FULL_DATASET_SHA256",
                "FULL_DATASET_IMAGES", "WINDOWS_API_URL", "WINDOWS_API_SHA256"]
        result = subprocess.run(["bash", "-c", 'source scripts/smoke-test.conf; printf "%s\\n" '
                                 + " ".join('"$%s"' % key for key in keys)], cwd=ROOT,
                                check=True, capture_output=True, text=True)
        self.assertEqual(result.stdout.splitlines(), [config[key] for key in keys])
        self.assertEqual(smoke.engine_options(config["DEFAULT_OPTIONS"]), {"dsm": True})
        self.assertEqual(len(config["OUTPUTS"].split()), 4)
        self.assertRegex(config["WINDOWS_API_SHA256"], r"^[0-9a-f]{64}$")

    def test_options_with_values(self):
        self.assertEqual(smoke.engine_options('--dsm --pc-quality high --name="a b"'),
                         {"dsm": True, "pc-quality": "high", "name": "a b"})
        with self.assertRaises(ValueError):
            smoke.engine_options("dsm")

    def test_dataset_selection(self):
        config = smoke.read_config()
        for name, count in (("ci", 32), ("full", 73)):
            dataset = smoke.select_dataset(config, name)
            self.assertEqual(dataset["images"], count)
            self.assertEqual(dataset["url"], config[name.upper() + "_DATASET_URL"])
            self.assertEqual(dataset["sha256"], config[name.upper() + "_DATASET_SHA256"])
        with self.assertRaises(ValueError):
            smoke.select_dataset(config, "other")

    def test_api_config_uses_released_keys(self):
        config = smoke.api_config("engine", "test-token", 51234)
        self.assertEqual(config, {"token": "test-token", "port": 51234,
                                  "parallelQueueProcessing": 1,
                                  "odx_path": str(Path("engine").resolve())})
        self.assertNotIn("test", config)

    def test_malformed_config(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bad.conf"
            for line in ("not a setting", 'BAD="two" "values"', '= "missing key"'):
                path.write_text(line)
                with self.subTest(line=line), self.assertRaises(ValueError):
                    smoke.read_config(path)


class TestArchives(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def archive(self, names):
        path = self.root / "input.zip"
        with zipfile.ZipFile(path, "w") as archive:
            for name, contents in names.items():
                archive.writestr(name, contents)
        return path

    def test_checked_cached_dataset_and_count(self):
        path = self.archive({"a.JPG": b"a", "b.jpg": b"b"})
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        cached = self.root / digest / "input.zip"
        cached.parent.mkdir()
        path.replace(cached)
        dataset = {"url": "https://example.invalid/input.zip", "sha256": digest, "images": 2}
        project = self.root / "images"
        images = smoke.fetch_dataset(dataset, self.root, project)
        self.assertEqual([Path(p).name for p in images], ["a.JPG", "b.jpg"])
        self.assertEqual([Path(p).read_bytes() for p in images], [b"a", b"b"])
        dataset["images"] = 3
        with self.assertRaisesRegex(RuntimeError, "expected 3 images"):
            smoke.fetch_dataset(dataset, self.root, project)
        cached.write_bytes(b"changed")
        with self.assertRaisesRegex(RuntimeError, "SHA-256 mismatch"):
            smoke.fetch(dataset["url"], digest, self.root)
        self.assertFalse(cached.exists())

    def test_download_is_checked(self):
        with mock.patch.object(smoke.urllib.request, "urlopen", return_value=io.BytesIO(b"zip")):
            digest = hashlib.sha256(b"zip").hexdigest()
            path = smoke.fetch("https://example.invalid/input.zip", digest, self.root)
        self.assertEqual(path.read_bytes(), b"zip")

    def test_json_error_is_not_an_archive(self):
        path = self.root / "all.zip"
        path.write_text('{"error":"Invalid asset"}')
        with self.assertRaises(zipfile.BadZipFile):
            smoke.unpack(path, self.root / "results")

    def test_unsafe_names_and_links_fail_before_extraction(self):
        for name in ("../outside", "C:/outside", "/outside", "a/../../outside",
                     "a\\..\\outside", "file:stream", "CON", "dir/trailing."):
            with self.subTest(name=name):
                path = self.archive({"safe": b"ok", name: b"bad"})
                with self.assertRaises(ValueError):
                    smoke.unpack(path, self.root / "results")
                self.assertFalse((self.root / "results/safe").exists())
        path = self.root / "link.zip"
        with zipfile.ZipFile(path, "w") as archive:
            link = zipfile.ZipInfo("link")
            link.external_attr = 0o120777 << 16
            archive.writestr(link, "../outside")
        with self.assertRaisesRegex(ValueError, "link"):
            smoke.unpack(path, self.root / "results")


class TestVerdicts(unittest.TestCase):
    def test_task_states(self):
        for code in (10, 20, 30, 40, 50):
            status = TaskInfo({"uuid": "task", "name": "smoke", "dateCreated": 0,
                               "processingTime": 0, "options": [], "imagesCount": 32,
                               "status": {"code": code}}).status
            with self.subTest(code=code):
                if code in (30, 50):
                    with self.assertRaises(RuntimeError):
                        smoke.task_finished(status)
                else:
                    self.assertEqual(smoke.task_finished(status), code == 40)
        with self.assertRaisesRegex(RuntimeError, "unknown"):
            smoke.task_finished(0)

    def test_wait_polls_only_pending_states(self):
        task = mock.Mock()
        task.info.side_effect = [SimpleNamespace(status=code) for code in (10, 20, 40)]
        process = mock.Mock()
        process.poll.return_value = None
        smoke.wait_task(task, process, interval=0)
        self.assertEqual(task.info.call_count, 3)
        for code in (30, 50):
            task.info.side_effect = [SimpleNamespace(status=code)]
            with self.assertRaises(RuntimeError):
                smoke.wait_task(task, process, interval=0)
        with self.assertRaisesRegex(RuntimeError, "did not finish"):
            smoke.wait_task(task, process, timeout=0)
        process.poll.return_value = 1
        process.returncode = 1
        with self.assertRaisesRegex(RuntimeError, "API exited"):
            smoke.wait_task(task, process)

    def test_each_required_output_must_be_nonempty(self):
        config = smoke.read_config()
        with tempfile.TemporaryDirectory() as folder:
            for name in config["OUTPUTS"].split():
                path = Path(folder) / name
                path.parent.mkdir(parents=True)
                path.write_bytes(b"output")
            self.assertEqual(smoke.check_outputs(folder, config["OUTPUTS"]), [])
            for name in config["OUTPUTS"].split():
                path = Path(folder) / name
                path.write_bytes(b"")
                self.assertEqual(smoke.check_outputs(folder, config["OUTPUTS"]),
                                 ["missing or empty output: " + name])
                path.unlink()
                self.assertTrue(smoke.check_outputs(folder, config["OUTPUTS"]))
                path.write_bytes(b"output")
            self.assertTrue(smoke.check_outputs(folder, ""))

    def test_georeferencing_verdict(self):
        good = ("[georef] ok    odm_orthophoto/odm_orthophoto.tif is in EPSG:32633 "
                "and its bounds contain all 32 image positions\n")
        self.assertEqual(smoke.check_georeferencing(0, good), [])
        for code, output in ((1, good), (0, ""), (0, "[georef] FAIL: no coordinate system"),
                             (0, good + "[georef] FAIL: outside bounds")):
            with self.subTest(code=code, output=output):
                self.assertTrue(smoke.check_georeferencing(code, output))

    def test_start_mode_version_and_driver_lines(self):
        info = NodeInfo({"engine": "aidm", "engineVersion": "0.2.0"})
        good = "[INFO] No CUDA drivers detected\n"
        self.assertEqual(smoke.check_start(info, "0.2.0", 0, good), [])
        for version, code, output in (("0.3.0", 0, good), ("", 0, good),
                                      ("0.2.0", 1, good), ("0.2.0", 0, ""),
                                      ("0.2.0", 0, "[INFO] CUDA drivers detected\n"),
                                      ("0.2.0", 0, good + "[INFO] CUDA drivers detected\n"),
                                      ("0.2.0", 0, "[WARNING] No CUDA drivers detected\n")):
            with self.subTest(version=version, code=code, output=output):
                self.assertTrue(smoke.check_start(info, version, code, output))
        info.engine = "other"
        self.assertTrue(smoke.check_start(info, "0.2.0", 0, good))

    def test_step_name_survives_failure(self):
        with contextlib.redirect_stdout(io.StringIO()), \
                self.assertRaisesRegex(RuntimeError, "download results: bad zip"):
            with smoke.step("download results"):
                raise ValueError("bad zip")


class TestRecordedInputs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.record = json.loads((ROOT / "tests/assets/windows-smoke.json").read_text())

    def test_task_state_verdicts(self):
        for name, payload in self.record["task_states"].items():
            info = TaskInfo(payload)
            with self.subTest(name=name):
                if info.status.value in (30, 50):
                    with self.assertRaises(RuntimeError):
                        smoke.task_finished(info.status)
                else:
                    self.assertEqual(smoke.task_finished(info.status), info.status.value == 40)

    def test_result_member_verdicts(self):
        config = smoke.read_config()
        for name, members in self.record["results"].items():
            with self.subTest(scenario=name), tempfile.TemporaryDirectory() as folder:
                for member in members:
                    path = Path(folder) / member["name"]
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b"x" * member["size"])
                expected = ["odm_texturing/odm_textured_model_geo.obj"]
                if name == "skip_orthophotos":
                    expected.append("odm_orthophoto/odm_orthophoto.tif")
                if name == "skip_dems":
                    expected.append("odm_dem/dsm.tif")
                self.assertCountEqual(smoke.check_outputs(folder, config["OUTPUTS"]),
                                      ["missing or empty output: " + path for path in expected])
        self.assertEqual(set(config["OUTPUTS"].split()),
                         {m["name"] for m in self.record["results"]["completed"]})

    def test_recorded_georeferencing_verdicts(self):
        for name, verdict in self.record["georeferencing"].items():
            with self.subTest(verdict=name):
                self.assertEqual(bool(smoke.check_georeferencing(verdict["code"], verdict["stdout"])),
                                 name == "negative")

    def test_start_verdict_uses_source_detection_and_stub_version(self):
        info = NodeInfo(self.record["api_info"])
        self.assertEqual(smoke.check_start(info, "0.2.0", 0,
                                           self.record["gpu_source_output"]), [])
        self.assertTrue(smoke.check_start(info, "0.3.0", 0,
                                          self.record["gpu_source_output"]))


class TestPackagePython(unittest.TestCase):
    def test_batch_environment_and_literal_python_arguments(self):
        engine = Path("package with spaces/engine")
        args = ["-c", 'print("two words & symbols")']
        setup = SimpleNamespace(returncode=0, stdout="Path=packaged-python\r\n"
                                "UNICODE=caf\u00e9\r\nVALUE=a=b\r\n=C:=ignored\r\n")
        result = SimpleNamespace(returncode=0, stdout="two words & symbols\n")
        with mock.patch.object(smoke.subprocess, "run", side_effect=[setup, result]) as run:
            self.assertIs(smoke.package_python(engine, args), result)
        setup_command = run.call_args_list[0].args[0]
        self.assertIsInstance(setup_command, str)
        self.assertIn('call "%s"' % (engine / "win32env.bat"), setup_command)
        self.assertNotIn('\\"', setup_command)
        self.assertEqual(run.call_args.args[0],
                         [str(engine / "python/python.exe"), "-X", "utf8", *args])
        self.assertEqual(run.call_args.kwargs["env"],
                         {"Path": "packaged-python", "UNICODE": "caf\u00e9", "VALUE": "a=b"})

    def test_setup_failure_stops_before_python(self):
        setup = SimpleNamespace(returncode=1, stdout="", stderr="batch failed")
        with mock.patch.object(smoke.subprocess, "run", return_value=setup) as run:
            self.assertIs(smoke.package_python(Path("engine"), ["-c", "print(1)"]), setup)
        run.assert_called_once()


class TestRun(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.package = self.root / "package with spaces"
        engine = self.package / "engine"
        for name in ("VERSION", "ENGINE", "win32env.bat", "run.bat", "python/python.exe"):
            path = engine / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("0.2.0" if name == "VERSION" else "aidm")
        self.config = smoke.read_config()
        self.api_zip = self.root / "api.zip"
        with zipfile.ZipFile(self.api_zip, "w") as archive:
            archive.writestr("api/aidm-api.exe", b"executable")
        self.results_zip = self.root / "all.zip"
        self.write_outputs()
        self.work = self.root / "work"
        self.work.mkdir()
        self.node = mock.Mock()
        self.task = self.node.create_task.return_value
        self.task.info.return_value = SimpleNamespace(status=40)
        self.task.download_zip.return_value = self.results_zip
        self.process = mock.Mock()
        self.process.poll.return_value = None
        self.patches = contextlib.ExitStack()
        self.addCleanup(self.patches.close)
        self.patches.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.patches.enter_context(mock.patch("pyodm.Node", return_value=self.node))
        self.patches.enter_context(mock.patch.object(smoke, "fetch", return_value=self.api_zip))
        self.dataset = self.patches.enter_context(mock.patch.object(
            smoke, "fetch_dataset", return_value=["image.JPG"]))
        self.patches.enter_context(mock.patch.object(smoke, "start_api", return_value=self.process))
        self.patches.enter_context(mock.patch.object(smoke, "wait_api", return_value=NodeInfo(
            {"engine": "aidm", "engineVersion": "0.2.0"})))
        self.stop = self.patches.enter_context(mock.patch.object(smoke, "stop_api"))
        self.python = self.patches.enter_context(mock.patch.object(smoke, "package_python",
            return_value=SimpleNamespace(returncode=0, stderr="", stdout=
                "[georef] ok    orthophoto bounds contain all 32 image positions\n")))

    def write_outputs(self, empty=None):
        with zipfile.ZipFile(self.results_zip, "w") as archive:
            for name in self.config["OUTPUTS"].split():
                archive.writestr(name, b"" if name == empty else b"output")

    def run_smoke(self, mode="smoke"):
        smoke.run(self.package, mode, self.config, self.work, self.root / "cache")

    def test_smoke_submits_shared_options_and_checks_results(self):
        self.run_smoke()
        self.node.create_task.assert_called_once_with(["image.JPG"], {"dsm": True},
                                                      name="Windows smoke test")
        config = json.loads((self.work / "api-config.json").read_text())
        self.assertEqual(set(config), {"token", "port", "parallelQueueProcessing", "odx_path"})
        self.assertEqual(config["odx_path"], str(self.package / "engine"))
        self.assertEqual(config["parallelQueueProcessing"], 1)
        self.python.assert_called_once_with(self.package / "engine",
                                             [smoke.GEOREF_CHECK, self.work / "project"])
        self.stop.assert_called_once_with(self.process)

    def test_empty_result_fails_and_stops_api(self):
        self.write_outputs(empty="odm_texturing/odm_textured_model_geo.obj")
        with self.assertRaisesRegex(RuntimeError, "outputs: missing or empty output"):
            self.run_smoke()
        self.python.assert_not_called()
        self.stop.assert_called_once_with(self.process)

    def test_georeferencing_failure_stops_api(self):
        self.python.return_value.returncode = 1
        with self.assertRaisesRegex(RuntimeError, "georeferencing:.*exited with code 1"):
            self.run_smoke()
        self.stop.assert_called_once_with(self.process)

    def test_task_failure_does_not_download_results(self):
        self.task.info.return_value.status = 30
        with self.assertRaisesRegex(RuntimeError, "wait for task: task failed"):
            self.run_smoke()
        self.task.download_zip.assert_not_called()
        self.stop.assert_called_once_with(self.process)

    def test_start_does_not_submit_images(self):
        self.python.return_value.stdout = "[INFO] No CUDA drivers detected\n"
        self.run_smoke("start")
        self.dataset.assert_not_called()
        self.node.create_task.assert_not_called()
        self.stop.assert_called_once_with(self.process)
        self.assertEqual(self.python.call_args.args[1][0], "-c")


if __name__ == "__main__":
    unittest.main()
