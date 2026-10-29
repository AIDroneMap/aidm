import importlib.util
from pathlib import Path
import unittest


BUILD_TEST = Path(__file__).resolve().parents[1] / "SuperBuild/openmvs/test_patch.py"


def load_tests(loader, standard_tests, pattern):
    if BUILD_TEST.exists():
        spec = importlib.util.spec_from_file_location("openmvs_patch_tests", BUILD_TEST)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return loader.loadTestsFromModule(module)
    return loader.loadTestsFromTestCase(TestBuildInputs)


class TestBuildInputs(unittest.TestCase):
    @unittest.skip("New OpenMVS build inputs are checked by the full image build")
    def test_build_inputs(self):
        pass
