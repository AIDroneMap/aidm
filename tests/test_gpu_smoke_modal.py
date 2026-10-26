import hashlib
import importlib.util
import os
import subprocess
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "gpu-smoke-modal.py")
CONF = os.path.join(ROOT, "scripts", "smoke-test.conf")

spec = importlib.util.spec_from_file_location("gpu_smoke_modal", SCRIPT)
gpu_smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gpu_smoke)

GOOD_LOG = """[INFO]    nvidia-smi detected
[INFO]    running "/code/SuperBuild/install/bin/OpenMVS/DensifyPointCloud" "/x/scene.mvs" --resolution-level 1 --cuda-device -1 --geometric-iters 0
"""
CPU_DENSIFY = """[INFO]    running "/code/SuperBuild/install/bin/OpenMVS/DensifyPointCloud" "/x/scene.mvs" --resolution-level 1 --cuda-device -2 --geometric-iters 0
"""


class TestReadConfig(unittest.TestCase):
    def test_matches_bash(self):
        out = subprocess.run(
            ["bash", "-c",
             'source scripts/smoke-test.conf; printf "%s\\n" "$DATASET_REPO" '
             '"$DATASET_COMMIT" "$DATASET_ZIP" "$DATASET_SHA256" "$DATASET_IMAGES" '
             '"$DEFAULT_OPTIONS" "$OUTPUTS"'],
            cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines()
        config = gpu_smoke.read_config(CONF)
        keys = ["DATASET_REPO", "DATASET_COMMIT", "DATASET_ZIP", "DATASET_SHA256",
                "DATASET_IMAGES", "DEFAULT_OPTIONS", "OUTPUTS"]
        self.assertEqual([config[k] for k in keys], out)
        self.assertEqual(len(config["OUTPUTS"].split()), 4)

    def test_pins_look_like_hashes(self):
        config = gpu_smoke.read_config(CONF)
        self.assertRegex(config["DATASET_SHA256"], r"^[0-9a-f]{64}$")
        self.assertRegex(config["DATASET_COMMIT"], r"^[0-9a-f]{40}$")

    def test_malformed_line(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "bad.conf")
            with open(path, "w") as f:
                f.write('GOOD="1"\nthis is not a setting\n')
            with self.assertRaises(ValueError):
                gpu_smoke.read_config(path)


class TestFetchDataset(unittest.TestCase):
    COMMIT = "0" * 40
    ZIP = "set.zip"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = os.path.join(self.tmp.name, "cache")
        self.folder = os.path.join(self.tmp.name, "images")
        os.makedirs(self.folder)

    def make_config(self, names, sha256=None):
        """Write a zip of fake images into the cache and return a matching config."""
        path = os.path.join(self.cache, self.COMMIT, self.ZIP)
        os.makedirs(os.path.dirname(path))
        with zipfile.ZipFile(path, "w") as z:
            for name in names:
                z.writestr(name, b"not an image")
        with open(path, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        return path, {
            "DATASET_REPO": "https://example.invalid/never-used",
            "DATASET_COMMIT": self.COMMIT,
            "DATASET_ZIP": self.ZIP,
            "DATASET_IMAGES": "3",
            "DATASET_SHA256": sha256 or digest,
        }

    def test_good_zip(self):
        _, config = self.make_config(["a.JPG", "b.jpg", "c.jpg"])
        paths = gpu_smoke.fetch_dataset(config, self.cache, self.folder)
        self.assertEqual(paths, [os.path.join(self.folder, n) for n in ("a.JPG", "b.jpg", "c.jpg")])
        self.assertTrue(all(os.path.isfile(p) for p in paths))

    def test_wrong_sha256(self):
        path, config = self.make_config(["a.JPG", "b.jpg", "c.jpg"], sha256="f" * 64)
        with self.assertRaisesRegex(RuntimeError, "SHA-256"):
            gpu_smoke.fetch_dataset(config, self.cache, self.folder)
        self.assertFalse(os.path.exists(path))

    def test_wrong_image_count(self):
        _, config = self.make_config(["a.JPG", "b.jpg"])
        with self.assertRaisesRegex(RuntimeError, "expected 3 images"):
            gpu_smoke.fetch_dataset(config, self.cache, self.folder)


QUERY_OUTPUT = "Tesla T4, 610.57.04, 15360 MiB\n"


class TestCheckNvidiaSmi(unittest.TestCase):
    def test_query_ok(self):
        self.assertEqual(gpu_smoke.check_nvidia_smi(0, QUERY_OUTPUT), [])

    def test_header_without_cuda_version_does_not_fail(self):
        # Driver 610.57.04 on Modal: the query works, but the header has no
        # "CUDA Version:" text. That must not fail the test.
        header = "| NVIDIA-SMI 610.57.04    Driver Version: 610.57.04 |\n"
        self.assertIsNone(gpu_smoke.cuda_version(header))
        self.assertEqual(gpu_smoke.check_nvidia_smi(0, QUERY_OUTPUT), [])

    def test_query_fails(self):
        self.assertTrue(gpu_smoke.check_nvidia_smi(127, "bash: nvidia-smi: command not found\n"))

    def test_query_lists_no_gpu(self):
        self.assertTrue(gpu_smoke.check_nvidia_smi(0, ""))


class TestCudaVersion(unittest.TestCase):
    def test_classic_header(self):
        header = "| NVIDIA-SMI 575.57.08    Driver Version: 575.57.08    CUDA Version: 12.9     |"
        self.assertEqual(gpu_smoke.cuda_version(header), "12.9")

    def test_driver_version_label(self):
        self.assertEqual(gpu_smoke.cuda_version("CUDA Driver Version: 13.3"), "13.3")

    def test_empty(self):
        self.assertIsNone(gpu_smoke.cuda_version(""))


class TestCheckEngineLog(unittest.TestCase):
    def test_passing_log(self):
        self.assertEqual(gpu_smoke.check_engine_log(GOOD_LOG), [])

    def test_no_nvidia_smi(self):
        text = "[INFO]    No nvidia-smi detected\n" + CPU_DENSIFY
        self.assertTrue(gpu_smoke.check_engine_log(text))

    def test_densify_on_cpu_though_gpu_detected(self):
        text = "[INFO]    nvidia-smi detected\n" + CPU_DENSIFY
        problems = gpu_smoke.check_engine_log(text)
        self.assertEqual(len(problems), 1)
        self.assertIn("--cuda-device -1", problems[0])

    def test_fallback_warning(self):
        text = (GOOD_LOG
                + "[WARNING] OpenMVS failed with GPU, is your graphics card "
                  "driver up to date? Falling back to CPU.\n"
                + CPU_DENSIFY)
        problems = gpu_smoke.check_engine_log(text)
        self.assertTrue(any("fell back" in p for p in problems))
        self.assertTrue(any("--cuda-device -1" in p for p in problems))

    def test_no_densify_line(self):
        problems = gpu_smoke.check_engine_log("[INFO]    nvidia-smi detected\n")
        self.assertTrue(any("did not run" in p for p in problems))

    def test_empty_log(self):
        self.assertTrue(gpu_smoke.check_engine_log(""))


if __name__ == "__main__":
    unittest.main()
