import datetime
from fractions import Fraction
import io
from math import ceil, floor
import time
import cv2
import os
import sys
import zipfile
import collections
import platform
from PIL import Image
import numpy as np
import piexif
from modules import system
from modules import log
from modules.net import download
from modules.video.srtparser import SrtFileParser
from modules.video.gpxparser import GpxFileParser, get_video_start_time

class Video2Dataset:
    def __init__(self, input, output, max_dimension=None, limit=None, use_loc_parsers=True):
        self.frame_index = 0
        self.input = input
        self.output = output
        self.max_dimension = max_dimension
        self.limit = limit
        self.use_loc_parsers = use_loc_parsers
        self.internal_resolution = 512
        self.frame_format = 'jpg'

    def ProcessVideo(self):
        if not os.path.exists(self.output):
            os.makedirs(self.output)

        self.date_now = None
        start = time.time()

        self.global_idx = 0
        output_file_paths = []
        
        # foreach input file
        for input_file in self.input:
            # get file name
            file_name = os.path.basename(input_file)
            log.INFO("Processing video: {}".format(input_file))

            # get video info
            video_info = get_video_info(input_file)
            log.INFO(video_info)

            # Set pseudo start time
            if self.date_now is None:
                try:
                    self.date_now = datetime.datetime.fromtimestamp(os.path.getmtime(input_file))
                except:
                    self.date_now = datetime.datetime.now()
            else:
                self.date_now += datetime.timedelta(seconds=video_info.total_frames / video_info.frame_rate)
            
            log.INFO("Use pseudo start time: %s" % self.date_now)

            if self.use_loc_parsers:

                name = os.path.splitext(input_file)[0]

                srt_files = [name + ".srt", name + ".SRT"]
                loc_parser = None

                for srt_file in srt_files:
                    if os.path.exists(srt_file):
                        log.INFO("Loading SRT file: {}".format(srt_file))
                        try:
                            loc_parser = SrtFileParser(srt_file)
                            loc_parser.parse()
                            break
                        except Exception as e:
                            log.INFO("Error parsing SRT file: {}".format(e))
                            loc_parser = None

                if loc_parser is None:
                    gpx_files = [name + ".gpx", name + ".GPX"]

                    for gpx_file in gpx_files:
                        if os.path.exists(gpx_file):
                            log.INFO("Loading GPX file: {}".format(gpx_file))
                            try:
                                loc_parser = GpxFileParser(gpx_file, get_video_start_time(input_file))
                                loc_parser.parse()
                                break
                            except Exception as e:
                                log.INFO("Error parsing GPX file: {}".format(e))
                                loc_parser = None
            else:
                loc_parser = None

            start2 = time.time()
            

            # open video file
            cap = cv2.VideoCapture(input_file)
            if (not cap.isOpened()):
                log.INFO("Error opening video stream or file")
                return

            self.frames_memory = []
            frame_index = 0
            frame_end = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            frames_to_process = video_info.total_frames
            sharpness_window_size = max(round(video_info.frame_rate / 4.0), 1)
            sharpness_window = []

            progress = prev_progress = 0
            while (cap.isOpened()):
                ret, frame = cap.read()

                if not ret:
                    break

                # Calculate progress percentage
                progress = floor((self.frame_index + 1) / frames_to_process * 100)
                if abs(progress - prev_progress) >= 1:
                    prev_progress = progress
                    print("[{}][{:3d}%] Processing frame {}/{}: ".format(file_name, progress, self.frame_index + 1, frames_to_process), end="\r")


                frame_bw = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

                h, w = frame_bw.shape
                resolution = self.internal_resolution
                if resolution < w or resolution < h:
                    m = max(w, h)
                    factor = resolution / m
                    frame_bw = cv2.resize(frame_bw, (int(ceil(w * factor)), int(ceil(h * factor))), interpolation=cv2.INTER_AREA)

                sharpness_window.append((self.frame_index, self.CalculateSharpness(frame_bw), frame, frame_bw))
                self.frame_index += 1

                if len(sharpness_window) >= sharpness_window_size:
                    output_file_paths.extend(self.ProcessFrame(sharpness_window, video_info, loc_parser))
                    sharpness_window = []

            if sharpness_window:
                output_file_paths.extend(self.ProcessFrame(sharpness_window, video_info, loc_parser))

            cap.release()

        if self.limit is not None and self.limit > 0 and self.global_idx >= self.limit:
            log.INFO("Limit of {} frames reached, trimming dataset".format(self.limit))
            output_file_paths = limit_files(output_file_paths, self.limit)

        end = time.time()
        log.INFO("Total processing time: {:.2f}s".format(end - start))
        return output_file_paths

    def CalculateSharpness(self, frame_bw):
        return cv2.Laplacian(frame_bw, cv2.CV_64F).var()

    def IsBlack(self, frame_bw, picture_black_ratio_th=0.98, pixel_black_th=0.30):
        abs_thresh = pixel_black_th * 255
        nb_black_pixels = np.sum(frame_bw < abs_thresh)
        ratio_black_pixels = nb_black_pixels / (frame_bw.shape[0] * frame_bw.shape[1])
        return ratio_black_pixels >= picture_black_ratio_th
        
    def ProcessFrame(self, frames, video_info, loc_parser):
        frame_index, _, frame, frame_bw = max(frames, key=lambda frame_data: frame_data[1])

        if self.IsBlack(frame_bw):
            return []

        # Check for duplicates
        small = cv2.resize(frame_bw, (9, 8), interpolation=cv2.INTER_AREA)
        diff = small[:, 1:] > small[:, :-1]
        frame_key = np.packbits(diff.flatten())

        is_duplicate = False
        for past_key in self.frames_memory:
            hamming_distance = np.count_nonzero(frame_key != past_key)
            if hamming_distance <= 2: # small tolerance, higher = more tolerant, 0 = exact
                is_duplicate = True
                break

        if is_duplicate:
            return []

        self.frames_memory.append(frame_key)
        if len(self.frames_memory) > 10:
            self.frames_memory.pop(0)

        current_frame_index = self.frame_index
        self.frame_index = frame_index
        path = self.SaveFrame(frame, video_info, loc_parser)
        self.frame_index = current_frame_index
        self.global_idx += 1
        return [path]


    def SaveFrame(self, frame, video_info, loc_parser: SrtFileParser):
        max_dim = self.max_dimension
        if max_dim is not None:
            h, w, _ = frame.shape
            if max_dim < w or max_dim < h:
                m = max(w, h)
                factor = max_dim / m
                frame = cv2.resize(frame, (int(ceil(w * factor)), int(ceil(h * factor))), interpolation=cv2.INTER_AREA)

        path = os.path.join(self.output,
            "{}_{}_{}.{}".format(video_info.basename, self.global_idx, self.frame_index, self.frame_format))

        _, buf = cv2.imencode('.' + self.frame_format, frame)

        delta = datetime.timedelta(seconds=(self.frame_index / video_info.frame_rate))
        elapsed_time = datetime.datetime(1900, 1, 1) + delta

        img = Image.open(io.BytesIO(buf))
        
        entry = gps_coords = None
        if loc_parser is not None:
            entry = loc_parser.get_entry(elapsed_time)
            gps_coords = loc_parser.get_gps(elapsed_time)

        exif_time = (elapsed_time + (self.date_now - datetime.datetime(1900, 1, 1)))
        elapsed_time_str = exif_time.strftime("%Y:%m:%d %H:%M:%S")
        subsec_time_str = exif_time.strftime("%f")

        # Exif dict contains the following keys: '0th', 'Exif', 'GPS', '1st', 'thumbnail'
        # Set the EXIF metadata
        exif_dict = {
            "0th": {
                piexif.ImageIFD.Software: "LGT",
                piexif.ImageIFD.DateTime: elapsed_time_str,
                piexif.ImageIFD.XResolution: (frame.shape[1], 1),
                piexif.ImageIFD.YResolution: (frame.shape[0], 1),
                piexif.ImageIFD.Make: "DJI" if video_info.basename.lower().startswith("dji") else "Unknown",
                piexif.ImageIFD.Model: "VideoFrame"
            },
            "Exif": {
                piexif.ExifIFD.DateTimeOriginal: elapsed_time_str,
                piexif.ExifIFD.DateTimeDigitized: elapsed_time_str,
                piexif.ExifIFD.SubSecTime: subsec_time_str,
                piexif.ExifIFD.PixelXDimension: frame.shape[1],
                piexif.ExifIFD.PixelYDimension: frame.shape[0],
            }}

        if entry is not None:
            if entry["shutter"] is not None:
                exif_dict["Exif"][piexif.ExifIFD.ExposureTime] = (1, int(entry["shutter"]))
            if entry["focal_len"] is not None:
                exif_dict["Exif"][piexif.ExifIFD.FocalLength] = (entry["focal_len"], 100)
            if entry["fnum"] is not None:
                exif_dict["Exif"][piexif.ExifIFD.FNumber] = float_to_rational(entry["fnum"])
            if entry["iso"] is not None:
                exif_dict["Exif"][piexif.ExifIFD.ISOSpeedRatings] = entry["iso"]
        
        if gps_coords is not None:
            exif_dict["GPS"] = get_gps_location(elapsed_time, gps_coords[1], gps_coords[0], gps_coords[2])

        exif_bytes = piexif.dump(exif_dict)
        img.save(path, exif=exif_bytes, quality=95)

        return path


def get_video_info(input_file):

    video = cv2.VideoCapture(input_file)
    basename = os.path.splitext(os.path.basename(input_file))[0]

    total_frames = int(video.get(cv2.CAP_PROP_FRAME_COUNT))
    frame_rate = video.get(cv2.CAP_PROP_FPS)

    video.release()

    return collections.namedtuple("VideoInfo", ["total_frames", "frame_rate", "basename"])(total_frames, frame_rate, basename)

def float_to_rational(f):
    f = Fraction(f).limit_denominator()
    return (f.numerator, f.denominator)

def limit_files(paths, limit):
    if len(paths) <= limit:
        return paths
    
    to_keep = []
    all_idxes = np.arange(0, len(paths))
    keep_idxes = np.linspace(0, len(paths) - 1, limit, dtype=int)
    remove_idxes = set(all_idxes) - set(keep_idxes)

    p = np.array(paths)
    to_keep = list(p[keep_idxes])

    for idx in remove_idxes:
        if os.path.isfile(paths[idx]):
            os.remove(paths[idx])

    return to_keep

def to_deg(value, loc):
    """convert decimal coordinates into degrees, munutes and seconds tuple
    Keyword arguments: value is float gps-value, loc is direction list ["S", "N"] or ["W", "E"]
    return: tuple like (25, 13, 48.343 ,'N')
    """
    if value < 0:
        loc_value = loc[0]
    elif value > 0:
        loc_value = loc[1]
    else:
        loc_value = ""
    abs_value = abs(value)
    deg =  int(abs_value)
    t1 = (abs_value-deg)*60
    min = int(t1)
    sec = round((t1 - min)* 60, 5)
    return (deg, min, sec, loc_value)

def get_gps_location(elapsed_time, lat, lng, altitude):

    lat_deg = to_deg(lat, ["S", "N"])
    lng_deg = to_deg(lng, ["W", "E"])

    exiv_lat = (float_to_rational(lat_deg[0]), float_to_rational(lat_deg[1]), float_to_rational(lat_deg[2]))
    exiv_lng = (float_to_rational(lng_deg[0]), float_to_rational(lng_deg[1]), float_to_rational(lng_deg[2]))

    gps_ifd = {
        piexif.GPSIFD.GPSVersionID: (2, 0, 0, 0),
        piexif.GPSIFD.GPSDateStamp: elapsed_time.strftime('%Y:%m:%d')
    }

    if lat is not None and lng is not None:
        gps_ifd[piexif.GPSIFD.GPSLatitudeRef] = lat_deg[3]
        gps_ifd[piexif.GPSIFD.GPSLatitude] = exiv_lat
        gps_ifd[piexif.GPSIFD.GPSLongitudeRef] = lng_deg[3]
        gps_ifd[piexif.GPSIFD.GPSLongitude] = exiv_lng
        if altitude is not None:
            gps_ifd[piexif.GPSIFD.GPSAltitudeRef] = 0
            gps_ifd[piexif.GPSIFD.GPSAltitude] = float_to_rational(round(altitude * 1000) / 1000)

    return gps_ifd

def extract_subtitles(video_file, overwrite=False):
    base, ext = os.path.splitext(video_file)
    srt_file = base + '.srt'

    if os.path.isfile(srt_file) and not overwrite:
        return

    ffmpeg_bin = get_ffmpeg()
    if ffmpeg_bin is None:
        log.WARNING("Cannot extract subtitles, no ffmpeg")
        return

    try:
        system.run(f'"{ffmpeg_bin}" -i "{video_file}" -map 0:s:0 "{srt_file}" -y -hide_banner -loglevel panic')
    except:
        log.INFO(f"No subtitles found in {os.path.basename(video_file)}")

    if os.path.isfile(srt_file):
        log.INFO(f"Extracted subtitles to {srt_file}")

def get_ffmpeg(version = '7.0.2'):
    base_dir = os.path.join(os.path.dirname(__file__), "..", "..")
    if sys.platform == 'win32':
        base_dir = os.path.join(os.getenv('PROGRAMDATA'),"LGT")
    base_dir = os.path.join(os.path.abspath(base_dir), "storage", "binaries")
    
    if not os.path.isdir(base_dir):
        os.makedirs(base_dir, exist_ok=True)
    
    machine = platform.machine().lower()
    url = f"https://github.com/pierotofy/photogrammetry-tools/releases/download/v1.0.0/ffmpeg-{version}-amd64.zip"
    
    if "arm" in machine:
        url = f"https://github.com/pierotofy/photogrammetry-tools/releases/download/v1.0.0/ffmpeg-{version}-arm64.zip"

    # Check if we need to download it
    ffmpeg_bin = os.path.join(base_dir, "ffmpeg")
    if not os.path.isfile(ffmpeg_bin):
        log.INFO("Downloading ffmpeg from %s ..." % url)

        last_update = 0

        def callback(progress):
            nonlocal last_update

            time_has_elapsed = time.time() - last_update >= 2

            if time_has_elapsed or int(progress) == 100:
                log.INFO("Downloading: %s%%" % int(progress))
                last_update = time.time()

        try:
            downloaded_file = download(url, base_dir, progress_callback=callback)
        except Exception as e:
            log.WARNING("Cannot download %s: %s" % (url, str(e)))
            return None

        if os.path.basename(downloaded_file).lower().endswith(".zip"):
            log.INFO("Extracting %s ..." % downloaded_file)
            with zipfile.ZipFile(downloaded_file, 'r') as z:
                z.extractall(base_dir)
            os.remove(downloaded_file)
        
        if not os.path.isfile(ffmpeg_bin):
            log.WARNING("Cannot find %s (is the URL correct?)" % ffmpeg_bin)
            return None
        else:
            if not os.access(ffmpeg_bin, os.X_OK):
                os.chmod(ffmpeg_bin, 0o755)
            return ffmpeg_bin
    else:
        return ffmpeg_bin