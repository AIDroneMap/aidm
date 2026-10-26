import contextlib
import io
import os
import tempfile
import unittest
from unittest import mock

import cv2
import numpy as np
from PIL import Image

from modules.video.video2dataset import Video2Dataset, limit_files

WIDTH, HEIGHT = 160, 120
MTIME = 1700000000


def write_video(path, frames, fps, seed, black=(), repeat=()):
    """Write a small mp4 whose frames change all the time.
    black: range of frame numbers that are all black.
    repeat: range of frame numbers that repeat the frame before the range."""
    rng = np.random.default_rng(seed)
    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (WIDTH, HEIGHT))
    assert writer.isOpened()
    last = None
    for i in range(frames):
        if i in black:
            frame = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
        elif i in repeat and last is not None:
            frame = last
        else:
            grid = rng.integers(0, 256, (12, 16, 3)).astype(np.uint8)
            frame = cv2.resize(grid, (WIDTH, HEIGHT), interpolation=cv2.INTER_CUBIC)
            noise = rng.integers(0, 30, (HEIGHT, WIDTH, 3)).astype(np.uint8)
            frame = cv2.add(frame, noise)
            last = frame
        writer.write(frame)
    writer.release()
    os.utime(path, (MTIME, MTIME))


def write_srt(path, frames, fps):
    """One row per frame, in the DJI Mavic Mini format"""
    def stamp(seconds):
        ms = int(round(seconds * 1000))
        return "%02d:%02d:%02d,%03d" % (ms // 3600000, ms // 60000 % 60, ms // 1000 % 60, ms % 1000)

    with open(path, "w") as f:
        for i in range(frames):
            f.write("%d\n" % (i + 1))
            f.write("%s --> %s\n" % (stamp(i / fps), stamp((i + 1) / fps)))
            f.write("F/2.8, SS 206.14, ISO 150, EV 0, GPS (%.6f, %.6f, %.1f), D 2.80m\n\n" % (
                11.0 + i * 0.0001, 45.0 + i * 0.00005, 30.0 + i * 0.1))


class Counts:
    """How often the expensive steps ran"""
    def __init__(self):
        self.encodes = 0
        self.saves = 0
        self.captures = 0


@contextlib.contextmanager
def counting(counts, fail_encode_after=None, fail_save_after=None):
    """Count cv2.imencode, PIL saves and VideoCapture constructions.
    Can raise from the nth encode or the nth PIL save to simulate a crash."""
    imencode = cv2.imencode
    capture = cv2.VideoCapture
    save = Image.Image.save

    def counting_imencode(*args, **kwargs):
        counts.encodes += 1
        if fail_encode_after is not None and counts.encodes > fail_encode_after:
            raise RuntimeError("encode failed")
        return imencode(*args, **kwargs)

    def counting_capture(*args, **kwargs):
        counts.captures += 1
        return capture(*args, **kwargs)

    def counting_save(self, *args, **kwargs):
        counts.saves += 1
        if fail_save_after is not None and counts.saves > fail_save_after:
            raise RuntimeError("save failed")
        return save(self, *args, **kwargs)

    with mock.patch("modules.video.video2dataset.cv2.imencode", side_effect=counting_imencode), \
         mock.patch("modules.video.video2dataset.cv2.VideoCapture", side_effect=counting_capture), \
         mock.patch.object(Image.Image, "save", counting_save):
        yield


def run(inputs, output, limit, **failures):
    """Run Video2Dataset quietly. Returns the file list and the counts."""
    v2d = Video2Dataset(input=inputs, output=output, limit=limit)
    counts = Counts()
    with counting(counts, **failures):
        with contextlib.redirect_stdout(io.StringIO()):
            paths = v2d.ProcessVideo()
    return paths, counts


def read_dir(path):
    result = {}
    for name in os.listdir(path):
        with open(os.path.join(path, name), "rb") as f:
            result[name] = f.read()
    return result


def expected_names(names, limit):
    """What limit_files keeps, computed on names only"""
    if len(names) <= limit:
        return list(names)
    return [names[i] for i in np.linspace(0, len(names) - 1, limit, dtype=int)]


class TestVideo2Dataset(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = cls.tmp.name
        cls.video_a = os.path.join(root, "a.mp4")
        cls.video_b = os.path.join(root, "b.mp4")
        write_video(cls.video_a, 240, 10, seed=1, black=range(50, 58), repeat=range(100, 106))
        write_video(cls.video_b, 180, 12, seed=2, black=range(30, 36), repeat=range(90, 99))
        write_srt(os.path.join(root, "a.srt"), 240, 10)

        # Reference: no limit, every selected frame is saved
        cls.ref_dir = os.path.join(root, "ref_one")
        cls.ref_paths, cls.ref_counts = run([cls.video_a], cls.ref_dir, None)
        cls.ref_dir2 = os.path.join(root, "ref_two")
        cls.ref_paths2, cls.ref_counts2 = run([cls.video_a, cls.video_b], cls.ref_dir2, None)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def out(self, name):
        return os.path.join(self.tmp.name, name)

    def check_limited(self, inputs, ref_dir, ref_paths, limit, name):
        n = len(ref_paths)
        ref_names = [os.path.basename(p) for p in ref_paths]
        want = expected_names(ref_names, limit)

        paths, counts = run(inputs, self.out(name), limit)

        self.assertEqual([os.path.basename(p) for p in paths], want)
        self.assertTrue(all(type(p) is str for p in paths))
        self.assertTrue(all(os.path.dirname(p) == self.out(name) for p in paths))

        files = read_dir(self.out(name))
        self.assertEqual(sorted(files), sorted(want))
        ref_files = read_dir(ref_dir)
        for file_name in want:
            self.assertEqual(files[file_name], ref_files[file_name], file_name)
        return n, counts

    def test_reference_has_enough_frames(self):
        self.assertGreater(len(self.ref_paths), 60)
        self.assertGreater(len(self.ref_paths2), len(self.ref_paths) + 30)
        self.assertEqual(self.ref_counts.saves, len(self.ref_paths))
        self.assertEqual(self.ref_counts2.saves, len(self.ref_paths2))
        # Black and repeated frames are not selected
        self.assertLess(len(self.ref_paths), 120)

    def test_limit_one_video(self):
        n = len(self.ref_paths)
        self.check_limited([self.video_a], self.ref_dir, self.ref_paths, n // 4, "one")

    def test_limit_two_videos(self):
        n = len(self.ref_paths2)
        self.check_limited([self.video_a, self.video_b], self.ref_dir2, self.ref_paths2, n // 4, "two")

    def test_limit_of_one(self):
        self.check_limited([self.video_a, self.video_b], self.ref_dir2, self.ref_paths2, 1, "single")

    def test_limit_encodes_every_frame_but_saves_only_kept_ones(self):
        n = len(self.ref_paths2)
        limit = n // 4
        _, counts = self.check_limited([self.video_a, self.video_b], self.ref_dir2, self.ref_paths2, limit, "early")
        self.assertEqual(counts.encodes, n)
        self.assertEqual(counts.saves, limit)
        # One capture per video for the info and one for the frames, no second pass
        self.assertEqual(counts.captures, self.ref_counts2.captures)
        self.assertEqual(counts.captures, 4)

    def test_without_limit_every_frame_is_encoded_and_saved(self):
        n = len(self.ref_paths2)
        self.assertEqual(self.ref_counts2.encodes, n)
        self.assertEqual(self.ref_counts2.saves, n)

    def test_limit_equal_to_frame_count(self):
        n = len(self.ref_paths)
        paths, counts = run([self.video_a], self.out("equal"), n)
        self.assertEqual([os.path.basename(p) for p in paths], [os.path.basename(p) for p in self.ref_paths])
        self.assertTrue(all(type(p) is str for p in paths))
        self.assertEqual(read_dir(self.out("equal")), read_dir(self.ref_dir))
        self.assertEqual(counts.saves, n)

    def test_limit_above_frame_count(self):
        n = len(self.ref_paths2)
        paths, counts = run([self.video_a, self.video_b], self.out("above"), n + 50)
        self.assertEqual([os.path.basename(p) for p in paths], [os.path.basename(p) for p in self.ref_paths2])
        self.assertEqual(read_dir(self.out("above")), read_dir(self.ref_dir2))
        self.assertEqual(counts.saves, n)

    def test_stale_file_of_a_dropped_frame_is_removed(self):
        n = len(self.ref_paths)
        names = [os.path.basename(p) for p in self.ref_paths]
        want = expected_names(names, n // 4)
        dropped = [name for name in names if name not in want][0]
        out = self.out("stale")
        os.makedirs(out)
        with open(os.path.join(out, dropped), "wb") as f:
            f.write(b"old")
        run([self.video_a], out, n // 4)
        self.assertEqual(sorted(os.listdir(out)), sorted(want))

    def project(self, name):
        """A fresh project folder with an images/ output folder inside it"""
        parent = self.out(name)
        os.makedirs(parent)
        return parent, os.path.join(parent, "images")

    def test_temp_folder_is_removed_after_success(self):
        n = len(self.ref_paths)
        parent, images = self.project("clean")
        want = expected_names([os.path.basename(p) for p in self.ref_paths], n // 4)
        run([self.video_a], images, n // 4)
        self.assertEqual(sorted(os.listdir(images)), sorted(want))
        self.assertEqual(os.listdir(parent), ["images"])

    def test_temp_folder_is_next_to_the_output_folder_while_running(self):
        parent, images = self.project("spy")
        seen = []
        imencode = cv2.imencode

        def spying_imencode(*args, **kwargs):
            seen.append((sorted(os.listdir(parent)), os.listdir(images)))
            return imencode(*args, **kwargs)

        with mock.patch("modules.video.video2dataset.cv2.imencode", side_effect=spying_imencode):
            run([self.video_a], images, 10)
        self.assertTrue(seen)
        for in_parent, in_images in seen:
            self.assertEqual(len(in_parent), 2)
            self.assertTrue(in_parent[0].startswith(".video-frames-"))
            self.assertEqual(in_parent[1], "images")
            self.assertEqual(in_images, [])

    def test_output_folder_given_with_a_trailing_slash(self):
        parent, images = self.project("slash")
        run([self.video_a], images + os.sep, 10)
        self.assertEqual(len(os.listdir(images)), 10)
        self.assertEqual(os.listdir(parent), ["images"])

    def check_cleaned_after_failure(self, name, **failures):
        parent, images = self.project(name)
        with self.assertRaises(RuntimeError):
            run([self.video_a], images, 10, **failures)
        # Only final frames written before the error remain in images/,
        # and the temporary folder next to it is gone
        names = set(os.path.basename(p) for p in self.ref_paths)
        for entry in os.listdir(images):
            self.assertIn(entry, names)
            self.assertTrue(os.path.isfile(os.path.join(images, entry)))
        self.assertEqual(os.listdir(parent), ["images"])
        return os.listdir(images)

    def test_temp_folder_is_removed_after_failure_while_reading(self):
        left = self.check_cleaned_after_failure("fail_read", fail_encode_after=20)
        self.assertEqual(left, [])

    def test_temp_folder_is_removed_after_failure_while_writing(self):
        left = self.check_cleaned_after_failure("fail_write", fail_save_after=3)
        self.assertEqual(len(left), 3)

    def test_limit_zero_means_no_limit(self):
        paths, counts = run([self.video_a], self.out("zero"), 0)
        self.assertEqual(len(paths), len(self.ref_paths))
        self.assertEqual(read_dir(self.out("zero")), read_dir(self.ref_dir))

    def test_limit_files_is_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            paths = []
            for i in range(10):
                p = os.path.join(d, "f%d.jpg" % i)
                with open(p, "wb") as f:
                    f.write(b"x")
                paths.append(p)
            kept = limit_files(paths, 4)
            self.assertEqual([os.path.basename(p) for p in kept], ["f0.jpg", "f3.jpg", "f6.jpg", "f9.jpg"])
            self.assertEqual(sorted(os.listdir(d)), ["f0.jpg", "f3.jpg", "f6.jpg", "f9.jpg"])


if __name__ == "__main__":
    unittest.main()
