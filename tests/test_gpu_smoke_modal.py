import importlib.util
import os
import subprocess
import tempfile
import unittest

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
             '"$DATASET_COMMIT" "$DATASET_IMAGES" "$DEFAULT_OPTIONS" "$OUTPUTS"'],
            cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines()
        config = gpu_smoke.read_config(CONF)
        keys = ["DATASET_REPO", "DATASET_COMMIT", "DATASET_IMAGES",
                "DEFAULT_OPTIONS", "OUTPUTS"]
        self.assertEqual([config[k] for k in keys], out)
        self.assertEqual(len(config["OUTPUTS"].split()), 4)

    def test_malformed_line(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "bad.conf")
            with open(path, "w") as f:
                f.write('GOOD="1"\nthis is not a setting\n')
            with self.assertRaises(ValueError):
                gpu_smoke.read_config(path)


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
