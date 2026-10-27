import io
import os
import tempfile
import unittest

from modules.photo import ODM_Photo, XMP_PREFIX_SIZE, find_xmp_packet

START = b'<x:xmpmeta'
END = b'</x:xmpmeta>'

PACKET = (
    b'<x:xmpmeta xmlns:x="adobe:ns:meta/">'
    b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    b'<rdf:Description xmlns:Camera="http://pix4d.com/camera/1.0/" '
    b'Camera:BandName="Green" Camera:GPSXYAccuracy="0.02"/>'
    b'</rdf:RDF></x:xmpmeta>'
)


def reference(data):
    """Original logic: read everything, then slice."""
    b = io.BytesIO(data).read()
    s = b.find(b'<x:xmpmeta')
    e = b.find(b'</x:xmpmeta')
    return b[s:e + 12] if s < e else None


class CountingReader(io.BytesIO):
    """BytesIO that records how many bytes were handed out."""
    def __init__(self, data):
        super().__init__(data)
        self.bytes_read = 0

    def read(self, size=-1):
        out = super().read(size)
        self.bytes_read += len(out)
        return out


class TestFindXmpPacket(unittest.TestCase):
    def check(self, data, prefix_size):
        got = find_xmp_packet(io.BytesIO(data), prefix_size)
        self.assertEqual(got, reference(data))
        return got

    def test_packet_inside_prefix(self):
        data = b'\xff\xd8' + b'a' * 50 + PACKET + b'b' * 500
        self.assertEqual(self.check(data, 256), PACKET)

    def test_packet_inside_default_prefix(self):
        data = b'\xff\xd8' + b'a' * 50 + PACKET + b'b' * 500
        got = find_xmp_packet(io.BytesIO(data))
        self.assertEqual(got, PACKET)

    def test_packet_after_prefix(self):
        data = b'a' * 300 + PACKET + b'b' * 100
        self.assertEqual(self.check(data, 64), PACKET)

    def test_start_in_prefix_end_after(self):
        data = b'a' * 10 + PACKET + b'b' * 100
        self.assertEqual(self.check(data, 10 + len(START) + 5), PACKET)

    def test_prefix_cuts_end_tag(self):
        data = b'a' * 10 + PACKET + b'b' * 100
        end = data.find(b'</x:xmpmeta')
        for cut in range(end, end + 13):
            with self.subTest(cut=cut):
                self.assertEqual(self.check(data, cut), PACKET)

    def test_prefix_ends_before_final_bracket(self):
        data = b'a' * 10 + PACKET + b'b' * 100
        end = data.find(b'</x:xmpmeta')
        # xmp_end + 12 is one more than the prefix length here
        self.assertEqual(self.check(data, end + 11), PACKET)

    def test_prefix_ends_right_after_packet(self):
        data = b'a' * 10 + PACKET + b'b' * 100
        end = data.find(b'</x:xmpmeta')
        self.assertEqual(self.check(data, end + 12), PACKET)

    def test_no_packet(self):
        data = b'a' * 500
        self.assertIsNone(self.check(data, 64))
        self.assertIsNone(self.check(data, 1024))

    def test_empty_file(self):
        self.assertIsNone(self.check(b'', 64))

    def test_end_tag_without_start_tag(self):
        # The original code returns an odd slice here. Keep that behaviour.
        data = b'a' * 20 + b'</x:xmpmeta>' + b'b' * 20
        got = self.check(data, 64)
        self.assertIsNotNone(got)
        self.check(data, 16)

    def test_end_before_start(self):
        data = b'a' * 20 + b'</x:xmpmeta>' + b'c' * 5 + PACKET + b'b' * 20
        self.check(data, 64)
        self.check(data, 1024)

    def test_start_tag_without_end_tag(self):
        data = b'a' * 20 + START + b' unterminated' + b'b' * 200
        self.assertIsNone(self.check(data, 64))

    def test_file_shorter_than_prefix(self):
        data = b'a' * 5 + PACKET
        self.assertEqual(self.check(data, XMP_PREFIX_SIZE), PACKET)

    def test_file_shorter_than_prefix_truncated_end(self):
        data = b'a' * 5 + PACKET[:-1]
        self.check(data, XMP_PREFIX_SIZE)

    def test_two_packets_returns_first(self):
        data = b'a' * 10 + PACKET + b'b' * 10 + PACKET
        self.assertEqual(self.check(data, 1024), PACKET)
        self.assertEqual(self.check(data, 20), PACKET)

    def test_every_prefix_size(self):
        data = b'xx' + PACKET + b'yy' + b'</x:xmpmeta>'
        for size in range(1, len(data) + 3):
            with self.subTest(prefix_size=size):
                self.check(data, size)

    def test_reads_only_prefix_when_packet_inside(self):
        data = b'a' * 100 + PACKET + b'b' * 10000
        f = CountingReader(data)
        self.assertEqual(find_xmp_packet(f, 1024), PACKET)
        self.assertEqual(f.bytes_read, 1024)

    def test_reads_whole_file_once_when_packet_after_prefix(self):
        data = b'a' * 3000 + PACKET + b'b' * 1000
        f = CountingReader(data)
        self.assertEqual(find_xmp_packet(f, 1024), PACKET)
        self.assertEqual(f.bytes_read, len(data))


class Stub:
    filename = 'stub.jpg'


class TestGetXmp(unittest.TestCase):
    def test_parsed_list(self):
        data = b'\xff\xd8' + b'a' * 100 + PACKET + b'b' * 100
        got = ODM_Photo.get_xmp(Stub(), io.BytesIO(data))
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]['@Camera:BandName'], 'Green')
        self.assertEqual(got[0]['@Camera:GPSXYAccuracy'], '0.02')

    def test_parsed_list_from_temp_file(self):
        data = b'\xff\xd8' + b'a' * 100 + PACKET + b'b' * 100
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'x.jpg')
            with open(path, 'wb') as f:
                f.write(data)
            with open(path, 'rb') as f:
                f.seek(0)
                got = ODM_Photo.get_xmp(Stub(), f)
        self.assertEqual(got[0]['@Camera:BandName'], 'Green')

    def test_no_packet_returns_empty_list(self):
        self.assertEqual(ODM_Photo.get_xmp(Stub(), io.BytesIO(b'a' * 100)), [])


if __name__ == '__main__':
    unittest.main()
