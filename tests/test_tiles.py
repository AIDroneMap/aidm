import os
import tempfile
import threading
import unittest
from unittest import mock

import numpy as np
import rasterio
from rasterio.enums import ColorInterp
from rasterio.transform import from_origin

from modules.tiles import tiler
from modules.tiles.static_tiler import StaticTiler

# 20 cm/px gives a maximum zoom of 20
RESOLUTION = 20


def make_orthophoto(path):
    rng = np.random.RandomState(1)
    data = rng.randint(0, 256, (4, 200, 300)).astype(np.uint8)
    data[3] = 255
    data[3, :40, :60] = 0
    with rasterio.open(
            path, "w", driver="GTiff", width=300, height=200, count=4,
            dtype="uint8", crs="EPSG:32617",
            transform=from_origin(500000, 4400000, 0.2, 0.2)) as dst:
        dst.write(data)
        dst.colorinterp = [ColorInterp.red, ColorInterp.green,
                           ColorInterp.blue, ColorInterp.alpha]


def make_dem(path):
    rng = np.random.RandomState(2)
    y, x = np.mgrid[0:200, 0:300]
    data = (x * 0.1 + y * 0.05 + rng.rand(200, 300)).astype(np.float32)
    data[:30, :50] = -9999
    with rasterio.open(
            path, "w", driver="GTiff", width=300, height=200, count=1,
            dtype="float32", nodata=-9999, crs="EPSG:32617",
            transform=from_origin(500000, 4400000, 0.2, 0.2)) as dst:
        dst.write(data, 1)


def list_files(root):
    found = []
    for dirpath, _, filenames in os.walk(root):
        for f in filenames:
            found.append(os.path.relpath(os.path.join(dirpath, f), root))
    return sorted(found)


class TestTiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name

    def assert_same_output(self, geotiff):
        serial = os.path.join(self.root, "serial")
        parallel = os.path.join(self.root, "parallel")
        tiler.generate_tiles(geotiff, serial, 1, RESOLUTION)
        tiler.generate_tiles(geotiff, parallel, 4, RESOLUTION)

        files = list_files(serial)
        self.assertEqual(files, list_files(parallel))
        self.assertGreater(len(files), 8)
        self.assertGreater(len({f.split(os.sep)[0] for f in files}), 1)
        for f in files:
            with open(os.path.join(serial, f), "rb") as a, \
                    open(os.path.join(parallel, f), "rb") as b:
                self.assertEqual(a.read(), b.read(), f)

    def test_orthophoto_parallel_matches_serial(self):
        geotiff = os.path.join(self.root, "ortho.tif")
        make_orthophoto(geotiff)
        self.assert_same_output(geotiff)

    def test_dem_parallel_matches_serial(self):
        geotiff = os.path.join(self.root, "dem.tif")
        make_dem(geotiff)
        self.assert_same_output(geotiff)

    def test_memory_limited_output_matches_serial(self):
        geotiff = os.path.join(self.root, "ortho.tif")
        make_orthophoto(geotiff)
        with mock.patch("modules.concurrency.virtual_memory",
                        return_value=mock.Mock(available=32 * 1024 * 1024)), \
                mock.patch.object(tiler, "parallel_map", wraps=tiler.parallel_map) as parallel:
            self.assert_same_output(geotiff)
        self.assertEqual([call.kwargs["max_workers"] for call in parallel.call_args_list], [1, 2])

    def test_global_range_is_cached_and_seedable(self):
        geotiff = os.path.join(self.root, "dem.tif")
        make_dem(geotiff)
        with StaticTiler(geotiff, os.path.join(self.root, "a"), 256, tms=True) as t:
            value = t.global_range()
            self.assertLess(value[0], value[1])
            self.assertIs(t.global_range(), value)
        with StaticTiler(geotiff, os.path.join(self.root, "b"), 256, tms=True,
                         global_range=(1.0, 2.0)) as t:
            self.assertEqual(t.global_range(), (1.0, 2.0))

    def test_tile_error_propagates_without_hanging(self):
        geotiff = os.path.join(self.root, "ortho.tif")
        make_orthophoto(geotiff)
        original = StaticTiler.tile
        calls = []

        def failing(self, tz, tx, ty):
            calls.append((tz, tx, ty))
            if len(calls) == 5:
                raise RuntimeError("boom")
            return original(self, tz, tx, ty)

        result = []

        def run():
            try:
                tiler.generate_tiles(geotiff, os.path.join(self.root, "out"), 4, RESOLUTION)
            except Exception as e:
                result.append(e)

        with mock.patch.object(StaticTiler, "tile", failing):
            t = threading.Thread(target=run, daemon=True)
            t.start()
            t.join(60)

        self.assertFalse(t.is_alive(), "generate_tiles hung")
        self.assertEqual(len(result), 1)
        self.assertEqual(str(result[0]), "boom")


if __name__ == '__main__':
    unittest.main()
