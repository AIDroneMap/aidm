import io
import struct
import unittest

from modules.photo import ODM_Photo, JXL_SIGNATURE, find_jxl_exif, read_exif_tags

JXL_ASSET = "tests/assets/test_0001.jxl"

# A little endian TIFF header followed by an empty IFD
TIFF = b'II*\x00\x08\x00\x00\x00\x00\x00'


def box(box_type, payload, large=False):
    if large:
        return struct.pack('>I4sQ', 1, box_type, 16 + len(payload)) + payload
    return struct.pack('>I4s', 8 + len(payload), box_type) + payload


def container(*boxes):
    return JXL_SIGNATURE + box(b'ftyp', b'jxl \x00\x00\x00\x00jxl ') + b''.join(boxes)


def exif_payload(tiff_offset=0):
    return struct.pack('>I', tiff_offset) + b'\x00' * tiff_offset + TIFF


class TestPhoto(unittest.TestCase):
    def setUp(self):
        pass

    def test_jpeg_xl(self):
        photo = ODM_Photo(JXL_ASSET)
        self.assertEqual(photo.width, 1280)
        self.assertEqual(photo.height, 1024)
        self.assertEqual(photo.camera_make, "Lab 308")
        self.assertEqual(photo.camera_model, "Smart Pilot Cam")
        self.assertEqual(photo.orientation, 1)
        self.assertEqual(photo.utc_time, 1755297953000.0)
        self.assertEqual(photo.latitude, 44.058116008051655)
        self.assertEqual(photo.longitude, -121.31559406946837)
        self.assertEqual(photo.altitude, 2019.845)
        self.assertEqual(photo.yaw, 105.780071)
        self.assertEqual(photo.pitch, 40)
        self.assertEqual(photo.roll, 0)

    def test_jpeg_xl_exif_tags(self):
        with open(JXL_ASSET, 'rb') as f:
            tags = read_exif_tags(f)
        self.assertEqual(tags['Image Make'].values, "Lab 308")
        self.assertEqual(tags['Image Model'].values, "Smart Pilot Cam")
        self.assertIn('GPS GPSLatitude', tags)
        self.assertIn('EXIF DateTimeOriginal', tags)


class TestFindJxlExif(unittest.TestCase):
    def find(self, data):
        return find_jxl_exif(io.BytesIO(data))

    def test_asset(self):
        with open(JXL_ASSET, 'rb') as f:
            exif = find_jxl_exif(f)
        self.assertEqual(exif[:8], b'II*\x00\x08\x00\x00\x00')
        self.assertEqual(len(exif), 490)

    def test_other_formats_return_none(self):
        self.assertIsNone(self.find(b'\xff\xd8\xff\xe1' + b'\x00' * 64))
        # A bare JPEG XL codestream has no boxes and no EXIF
        self.assertIsNone(self.find(b'\xff\x0a' + b'\x00' * 64))
        self.assertIsNone(self.find(b''))

    def test_container_without_exif(self):
        self.assertIsNone(self.find(container(box(b'jxlc', b'\xff\x0a'))))

    def test_exif_before_codestream(self):
        data = container(box(b'Exif', exif_payload()), box(b'jxlc', b'\xff\x0a'))
        self.assertEqual(self.find(data), TIFF)

    def test_exif_after_codestream(self):
        data = container(box(b'jxlc', b'\xff\x0a'), box(b'Exif', exif_payload()))
        self.assertEqual(self.find(data), TIFF)

    def test_tiff_header_offset(self):
        data = container(box(b'Exif', exif_payload(tiff_offset=6)))
        self.assertEqual(self.find(data), TIFF)

    def test_large_box_size(self):
        data = container(box(b'Exif', exif_payload(), large=True))
        self.assertEqual(self.find(data), TIFF)

    def test_box_running_to_end_of_file(self):
        data = container(struct.pack('>I4s', 0, b'Exif') + exif_payload())
        self.assertEqual(self.find(data), TIFF)

    def test_truncated_exif_box(self):
        data = container(struct.pack('>I4s', 64, b'Exif') + b'\x00\x00')
        self.assertIsNone(self.find(data))

    def test_tiff_offset_past_payload(self):
        data = container(box(b'Exif', struct.pack('>I', 1000) + TIFF))
        self.assertIsNone(self.find(data))


if __name__ == '__main__':
    unittest.main()