import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "smoke-test.sh")
CONF = os.path.join(ROOT, "scripts", "smoke-test.conf")

DENSE_CLOUD = "opensfm/undistorted/openmvs/scene_dense_dense_filtered.ply"

# What the engine leaves in the project folder after openmvs, by whether the
# golden input keeps it
KEPT = [
    "images.json",
    "cameras.json",
    "odm_georeferencing/coords.txt",
    "odm_georeferencing/odm_georeferencing_model_geo.txt",
    "odm_georeferencing/proj.txt",
    "opensfm/camera_mappings.npz",
    "opensfm/config.yaml",
    "opensfm/image_list.txt",
    "opensfm/reconstruction.json",
    "opensfm/reconstruction.topocentric.json",
    "opensfm/tracks.csv",
    "opensfm/updated_config.txt",
    "opensfm/stats/stats.json",
    "opensfm/stats/topview.png",
    "opensfm/undistorted/nominal_done.txt",
    "opensfm/undistorted/reconstruction.nvm",
    "opensfm/undistorted/images/a.jpg.tif",
    "opensfm/undistorted/images/b.jpg.tif",
    DENSE_CLOUD,
]
EMPTIED = [
    "opensfm/exif/a.jpg.exif",
    "opensfm/features/a.jpg.features.npz",
    "opensfm/matches/a.jpg_matches.pkl.gz",
]
DROPPED = [
    "benchmark.txt",
    "img_list.txt",
    "log.json",
    "options.json",
    "images/a.jpg",
    "opensfm/camera_models.json",
    "opensfm/reference_lla.json",
    "opensfm/reports/features.json",
    "opensfm/undistorted/reconstruction.json",
    "opensfm/undistorted/tracks.csv",
    "opensfm/undistorted/openmvs/scene.mvs",
    "opensfm/undistorted/openmvs/scene_dense.mvs",
    "opensfm/undistorted/openmvs/scene_dense.ply",
    "opensfm/undistorted/openmvs/scene_dense_dense_filtered.mvs",
    "opensfm/undistorted/openmvs/depthmaps/depth0000.dmap",
]

# Stands in for docker. An engine run (a -v mount on /datasets) writes the
# files named in FAKE_PROJECT_FILES into the project folder.
FAKE_DOCKER = r"""#!/usr/bin/env bash
echo "$*" >> "$FAKE_DOCKER_LOG"
work="" prev=""
for a in "$@"; do
    if [ "$prev" = "-v" ] && [[ "$a" == *:/datasets ]]; then
        work="${a%:/datasets}"
    fi
    prev="$a"
done
[ -n "$work" ] || exit 0
for f in $FAKE_PROJECT_FILES; do
    mkdir -p "$(dirname "$work/project/$f")"
    echo "$f" > "$work/project/$f"
done
echo "engine done"
"""


def read_conf(path):
    config = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                key, _, value = line.partition("=")
                config[key] = value.strip('"')
    return config


class TestGoldenInput(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

        # The script reads the conf next to it, so it runs from a copy with
        # a two-image dataset already in the download cache
        scripts = os.path.join(self.tmp, "scripts")
        os.mkdir(scripts)
        self.script = os.path.join(scripts, "smoke-test.sh")
        shutil.copy(SCRIPT, self.script)

        dataset = os.path.join(self.tmp, "fake-v1.zip")
        with zipfile.ZipFile(dataset, "w") as zf:
            zf.writestr("a.jpg", "a")
            zf.writestr("b.jpg", "b")
        with open(dataset, "rb") as f:
            self.dataset_sha = hashlib.sha256(f.read()).hexdigest()
        cache = os.path.join(self.tmp, "cache")
        os.makedirs(os.path.join(cache, self.dataset_sha))
        shutil.move(dataset, os.path.join(cache, self.dataset_sha, "fake-v1.zip"))

        with open(os.path.join(scripts, "smoke-test.conf"), "w") as f:
            f.write('CI_DATASET_URL="https://example.invalid/ci.zip"\n'
                    'CI_DATASET_SHA256="%s"\n'
                    'CI_DATASET_IMAGES="2"\n'
                    'FULL_DATASET_URL="https://example.invalid/fake-v1.zip"\n'
                    'FULL_DATASET_SHA256="%s"\n'
                    'FULL_DATASET_IMAGES="2"\n'
                    'FULL_DATASET_TAG="v1"\n'
                    'FULL_DATASET_COMMIT="%s"\n'
                    'DEFAULT_OPTIONS="--dsm"\n'
                    'OUTPUTS="unused"\n' % ("0" * 64, self.dataset_sha, "c" * 40))

        bin_dir = os.path.join(self.tmp, "bin")
        os.mkdir(bin_dir)
        for name, body in (("docker", FAKE_DOCKER), ("curl", "#!/bin/sh\nexit 1\n")):
            path = os.path.join(bin_dir, name)
            with open(path, "w") as f:
                f.write(body)
            os.chmod(path, 0o755)

        self.out = os.path.join(self.tmp, "out")
        self.docker_log = os.path.join(self.tmp, "docker.log")
        self.env = {
            "PATH": bin_dir + os.pathsep + os.environ["PATH"],
            "HOME": self.tmp,
            "TMPDIR": self.tmp,
            "SMOKE_CACHE": cache,
            "GOLDEN_OUT": self.out,
            "GOLDEN_COMMIT": "e" * 40,
            "GOLDEN_IMAGE": "ghcr.io/aidronemap/aidm-ci@sha256:" + "d" * 64,
            "FAKE_DOCKER_LOG": self.docker_log,
            "FAKE_PROJECT_FILES": " ".join(KEPT + EMPTIED + DROPPED),
        }

    def run_script(self, **env):
        p = subprocess.run(["bash", self.script, "golden-input", "aidm-smoke:test"],
                           env=dict(self.env, **env), capture_output=True, text=True)
        return p.returncode, p.stdout + p.stderr

    def docker_calls(self):
        if not os.path.exists(self.docker_log):
            return []
        with open(self.docker_log) as f:
            return f.read().splitlines()

    def test_packs_the_project_folder_up_to_openmvs(self):
        code, out = self.run_script()
        self.assertEqual(code, 0, out)

        engine = [c for c in self.docker_calls() if "--project-path" in c]
        self.assertEqual(len(engine), 1)
        self.assertTrue(engine[0].endswith("--dsm --end-with openmvs"), engine[0])

        archive = os.path.join(self.out, "golden-input.zip")
        with zipfile.ZipFile(archive) as zf:
            names = zf.namelist()
        self.assertEqual(sorted(n for n in names if not n.endswith("/")), sorted(KEPT))
        for folder in ("opensfm/exif/", "opensfm/features/", "opensfm/matches/"):
            self.assertIn(folder, names)

        with open(archive, "rb") as f:
            sha = hashlib.sha256(f.read()).hexdigest()
        with open(os.path.join(self.out, "golden-input.zip.sha256")) as f:
            self.assertEqual(f.read().split(), [sha, "golden-input.zip"])

        manifest = read_conf(os.path.join(self.out, "golden-input.manifest"))
        self.assertEqual(manifest, {
            "ENGINE_COMMIT": "e" * 40,
            "IMAGE": "ghcr.io/aidronemap/aidm-ci@sha256:" + "d" * 64,
            "DATASET_URL": "https://example.invalid/fake-v1.zip",
            "DATASET_SHA256": self.dataset_sha,
            "DATASET_TAG": "v1",
            "DATASET_COMMIT": "c" * 40,
            "OPTIONS": "--dsm --end-with openmvs",
            "ARCHIVE_SHA256": sha,
        })

    def test_refuses_the_ci_set(self):
        code, out = self.run_script(SMOKE_DATASET="ci")
        self.assertEqual(code, 1, out)
        self.assertIn("golden-input runs the full set", out)
        self.assertEqual(self.docker_calls(), [])

    def test_needs_the_commit_and_the_image(self):
        for name in ("GOLDEN_COMMIT", "GOLDEN_IMAGE"):
            with self.subTest(missing=name):
                code, out = self.run_script(**{name: ""})
                self.assertEqual(code, 1, out)
                self.assertIn("golden-input needs %s" % name, out)
                self.assertEqual(self.docker_calls(), [])

    def test_fails_without_the_dense_point_cloud(self):
        files = [f for f in KEPT + EMPTIED + DROPPED if f != DENSE_CLOUD]
        code, out = self.run_script(FAKE_PROJECT_FILES=" ".join(files))
        self.assertEqual(code, 1, out)
        self.assertIn("openmvs wrote no dense point cloud", out)
        self.assertFalse(os.path.exists(os.path.join(self.out, "golden-input.zip")))

    def test_fails_when_a_kept_path_is_missing(self):
        files = [f for f in KEPT + EMPTIED + DROPPED if f != "cameras.json"]
        code, out = self.run_script(FAKE_PROJECT_FILES=" ".join(files))
        self.assertEqual(code, 1, out)
        self.assertIn("cameras.json is not in the project folder", out)
        self.assertFalse(os.path.exists(os.path.join(self.out, "golden-input.manifest")))


class TestFullDatasetPin(unittest.TestCase):
    def test_tag_and_commit_name_the_pinned_url(self):
        config = read_conf(CONF)
        self.assertRegex(config["FULL_DATASET_TAG"], r"^v\d+$")
        self.assertRegex(config["FULL_DATASET_COMMIT"], r"^[0-9a-f]{40}$")
        self.assertIn("/resolve/%s/" % config["FULL_DATASET_COMMIT"], config["FULL_DATASET_URL"])
        self.assertTrue(re.search(r"-%s\.zip$" % config["FULL_DATASET_TAG"], config["FULL_DATASET_URL"]))


if __name__ == "__main__":
    unittest.main()
