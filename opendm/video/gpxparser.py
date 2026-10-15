from datetime import datetime, timedelta, timezone
from statistics import median
import struct
import xml.etree.ElementTree as ET
from opendm import log
from opendm.video.srtparser import SrtFileParser

ELAPSED_ORIGIN = datetime(1900, 1, 1)

def parse_gpx_time(s):
    """Parse an ISO 8601 timestamp (e.g. 2026-07-07T17:32:51Z) into a naive UTC datetime"""
    s = s.strip()
    if s.endswith("Z") or s.endswith("z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt

def get_video_start_time(video_file):
    """Read the creation time (UTC) (should work with .mp4, .mov, .lrv)
    Returns a naive UTC datetime or None"""
    try:
        with open(video_file, 'rb') as f:
            f.seek(0, 2)
            file_size = f.tell()

            def find_atom(name, start, end):
                pos = start
                while pos + 8 <= end:
                    f.seek(pos)
                    size, atype = struct.unpack(">I4s", f.read(8))
                    header = 8
                    if size == 1:
                        size = struct.unpack(">Q", f.read(8))[0]
                        header = 16
                    elif size == 0:
                        size = end - pos
                    if size < header:
                        return None
                    if atype == name:
                        return pos + header, pos + size
                    pos += size
                return None

            moov = find_atom(b'moov', 0, file_size)
            if moov is None:
                return None
            mvhd = find_atom(b'mvhd', *moov)
            if mvhd is None:
                return None

            f.seek(mvhd[0])
            version = f.read(1)[0]
            f.read(3) # flags
            if version == 1:
                creation_time = struct.unpack(">Q", f.read(8))[0]
            else:
                creation_time = struct.unpack(">I", f.read(4))[0]

            if creation_time == 0:
                return None
            return datetime(1904, 1, 1) + timedelta(seconds=creation_time)
    except Exception as e:
        log.WARNING("Cannot read creation time from %s: %s" % (video_file, str(e)))
        return None

class GpxFileParser(SrtFileParser):
    def __init__(self, filename, video_start=None):
        super().__init__(filename)
        self.video_start = video_start

    def get_entry(self, timestamp: datetime):
        # GPX files do not carry camera settings
        return None

    def parse(self):
        points = []

        for _, el in ET.iterparse(self.filename):
            # Strip namespace
            tag = el.tag.rsplit('}', 1)[-1]
            if tag not in ("trkpt", "rtept"):
                continue

            lat, lon = el.get('lat'), el.get('lon')
            ele = tm = None
            for child in el:
                ctag = child.tag.rsplit('}', 1)[-1]
                if ctag == "ele" and child.text:
                    ele = float(child.text)
                elif ctag == "time" and child.text:
                    tm = parse_gpx_time(child.text)

            if lat is not None and lon is not None and tm is not None:
                points.append((tm, float(lat), float(lon), ele))
            el.clear()

        if not points:
            raise Exception("No timestamped track points found in %s" % self.filename)

        points.sort(key=lambda p: p[0])
        times = self.spread_timestamps([p[0] for p in points])

        if self.video_start is None:
            self.video_start = times[0]
            log.WARNING("Video start time unknown, assuming it matches the first GPX point (%s)" % self.video_start)

        log.INFO("GPX track spans %s --> %s (%s points), video starts at %s" % (times[0], times[-1], len(points), self.video_start))

        for tm, (_, lat, lon, ele) in zip(times, points):
            # Express track time as elapsed video time
            elapsed = ELAPSED_ORIGIN + (tm - self.video_start)
            self.data.append({
                "start": elapsed,
                "end": elapsed,
                "latitude": lat,
                "longitude": lon,
                "altitude": ele
            })

    def spread_timestamps(self, times):
        # Some devices (e.g. Insta360) log at > 1Hz, but write timestamps
        # with 1 second resolution, so several points share the same time.
        # We spread these evenly within their second, assuming a constant rate.
        groups = []
        for tm in times:
            if groups and groups[-1][0] == tm:
                groups[-1][1] += 1
            else:
                groups.append([tm, 1])

        rate = median([n for _, n in groups])
        if rate <= 1:
            return times

        result = []
        for i, (tm, n) in enumerate(groups):
            step = 1.0 / max(n, rate)
            if i == 0:
                # Recording started mid-second: points belong to the end of it
                offset = 1.0 - n * step
            else:
                offset = 0.0
            for k in range(n):
                result.append(tm + timedelta(seconds=offset + k * step))
        return result
