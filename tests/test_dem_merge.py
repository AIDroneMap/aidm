import os
import shutil
import tempfile
import threading
import unittest
from unittest import mock

import numpy as np
import rasterio
from rasterio.transform import from_origin

from modules.dem import merge
from modules.dem.merge import euclidean_merge_dems

NODATA = -9999
CREATION = {'TILED': 'YES', 'BLOCKXSIZE': 16, 'BLOCKYSIZE': 16, 'COMPRESS': 'LZW'}


def write_dem(path, data, x0, y0):
    with rasterio.open(path, 'w', driver='GTiff', width=data.shape[1],
                       height=data.shape[0], count=1, dtype='float32',
                       crs='EPSG:32617', transform=from_origin(x0, y0, 1.0, 1.0),
                       nodata=NODATA) as dst:
        dst.write(data, 1)


def make_dem(rng, shape, base):
    yy, xx = np.mgrid[0:shape[0], 0:shape[1]]
    data = base + 0.05 * xx + 0.03 * yy + rng.random(shape) * 0.5
    # Smooth patch, like interpolated data.
    data[60:90, 20:50] = base + 5.0
    # Nodata hole.
    data[30:45, 70:90] = NODATA
    # Nodata border on one side.
    data[:, :4] = NODATA
    return data.astype('float32')


class TestEuclideanMergeDems(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        rng = np.random.default_rng(42)
        cls.a_data = make_dem(rng, (120, 150), 100.0)
        cls.b_data = make_dem(rng, (120, 150), 103.0)
        cls.src_dir = os.path.join(cls.tmp.name, 'src')
        os.makedirs(cls.src_dir)
        # B is offset by 50 px in x and 30 px in y, so the two overlap.
        write_dem(os.path.join(cls.src_dir, 'a.tif'), cls.a_data, 500000, 4000000)
        write_dem(os.path.join(cls.src_dir, 'b.tif'), cls.b_data, 500050, 3999970)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_merge(self, name, max_workers):
        # Euclidean maps are written next to the inputs, so each run
        # gets its own copy of the inputs.
        work = os.path.join(self.tmp.name, name)
        shutil.copytree(self.src_dir, work)
        out = os.path.join(work, 'merged.tif')
        dems = [os.path.join(work, 'a.tif'), os.path.join(work, 'b.tif')]
        result = euclidean_merge_dems(dems, out, dict(CREATION), max_workers=max_workers)
        self.assertEqual(result, out)
        with open(out, 'rb') as f:
            return out, f.read()

    def test_parallel_output_is_byte_identical_to_serial(self):
        _, serial = self.run_merge('serial', 1)
        _, parallel = self.run_merge('parallel', 4)
        self.assertEqual(serial, parallel)

    def test_parallel_output_is_repeatable(self):
        _, first = self.run_merge('repeat1', 4)
        _, second = self.run_merge('repeat2', 4)
        self.assertEqual(first, second)

    def test_pixels_covered_by_one_dem(self):
        out, _ = self.run_merge('pixels', 4)
        with rasterio.open(out) as dst:
            self.assertEqual((dst.height, dst.width), (150, 200))
            self.assertEqual(dst.nodata, NODATA)
            merged = dst.read(1)
        # Weighting divides by the same distance it multiplied by, so the
        # float32 result can differ from the source in the last bit.
        # Output rows 0-29 and columns 5-49 are covered only by A.
        np.testing.assert_allclose(merged[5:25, 10:45], self.a_data[5:25, 10:45], rtol=1e-6)
        # Output rows 130-149 and columns 160-199 are covered only by B.
        np.testing.assert_allclose(merged[135:149, 165:199], self.b_data[105:119, 115:149], rtol=1e-6)

    def test_error_in_a_block_is_raised_without_hanging(self):
        calls = [0]
        lock = threading.Lock()
        real = merge.ndimage.distance_transform_edt

        def failing_edt(*args, **kwargs):
            with lock:
                calls[0] += 1
                n = calls[0]
            if n == 7:
                raise RuntimeError("boom")
            return real(*args, **kwargs)

        work = os.path.join(self.tmp.name, 'error')
        shutil.copytree(self.src_dir, work)
        dems = [os.path.join(work, 'a.tif'), os.path.join(work, 'b.tif')]
        out = os.path.join(work, 'merged.tif')
        outcome = {}

        def run():
            try:
                euclidean_merge_dems(dems, out, dict(CREATION), max_workers=4)
            except Exception as e:
                outcome['error'] = e

        with mock.patch.object(merge.ndimage, 'distance_transform_edt', failing_edt):
            t = threading.Thread(target=run, daemon=True)
            t.start()
            t.join(60)
            self.assertFalse(t.is_alive(), "euclidean_merge_dems hung")

        self.assertIsInstance(outcome.get('error'), RuntimeError)
        self.assertEqual(str(outcome['error']), "boom")


if __name__ == '__main__':
    unittest.main()
