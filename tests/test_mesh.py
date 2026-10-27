import os
import tempfile
import unittest
from unittest import mock

from modules import mesh


class TestPoissonReconRetry(unittest.TestCase):
    def run_poisson(self, threads, succeed_with):
        """Run screened_poisson_reconstruction with a fake PoissonRecon that
        aborts unless it gets succeed_with threads. Return the thread count of
        each PoissonRecon run and the retry warnings."""
        runs = []
        warnings = []

        with tempfile.TemporaryDirectory() as d:
            out_mesh = os.path.join(d, "odm_mesh.ply")
            dirty_mesh = os.path.join(d, "odm_mesh.dirty.ply")

            def fake_run(cmd, env_vars=None, **kwargs):
                if not env_vars or "OMP_NUM_THREADS" not in env_vars:
                    return # ReconstructMesh cleanup
                runs.append(env_vars["OMP_NUM_THREADS"])
                if env_vars["OMP_NUM_THREADS"] != succeed_with:
                    raise Exception("Child returned 134")
                open(dirty_mesh, "w").close()

            def warning(msg):
                if msg.startswith("PoissonRecon failed"):
                    warnings.append(msg)

            with mock.patch.object(mesh.system, "run", side_effect=fake_run), \
                 mock.patch.object(mesh.log, "WARNING", side_effect=warning), \
                 mock.patch.object(mesh.platform, "machine", return_value="x86_64"):
                mesh.screened_poisson_reconstruction("point_cloud.ply", out_mesh, threads=threads)

        return runs, warnings

    def test_retry_message_reports_failed_threads(self):
        runs, warnings = self.run_poisson(15, succeed_with=1)

        self.assertEqual(runs, [15, 7, 3, 1])
        self.assertEqual(warnings, [
            "PoissonRecon failed with 15 threads, let's retry with 7...",
            "PoissonRecon failed with 7 threads, let's retry with 3...",
            "PoissonRecon failed with 3 threads, let's retry with 1...",
        ])

    def test_retry_message_with_even_threads(self):
        runs, warnings = self.run_poisson(14, succeed_with=7)

        self.assertEqual(runs, [14, 7])
        self.assertEqual(warnings, [
            "PoissonRecon failed with 14 threads, let's retry with 7...",
        ])

if __name__ == '__main__':
    unittest.main()
