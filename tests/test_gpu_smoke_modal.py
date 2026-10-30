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

# Matching lines of the Modal job log of release dry run 40973807769. The
# engine line comes from modules/osfm.py, in color; the other lines come from
# OpenSfM. "Matching 100 training pairs with FLANN" is part of the GPU path.
ENGINE_GPU_MATCHER = "\x1b[39m[INFO] Using GPU for image matching\x1b[0m\n"
OPENSFM_GPU_MATCHER = """[CUDA] 1 device(s) available for matching:
  [0] Tesla T4 (14920 MB)
[INFO] Computing pair matching on GPU (CUDA, 1 device(s))
[INFO] Training projection at runtime
[INFO] Matching 100 training pairs with FLANN (4 processes)
[INFO] Batched Hamming matching 255 pairs (5100000 total features) on GPU
[INFO] Batched Hamming matching completed in 0.61 seconds
[INFO] Computing pair matching with 4 processes
"""
# The warning of opensfm/matching.py, in the "[%(levelname)s] %(message)s"
# format of opensfm/log.py, at the commit SuperBuild/cmake/External-OpenSfM.cmake
# pins. OpenSfM writes it instead of the GPU line when CUDA is missing.
OPENSFM_CPU_FALLBACK = ("[WARNING] GPU matching requested but CUDA is not available. "
                        "Falling back to CPU FLANN.\n")
GPU_MATCHER = ENGINE_GPU_MATCHER + OPENSFM_GPU_MATCHER

GOOD_LOG = """[INFO]    nvidia-smi detected
""" + GPU_MATCHER + """[INFO]    running "/code/SuperBuild/install/bin/OpenMVS/DensifyPointCloud" "/x/scene.mvs" --resolution-level 1 --cuda-device -1 --geometric-iters 0
"""
CPU_DENSIFY = """[INFO]    running "/code/SuperBuild/install/bin/OpenMVS/DensifyPointCloud" "/x/scene.mvs" --resolution-level 1 --cuda-device -2 --geometric-iters 0
"""


DATASET_KEYS = ["CI_DATASET_URL", "CI_DATASET_SHA256", "CI_DATASET_IMAGES",
                "FULL_DATASET_URL", "FULL_DATASET_SHA256", "FULL_DATASET_IMAGES"]


class TestReadConfig(unittest.TestCase):
    def test_matches_bash(self):
        keys = DATASET_KEYS + ["DEFAULT_OPTIONS", "OUTPUTS"]
        out = subprocess.run(
            ["bash", "-c",
             'source scripts/smoke-test.conf; printf "%s\\n" '
             + " ".join('"$%s"' % k for k in keys)],
            cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines()
        config = gpu_smoke.read_config(CONF)
        self.assertEqual([config[k] for k in keys], out)
        self.assertEqual(len(config["OUTPUTS"].split()), 4)

    def test_pins(self):
        config = gpu_smoke.read_config(CONF)
        for name in gpu_smoke.DATASETS:
            with self.subTest(dataset=name):
                prefix = name.upper() + "_DATASET_"
                self.assertRegex(config[prefix + "SHA256"], r"^[0-9a-f]{64}$")
                self.assertRegex(config[prefix + "URL"], r"^https://\S+/[^/]+\.zip$")
                self.assertGreater(int(config[prefix + "IMAGES"]), 0)
        # The full set is the 73-image survey that releases and benchmarks use
        self.assertEqual(config["FULL_DATASET_IMAGES"], "73")

    def test_malformed_line(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "bad.conf")
            with open(path, "w") as f:
                f.write('GOOD="1"\nthis is not a setting\n')
            with self.assertRaises(ValueError):
                gpu_smoke.read_config(path)


class TestSelectDataset(unittest.TestCase):
    def setUp(self):
        self.config = gpu_smoke.read_config(CONF)

    def test_ci_and_full(self):
        for name in ("ci", "full"):
            with self.subTest(dataset=name):
                prefix = name.upper() + "_DATASET_"
                self.assertEqual(gpu_smoke.select_dataset(self.config, name), {
                    "url": self.config[prefix + "URL"],
                    "sha256": self.config[prefix + "SHA256"],
                    "images": int(self.config[prefix + "IMAGES"]),
                })

    def test_unknown_name(self):
        with self.assertRaisesRegex(RuntimeError, "expected ci or full"):
            gpu_smoke.select_dataset(self.config, "small")


class TestFetchDataset(unittest.TestCase):
    ZIP = "set.zip"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = os.path.join(self.tmp.name, "cache")
        self.folder = os.path.join(self.tmp.name, "images")
        os.makedirs(self.folder)

    def make_dataset(self, names, sha256=None):
        """Write a zip of fake images into the cache and return a matching dataset.

        The zip goes where fetch_dataset looks for it, so nothing is downloaded.
        """
        with tempfile.TemporaryDirectory() as d:
            staged = os.path.join(d, self.ZIP)
            with zipfile.ZipFile(staged, "w") as z:
                for name in names:
                    z.writestr(name, b"not an image")
            with open(staged, "rb") as f:
                data = f.read()
        digest = sha256 or hashlib.sha256(data).hexdigest()
        path = os.path.join(self.cache, digest, self.ZIP)
        os.makedirs(os.path.dirname(path))
        with open(path, "wb") as f:
            f.write(data)
        return path, {
            "url": "https://example.invalid/never-used/" + self.ZIP,
            "sha256": digest,
            "images": 3,
        }

    def test_good_zip(self):
        _, dataset = self.make_dataset(["a.JPG", "b.jpg", "c.jpg"])
        paths = gpu_smoke.fetch_dataset(dataset, self.cache, self.folder)
        self.assertEqual(paths, [os.path.join(self.folder, n) for n in ("a.JPG", "b.jpg", "c.jpg")])
        self.assertTrue(all(os.path.isfile(p) for p in paths))

    def test_wrong_sha256(self):
        path, dataset = self.make_dataset(["a.JPG", "b.jpg", "c.jpg"], sha256="f" * 64)
        with self.assertRaisesRegex(RuntimeError, "SHA-256"):
            gpu_smoke.fetch_dataset(dataset, self.cache, self.folder)
        self.assertFalse(os.path.exists(path))

    def test_wrong_image_count(self):
        _, dataset = self.make_dataset(["a.JPG", "b.jpg"])
        with self.assertRaisesRegex(RuntimeError, "expected 3 images"):
            gpu_smoke.fetch_dataset(dataset, self.cache, self.folder)


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
        text = "[INFO]    nvidia-smi detected\n" + GPU_MATCHER + CPU_DENSIFY
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


class TestCheckMatcher(unittest.TestCase):
    """Feature matching ran on the GPU (ADR 0002)."""

    def test_both_lines(self):
        self.assertEqual(gpu_smoke.check_engine_log(GOOD_LOG), [])

    def test_engine_line_missing(self):
        text = GOOD_LOG.replace(ENGINE_GPU_MATCHER, "")
        self.assertEqual(gpu_smoke.check_engine_log(text),
                         ["the engine did not log 'Using GPU for image matching'"])

    def test_opensfm_line_missing(self):
        text = GOOD_LOG.replace(OPENSFM_GPU_MATCHER, "")
        self.assertEqual(gpu_smoke.check_engine_log(text),
                         ["OpenSfM did not log 'Computing pair matching on GPU'"])

    def test_cpu_fallback(self):
        # What OpenSfM writes when the engine asks for the GPU matcher and
        # CUDA is missing: the warning, and no GPU line
        text = GOOD_LOG.replace(OPENSFM_GPU_MATCHER, OPENSFM_CPU_FALLBACK)
        self.assertEqual(gpu_smoke.check_engine_log(text), [
            "OpenSfM did not log 'Computing pair matching on GPU'",
            "feature matching fell back to the CPU: " + OPENSFM_CPU_FALLBACK.strip(),
        ])

    def test_cpu_fallback_next_to_gpu_line(self):
        # One fallback fails the test even when another matching run used the GPU
        text = GOOD_LOG + OPENSFM_CPU_FALLBACK
        self.assertEqual(gpu_smoke.check_engine_log(text),
                         ["feature matching fell back to the CPU: " + OPENSFM_CPU_FALLBACK.strip()])

    def test_each_fallback_string(self):
        # OpenSfM writes both strings in one warning; either one fails the test
        for fallback in gpu_smoke.MATCHER_FALLBACK:
            with self.subTest(fallback=fallback):
                self.assertIn(fallback, OPENSFM_CPU_FALLBACK)
                line = "[WARNING] " + fallback + "\n"
                self.assertEqual(gpu_smoke.check_engine_log(GOOD_LOG + line),
                                 ["feature matching fell back to the CPU: " + line.strip()])


if __name__ == "__main__":
    unittest.main()
