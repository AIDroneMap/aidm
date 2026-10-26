import hashlib
import os
import shutil
import tempfile
import tracemalloc
import unittest
from unittest import mock

import fiona
import numpy as np
import rasterio
from edt import edt
from rasterio.enums import ColorInterp
from rasterio.mask import mask
from rasterio.transform import Affine

from modules import orthophoto

CRS = "EPSG:32617"
ORIGIN_X, ORIGIN_Y, PIXEL = 500000.0, 4000000.0, 0.5
TRANSFORM = Affine(PIXEL, 0, ORIGIN_X, 0, -PIXEL, ORIGIN_Y)
BLOCK = 64
MIB = 1024 * 1024


# The 0.1.0 code, kept as the reference: it works on the whole raster at once.
def full_array_mask_raster(input_raster, vector_mask, output_raster, blend_distance=20, only_max_coords_feature=False):
    with rasterio.open(input_raster, 'r') as rast:
        with fiona.open(vector_mask) as src:
            burn_features = src

            if only_max_coords_feature:
                max_coords_count = 0
                max_coords_feature = None
                for feature in src:
                    if feature is not None:
                        # No complex shapes
                        if len(feature['geometry']['coordinates'][0]) > max_coords_count:
                            max_coords_count = len(feature['geometry']['coordinates'][0])
                            max_coords_feature = feature
                if max_coords_feature is not None:
                    burn_features = [max_coords_feature]

            shapes = [feature["geometry"] for feature in burn_features]
            out_image, out_transform = mask(rast, shapes, nodata=0)

            if blend_distance > 0:
                if out_image.shape[0] >= 4:
                    alpha_band = out_image[-1]
                    dist_t = edt(alpha_band, black_border=True, parallel=0)
                    dist_t[dist_t <= blend_distance] /= blend_distance
                    dist_t[dist_t > blend_distance] = 1
                    np.multiply(alpha_band, dist_t, out=alpha_band, casting="unsafe")

            with rasterio.open(output_raster, 'w', BIGTIFF="IF_SAFER", **rast.profile) as dst:
                dst.colorinterp = rast.colorinterp
                dst.write(out_image)

            return output_raster


# The 0.1.0 code, kept as the reference: it works on the whole raster at once.
def full_array_feather_raster(input_raster, output_raster, blend_distance=20):
    with rasterio.open(input_raster, 'r') as rast:
        out_image = rast.read()
        if blend_distance > 0:
            if out_image.shape[0] >= 4:
                alpha_band = out_image[-1]
                dist_t = edt(alpha_band, black_border=True, parallel=0)
                dist_t[dist_t <= blend_distance] /= blend_distance
                dist_t[dist_t > blend_distance] = 1
                np.multiply(alpha_band, dist_t, out=alpha_band, casting="unsafe")

        with rasterio.open(output_raster, 'w', BIGTIFF="IF_SAFER", **rast.profile) as dst:
            dst.colorinterp = rast.colorinterp
            dst.write(out_image)

        return output_raster


def sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def write_raster(path, data, tiled=True, compress="LZW"):
    """Write a RGBA uint8 GeoTIFF with a projected CRS."""
    count, height, width = data.shape
    options = dict(driver="GTiff", width=width, height=height, count=count, dtype="uint8",
                   crs=CRS, transform=TRANSFORM, compress=compress, photometric="RGB")
    if tiled:
        options.update(tiled=True, blockxsize=BLOCK, blockysize=BLOCK)
    with rasterio.open(path, "w", **options) as dst:
        dst.write(data)
        dst.colorinterp = [ColorInterp.red, ColorInterp.green, ColorInterp.blue, ColorInterp.alpha][:count]


def synthetic_rgba(width=700, height=520, seed=1):
    """
    Noise in RGB. The alpha band has an irregular opaque region that touches the
    left and bottom borders, is transparent at the top and right borders, and has
    holes, a one pixel hole and a patch of alpha 128.
    """
    rng = np.random.default_rng(seed)
    data = rng.integers(0, 256, size=(4, height, width), dtype=np.uint8)

    yy, xx = np.mgrid[0:height, 0:width]
    alpha = np.zeros((height, width), dtype=np.uint8)
    alpha[((xx - 350) / 320.) ** 2 + ((yy - 260) / 230.) ** 2 < 1] = 255
    alpha[100:300, 0:40] = 255          # opaque, touches the left border
    alpha[height - 20:, 200:420] = 255  # opaque, touches the bottom border
    alpha[:30, 600:] = 0                # transparent, touches the top and right borders

    alpha[124:132, 120:136] = 0         # hole across a window corner
    alpha[190:200, 63:66] = 0           # hole across a block edge
    alpha[300:320, 300:320] = 0         # hole far from window edges
    alpha[(yy - 400) ** 2 + (xx - 500) ** 2 < 12 ** 2] = 0
    alpha[256, 256] = 0                 # single pixel hole
    alpha[200:260, 400:470] = 128       # other non-zero alpha value
    data[3] = alpha
    return data


def pixel_to_world(points):
    return [(ORIGIN_X + col * PIXEL, ORIGIN_Y - row * PIXEL) for col, row in points]


def write_cutline(path, big, small):
    """Two polygons given as lists of (col, row) points. big has more points."""
    schema = {"geometry": "Polygon", "properties": {"id": "int"}}
    with fiona.open(path, "w", driver="GPKG", schema=schema, crs=CRS) as dst:
        for i, points in enumerate((big, small)):
            ring = pixel_to_world(points + points[:1])
            dst.write({"geometry": {"type": "Polygon", "coordinates": [ring]}, "properties": {"id": i}})


# Crosses the raster: it starts outside on the left, cuts the top and right.
BIG_CUTLINE = [(-50, 80), (300, -30), (400, 40), (720, 150), (650, 500), (400, 600), (100, 430), (-30, 300)]
# A triangle in the top right corner, outside the big polygon.
SMALL_CUTLINE = [(600, -10), (710, -10), (710, 60)]
# Far from the raster
OUTSIDE_CUTLINE = [(2000, 2000), (2100, 2000), (2100, 2100), (2000, 2100)]


class OrthophotoTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aidm_test_ortho_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def set_window_pixels(self, value):
        patcher = mock.patch.object(orthophoto, "WINDOW_PIXELS", value, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def path(self, name):
        return os.path.join(self.tmp, name)

    def assert_same_raster(self, actual, expected):
        with rasterio.open(actual) as a, rasterio.open(expected) as e:
            self.assertEqual(a.profile, e.profile)
            self.assertEqual(a.colorinterp, e.colorinterp)
            self.assertEqual(a.crs, e.crs)
            self.assertEqual(a.transform, e.transform)
            self.assertEqual(a.count, e.count)
            for band in range(1, e.count + 1):
                np.testing.assert_array_equal(a.read(band), e.read(band), err_msg="band %d" % band)
        self.assertEqual(sha256(actual), sha256(expected))


class TestWindowedRasters(OrthophotoTestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared = tempfile.mkdtemp(prefix="aidm_test_ortho_in_")
        data = synthetic_rgba()
        cls.tiled = os.path.join(cls.shared, "tiled.tif")
        cls.striped = os.path.join(cls.shared, "striped.tif")
        cls.deflate = os.path.join(cls.shared, "deflate.tif")
        write_raster(cls.tiled, data, tiled=True, compress="LZW")
        write_raster(cls.striped, data, tiled=False, compress="LZW")
        write_raster(cls.deflate, data, tiled=True, compress="DEFLATE")
        cls.cutline = os.path.join(cls.shared, "cutline.gpkg")
        write_cutline(cls.cutline, BIG_CUTLINE, SMALL_CUTLINE)
        cls.outside = os.path.join(cls.shared, "outside.gpkg")
        write_cutline(cls.outside, OUTSIDE_CUTLINE, OUTSIDE_CUTLINE[:3])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.shared, ignore_errors=True)

    def setUp(self):
        super().setUp()
        # Windows of one block row and two blocks wide: many windows, with
        # partial ones at the right and bottom edges (700 and 520 px)
        self.set_window_pixels(BLOCK * 2 * BLOCK)

    def check_mask(self, source, cutline, blend_distance, only_max):
        actual = self.path("mask_new.tif")
        expected = self.path("mask_ref.tif")
        result = orthophoto.compute_mask_raster(source, cutline, actual, blend_distance=blend_distance,
                                                only_max_coords_feature=only_max)
        self.assertEqual(result, actual)
        full_array_mask_raster(source, cutline, expected, blend_distance=blend_distance,
                               only_max_coords_feature=only_max)
        self.assert_same_raster(actual, expected)
        os.remove(actual)
        os.remove(expected)

    def check_feather(self, source, blend_distance):
        actual = self.path("feather_new.tif")
        expected = self.path("feather_ref.tif")
        result = orthophoto.feather_raster(source, actual, blend_distance=blend_distance)
        self.assertEqual(result, actual)
        full_array_feather_raster(source, expected, blend_distance=blend_distance)
        self.assert_same_raster(actual, expected)
        os.remove(actual)
        os.remove(expected)

    def test_input_is_split_in_many_windows(self):
        """The test input really goes through many windows, some of them partial."""
        with rasterio.open(self.tiled) as rast:
            windows = list(orthophoto.processing_windows(rast))
            self.assertEqual(rast.block_shapes[0], (BLOCK, BLOCK))
        self.assertGreater(len(windows), 50)
        self.assertEqual(sum(w.width * w.height for w in windows), 700 * 520)
        self.assertTrue(any(w.width < 128 for w in windows))
        self.assertTrue(any(w.height < BLOCK for w in windows))

    def test_mask_raster_same_as_full_array(self):
        """compute_mask_raster gives the same file as the full array code."""
        for only_max in (True, False):
            for blend in (20, 90):
                with self.subTest(only_max_coords_feature=only_max, blend_distance=blend):
                    self.check_mask(self.tiled, self.cutline, blend, only_max)

    def test_mask_raster_only_max_coords_feature_matters(self):
        """The two cutline features give different masks, so the option is tested."""
        one = self.path("one.tif")
        both = self.path("both.tif")
        orthophoto.compute_mask_raster(self.tiled, self.cutline, one, only_max_coords_feature=True)
        orthophoto.compute_mask_raster(self.tiled, self.cutline, both, only_max_coords_feature=False)
        self.assertNotEqual(sha256(one), sha256(both))

    def test_mask_raster_without_blend(self):
        """blend_distance 0 only applies the cutline."""
        self.check_mask(self.tiled, self.cutline, 0, True)

    def test_mask_raster_deflate(self):
        """Same bytes with DEFLATE compression."""
        self.check_mask(self.deflate, self.cutline, 20, True)

    def test_mask_raster_striped_input(self):
        """Same file for an untiled (striped) input."""
        for blend in (20, 90):
            with self.subTest(blend_distance=blend):
                self.check_mask(self.striped, self.cutline, blend, False)

    def test_mask_raster_cutline_outside_the_raster(self):
        """A cutline that misses the raster gives an all transparent raster, like the full array code."""
        actual = self.path("outside_new.tif")
        expected = self.path("outside_ref.tif")
        orthophoto.compute_mask_raster(self.tiled, self.outside, actual)
        full_array_mask_raster(self.tiled, self.outside, expected)
        self.assert_same_raster(actual, expected)
        with rasterio.open(actual) as rast:
            self.assertEqual(int(rast.read().max()), 0)

    def test_blend_changes_the_alpha_band(self):
        """The input is not trivial: feathering and the cutline both change the alpha band."""
        out = self.path("mask.tif")
        orthophoto.compute_mask_raster(self.tiled, self.cutline, out, blend_distance=20, only_max_coords_feature=True)
        with rasterio.open(self.tiled) as src, rasterio.open(out) as dst:
            before = src.read(4)
            after = dst.read(4)
        self.assertGreater(len(set(np.unique(after)) - {0, 128, 255}), 10)
        self.assertGreater(int(np.count_nonzero((before > 0) & (after == 0))), 1000)
        self.assertGreater(int(np.count_nonzero(before == 0)), 1000)

    def test_mask_raster_leaves_no_temporary_file(self):
        """The helper raster for the cutline is removed."""
        orthophoto.compute_mask_raster(self.tiled, self.cutline, self.path("mask.tif"))
        self.assertEqual(os.listdir(self.tmp), ["mask.tif"])

    def test_feather_raster_same_as_full_array(self):
        """feather_raster gives the same file as the full array code."""
        for blend in (20, 90):
            with self.subTest(blend_distance=blend):
                self.check_feather(self.tiled, blend)

    def test_feather_raster_without_blend(self):
        """blend_distance 0 copies the raster."""
        self.check_feather(self.tiled, 0)

    def test_feather_raster_deflate(self):
        """Same bytes with DEFLATE compression."""
        self.check_feather(self.deflate, 20)

    def test_feather_raster_striped_input(self):
        """Same file for an untiled (striped) input."""
        for blend in (20, 90):
            with self.subTest(blend_distance=blend):
                self.check_feather(self.striped, blend)


class TestMemory(OrthophotoTestCase):
    SIZE = 2048
    BANDS = 4
    ONE_BAND = SIZE * SIZE

    def setUp(self):
        super().setUp()
        rng = np.random.default_rng(2)
        data = rng.integers(0, 256, size=(self.BANDS, self.SIZE, self.SIZE), dtype=np.uint8)
        data[3] = 255
        data[3, 700:800, 900:1200] = 0
        data[3, :, :50] = 0
        self.source = self.path("big.tif")
        with rasterio.open(self.source, "w", driver="GTiff", width=self.SIZE, height=self.SIZE, count=self.BANDS,
                           dtype="uint8", crs=CRS, transform=TRANSFORM, compress="DEFLATE", tiled=True,
                           blockxsize=256, blockysize=256, photometric="RGB") as dst:
            dst.write(data)
            dst.colorinterp = [ColorInterp.red, ColorInterp.green, ColorInterp.blue, ColorInterp.alpha]
        del data

        side = self.SIZE
        self.cutline = self.path("cutline.gpkg")
        write_cutline(self.cutline,
                      [(-10, 100), (side // 2, -10), (side + 10, 200), (side + 10, side - 100), (side // 2, side + 10), (-10, side - 300)],
                      [(0, 0), (10, 0), (10, 10)])
        self.set_window_pixels(256 * 256)

    def peak(self, func, *args, **kwargs):
        tracemalloc.start()
        try:
            func(*args, **kwargs)
            return tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()

    def test_mask_raster_peak_below_one_band(self):
        """compute_mask_raster never holds as much as one band of the raster."""
        peak = self.peak(orthophoto.compute_mask_raster, self.source, self.cutline, self.path("mask.tif"))
        print("\ncompute_mask_raster tracemalloc peak: %.2f MiB (one band is %.0f MiB)" % (peak / MIB, self.ONE_BAND / MIB))
        self.assertLess(peak, self.ONE_BAND)

    def test_feather_raster_peak_below_one_band(self):
        """feather_raster never holds as much as one band of the raster."""
        peak = self.peak(orthophoto.feather_raster, self.source, self.path("feather.tif"))
        print("\nfeather_raster tracemalloc peak: %.2f MiB (one band is %.0f MiB)" % (peak / MIB, self.ONE_BAND / MIB))
        self.assertLess(peak, self.ONE_BAND)


if __name__ == '__main__':
    unittest.main()
