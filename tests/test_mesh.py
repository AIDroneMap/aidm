import os
import tempfile
import unittest
from unittest import mock

from modules import mesh


class TestPoissonReconRetry(unittest.TestCase):
    def run_poisson(self, threads, serial_succeeds=True):
        """Run screened_poisson_reconstruction with a fake PoissonRecon that
        aborts in parallel mode and succeeds in serial mode (--parallel 2)
        when serial_succeeds is set. Return the OMP_NUM_THREADS and serial
        flag of each PoissonRecon run, and the retry warnings."""
        runs = []
        warnings = []

        with tempfile.TemporaryDirectory() as d:
            out_mesh = os.path.join(d, "odm_mesh.ply")
            dirty_mesh = os.path.join(d, "odm_mesh.dirty.ply")

            def fake_run(cmd, env_vars=None, **kwargs):
                if not env_vars or "OMP_NUM_THREADS" not in env_vars:
                    return # ReconstructMesh cleanup
                serial = "--parallel 2" in cmd
                runs.append((env_vars["OMP_NUM_THREADS"], serial))
                if not (serial and serial_succeeds):
                    raise Exception("Child returned 134")
                open(dirty_mesh, "w").close()

            def warning(msg):
                if msg.startswith("PoissonRecon failed"):
                    warnings.append(msg)

            with mock.patch.object(mesh.system, "run", side_effect=fake_run), \
                 mock.patch.object(mesh.log, "WARNING", side_effect=warning), \
                 mock.patch.object(mesh.platform, "machine", return_value="x86_64"), \
                 mock.patch.object(mesh.os, "remove"):
                mesh.screened_poisson_reconstruction("point_cloud.ply", out_mesh, threads=threads)

        return runs, warnings

    def test_failed_run_retries_once_serially(self):
        runs, warnings = self.run_poisson(15)

        self.assertEqual(runs, [(15, False), (1, True)])
        self.assertEqual(warnings, [
            "PoissonRecon failed with 15 threads, let's retry with 1...",
        ])

    def test_three_threads_retry_serially(self):
        runs, warnings = self.run_poisson(3)

        self.assertEqual(runs, [(3, False), (1, True)])
        self.assertEqual(warnings, [
            "PoissonRecon failed with 3 threads, let's retry with 1...",
        ])

    def test_serial_failure_is_not_retried(self):
        runs, warnings = self.run_poisson(15, serial_succeeds=False)

        self.assertEqual(runs, [(15, False), (1, True)])

    def test_one_thread_runs_serially_once(self):
        runs, warnings = self.run_poisson(1, serial_succeeds=False)

        self.assertEqual(runs, [(1, True)])
        self.assertEqual(warnings, [])

if __name__ == '__main__':
    unittest.main()
