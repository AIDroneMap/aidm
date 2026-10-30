import hashlib
import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMPARE = os.path.join(ROOT, "scripts", "golden-compare.py")
SMOKE = os.path.join(ROOT, "scripts", "smoke-test.sh")
CONF = os.path.join(ROOT, "scripts", "smoke-test.conf")
HASHES = os.path.join(ROOT, "scripts", "golden-hashes")

spec = importlib.util.spec_from_file_location("golden_compare", COMPARE)
golden = importlib.util.module_from_spec(spec)
spec.loader.exec_module(golden)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write(root, path, data):
    full = os.path.join(root, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as f:
        f.write(data)


def write_list(path, hashes):
    with open(path, "w") as f:
        f.write("# a comment\n\n")
        for name, digest in sorted(hashes.items()):
            f.write("%s  %s\n" % (digest, name))


def read_conf(path):
    config = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                key, _, value = line.partition("=")
                config[key] = value.strip('"')
    return config


# A synthetic project folder, with files that LEFT_OUT does not name
FILES = {
    "odm_dem/dsm.tif": b"dsm",
    "odm_orthophoto/odm_orthophoto.tfw": b"tfw",
    "odm_report/camera_mappings.npz": b"shots",
    "opensfm/stats/overlap.png": b"overlap",
}


def las(date, points=b"points"):
    """A LAS header of 94 bytes with the creation day and year in date,
    then the points."""
    return b"LASF" + bytes(86) + date + points


class TestCompareScript(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.project = os.path.join(self.tmp, "project")
        for path, data in FILES.items():
            write(self.project, path, data)
        self.list = os.path.join(self.tmp, "amd64.txt")
        write_list(self.list, {path: sha(data) for path, data in FILES.items()})

    def run_compare(self, *args):
        p = subprocess.run(["python3", "-I", COMPARE] + list(args),
                           capture_output=True, text=True)
        return p.returncode, p.stdout + p.stderr

    def test_identical_folder_passes(self):
        code, out = self.run_compare(self.project, self.list)
        self.assertEqual(code, 0, out)
        self.assertEqual(out, "Golden comparison: the 4 golden outputs in amd64.txt match.\n")

    def test_one_changed_file(self):
        write(self.project, "odm_dem/dsm.tif", b"another dsm")
        code, out = self.run_compare(self.project, self.list)
        self.assertEqual(code, 1, out)
        self.assertEqual(out.splitlines(), [
            "Golden comparison against amd64.txt, 4 golden outputs: 1 file differs.",
            "",
            "| File | Expected SHA-256 | Actual SHA-256 |",
            "| --- | --- | --- |",
            "| `odm_dem/dsm.tif` | %s | %s |" % (sha(b"dsm"), sha(b"another dsm")),
        ])

    def test_missing_file(self):
        os.remove(os.path.join(self.project, "odm_report", "camera_mappings.npz"))
        code, out = self.run_compare(self.project, self.list)
        self.assertEqual(code, 1, out)
        self.assertIn("| `odm_report/camera_mappings.npz` | %s | missing |" % sha(b"shots"), out)
        self.assertIn("1 file differs", out)

    def test_extra_file(self):
        write(self.project, "odm_report/new.json", b"new")
        code, out = self.run_compare(self.project, self.list)
        self.assertEqual(code, 1, out)
        self.assertIn("| `odm_report/new.json` | not in the list | %s |" % sha(b"new"), out)

    def test_every_difference_is_listed_by_path(self):
        write(self.project, "odm_orthophoto/odm_orthophoto.tfw", b"other")
        os.remove(os.path.join(self.project, "odm_dem", "dsm.tif"))
        write(self.project, "odm_report/new.json", b"new")
        code, out = self.run_compare(self.project, self.list)
        self.assertEqual(code, 1, out)
        rows = [line.split("`")[1] for line in out.splitlines() if line.startswith("| `")]
        self.assertEqual(rows, ["odm_dem/dsm.tif", "odm_orthophoto/odm_orthophoto.tfw", "odm_report/new.json"])
        self.assertIn("3 files differ", out)

    def test_left_out_files_are_not_compared(self):
        with mock.patch.object(golden, "LEFT_OUT", [
                ("odm_report/", "a folder"), ("odm_dem/dsm.tif", "a file"),
                ("opensfm/stats/heatmap *", "a pattern")]):
            write(self.project, "odm_report/new.json", b"new")
            write(self.project, "odm_dem/dsm.tif", b"another dsm")
            write(self.project, "opensfm/stats/heatmap dji m3m.png", b"heatmap")
            write(self.project, "odm_report.txt", b"not in the folder")
            self.assertEqual(golden.hash_folder(self.project), {
                "odm_orthophoto/odm_orthophoto.tfw": sha(b"tfw"),
                "odm_report.txt": sha(b"not in the folder"),
                "opensfm/stats/overlap.png": sha(b"overlap"),
            })

    def test_las_creation_date_is_not_compared(self):
        path = "odm_georeferencing/odm_georeferenced_model.laz"
        write(self.project, path, las(b"\x2f\x01\xea\x07"))
        hashes = golden.hash_folder(self.project)
        self.assertEqual(hashes[path], sha(las(bytes(4))))
        write_list(self.list, hashes)

        # Another day, same points
        write(self.project, path, las(b"\x30\x01\xea\x07"))
        code, out = self.run_compare(self.project, self.list)
        self.assertEqual(code, 0, out)

        # Same day, other points
        write(self.project, path, las(b"\x2f\x01\xea\x07", b"pointz"))
        code, out = self.run_compare(self.project, self.list)
        self.assertEqual(code, 1, out)
        self.assertIn("| `%s` |" % path, out)

    def test_only_las_files_get_the_tolerance(self):
        data = las(b"\x2f\x01\xea\x07")
        write(self.project, "odm_dem/dsm.tif", data)
        write(self.project, "odm_report/short.laz", b"LASF")
        hashes = golden.hash_folder(self.project)
        self.assertEqual(hashes["odm_dem/dsm.tif"], sha(data))
        self.assertEqual(hashes["odm_report/short.laz"], sha(b"LASF"))

    def test_list_prints_a_hash_list_of_the_folder(self):
        code, out = self.run_compare("--list", self.project)
        self.assertEqual(code, 0, out)
        with open(self.list) as f:
            expected = [line for line in f.read().splitlines() if line and not line.startswith("#")]
        self.assertEqual(out.splitlines(), expected)
        code, out = self.run_compare("--list", os.path.join(self.tmp, "nothing"))
        self.assertEqual(code, 2, out)

    def test_bad_list_line(self):
        with open(self.list, "a") as f:
            f.write("abc  odm_dem/dtm.tif\n")
        code, out = self.run_compare(self.project, self.list)
        self.assertEqual(code, 2, out)
        self.assertIn("is not '<sha256>  <path>'", out)

    def test_path_named_twice(self):
        with open(self.list, "a") as f:
            f.write("%s  odm_dem/dsm.tif\n" % sha(b"dsm"))
        code, out = self.run_compare(self.project, self.list)
        self.assertEqual(code, 2, out)
        self.assertIn("names odm_dem/dsm.tif a second time", out)

    def test_missing_folder_or_list(self):
        code, out = self.run_compare(os.path.join(self.tmp, "nothing"), self.list)
        self.assertEqual(code, 2, out)
        code, out = self.run_compare(self.project, os.path.join(self.tmp, "nothing.txt"))
        self.assertEqual(code, 2, out)
        code, out = self.run_compare(self.project)
        self.assertEqual(code, 2, out)


class TestLeftOut(unittest.TestCase):
    def test_every_entry_has_a_reason(self):
        for pattern, reason in golden.LEFT_OUT:
            with self.subTest(pattern=pattern):
                self.assertFalse(pattern.startswith("/"))
                self.assertTrue(reason.strip())

    def test_the_outputs_that_matter_are_compared(self):
        for path in ("odm_report/shots.geojson", "opensfm/stats/dsm.png"):
            self.assertTrue(golden.left_out(path), path)
        for path in ("odm_dem/dsm.tif",
                     "odm_filterpoints/point_cloud.ply",
                     "odm_georeferencing/odm_georeferenced_model.laz",
                     "odm_meshing/odm_25dmesh.ply"):
            self.assertFalse(golden.left_out(path), path)


class TestHashLists(unittest.TestCase):
    def test_lists_are_well_formed_and_leave_nothing_out(self):
        names = [n for n in os.listdir(HASHES) if n.endswith(".txt")]
        self.assertTrue(names)
        for name in names:
            with self.subTest(list=name):
                hashes = golden.read_list(os.path.join(HASHES, name))
                self.assertTrue(hashes)
                for path in hashes:
                    self.assertFalse(golden.left_out(path), path)

    def test_golden_input_pin(self):
        config = read_conf(CONF)
        self.assertRegex(config["GOLDEN_INPUT_SHA256"], r"^[0-9a-f]{64}$")
        self.assertRegex(config["GOLDEN_INPUT_URL"],
                         r"^https://github\.com/AIDroneMap/aidm-data/releases/download/golden-[^/]+/golden-input\.zip$")


# Stands in for docker. Mounts (-v host:container) are mapped to the host
# paths in every argument, and a command with --entrypoint runs on the host.
# An engine run writes FAKE_OUTPUTS ("path=content" pairs separated by
# spaces) into the project folder; a content of "{run}" becomes the number
# of the engine run.
FAKE_DOCKER = r"""#!/usr/bin/env python3
import os, subprocess, sys
args = sys.argv[1:]
with open(os.environ["FAKE_DOCKER_LOG"], "a") as f:
    f.write(" ".join(args) + "\n")
if args[:2] == ["image", "inspect"]:
    print(os.environ.get("FAKE_ARCH", "amd64"))
    sys.exit(0)
mounts, entry, i = [], None, 1
while args[i].startswith("-"):
    if args[i] == "-v":
        host, container = args[i + 1].split(":")[:2]
        mounts.append((container, host))
        i += 2
    elif args[i] == "--entrypoint":
        entry = args[i + 1]
        i += 2
    else:
        i += 1
rest = args[i + 1:]
for container, host in sorted(mounts, key=lambda m: -len(m[0])):
    rest = [a.replace(container, host) for a in rest]
if entry:
    sys.exit(subprocess.run([entry] + rest).returncode)
project = os.path.join(rest[rest.index("--project-path") + 1], rest[rest.index("--project-path") + 2])
with open(os.environ["FAKE_DOCKER_LOG"]) as f:
    run = sum(1 for line in f if "--project-path" in line)
for pair in os.environ["FAKE_OUTPUTS"].split():
    path, content = pair.split("=")
    os.makedirs(os.path.dirname(os.path.join(project, path)), exist_ok=True)
    with open(os.path.join(project, path), "w") as f:
        f.write(content.replace("{run}", str(run)))
print("engine done")
"""

GOLDEN_INPUT = {
    "images.json": b"[]",
    "opensfm/undistorted/openmvs/scene_dense_dense_filtered.ply": b"ply",
}
OUTPUTS = {
    "odm_dem/dsm.tif": "dsm",
    "odm_report/camera_mappings.npz": "shots",
}


class TestGoldenMode(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

        # The script reads the conf and the compare script next to it, so it
        # runs from a copy with the golden input already in the cache
        scripts = os.path.join(self.tmp, "scripts")
        os.makedirs(os.path.join(scripts, "golden-hashes"))
        self.script = os.path.join(scripts, "smoke-test.sh")
        shutil.copy(SMOKE, self.script)
        shutil.copy(COMPARE, scripts)

        archive = os.path.join(self.tmp, "golden-input.zip")
        with zipfile.ZipFile(archive, "w") as zf:
            for path, data in GOLDEN_INPUT.items():
                zf.writestr(path, data)
        with open(archive, "rb") as f:
            self.input_sha = sha(f.read())
        cache = os.path.join(self.tmp, "cache")
        os.makedirs(os.path.join(cache, self.input_sha))
        shutil.move(archive, os.path.join(cache, self.input_sha, "golden-input.zip"))

        with open(os.path.join(scripts, "smoke-test.conf"), "w") as f:
            f.write('CI_DATASET_URL="https://example.invalid/ci.zip"\n'
                    'CI_DATASET_SHA256="%s"\n'
                    'CI_DATASET_IMAGES="2"\n'
                    'FULL_DATASET_URL="https://example.invalid/full.zip"\n'
                    'FULL_DATASET_SHA256="%s"\n'
                    'FULL_DATASET_IMAGES="2"\n'
                    'GOLDEN_INPUT_URL="https://example.invalid/golden-input.zip"\n'
                    'GOLDEN_INPUT_SHA256="%s"\n'
                    'DEFAULT_OPTIONS="--dsm"\n'
                    'OUTPUTS="unused"\n' % ("0" * 64, "1" * 64, self.input_sha))

        # The golden input is left out of the comparison, the outputs are in
        for path in GOLDEN_INPUT:
            self.assertTrue(golden.left_out(path), path)
        self.expected = {path: sha(data.encode()) for path, data in OUTPUTS.items()}
        self.hashes = os.path.join(scripts, "golden-hashes")
        for arch in ("amd64", "arm64"):
            write_list(os.path.join(self.hashes, arch + ".txt"), self.expected)

        bin_dir = os.path.join(self.tmp, "bin")
        os.mkdir(bin_dir)
        for name, body in (("docker", FAKE_DOCKER), ("curl", "#!/bin/sh\nexit 1\n")):
            path = os.path.join(bin_dir, name)
            with open(path, "w") as f:
                f.write(body)
            os.chmod(path, 0o755)

        self.docker_log = os.path.join(self.tmp, "docker.log")
        self.report = os.path.join(self.tmp, "summary.md")
        self.env = {
            "PATH": bin_dir + os.pathsep + os.environ["PATH"],
            "HOME": self.tmp,
            "TMPDIR": self.tmp,
            "SMOKE_CACHE": cache,
            "GOLDEN_REPORT": self.report,
            "FAKE_DOCKER_LOG": self.docker_log,
            "FAKE_OUTPUTS": " ".join("%s=%s" % item for item in OUTPUTS.items()),
        }

    def run_script(self, **env):
        p = subprocess.run(["bash", self.script, "golden", "aidm-smoke:test"],
                           env=dict(self.env, **env), capture_output=True, text=True)
        return p.returncode, p.stdout + p.stderr

    def engine_calls(self):
        if not os.path.exists(self.docker_log):
            return []
        with open(self.docker_log) as f:
            return [line for line in f.read().splitlines() if "--project-path" in line]

    def read_report(self):
        with open(self.report) as f:
            return f.read()

    def test_same_outputs_pass(self):
        code, out = self.run_script()
        self.assertEqual(code, 0, out)
        engine = self.engine_calls()
        self.assertEqual(len(engine), 1)
        self.assertTrue(engine[0].endswith("--project-path /datasets project --dsm --rerun-from odm_filterpoints"), engine[0])
        self.assertIn("the 2 golden outputs in amd64.txt match", self.read_report())

    def test_changed_output_fails_with_the_report(self):
        code, out = self.run_script(FAKE_OUTPUTS="odm_dem/dsm.tif=other odm_report/camera_mappings.npz=shots")
        self.assertEqual(code, 1, out)
        self.assertIn("the outputs differ from", out)
        self.assertIn("| `odm_dem/dsm.tif` | %s | %s |" % (sha(b"dsm"), sha(b"other")), self.read_report())

    def test_missing_output_fails(self):
        code, out = self.run_script(FAKE_OUTPUTS="odm_dem/dsm.tif=dsm")
        self.assertEqual(code, 1, out)
        self.assertIn("| `odm_report/camera_mappings.npz` | %s | missing |" % sha(b"shots"), self.read_report())

    def test_list_follows_the_image_architecture(self):
        write_list(os.path.join(self.hashes, "arm64.txt"), {"odm_dem/dsm.tif": sha(b"dsm")})
        code, out = self.run_script(FAKE_ARCH="arm64")
        self.assertEqual(code, 1, out)
        self.assertIn("against arm64.txt", self.read_report())

    def test_golden_hashes_overrides_the_list(self):
        other = os.path.join(self.tmp, "gpu.txt")
        write_list(other, self.expected)
        code, out = self.run_script(GOLDEN_HASHES=other, FAKE_ARCH="arm64")
        self.assertEqual(code, 0, out)
        self.assertIn("golden outputs in gpu.txt match", self.read_report())

    def test_no_list_for_the_architecture(self):
        code, out = self.run_script(FAKE_ARCH="riscv64")
        self.assertEqual(code, 1, out)
        self.assertIn("no hash list at", out)
        self.assertEqual(self.engine_calls(), [])

    def test_refuses_smoke_options(self):
        code, out = self.run_script(SMOKE_OPTIONS="--fast-orthophoto")
        self.assertEqual(code, 1, out)
        self.assertIn("golden runs with DEFAULT_OPTIONS", out)
        self.assertEqual(self.engine_calls(), [])

    def test_wrong_golden_input_fails(self):
        conf = os.path.join(self.tmp, "scripts", "smoke-test.conf")
        with open(conf) as f:
            text = f.read().replace(self.input_sha, "2" * 64)
        with open(conf, "w") as f:
            f.write(text)
        os.makedirs(os.path.join(self.tmp, "cache", "2" * 64))
        shutil.copy(os.path.join(self.tmp, "cache", self.input_sha, "golden-input.zip"),
                    os.path.join(self.tmp, "cache", "2" * 64))
        code, out = self.run_script()
        self.assertEqual(code, 1, out)
        self.assertIn("expected %s" % ("2" * 64), out)
        self.assertEqual(self.engine_calls(), [])

    def test_diagnostic_runs_twice_and_passes_on_differences(self):
        code, out = self.run_script(GOLDEN_DIAGNOSTIC="1",
                                    FAKE_OUTPUTS="odm_dem/dsm.tif=dsm odm_report/camera_mappings.npz={run}")
        self.assertEqual(code, 0, out)
        engine = self.engine_calls()
        self.assertEqual(len(engine), 2)
        self.assertNotEqual(engine[0].split()[3], engine[1].split()[3])
        runs = out.split("SHA-256 of every file of run ")[1:]
        self.assertEqual(len(runs), 2)
        for run, text in zip(("1", "2"), runs):
            every, hash_list = text.split("Hash list of run " + run)
            # Every file, the golden input too
            self.assertIn("%s  images.json" % sha(b"[]"), every)
            self.assertIn("%s  odm_report/camera_mappings.npz" % sha(run.encode()), every)
            # The hash list leaves out the golden input
            hash_list = hash_list.split("[smoke]")[0]
            self.assertEqual(hash_list.strip().splitlines(), [
                "%s  odm_dem/dsm.tif" % sha(b"dsm"),
                "%s  odm_report/camera_mappings.npz" % sha(run.encode()),
            ])
        diff = out.split("Lines that differ between run 1 (<) and run 2 (>)")[1]
        self.assertIn("< %s  odm_report/camera_mappings.npz" % sha(b"1"), diff)
        self.assertIn("> %s  odm_report/camera_mappings.npz" % sha(b"2"), diff)
        self.assertNotIn("odm_dem/dsm.tif", diff)
        self.assertFalse(os.path.exists(self.report))


if __name__ == "__main__":
    unittest.main()
