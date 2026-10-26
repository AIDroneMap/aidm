import os
import random
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from unittest import mock

from modules.video.gpxparser import GpxFileParser
from modules.video.srtparser import SrtFileParser

ORIGIN = datetime(1900, 1, 1)
MICRO = timedelta(microseconds=1)


# Verbatim copies of the linear get_entry and get_gps that the indexed
# versions replaced. They are the reference the new code must match.

def reference_get_entry(self, timestamp: datetime):
    if not self.data:
        self.parse()

    # check min and max
    if timestamp < self.data[0]["start"] or timestamp > self.data[len(self.data) - 1]["end"]:
        return None

    for entry in self.data:
        if entry["start"] <= timestamp and entry["end"] >= timestamp:
            return entry

    return None


def reference_get_gps(self, timestamp):
    from modules import location

    if not self.data:
        self.parse()

    # Initialize on first call
    prev_coords = None

    if not self.gps_data:
        i = 0
        for d in self.data:
            lat, lon, alt = d.get('latitude'), d.get('longitude'), d.get('altitude')
            if alt is None:
                alt = 0
            tm = d.get('start')

            if lat is not None and lon is not None:
                if self.ll_to_utm is None:
                    self.ll_to_utm, self.utm_to_ll = location.utm_transformers_from_ll(lon, lat)

                coords = self.ll_to_utm.TransformPoint(lon, lat, alt)

                # First or new (in X/Y only) or last
                add = (not len(self.gps_data)) or (coords[0], coords[1]) != (self.gps_data[-1][1][0], self.gps_data[-1][1][1]) or i == len(self.data) - 1
                if add:
                    self.gps_data.append((tm, coords))
            i += 1

    # No data available
    if not len(self.gps_data) or self.gps_data[0][0] > timestamp:
        return None

    # Interpolate
    start = None
    for i in range(len(self.gps_data)):
        tm, coords = self.gps_data[i]

        # Perfect match
        if timestamp == tm:
            return self.utm_to_ll.TransformPoint(*coords)

        elif tm > timestamp:
            end = i
            start = i - 1
            if start < 0:
                return None

            gd_s = self.gps_data[start]
            gd_e = self.gps_data[end]
            sx, sy, sz = gd_s[1]
            ex, ey, ez = gd_e[1]

            dt = (gd_e[0] - gd_s[0]).total_seconds()
            if dt == 0:
                return None

            dx = (ex - sx) / dt
            dy = (ey - sy) / dt
            dz = (ez - sz) / dt
            t = (timestamp - gd_s[0]).total_seconds()

            return self.utm_to_ll.TransformPoint(
                sx + dx * t,
                sy + dy * t,
                sz + dz * t
            )


class ReferenceSrtParser(SrtFileParser):
    get_entry = reference_get_entry
    get_gps = reference_get_gps


class ReferenceGpxParser(GpxFileParser):
    get_gps = reference_get_gps


def ms_to_srt(ms):
    h, rest = divmod(ms, 3600000)
    m, rest = divmod(rest, 60000)
    s, milli = divmod(rest, 1000)
    return "%02d:%02d:%02d,%03d" % (h, m, s, milli)


def mavic_mini_block(n, start, end, lon, lat, alt):
    return "%d\n%s --> %s\nF/2.8, SS 206.14, ISO 150, EV 0, GPS (%.6f, %.6f, %d), D 2.80m, H 0.00m, H.S 0.00m/s, V.S 0.00m/s \n\n" % (
        n, ms_to_srt(start), ms_to_srt(end), lon, lat, alt)


def mavic_air2_block(n, start, end, lon, lat, alt):
    return ("%d\n%s --> %s\n<font size=\"36\">SrtCnt : %d, DiffTime : 16ms\n2023-01-06 18:56:48,380,821\n"
            "[iso : 3200] [shutter : 1/60.0] [fnum : 280] [ev : 0] [ct : 3925] [color_md : default] "
            "[focal_len : 240] [latitude: %.6f] [longitude: %.6f] [altitude: %.6f] </font>\n</font> \n\n") % (
        n, ms_to_srt(start), ms_to_srt(end), n, lat, lon, alt)


def no_gps_block(n, start, end):
    return "%d\n%s --> %s\nF/2.8, SS 206.14, ISO 150, EV 0\n\n" % (n, ms_to_srt(start), ms_to_srt(end))


def contiguous_rows(count=300, step=100):
    """The end of one row is the start of the next, as in real DJI files.
    Every fourth block repeats the coordinates of the one before it."""
    rows = []
    for k in range(count):
        moved = k - (k % 4 == 3)
        rows.append((k * step, (k + 1) * step, 11.0 + 0.00005 * moved, 45.0 + 0.00003 * moved, 100 + moved % 7))
    return rows


def gap_rows(count=300, step=100):
    """Rows with gaps between them and some repeated coordinates"""
    rows = []
    t = 500
    for k in range(count):
        if k % 10 == 9:
            t += 700
        moved = k - (k % 5 in (2, 3))
        rows.append((t, t + step - 20, 11.2 + 0.00004 * moved, 45.1 - 0.00002 * moved, 90 + moved % 5))
        t += step
    return rows


def overlapping_unsorted_rows(count=300, step=100):
    """Overlapping rows with a stretch in the middle shuffled out of order"""
    rows = [(k * step, k * step + 3 * step, 11.4 + 0.00005 * k, 45.2 + 0.00003 * k, 80 + k % 9) for k in range(count)]
    middle = rows[100:200]
    random.Random(7).shuffle(middle)
    rows[100:200] = middle
    return rows


def write_srt(directory, name, rows, block):
    path = os.path.join(directory, name)
    with open(path, "w") as f:
        for n, row in enumerate(rows, start=1):
            f.write(block(n, *row))
    return path


def at(ms):
    return ORIGIN + timedelta(milliseconds=ms)


def probe_times(parser):
    """Timestamps around every boundary, the middle of every row and gap, and outside the data"""
    times = set()
    data = parser.data
    for i, e in enumerate(data):
        for t in (e["start"], e["end"]):
            times.update((t, t - MICRO, t + MICRO))
        times.add(e["start"] + (e["end"] - e["start"]) / 2)
        if i + 1 < len(data):
            nxt = data[i + 1]["start"]
            times.add(e["end"] + (nxt - e["end"]) / 2)
    for tm, _ in parser.gps_data:
        times.update((tm, tm - MICRO, tm + MICRO))
    first, last = data[0]["start"], data[-1]["end"]
    for delta in (timedelta(seconds=1), timedelta(hours=1)):
        times.update((first - delta, last + delta))
    times.update((min(e["start"] for e in data), max(e["end"] for e in data)))
    return sorted(times)


def position(parser, entry):
    if entry is None:
        return None
    for i, e in enumerate(parser.data):
        if e is entry:
            return i
    raise AssertionError("entry is not part of the parser data")


class TestSrtParser(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def parsers(self, rows, block):
        path = write_srt(self.tmp.name, "video_%d.srt" % id(rows), rows, block)
        new, ref = SrtFileParser(path), ReferenceSrtParser(path)
        new.parse()
        ref.parse()
        self.assertEqual(new.data, ref.data)
        return new, ref

    def assert_same_results(self, new, ref):
        probes = probe_times(ref)
        self.assertGreater(len(probes), 500)
        found = 0
        for t in probes:
            expected = ref.get_entry(t)
            self.assertEqual(position(new, new.get_entry(t)), position(ref, expected), t)
            found += expected is not None
        self.assertGreater(found, 0)

        located = 0
        for t in probes:
            expected = ref.get_gps(t)
            self.assertEqual(new.get_gps(t), expected, t)
            located += expected is not None
        self.assertGreater(located, 0)
        self.assertEqual(new.gps_data, ref.gps_data)

    def test_contiguous_rows_mavic_mini(self):
        new, ref = self.parsers(contiguous_rows(), mavic_mini_block)
        self.assert_same_results(new, ref)
        self.assertTrue(new._entry_index()[2])
        self.assertIsNotNone(new._gps_times())

    def test_contiguous_rows_mavic_air2(self):
        new, ref = self.parsers(contiguous_rows(), mavic_air2_block)
        self.assert_same_results(new, ref)
        self.assertTrue(new._entry_index()[2])

    def test_rows_with_gaps_mavic_mini(self):
        new, ref = self.parsers(gap_rows(), mavic_mini_block)
        self.assert_same_results(new, ref)
        self.assertTrue(new._entry_index()[2])
        self.assertIsNotNone(new._gps_times())
        # A timestamp inside a gap has no entry
        self.assertIsNone(new.get_entry(at(500 + 9 * 100 + 90 + 400)))

    def test_rows_with_gaps_mavic_air2(self):
        new, ref = self.parsers(gap_rows(), mavic_air2_block)
        self.assert_same_results(new, ref)

    def test_repeated_coordinates_are_merged(self):
        new, ref = self.parsers(contiguous_rows(), mavic_mini_block)
        new.get_gps(at(1000))
        ref.get_gps(at(1000))
        self.assertLess(len(new.gps_data), len(new.data))
        self.assertEqual(new.gps_data, ref.gps_data)

    def test_exact_boundaries(self):
        new, ref = self.parsers(contiguous_rows(), mavic_mini_block)
        # Where one row ends and the next starts, the first one in file order wins
        self.assertEqual(position(new, new.get_entry(at(1000))), 9)
        self.assertEqual(position(new, new.get_entry(at(1000) + MICRO)), 10)
        self.assertIsNone(new.get_entry(at(0) - MICRO))
        self.assertIsNone(new.get_entry(at(300 * 100) + MICRO))
        self.assertEqual(position(new, new.get_entry(at(300 * 100))), 299)

    def test_unsorted_overlapping_rows_use_linear_scan(self):
        for block in (mavic_mini_block, mavic_air2_block):
            new, ref = self.parsers(overlapping_unsorted_rows(), block)
            self.assert_same_results(new, ref)
            self.assertFalse(new._entry_index()[2])
            self.assertIsNone(new._gps_times())

    def test_overlapping_rows_in_order(self):
        rows = [(k * 100, k * 100 + 350, 11.4 + 0.00005 * k, 45.2 + 0.00003 * k, 80) for k in range(300)]
        new, ref = self.parsers(rows, mavic_mini_block)
        self.assert_same_results(new, ref)
        self.assertTrue(new._entry_index()[2])

    def test_no_gps(self):
        rows = [(k * 100, (k + 1) * 100) for k in range(300)]
        new, ref = self.parsers(rows, no_gps_block)
        with mock.patch.object(new, "_build_gps_data", wraps=new._build_gps_data) as build:
            for k in range(0, 30000, 100):
                self.assertIsNone(new.get_gps(at(k)))
            self.assertEqual(build.call_count, 1)
        self.assertEqual(new.gps_data, [])
        self.assertEqual(position(new, new.get_entry(at(5050))), position(ref, ref.get_entry(at(5050))))

    def test_data_changes_after_first_lookup(self):
        new, ref = self.parsers(contiguous_rows(count=50), mavic_mini_block)
        end = at(5000)
        self.assertIsNone(new.get_entry(end + timedelta(seconds=2)))
        extra = dict(new.data[-1], start=end, end=end + timedelta(seconds=5))
        new.data.append(dict(extra))
        ref.data.append(dict(extra))
        for t in (end, end + timedelta(seconds=2), end + timedelta(seconds=5), end + timedelta(seconds=6)):
            self.assertEqual(position(new, new.get_entry(t)), position(ref, ref.get_entry(t)))
        self.assertEqual(position(new, new.get_entry(end + timedelta(seconds=2))), 50)

    def test_many_lookups_on_a_long_file(self):
        count = 20000
        parser = SrtFileParser("unused.srt")
        parser.data = [{
            "start": at(k * 33),
            "end": at((k + 1) * 33),
            "latitude": 45.0 + 0.000001 * k,
            "longitude": 11.0 + 0.000001 * k,
            "altitude": 100.0 + (k % 5),
        } for k in range(count)]

        begin = time.monotonic()
        for k in range(count):
            t = at(k * 33 + 16)
            self.assertIs(parser.get_entry(t), parser.data[k])
            # No GPS time after the last row start, so the last lookup has no answer
            if 0 < k < count - 1:
                self.assertIsNotNone(parser.get_gps(t))
        elapsed = time.monotonic() - begin
        # The linear scan needs minutes for this. The bound is loose on purpose.
        self.assertLess(elapsed, 20)


GPX_HEADER = '<?xml version="1.0" encoding="UTF-8"?>\n<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>\n'
GPX_FOOTER = '</trkseg></trk></gpx>\n'


def write_gpx(directory, name, points):
    path = os.path.join(directory, name)
    with open(path, "w") as f:
        f.write(GPX_HEADER)
        for tm, lat, lon, ele in points:
            f.write('<trkpt lat="%.6f" lon="%.6f"><ele>%.1f</ele><time>%s</time></trkpt>\n' % (
                lat, lon, ele, tm.strftime("%Y-%m-%dT%H:%M:%SZ")))
        f.write(GPX_FOOTER)
    return path


class TestGpxParser(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def check(self, points):
        start = points[0][0]
        path = write_gpx(self.tmp.name, "track_%d.gpx" % id(points), points)
        new = GpxFileParser(path, video_start=start)
        ref = ReferenceGpxParser(path, video_start=start)
        new.parse()
        ref.parse()
        self.assertEqual(new.data, ref.data)

        probes = probe_times(ref)
        located = 0
        for t in probes:
            expected = ref.get_gps(t)
            self.assertEqual(new.get_gps(t), expected, t)
            located += expected is not None
        self.assertGreater(located, 50)
        self.assertEqual(new.gps_data, ref.gps_data)
        self.assertIsNotNone(new._gps_times())
        self.assertIsNone(new.get_entry(probes[len(probes) // 2]))

    def test_one_point_per_second_with_a_gap_and_stops(self):
        base = datetime(2026, 7, 7, 17, 0, 0)
        points = []
        t = 0
        for k in range(120):
            if k == 60:
                t += 15
            # Standing still between points 30 and 40 repeats the coordinates
            moved = min(k, 30) if k < 40 else k - 10
            points.append((base + timedelta(seconds=t), 45.0 + 0.00002 * moved, 11.0 + 0.00003 * moved, 120.0 + moved % 4))
            t += 1
        self.check(points)

    def test_two_points_per_second_share_a_timestamp(self):
        base = datetime(2026, 7, 7, 17, 0, 0)
        points = []
        for k in range(160):
            points.append((base + timedelta(seconds=k // 2), 45.0 + 0.00001 * k, 11.0 + 0.00002 * k, 110.0 + k % 3))
        self.check(points)


if __name__ == "__main__":
    unittest.main()
