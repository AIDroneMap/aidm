import unittest
from unittest import mock

from modules import concurrency

GIB = 1024 ** 3


class FakeMemory:
    available = 8 * GIB


class TestMaxWorkersForMemory(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch("modules.concurrency.virtual_memory", return_value=FakeMemory())
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_memory_limits_the_workers(self):
        """8 GiB available, half usable: 4 workers of 1 GiB fit."""
        self.assertEqual(concurrency.max_workers_for_memory(1 * GIB, 12), 4)

    def test_max_workers_limits_the_workers(self):
        """Never more workers than the caller allows."""
        self.assertEqual(concurrency.max_workers_for_memory(1 * GIB, 2), 2)

    def test_use_at_most(self):
        """The fraction of memory to use is respected."""
        self.assertEqual(concurrency.max_workers_for_memory(1 * GIB, 12, use_at_most=0.25), 2)
        self.assertEqual(concurrency.max_workers_for_memory(1 * GIB, 12, use_at_most=1.0), 8)

    def test_never_zero(self):
        """A worker that does not fit still gets one worker."""
        self.assertEqual(concurrency.max_workers_for_memory(100 * GIB, 12), 1)

    def test_zero_bytes_per_worker(self):
        """No division by zero: the only limit is max_workers."""
        self.assertEqual(concurrency.max_workers_for_memory(0, 12), 12)

    def test_returns_int(self):
        """The result can be passed as a thread count."""
        self.assertIsInstance(concurrency.max_workers_for_memory(1 * GIB, 12), int)


if __name__ == '__main__':
    unittest.main()
