import os
import shutil
import tempfile
import threading
import unittest
from collections import Counter
from unittest import mock

import numpy as np
import rasterio
from rasterio.enums import ColorInterp
from rasterio.transform import from_origin

from modules import orthophoto

SIZE = 64
BLOCK = 16
BLOCKS = (SIZE // BLOCK) ** 2

real_open = rasterio.open


class ReadCounter:
    """Thin proxy around a read-mode dataset. Records every read() window."""

    def __init__(self, dataset, path, reads, lock):
        self._dataset = dataset
        self._path = path
        self._reads = reads
        self._lock = lock

    def read(self, *args, **kwargs):
        with self._lock:
            self._reads.append((self._path, kwargs.get("window")))
        return self._dataset.read(*args, **kwargs)

    def __enter__(self):
        self._dataset.__enter__()
        return self

    def __exit__(self, *exc):
        return self._dataset.__exit__(*exc)

    def __getattr__(self, name):
        return getattr(self._dataset, name)


def write_raster(path, rgb, alpha):
    """Write an RGBA GeoTIFF with one RGB color wherever alpha is nonzero."""
    height, width = alpha.shape
    data = np.zeros((4, height, width), dtype=np.uint8)
    for b in range(3):
        data[b][alpha != 0] = rgb[b]
    data[3] = alpha
    with real_open(
        path,
        "w",
        driver="GTiff",
        width=width,
        height=height,
        count=4,
        dtype="uint8",
        crs="EPSG:32615",
        transform=from_origin(500000, 4000000, 1, 1),
    ) as dst:
        dst.write(data)
        dst.colorinterp = [
            ColorInterp.red,
            ColorInterp.green,
            ColorInterp.blue,
            ColorInterp.alpha,
        ]


class TestOrthophotoMerge(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.reads = []
        self.lock = threading.Lock()

    def path(self, name):
        return os.path.join(self.tmp, name)

    def make_input(self, name, rgb, alpha):
        ortho = self.path(name + ".tif")
        cut = self.path(name + "_cut.tif")
        write_raster(ortho, rgb, alpha)
        write_raster(cut, (0, 0, 0), np.zeros_like(alpha))
        return ortho, cut

    def fake_open(self, path, mode="r", *args, **kwargs):
        dataset = real_open(path, mode, *args, **kwargs)
        if mode == "r":
            return ReadCounter(dataset, os.fspath(path), self.reads, self.lock)
        return dataset

    def run_merge(self, inputs, max_workers=1):
        out = self.path("merged.tif")
        with mock.patch("modules.orthophoto.rasterio.open", side_effect=self.fake_open):
            result = orthophoto.merge(
                inputs, out, orthophoto_vars={"BLOCKXSIZE": BLOCK, "BLOCKYSIZE": BLOCK},
                max_workers=max_workers,
            )
        self.assertEqual(result, out)
        with real_open(out) as src:
            return src.read()

    def reads_of(self, path):
        return [w for p, w in self.reads if p == path]

    def test_feather_blends_later_source_when_coverage_equals_block_width(self):
        for height, width in ((SIZE, SIZE), (19, 21), (1, 21)):
            for workers in (1, 2):
                with self.subTest(height=height, width=width, workers=workers):
                    # Each block has exactly one row of data. Coverage equals
                    # its width, including partial tiles at the output edges.
                    alpha = np.zeros((height, width), dtype=np.uint8)
                    alpha[::BLOCK] = 255
                    first = self.make_input("first", (200, 100, 50), alpha)
                    feather = np.zeros_like(alpha)
                    feather[::BLOCK] = 128
                    second = self.make_input("second", (40, 80, 120), feather)

                    out = self.run_merge([first, second], max_workers=workers)

                    expected = np.zeros_like(out)
                    # Source 1 supplies the base. Source 2 must still blend
                    # after the destination alpha has become opaque.
                    for band, value in enumerate((119, 89, 85)):
                        expected[band][alpha != 0] = value
                    expected[3] = alpha
                    np.testing.assert_array_equal(out, expected)

    def test_cutline_blends_after_all_feather_sources(self):
        opaque = np.full((SIZE, SIZE), 255, dtype=np.uint8)
        half = np.full((SIZE, SIZE), 128, dtype=np.uint8)
        first = self.make_input("first", (200, 100, 50), opaque)
        second = self.make_input("second", (40, 80, 120), half)
        third = self.make_input("third", (80, 160, 20), half)
        write_raster(third[1], (220, 60, 100), half)

        out = self.run_merge([first, second, third])

        # Feather results are (119, 89, 85), then (99, 124, 52).
        # The cutline blends over that result last.
        for band, value in enumerate((159, 91, 76)):
            np.testing.assert_array_equal(out[band], np.full_like(out[band], value))
        np.testing.assert_array_equal(out[3], opaque)

    def test_second_source_is_not_read_in_first_pass_when_block_is_full(self):
        opaque = np.full((SIZE, SIZE), 255, dtype=np.uint8)
        o1, c1 = self.make_input("o1", (200, 100, 50), opaque)
        o2, c2 = self.make_input("o2", (40, 80, 120), opaque)

        out = self.run_merge([(o1, c1), (o2, c2)])

        # The first source fills every block in the first pass, so the second
        # source is read only by the second pass: once per block.
        windows = self.reads_of(o2)
        self.assertEqual(len(windows), BLOCKS)
        self.assertEqual(set(Counter(windows).values()), {1})
        # The first source is read in the first and in the second pass.
        self.assertEqual(len(self.reads_of(o1)), 2 * BLOCKS)

        # The second pass blends source 2 at alpha 255 over source 1.
        for band, value in enumerate((40, 80, 120)):
            self.assertTrue(np.all(out[band] == value), "band %d" % band)
        self.assertTrue(np.all(out[3] == 255))

    def test_second_source_is_read_in_first_pass_when_block_is_not_full(self):
        left_only = np.zeros((SIZE, SIZE), dtype=np.uint8)
        left_only[:, : SIZE // 2] = 255
        half = np.full((SIZE, SIZE), 128, dtype=np.uint8)
        c1_rgb = np.array((200, 100, 50))
        c2_rgb = np.array((40, 80, 120))
        o1, c1 = self.make_input("o1", c1_rgb, left_only)
        o2, c2 = self.make_input("o2", c2_rgb, half)

        out = self.run_merge([(o1, c1), (o2, c2)])

        # Blocks in the left half are full after source 1, so source 2 is read
        # once there (second pass). Blocks in the right half are not, so it is
        # read twice (first and second pass).
        per_window = Counter(self.reads_of(o2))
        self.assertEqual(len(per_window), BLOCKS)
        self.assertEqual(sorted(per_window.values()), [1] * (BLOCKS // 2) + [2] * (BLOCKS // 2))
        for (rows, cols), count in per_window.items():
            self.assertEqual(count, 1 if cols[1] <= SIZE // 2 else 2, (rows, cols))

        # Expected pixels, following the passes in the code:
        # Pass 1: left = source 1 color, right = source 2 color (alpha 128).
        # Pass 2: source 1 only touches the left. Source 2 (alpha 128) is then
        #         blended over the current value everywhere, and alpha is set
        #         to 255.
        # Pass 3: the cut rasters have alpha 0, so nothing changes.
        a = 128 / 255.0
        for band in range(3):
            left = np.uint8(a * c2_rgb[band] + (1 - a) * c1_rgb[band])
            right = np.uint8(a * c2_rgb[band] + (1 - a) * c2_rgb[band])
            self.assertTrue(np.all(out[band][:, : SIZE // 2] == left), "left band %d" % band)
            self.assertTrue(np.all(out[band][:, SIZE // 2 :] == right), "right band %d" % band)
        self.assertTrue(np.all(out[3] == 255))


if __name__ == "__main__":
    unittest.main()
