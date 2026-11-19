import os
import tempfile
import unittest
from unittest import mock

from modules import ai


class TestModelsDir(unittest.TestCase):
    def test_windows_keeps_models_in_local_application_data(self):
        local = "C:\\Users\\pilot\\AppData\\Local"
        path = ai.models_dir(platform="win32", environ={"LOCALAPPDATA": local, "PROGRAMDATA": "C:\\ProgramData"})
        self.assertEqual(path, os.path.join(local, "AIDM", "models"))
        self.assertNotIn("ProgramData", path)
        self.assertNotIn("ODX", path.upper())
        self.assertNotIn("ODM", path.upper())

    def test_windows_without_local_application_data(self):
        path = ai.models_dir(platform="win32", environ={})
        self.assertEqual(path, os.path.join(os.path.expanduser("~"), "AppData", "Local", "AIDM", "models"))

    def test_linux_keeps_models_in_the_engine_folder(self):
        engine = os.path.abspath(os.path.join(os.path.dirname(ai.__file__), ".."))
        self.assertEqual(ai.models_dir(platform="linux", environ={"LOCALAPPDATA": "x"}),
                         os.path.join(engine, "storage", "models"))

    def test_get_model_on_windows_reads_local_application_data(self):
        with tempfile.TemporaryDirectory() as local, tempfile.TemporaryDirectory() as program_data:
            model = os.path.join(local, "AIDM", "models", "sky", "v1_0", "model.onnx")
            os.makedirs(os.path.dirname(model))
            open(model, "wb").close()
            env = {"LOCALAPPDATA": local, "PROGRAMDATA": program_data}
            with mock.patch.object(ai.sys, "platform", "win32"), mock.patch.dict(os.environ, env), \
                    mock.patch.object(ai, "download", side_effect=OSError("no network in this test")):
                self.assertEqual(ai.get_model("sky", "https://example.invalid/m.zip", "v1.0"), model)
            self.assertEqual(os.listdir(program_data), [])


if __name__ == "__main__":
    unittest.main()
