import collections
import contextlib
import importlib.util
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

import numpy
import piexif
import pyproj
import rasterio
import rasterio.transform
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "check-georeferencing.py")

spec = importlib.util.spec_from_file_location("check_georeferencing", SCRIPT)
georef = importlib.util.module_from_spec(spec)
spec.loader.exec_module(georef)

Ratio = collections.namedtuple("Ratio", "num den")

# Two positions near 45.0 N, 15.0 E, inside UTM zone 33N
INSIDE = [(15.0, 45.0), (15.002, 45.001)]
# About 1.5 km east of the first one
FAR = (15.02, 45.0)
MARGIN = 50.0


def utm_bounds(positions, margin=MARGIN):
    to_utm = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:32633", always_xy=True)
    points = [to_utm.transform(lon, lat) for lon, lat in positions]
    xs, ys = zip(*points)
    return min(xs) - margin, min(ys) - margin, max(xs) + margin, max(ys) + margin


def degree_bounds(positions, margin=0.001):
    lons, lats = zip(*positions)
    return min(lons) - margin, min(lats) - margin, max(lons) + margin, max(lats) + margin


def write_orthophoto(project, bounds, crs="EPSG:32633"):
    folder = os.path.join(project, "odm_orthophoto")
    os.makedirs(folder, exist_ok=True)
    left, bottom, right, top = bounds
    with rasterio.open(
            os.path.join(folder, "odm_orthophoto.tif"), "w", driver="GTiff",
            width=10, height=10, count=1, dtype="uint8", crs=crs,
            transform=rasterio.transform.from_bounds(left, bottom, right, top, 10, 10)) as dst:
        dst.write(numpy.zeros((10, 10), dtype="uint8"), 1)


def dms(value):
    """Decimal degrees as EXIF rationals: degrees, minutes, seconds in 1/100."""
    degrees = int(value)
    minutes = int((value - degrees) * 60)
    seconds = round(((value - degrees) * 60 - minutes) * 60 * 100)
    return ((degrees, 1), (minutes, 1), (seconds, 100))


def write_image(project, name, position=None):
    folder = os.path.join(project, "images")
    os.makedirs(folder, exist_ok=True)
    image = Image.new("RGB", (8, 8))
    path = os.path.join(folder, name)
    if position is None:
        image.save(path, "jpeg")
        return
    lon, lat = position
    gps = {
        piexif.GPSIFD.GPSLatitudeRef: b"N" if lat >= 0 else b"S",
        piexif.GPSIFD.GPSLatitude: dms(abs(lat)),
        piexif.GPSIFD.GPSLongitudeRef: b"E" if lon >= 0 else b"W",
        piexif.GPSIFD.GPSLongitude: dms(abs(lon)),
    }
    image.save(path, "jpeg", exif=piexif.dump({"GPS": gps}))


class ProjectCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.project = tmp.name

    def make_project(self, positions, bounds=None, crs="EPSG:32633"):
        write_orthophoto(self.project, bounds or utm_bounds(INSIDE), crs)
        for i, position in enumerate(positions):
            write_image(self.project, "img%d.jpg" % i, position)


class TestToDegrees(unittest.TestCase):
    def test_north_east(self):
        values = [Ratio(45, 1), Ratio(30, 1), Ratio(0, 1)]
        self.assertAlmostEqual(georef.to_degrees(values, "N"), 45.5)
        self.assertAlmostEqual(georef.to_degrees(values, "E"), 45.5)

    def test_south_west(self):
        values = [Ratio(45, 1), Ratio(30, 1), Ratio(0, 1)]
        self.assertAlmostEqual(georef.to_degrees(values, "S"), -45.5)
        self.assertAlmostEqual(georef.to_degrees(values, "W"), -45.5)

    def test_seconds(self):
        values = [Ratio(10, 1), Ratio(0, 1), Ratio(3600, 100)]
        self.assertAlmostEqual(georef.to_degrees(values, "N"), 10.01)


class TestCheck(ProjectCase):
    def test_all_inside(self):
        self.make_project(INSIDE)
        problems, summary = georef.check(self.project)
        self.assertEqual(problems, [])
        self.assertIn("2 image positions", summary)

    def test_one_outside(self):
        self.make_project(INSIDE + [FAR])
        problems, _ = georef.check(self.project)
        self.assertEqual(len(problems), 1)
        self.assertIn("1 of 3", problems[0])
        self.assertIn("outside", problems[0])

    def test_geographic_crs(self):
        self.make_project(INSIDE, bounds=degree_bounds(INSIDE), crs="EPSG:4326")
        problems, _ = georef.check(self.project)
        self.assertEqual(len(problems), 1)
        self.assertIn("not projected", problems[0])

    def test_no_crs(self):
        self.make_project(INSIDE, crs=None)
        problems, _ = georef.check(self.project)
        self.assertEqual(len(problems), 1)
        self.assertIn("no coordinate system", problems[0])

    def test_image_without_gps(self):
        self.make_project(INSIDE + [None])
        problems, _ = georef.check(self.project)
        self.assertEqual(len(problems), 1)
        self.assertIn("no GPS position", problems[0])


class TestMain(ProjectCase):
    def run_main(self):
        out = io.StringIO()
        with mock.patch.object(sys, "argv", [SCRIPT, self.project]), \
                contextlib.redirect_stdout(out):
            return georef.main()

    def test_good_project_exits_0(self):
        self.make_project(INSIDE)
        self.assertEqual(self.run_main(), 0)

    def test_bad_project_exits_1(self):
        self.make_project(INSIDE + [FAR])
        self.assertEqual(self.run_main(), 1)

    def test_wrong_arguments_exit_2(self):
        with mock.patch.object(sys, "argv", [SCRIPT]), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(georef.main(), 2)


if __name__ == "__main__":
    unittest.main()
