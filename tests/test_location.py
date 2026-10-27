import math
import os
import tempfile
import unittest
from types import SimpleNamespace

from pyproj import CRS

from modules import location
from modules.geo import GeoFile


def photo(filename, lat, lon, alt):
    return SimpleNamespace(filename=filename, latitude=lat, longitude=lon, altitude=alt)


def reference_extract_utm_coords(photos, output_coords_file):
    """Copy of extract_utm_coords before the Proj object was built once."""
    utm_zone = None
    hemisphere = None
    coords = []
    for p in photos:
        if p.latitude is None or p.longitude is None:
            continue

        if utm_zone is None:
            utm_zone, hemisphere = location.get_utm_zone_and_hemisphere_from(p.longitude, p.latitude)

        alt = p.altitude if p.altitude is not None else 0
        coords.append(location.convert_to_utm(p.longitude, p.latitude, alt, utm_zone, hemisphere))

    dx = 0.0
    dy = 0.0
    num = float(len(coords))
    for coord in coords:
        dx += coord[0] / num
        dy += coord[1] / num

    dx = int(math.floor(dx))
    dy = int(math.floor(dy))

    with open(output_coords_file, "w") as f:
        f.write("WGS84 UTM %s%s\n" % (utm_zone, hemisphere))
        f.write("%s %s\n" % (dx, dy))
        for coord in coords:
            f.write("%s %s %s\n" % (coord[0] - dx, coord[1] - dy, coord[2]))


class TestExtractUtmCoords(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def compare(self, photos):
        new = os.path.join(self.tmp.name, "new.txt")
        old = os.path.join(self.tmp.name, "old.txt")
        location.extract_utm_coords(photos, self.tmp.name, new)
        reference_extract_utm_coords(photos, old)
        with open(new) as f:
            new_lines = f.read().split("\n")
        with open(old) as f:
            old_lines = f.read().split("\n")
        self.assertEqual(new_lines, old_lines)
        return new_lines

    def test_northern_hemisphere(self):
        photos = [
            photo("a.jpg", 44.0581, -121.3155, 2019.8),
            photo("b.jpg", 44.0590, -121.3140, 2020.1),
            photo("nogps.jpg", None, None, None),
            photo("noalt.jpg", 44.0600, -121.3120, None),
            photo("c.jpg", 44.0610, -121.3100, 2021.5),
        ]
        lines = self.compare(photos)
        self.assertEqual(lines[0], "WGS84 UTM 10N")
        self.assertEqual(len(lines), 2 + 4 + 1)

    def test_missing_altitude_is_zero(self):
        lines = self.compare([photo("a.jpg", 44.0, -121.0, None)])
        self.assertTrue(lines[2].endswith(" 0"))

    def test_southern_hemisphere(self):
        photos = [
            photo("a.jpg", -33.8688, 151.2093, 58.0),
            photo("b.jpg", -33.8700, 151.2100, 59.5),
            photo("c.jpg", -33.8710, 151.2110, None),
        ]
        lines = self.compare(photos)
        self.assertEqual(lines[0], "WGS84 UTM 56S")

    def test_first_photo_without_gps(self):
        photos = [
            photo("nogps.jpg", None, 10.0, 1.0),
            photo("a.jpg", 50.0, 10.0, 1.0),
            photo("b.jpg", 50.001, 10.001, 2.0),
        ]
        lines = self.compare(photos)
        self.assertEqual(lines[0], "WGS84 UTM 32N")

    def test_zone_fixed_by_first_photo(self):
        # The second photo is across a zone border but still uses zone 32
        photos = [
            photo("a.jpg", 50.0, 11.99, 1.0),
            photo("b.jpg", 50.0, 12.01, 1.0),
        ]
        lines = self.compare(photos)
        self.assertEqual(lines[0], "WGS84 UTM 32N")

    def test_no_photos(self):
        with self.assertRaises(Exception):
            location.extract_utm_coords([], self.tmp.name, os.path.join(self.tmp.name, "o.txt"))

    def test_no_gps_at_all(self):
        with self.assertRaises(Exception):
            location.extract_utm_coords([photo("a.jpg", None, None, None)],
                                        self.tmp.name, os.path.join(self.tmp.name, "o.txt"))

    def test_utm_proj_matches_convert_to_utm(self):
        for zone, hemisphere, lon, lat in [(17, 'N', -81.0, 28.0), (56, 'S', 151.2, -33.9)]:
            x, y = location.utm_proj(zone, hemisphere)(lon, lat)
            self.assertEqual(location.convert_to_utm(lon, lat, 5.0, zone, hemisphere), [x, y, 5.0])


class TestGeoFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def load(self, text):
        path = os.path.join(self.tmp.name, "geo.txt")
        with open(path, "w") as f:
            f.write(text)
        return GeoFile(path)

    def check(self, header, rows):
        """rows: list of (filename, [numbers after filename], extras string)"""
        text = header + "\n" + "\n".join(
            " ".join([name] + [repr(v) for v in values] + ([extras] if extras else []))
            for name, values, extras in rows) + "\n"
        geo = self.load(text)
        srs = location.parse_srs_header(header)
        longlat = CRS.from_epsg("4326")

        self.assertEqual(len(geo.entries), len(rows))
        for name, values, extras in rows:
            e = geo.get_entry(name)
            if len(values) >= 3:
                x, y, z = location.transform3(srs, longlat, values[0], values[1], values[2])
                self.assertEqual((e.x, e.y, e.z), (x, y, z))
            else:
                x, y = location.transform2(srs, longlat, values[0], values[1])
                self.assertEqual((e.x, e.y), (x, y))
                self.assertIsNone(e.z)
        return geo

    def test_epsg_header_with_z(self):
        geo = self.check("EPSG:32617", [
            ("a.jpg", [500000.0, 3100000.0, 12.5], None),
            ("b.jpg", [500100.5, 3100200.25, 13.0], None),
        ])
        self.assertEqual(geo.raw_srs, "EPSG:32617")

    def test_epsg_header_without_z(self):
        self.check("EPSG:32617", [
            ("a.jpg", [500000.0, 3100000.0], None),
        ])

    def test_mixed_lines_with_and_without_z(self):
        self.check("EPSG:32617", [
            ("a.jpg", [500000.0, 3100000.0, 12.5], None),
            ("b.jpg", [500100.0, 3100100.0], None),
            ("c.jpg", [500200.0, 3100200.0, 14.0], None),
        ])

    def test_wgs84_utm_header(self):
        self.check("WGS84 UTM 17N", [
            ("a.jpg", [500000.0, 3100000.0, 12.5], None),
            ("b.jpg", [500100.0, 3100100.0], None),
        ])

    def test_southern_hemisphere(self):
        self.check("WGS84 UTM 56S", [
            ("a.jpg", [334000.0, 6252000.0, 58.0], None),
            ("b.jpg", [334100.0, 6252100.0], None),
        ])

    def test_orientation_and_accuracy_columns(self):
        geo = self.check("EPSG:32617", [
            ("a.jpg", [500000.0, 3100000.0, 12.5, 10.0, -90.0, 0.5, 0.02, 0.04], "extra1 extra2"),
            ("b.jpg", [500100.0, 3100100.0, 13.0, 20.0, -85.0, 1.5], None),
        ])
        a = geo.get_entry("a.jpg")
        self.assertEqual((a.yaw, a.pitch, a.roll), (10.0, -90.0, 0.5))
        self.assertEqual((a.horizontal_accuracy, a.vertical_accuracy), (0.02, 0.04))
        self.assertEqual(a.extras, "extra1 extra2")
        b = geo.get_entry("b.jpg")
        self.assertEqual((b.yaw, b.pitch, b.roll), (20.0, -85.0, 1.5))
        self.assertIsNone(b.horizontal_accuracy)

    def test_nan_orientation_is_dropped(self):
        geo = self.load("EPSG:32617\na.jpg 500000 3100000 12.5 nan nan nan\n")
        e = geo.get_entry("a.jpg")
        self.assertIsNone(e.yaw)
        self.assertIsNone(e.pitch)
        self.assertIsNone(e.roll)

    def test_comments_blank_and_malformed_lines(self):
        geo = self.load("EPSG:32617\n# comment\n\na.jpg 500000 3100000\nbad.jpg 1\n")
        self.assertEqual(list(geo.entries), ["a.jpg"])

    def test_header_only(self):
        geo = self.load("EPSG:32617\n")
        self.assertEqual(geo.entries, {})


if __name__ == '__main__':
    unittest.main()
