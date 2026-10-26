import os
import shutil
import tempfile
import unittest
from unittest import mock

from PIL import Image

from modules import config
from stages.dataset import ODMLoadDatasetStage


class FailingBgFilter:
    """Fake BgFilter whose run_img raises."""

    def __init__(self, model=None):
        pass

    def run_img(self, img, images_dir):
        raise RuntimeError("boom")


class EmptyBgFilter:
    """Fake BgFilter whose run_img produces no mask."""

    def __init__(self, model=None):
        pass

    def run_img(self, img, images_dir):
        return None


class TestBgRemovalWarnings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

        project = os.path.join(self.tmp, "project")
        self.images_dir = os.path.join(project, "images")
        os.makedirs(self.images_dir)
        # tests/assets/images/DJI_0002.JPG is an empty file, which the stage
        # rejects as corrupted, so write a small real JPEG instead.
        self.image = os.path.join(self.images_dir, "image_0001.jpg")
        Image.new("RGB", (64, 48), (10, 20, 30)).save(self.image)

        # Same as run.py: project_path is the base path joined with the name.
        args = config.config(
            ["--project-path", self.tmp, "--bg-removal", "--max-concurrency", "1", "project"]
        )
        args.project_path = os.path.join(args.project_path, args.name)
        self.assertEqual(args.project_path, project)
        self.assertTrue(args.bg_removal)
        self.assertFalse(args.sky_removal)
        self.args = args

    def run_stage(self, fake_filter):
        stage = ODMLoadDatasetStage("dataset", self.args, progress=5.0)
        with mock.patch("stages.dataset.ai.get_model", return_value="/dummy/model"), \
                mock.patch("stages.dataset.BgFilter", fake_filter), \
                mock.patch("stages.dataset.log.WARNING") as warning:
            stage.process(self.args, {})
        return [c.args[0] for c in warning.call_args_list if c.args and c.args[0].startswith("Cannot generate mask")]

    def test_warns_with_image_path_when_mask_generation_raises(self):
        messages = self.run_stage(FailingBgFilter)
        self.assertEqual(messages, ["Cannot generate mask for %s: boom" % self.image])

    def test_warns_with_image_path_when_no_mask_is_returned(self):
        messages = self.run_stage(EmptyBgFilter)
        self.assertEqual(messages, ["Cannot generate mask for %s" % self.image])


if __name__ == "__main__":
    unittest.main()
