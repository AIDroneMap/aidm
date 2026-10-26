import os
import shutil
import tempfile
import tracemalloc
import unittest

import cv2
import numpy as np

from modules.skyremoval import skyfilter
from modules.skyremoval.guidedfilter import guided_filter
from modules.skyremoval.skyfilter import SkyFilter

MODEL_SIZE = 384
# The new SkyFilter.BYTES_PER_PIXEL. It is written out here because the
# 0.1.0 code does not have that constant. That code needs about 96 bytes.
BYTES_PER_PIXEL = 48


# The 0.1.0 guided filter, kept as the reference: it sums in float64.
def box_reference(img, radius):
    dst = np.zeros_like(img)
    (r, c) = img.shape

    s = [radius, 1]
    c_sum = np.cumsum(img, 0)
    dst[0:radius+1, :, ...] = c_sum[radius:2*radius+1, :, ...]
    dst[radius+1:r-radius, :, ...] = c_sum[2*radius+1:r, :, ...] - c_sum[0:r-2*radius-1, :, ...]
    dst[r-radius:r, :, ...] = np.tile(c_sum[r-1:r, :, ...], s) - c_sum[r-2*radius-1:r-radius-1, :, ...]

    s = [1, radius]
    c_sum = np.cumsum(dst, 1)
    dst[:, 0:radius+1, ...] = c_sum[:, radius:2*radius+1, ...]
    dst[:, radius+1:c-radius, ...] = c_sum[:, 2*radius+1 : c, ...] - c_sum[:, 0 : c-2*radius-1, ...]
    dst[:, c-radius: c, ...] = np.tile(c_sum[:, c-1:c, ...], s) - c_sum[:, c-2*radius-1 : c-radius-1, ...]

    return dst


# The 0.1.0 guided filter, kept as the reference.
def guided_filter_reference(img, guide, radius, eps):
    (r, c) = img.shape

    CNT = box_reference(np.ones([r, c]), radius)

    mean_img = box_reference(img, radius) / CNT
    mean_guide = box_reference(guide, radius) / CNT

    a = ((box_reference(img * guide, radius) / CNT) - mean_img * mean_guide) / (((box_reference(img * img, radius) / CNT) - mean_img * mean_img) + eps)
    b = mean_guide - a * mean_img

    return (box_reference(a, radius) / CNT) * img + (box_reference(b, radius) / CNT)


def smooth_prediction(width, height):
    """A smooth pattern in [0, 1]."""
    yy, xx = np.mgrid[0:height, 0:width]
    return np.clip(0.5 + 0.6 * np.sin(xx / 40.) * np.cos(yy / 35.), 0, 1).astype(np.float32)


class TestGuidedFilter(unittest.TestCase):
    def test_same_result_as_reference_in_float32(self):
        """The float32 filter stays within 1e-6 of the 0.1.0 filter."""
        rng = np.random.default_rng(3)
        height, width = 300, 400
        yy, xx = np.mgrid[0:height, 0:width]
        img = np.clip(0.5 + 0.3 * np.sin(xx / 25.) * np.cos(yy / 30.) + 0.2 * rng.random((height, width)), 0, 1).astype(np.float32)
        guide = smooth_prediction(width, height)
        img_copy, guide_copy = img.copy(), guide.copy()

        expected = guided_filter_reference(img, guide, 20, 0.01)
        actual = guided_filter(img, guide, 20, 0.01)

        self.assertEqual(actual.dtype, np.float32)
        self.assertEqual(actual.shape, img.shape)
        self.assertLess(float(np.abs(actual - expected).max()), 1e-6)
        # The inputs are not changed by the in place steps
        np.testing.assert_array_equal(img, img_copy)
        np.testing.assert_array_equal(guide, guide_copy)


class FakeInput:
    name = "input"


class FakeSession:
    """Stands in for the onnxruntime session: returns a fixed smooth prediction."""

    def __init__(self):
        self.prediction = smooth_prediction(MODEL_SIZE, MODEL_SIZE).reshape(1, 1, MODEL_SIZE, MODEL_SIZE)

    def get_inputs(self):
        return [FakeInput()]

    def run(self, output_names, inputs):
        assert inputs["input"].shape == (1, 3, MODEL_SIZE, MODEL_SIZE)
        return [self.prediction.copy()]


def reference_mask(img_path, prediction):
    """The 0.1.0 get_mask and refine, on the image at img_path."""
    bgr = cv2.imread(img_path, cv2.IMREAD_COLOR)
    img = np.array(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB) / 255., dtype=np.float32)
    height, width, _ = img.shape

    output = np.array([prediction])
    output = output[0][0].transpose((1, 2, 0))
    output = cv2.resize(output, (width, height), interpolation=cv2.INTER_LANCZOS4)
    output = np.array([output, output, output]).transpose((1, 2, 0))
    output = np.clip(output, a_max=1.0, a_min=0.0)

    refined = guided_filter_reference(img[:, :, 2], output[:, :, 0], 20, 0.01)
    res = np.clip(refined, a_min=0, a_max=1)
    res = np.array(res * 255., dtype=np.uint8)
    return cv2.threshold(res, 127, 255, cv2.THRESH_BINARY_INV)[1]


class TestRunImg(unittest.TestCase):
    WIDTH, HEIGHT = 1600, 1200

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aidm_test_sky_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

        rng = np.random.default_rng(4)
        yy, xx = np.mgrid[0:self.HEIGHT, 0:self.WIDTH]
        img = np.stack([xx * 255. / self.WIDTH,
                        yy * 255. / self.HEIGHT,
                        128 + 100 * np.sin(xx / 90.) * np.cos(yy / 70.)], axis=2)
        img += rng.normal(0, 8, img.shape)
        self.img_path = os.path.join(self.tmp, "photo.png")
        cv2.imwrite(self.img_path, np.clip(img, 0, 255).astype(np.uint8))
        del img, xx, yy

        self.session = FakeSession()
        self.sky = SkyFilter.__new__(SkyFilter)
        self.sky.width = self.sky.height = MODEL_SIZE
        self.sky.session = self.session

    def run_img(self):
        return self.sky.run_img(self.img_path, self.tmp)

    def test_mask_file(self):
        """run_img writes a binary mask PNG with the size of the image."""
        mask_path = self.run_img()
        self.assertEqual(mask_path, os.path.join(self.tmp, "photo_mask.png"))
        mask = cv2.imread(mask_path, cv2.IMREAD_UNCHANGED)
        self.assertEqual(mask.dtype, np.uint8)
        self.assertEqual(mask.shape, (self.HEIGHT, self.WIDTH))
        self.assertEqual(set(np.unique(mask).tolist()), {0, 255})

    def test_same_mask_as_reference(self):
        """The mask matches the 0.1.0 pipeline, except for a few pixels on the edge."""
        mask = cv2.imread(self.run_img(), cv2.IMREAD_UNCHANGED)
        expected = reference_mask(self.img_path, self.session.prediction)
        self.assertEqual(mask.shape, expected.shape)

        different = mask != expected
        count = int(np.count_nonzero(different))
        print("\nmask pixels that differ from the reference: %d of %d" % (count, mask.size))
        self.assertLessEqual(count, mask.size * 0.001 / 100, "%d different pixels" % count)

        # A different pixel must have a neighbour with the other value in the reference
        edge = np.zeros(expected.shape, dtype=bool)
        for value in (0, 255):
            of_value = expected == value
            near_other = cv2.dilate((~of_value).astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
            edge |= of_value & near_other
        self.assertTrue(np.all(edge[different]))

        # The mask has both values, so the comparison is not trivial
        self.assertGreater(int(np.count_nonzero(expected == 0)), 10000)
        self.assertGreater(int(np.count_nonzero(expected == 255)), 10000)

    def test_peak_memory_per_pixel(self):
        """The peak memory of run_img stays below 48 bytes per pixel (the 0.1.0 code needs about 96)."""
        tracemalloc.start()
        try:
            self.run_img()
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        per_pixel = peak / float(self.WIDTH * self.HEIGHT)
        print("\nrun_img tracemalloc peak: %.1f MiB, %.1f bytes per pixel" % (peak / 1024. ** 2, per_pixel))
        self.assertLess(per_pixel, BYTES_PER_PIXEL)

    def test_memory_per_image_matches_the_constant(self):
        """memory_per_image is BYTES_PER_PIXEL bytes for each pixel."""
        self.assertEqual(skyfilter.BYTES_PER_PIXEL, BYTES_PER_PIXEL)
        self.assertEqual(SkyFilter.memory_per_image(1600, 1200), BYTES_PER_PIXEL * 1600 * 1200)


if __name__ == '__main__':
    unittest.main()
