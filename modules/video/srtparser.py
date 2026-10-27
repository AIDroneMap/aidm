from bisect import bisect_left
from datetime import datetime
from modules import location, log
import re


def match_single(regexes, line, dtype=int):
    if isinstance(regexes, str):
        regexes = [(regexes, dtype)]
    
    for i in range(len(regexes)):
        if isinstance(regexes[i], str):
            regexes[i] = (regexes[i], dtype)
    
    try:
        for r, transform in regexes:
            match = re.search(r, line)
            if match:
                res = match.group(1)
                return transform(res)
    except Exception as e:
        log.WARNING("Cannot parse SRT line \"%s\": %s" % (line, str(e)))

    return None

class SrtFileParser:
    def __init__(self, filename):
        self.filename = filename
        self.data = []
        self.gps_data = []
        self.ll_to_utm = None
        self.utm_to_ll = None
        self._entry_index_len = None
        self._gps_built_len = None
        self._gps_times_len = None

    def _entry_index(self):
        """Lists of start and end times, built once and rebuilt if self.data changes size.
        The index is only usable for bisection if both lists never go backwards."""
        if self._entry_index_len != len(self.data):
            starts = [e["start"] for e in self.data]
            ends = [e["end"] for e in self.data]
            try:
                usable = all(a <= b for a, b in zip(starts, starts[1:])) and \
                         all(a <= b for a, b in zip(ends, ends[1:]))
            except TypeError:
                usable = False
            self._entry_starts = starts
            self._entry_ends = ends
            self._entry_index_usable = usable
            self._entry_index_len = len(self.data)
        return self._entry_starts, self._entry_ends, self._entry_index_usable

    def get_entry(self, timestamp: datetime):
        if not self.data:
            self.parse()

        # check min and max
        if timestamp < self.data[0]["start"] or timestamp > self.data[len(self.data) - 1]["end"]:
            return None

        starts, ends, usable = self._entry_index()
        if usable:
            # First entry that ends at or after the timestamp. If it also
            # starts by then it is the first match in file order.
            i = bisect_left(ends, timestamp)
            if i < len(ends) and starts[i] <= timestamp:
                return self.data[i]
            return None

        for entry in self.data:
            if entry["start"] <= timestamp and entry["end"] >= timestamp:
                return entry

        return None

    def _build_gps_data(self):
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

    def _gps_times(self):
        """List of GPS times, rebuilt if self.gps_data changes size.
        Returns None if the times are not in order, so bisection cannot be used."""
        if self._gps_times_len != len(self.gps_data):
            times = [g[0] for g in self.gps_data]
            try:
                usable = all(a <= b for a, b in zip(times, times[1:]))
            except TypeError:
                usable = False
            self._gps_times_list = times if usable else None
            self._gps_times_len = len(self.gps_data)
        return self._gps_times_list

    def _gps_find(self, timestamp):
        """Index of the first GPS point with a time at or after the timestamp, or None"""
        times = self._gps_times()
        if times is not None:
            i = bisect_left(times, timestamp)
            return i if i < len(times) else None

        for i in range(len(self.gps_data)):
            if self.gps_data[i][0] >= timestamp:
                return i
        return None

    def get_gps(self, timestamp):
        if not self.data:
            self.parse()

        # Build once. An SRT without GPS stays empty, so remember that we tried.
        if not self.gps_data and self._gps_built_len != len(self.data):
            self._build_gps_data()
            self._gps_built_len = len(self.data)

        # No data available
        if not len(self.gps_data) or self.gps_data[0][0] > timestamp:
            return None

        # Interpolate
        end = self._gps_find(timestamp)
        if end is None:
            return None

        # Perfect match
        if self.gps_data[end][0] == timestamp:
            return self.utm_to_ll.TransformPoint(*self.gps_data[end][1])

        start = end - 1
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

    def parse(self):

        # SRT metadata is not standarized, we support the following formats:

        # DJI mavic air 2
        # 1
        # 00:00:00,000 --> 00:00:00,016
        # <font size="36">SrtCnt : 1, DiffTime : 16ms
        # 2023-01-06 18:56:48,380,821
        # [iso : 3200] [shutter : 1/60.0] [fnum : 280] [ev : 0] [ct : 3925] [color_md : default] [focal_len : 240] [latitude: 0.000000] [longitude: 0.000000] [altitude: 0.000000] </font>
        # </font> 

        # DJI Mavic Mini
        # 1
        # 00:00:00,000 --> 00:00:01,000
        # F/2.8, SS 206.14, ISO 150, EV 0, GPS (-82.6669, 27.7716, 10), D 2.80m, H 0.00m, H.S 0.00m/s, V.S 0.00m/s 

        # DJI Phantom4 RTK
        # 36
        # 00:00:35,000 --> 00:00:36,000
        # F/6.3, SS 60, ISO 100, EV 0, RTK (120.083799, 30.213635, 28), HOME (120.084146, 30.214243, 103.55m), D 75.36m, H 76.19m, H.S 0.30m/s, V.S 0.00m/s, F.PRY (-5.3°, 2.1°, 28.3°), G.PRY (-40.0°, 0.0°, 28.2°)

        # DJI Unknown Model #1
        # 1
        # 00:00:00,000 --> 00:00:00,033
        # <font size="28">SrtCnt : 1, DiffTime : 33ms
        # 2024-01-18 10:23:26.397
        # [iso : 150] [shutter : 1/5000.0] [fnum : 170] [ev : 0] [ct : 5023] [color_md : default] [focal_len : 240] [dzoom_ratio: 10000, delta:0],[latitude: -22.724555] [longitude: -47.602414] [rel_alt: 0.300 abs_alt: 549.679] </font>

        # DJI Mavic 2 Zoom
        # 1
        # 00:00:00,000 --> 00:00:00,041
        # <font size="36">FrameCnt : 1, DiffTime : 41ms
        # 2023-07-15 11:55:16,320,933
        # [iso : 100] [shutter : 1/400.0] [fnum : 280] [ev : 0] [ct : 5818] [color_md : default] [focal_len : 240] [latitude : 0.000000] [longtitude : 0.000000] [altitude: 0.000000] </font>

        # DJI Unknown Model #2
        # 1
        # 00:00:00,000 --> 00:00:00,033
        # No:1, F/2.8, SS 155.55, ISO 100, EV 0, M.M AE_METER_CENTER, A.T (126,109), Luma 106, Coef(1.000000, 1.000000, 1.000000), FaceDetectTag (0), FaceDetectRect (0,0,0,0,), Gain (1.000000,4096), Index (Ev:10085,Nf:0), E.M 0, AERect(n/a), AeAdvScene (GR:91.000000,GWR:1.000000,LLR:0.196683,RR:0.870551), LeCurve(64) (1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,1024,128,), AfSpd 0/0, Af Rect(X:0, Y:0, W:0, H:0), AfPos 0, AwbMode WB_AUTOMATIC, Awb Gain(R:8206, G:4096, B:7058), ColorTemp 5241, B.L (-1020, -1020, -1020, -1020), IQS (39253, 208), ToneInfo (0,16,33,51,68,85,102,119,136,152,169,185,202,218,234,250,266,282,298,314,330,346,362,378,394,410,425,441,457,473,488,500,514,532,550,567,584,602,619,637,654,671,688,705,721,738,754,770,786,801,817,832,847,862,877,892,907,922,937,951,966,981,995,1011,0,64,134,205,274,342,410,477,544,611,677,743,809,873,937,1002,1066,1130,1194,1258,1322,1385,1449,1512,1576,1640,1703,1766,1829,1893,1952,2003,2058,2130,2201,2270,2339,2410,2479,2548,2616,2685,2753,2820,2886,2952,3016,3080,3144,3207,3270,3329,3391,3451,3511,3571,3630,3688,3748,3807,3866,3924,3983,4044,), Isp Info (PIPE 1,ADJ 0,De 0) GPS (-2.5927, 52.0035, 15), D 0.61m, H 1.00m, H.S 0.00m/s, V.S 0.00m/s 


        with open(self.filename, 'r') as f:

            iso = None
            shutter = None
            fnum = None
            focal_len = None
            latitude = None
            longitude = None
            altitude = None
            start = None
            end = None

            for line in f:
                # Remove html tags, spaces
                line = re.sub('<[^<]+?>', '', line).strip()

                if not line:
                    if start is not None:
                        self.data.append({
                            "start": start,
                            "end": end,
                            "iso": iso,
                            "shutter": shutter,
                            "fnum": fnum,
                            "focal_len": focal_len,
                            "latitude": latitude,
                            "longitude": longitude,
                            "altitude": altitude
                        })

                    iso = None
                    shutter = None
                    fnum = None
                    ct = None
                    focal_len = None
                    latitude = None
                    longitude = None
                    altitude = None
                    start = None
                    end = None

                    continue

                # Search this "00:00:00,000 --> 00:00:00,016"
                match = re.search("(\d{2}:\d{2}:\d{2},\d+) --> (\d{2}:\d{2}:\d{2},\d+)", line)
                if match:
                    start = datetime.strptime(match.group(1), "%H:%M:%S,%f")
                    end = datetime.strptime(match.group(2), "%H:%M:%S,%f")

                def non_zero_float(v):
                    try:
                        v = float(v)
                        if v == 0:
                            return None
                        return v
                    except ValueError:
                        return None

                iso = match_single([
                    "iso : (\d+)",
                    "ISO (\d+)"
                ], line)

                shutter = match_single([
                    ("shutter : \d+/(\d+\.?\d*)", non_zero_float),
                    ("SS (\d+\.?\d*)", non_zero_float)
                ], line)

                fnum = match_single([
                    ("fnum : (\d+)", lambda v: float(v)/100.0),
                    ("F/([\d\.]+)", float),
                ], line)

                focal_len = match_single("focal_len : (\d+)", line)

                latitude = match_single([
                    ("latitude: ([\d\.\-]+)", non_zero_float),
                    ("latitude : ([\d\.\-]+)", non_zero_float),
                    ("GPS \([\d\.\-]+,? ([\d\.\-]+),? [\d\.\-]+\)", non_zero_float),
                    ("RTK \([-+]?\d+\.\d+, (-?\d+\.\d+), [\d\.\-]+\)", non_zero_float),
                ], line)
                
                longitude = match_single([
                    ("longitude: ([\d\.\-]+)", non_zero_float),
                    ("longtitude : ([\d\.\-]+)", non_zero_float),
                    ("GPS \(([\d\.\-]+),? [\d\.\-]+,? [\d\.\-]+\)", non_zero_float),
                    ("RTK \((-?\d+\.\d+), [-+]?\d+\.\d+, [\d\.\-]+\)", non_zero_float),
                ], line)
                
                altitude = match_single([
                    ("altitude: ([\d\.\-]+)", non_zero_float),
                    ("GPS \([\d\.\-]+,? [\d\.\-]+,? ([\d\.\-]+)\)", non_zero_float),
                    ("RTK \([-+]?\d+\.\d+, [-+]?\d+\.\d+, ([\d\.\-]+)\)", non_zero_float),
                    ("abs_alt: ([\d\.\-]+)", non_zero_float),
                ], line)
