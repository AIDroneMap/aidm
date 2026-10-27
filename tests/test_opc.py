import os
import shutil
import tempfile
import unittest
from unittest import mock

from modules.opc import classify

MODEL_URL = "https://github.com/uav4geo/OpenPointClass/releases/download/v1.1.3/vehicles-vegetation-buildings.zip"


class TestClassify(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.point_cloud = os.path.join(self.tmp, "cloud.laz")
        with open(self.point_cloud, "wb") as f:
            f.write(b"point cloud data")

    def test_missing_model_logs_the_url_and_leaves_the_point_cloud(self):
        with mock.patch("modules.opc.get_model", return_value=None), \
                mock.patch("modules.opc.run") as run, \
                mock.patch("modules.opc.log.WARNING") as warning:
            classify(self.point_cloud)

        run.assert_not_called()
        warning.assert_called_once_with("Cannot download/access model from %s" % MODEL_URL)
        with open(self.point_cloud, "rb") as f:
            self.assertEqual(f.read(), b"point cloud data")
        self.assertEqual(os.listdir(self.tmp), ["cloud.laz"])


if __name__ == "__main__":
    unittest.main()
