#!/usr/bin/env python3
#
# Golden comparison: compares the files of a project folder with a hash list
# (docs/adr/0004-golden-outputs-start-from-the-stored-dense-point-cloud.md).
#
# scripts/smoke-test.sh golden runs the engine from the golden input, from
# odm_filterpoints to the end, and then this script on the project folder.
# It runs inside the image under test, so it reads every file the engine
# wrote whatever their owner and mode.
#
# Usage:
#   golden-compare.py <project folder> <hash list>
#   golden-compare.py --list <project folder>
#
# The hash list has one line per golden output, "<sha256>  <path>" as
# sha256sum writes it, with the path relative to the project folder. Lines
# that start with # are comments. --list prints the hash list of a folder,
# which is how the lists in scripts/golden-hashes/ are written.
#
# Every file of the folder is compared, except the files LEFT_OUT names
# below, each with the reason. A file is different when its SHA-256 is not
# the one in the list, missing when the list names it and the folder does
# not have it, and extra when the folder has it and the list does not name
# it. The report is a Markdown table of the differences (file, expected
# SHA-256, actual SHA-256), so it reads the same in a log and in a job
# summary.
#
# One tolerance: a LAS or LAZ file is hashed with its creation day and year
# (header bytes 90 to 93) set to zero, because the writer puts the day of
# the run there. Every other byte counts.
#
# Exits 0 when every file matches, 1 on any difference, 2 when the folder or
# the list cannot be read.

import fnmatch
import hashlib
import os
import sys

INPUT = ("Golden input that no stage after openmvs writes. The SHA-256 of the "
         "golden input already checks it.")
NOT_STABLE = "Not byte-stable between two runs from the same golden input"

# Files left out of the comparison, and why, as (pattern, reason). A pattern
# ending in / leaves out a folder with everything in it; any other pattern is
# matched against the whole path, with * and ? as in a shell. Found by the
# diagnostic of #82: two runs from the same golden input on a scratch
# machine, and two runs in each build job of release dry run 41016730652.
# The runners and the scratch machine found the same outputs not stable.
# Release dry runs 41030223058 and 41045655132 ran on other CPU models and
# found two more.
LEFT_OUT = [
    ("images.json", INPUT),
    ("cameras.json", INPUT),
    ("odm_georeferencing/coords.txt", INPUT),
    ("odm_georeferencing/odm_georeferencing_model_geo.txt", INPUT),
    ("odm_georeferencing/proj.txt", INPUT),
    ("opensfm/camera_mappings.npz", INPUT),
    ("opensfm/config.yaml", INPUT),
    ("opensfm/image_list.txt", INPUT),
    ("opensfm/reconstruction.json", INPUT),
    ("opensfm/reconstruction.topocentric.json", INPUT),
    ("opensfm/tracks.csv", INPUT),
    ("opensfm/updated_config.txt", INPUT),
    ("opensfm/undistorted/", INPUT),
    ("opensfm/stats/stats.json", INPUT),
    ("opensfm/stats/matchgraph.json", INPUT),
    ("opensfm/stats/matchgraph.png", INPUT),
    ("opensfm/stats/residual_histogram.png", INPUT),
    ("opensfm/stats/topview.png", INPUT),
    ("opensfm/stats/heatmap_v2 *", INPUT),
    ("opensfm/stats/residuals_v2 *", INPUT),
    ("benchmark.txt", NOT_STABLE + ": stage times."),
    ("log.json", NOT_STABLE + ": start and end times."),
    ("options.json", "Records max_concurrency, which defaults to the CPU count of the machine."),
    ("odm_meshing/odm_mesh.ply", NOT_STABLE + ": the 3D mesh has a different number of vertices."),
    ("odm_meshing/odm_mesh.mvs", NOT_STABLE + ": the scene of the 3D mesh."),
    ("odm_texturing/", NOT_STABLE + ": textured from the 3D mesh."),
    ("odm_texturing_25d/", NOT_STABLE + ": the 2.5D mesh is stable, but texturing picks "
     "other texture patches and packs another atlas."),
    ("odm_orthophoto/odm_orthophoto.tif", NOT_STABLE + ": rendered from the textured 2.5D model."),
    ("odm_orthophoto/odm_orthophoto.original.tif", NOT_STABLE + ": rendered from the textured 2.5D model."),
    ("odm_orthophoto/odm_orthophoto_log.txt", NOT_STABLE + ": GDAL_CACHEMAX follows the free memory."),
    ("opensfm/stats/ortho.png", NOT_STABLE + ": a preview of the orthophoto."),
    ("opensfm/stats/dsm.png", "A rendered preview of the DSM whose bytes depend on the runner's CPU: "
     "equal on two Intel Xeon runners, different on an AMD EPYC runner. odm_dem/dsm.tif, the DSM it "
     "is drawn from, is compared."),
    ("odm_report/shots.geojson", "The camera positions export, whose bytes depend on the runner's CPU: "
     "equal on Intel Xeon and newer AMD EPYC runners, different on an AMD EPYC 7763 runner "
     "(run 41045655132)."),
    ("odm_dem/dsm.original.tif", NOT_STABLE + ": same pixels and tags in both runs, but the "
     "bytes are laid out in another order. odm_dem/dsm.tif, cropped from it, is compared."),
    ("odm_georeferencing/odm_georeferenced_model.bounds.gpkg", NOT_STABLE + ": the GeoPackage "
     "records the time it was written. The same bounds are compared in the .geojson."),
    ("odm_georeferencing/odm_georeferenced_model.info.json", NOT_STABLE + ": records the time it was written."),
    ("odm_georeferencing/odm_georeferenced_model.summary.json", NOT_STABLE + ": records the time it was written."),
    ("odm_report/report.pdf", NOT_STABLE + ": shows the orthophoto, the processing time and the date."),
    ("odm_report/stats.json", NOT_STABLE + ": records the processing time and the time it was written."),
]

# Header bytes of a LAS or LAZ file that hold its creation day of the year
# and year
LAS_DATE = slice(90, 94)


def log(msg):
    print(msg, flush=True)


def left_out(path):
    for pattern, _reason in LEFT_OUT:
        if pattern.endswith("/"):
            if path.startswith(pattern):
                return True
        elif fnmatch.fnmatchcase(path, pattern):
            return True
    return False


def sha256(path):
    """SHA-256 of a file; of a LAS or LAZ file with its creation date set
    to zero."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        if path.lower().endswith((".las", ".laz")):
            header = bytearray(f.read(LAS_DATE.stop))
            if header[:4] == b"LASF" and len(header) == LAS_DATE.stop:
                header[LAS_DATE] = bytes(LAS_DATE.stop - LAS_DATE.start)
            h.update(header)
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def hash_folder(root):
    """SHA-256 of every file under root that is not left out, by path
    relative to root with / as separator."""
    hashes = {}
    for folder, dirs, files in os.walk(root):
        dirs.sort()
        for name in sorted(files):
            full = os.path.join(folder, name)
            path = os.path.relpath(full, root).replace(os.sep, "/")
            if not left_out(path):
                hashes[path] = sha256(full)
    return hashes


def read_list(path):
    """Read a hash list into {path: sha256}. Raises ValueError on a line
    that is not "<sha256>  <path>" or on a path named twice."""
    hashes = {}
    with open(path) as f:
        for number, line in enumerate(f, 1):
            line = line.rstrip("\n")
            if not line.strip() or line.startswith("#"):
                continue
            digest, sep, name = line.partition("  ")
            if not sep or len(digest) != 64 or not name \
                    or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("%s line %d is not '<sha256>  <path>': %s" % (path, number, line))
            if name in hashes:
                raise ValueError("%s line %d names %s a second time" % (path, number, name))
            hashes[name] = digest
    return hashes


def compare(expected, actual):
    """Rows (path, expected, actual) for every difference, sorted by path.
    A missing file has actual "missing"; an extra file has expected
    "not in the list"."""
    rows = []
    for path in sorted(set(expected) | set(actual)):
        want = expected.get(path, "not in the list")
        got = actual.get(path, "missing")
        if want != got:
            rows.append((path, want, got))
    return rows


def report(rows, expected, list_name):
    if not rows:
        return ["Golden comparison: the %d golden outputs in %s match." % (len(expected), list_name)]
    differ = "1 file differs" if len(rows) == 1 else "%d files differ" % len(rows)
    lines = ["Golden comparison against %s, %d golden outputs: %s." % (list_name, len(expected), differ),
             "",
             "| File | Expected SHA-256 | Actual SHA-256 |",
             "| --- | --- | --- |"]
    for path, want, got in rows:
        lines.append("| `%s` | %s | %s |" % (path, want, got))
    return lines


def main(argv):
    if len(argv) == 3 and argv[1] == "--list":
        if not os.path.isdir(argv[2]):
            log("Golden comparison: %s is not a folder" % argv[2])
            return 2
        for path, digest in hash_folder(argv[2]).items():
            log("%s  %s" % (digest, path))
        return 0
    if len(argv) != 3:
        log("Usage: golden-compare.py <project folder> <hash list>")
        log("       golden-compare.py --list <project folder>")
        return 2
    folder, list_path = argv[1], argv[2]
    if not os.path.isdir(folder):
        log("Golden comparison: %s is not a folder" % folder)
        return 2
    try:
        expected = read_list(list_path)
        actual = hash_folder(folder)
    except (OSError, ValueError) as e:
        log("Golden comparison: %s" % e)
        return 2
    rows = compare(expected, actual)
    for line in report(rows, expected, os.path.basename(list_path)):
        log(line)
    return 1 if rows else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
