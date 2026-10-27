#!/usr/bin/env python3
#
# Georeferencing check for the smoke tests.
#
# Passes when the orthophoto of a project has a projected coordinate system
# and its bounds contain the GPS position of every image in the project's
# images folder, read from the image EXIF. It does not check accuracy: a
# position inside the bounds only shows that the orthophoto is in the right
# place on Earth.
#
# Used by scripts/smoke-test.sh and scripts/gpu-smoke-modal.py. It runs inside
# the image under test, with the rasterio, pyproj and exifread packages that
# the engine already ships, and takes a few seconds.
#
# Usage:
#   check-georeferencing.py <project folder>
#
# Prints one line per problem and exits 1 if there is any.

import os
import sys

ORTHOPHOTO = os.path.join("odm_orthophoto", "odm_orthophoto.tif")
IMAGES = "images"

# Show at most this many positions that fall outside the bounds
MAX_LISTED = 5


def log(msg):
    print("[georef] %s" % msg, flush=True)


def to_degrees(values, ref):
    """Turn EXIF degrees, minutes and seconds into signed decimal degrees."""
    degrees = 0.0
    for value, scale in zip(values, (1, 60, 3600)):
        degrees += float(value.num) / float(value.den) / scale
    if str(ref).strip().upper() in ("S", "W"):
        degrees = -degrees
    return degrees


def read_position(path):
    """Return (longitude, latitude) from the EXIF of an image, or None."""
    import exifread

    with open(path, "rb") as f:
        tags = exifread.process_file(f, details=False)
    try:
        lat = to_degrees(tags["GPS GPSLatitude"].values, tags["GPS GPSLatitudeRef"].values)
        lon = to_degrees(tags["GPS GPSLongitude"].values, tags["GPS GPSLongitudeRef"].values)
    except (KeyError, ZeroDivisionError):
        return None
    return lon, lat


def outside(bounds, x, y):
    """Return how far (x, y) lies outside bounds, in CRS units; 0 if inside."""
    dx = max(bounds.left - x, 0.0, x - bounds.right)
    dy = max(bounds.bottom - y, 0.0, y - bounds.top)
    return (dx * dx + dy * dy) ** 0.5


def check(project):
    """Return (problems, summary) for the project folder.

    problems is a list of reasons why the check fails, empty when it passes.
    """
    import pyproj
    import rasterio

    orthophoto = os.path.join(project, ORTHOPHOTO)
    images = os.path.join(project, IMAGES)

    with rasterio.open(orthophoto) as src:
        crs = src.crs
        bounds = src.bounds
    if crs is None:
        return ["%s has no coordinate system" % ORTHOPHOTO], ""
    if not crs.is_projected:
        return ["%s has coordinate system %s, which is not projected" % (ORTHOPHOTO, crs)], ""

    to_crs = pyproj.Transformer.from_crs("EPSG:4326", crs.to_wkt(), always_xy=True)

    names = sorted(n for n in os.listdir(images) if n.lower().endswith((".jpg", ".jpeg")))
    if not names:
        return ["no JPEG images in %s" % images], ""

    problems = []
    missing = []
    far = []
    for name in names:
        position = read_position(os.path.join(images, name))
        if position is None:
            missing.append(name)
            continue
        x, y = to_crs.transform(*position)
        distance = outside(bounds, x, y)
        if distance > 0:
            far.append((distance, name, position))

    if missing:
        problems.append("%d of %d images have no GPS position in their EXIF: %s"
                        % (len(missing), len(names), ", ".join(missing[:MAX_LISTED])))
    if far:
        far.sort(reverse=True)
        units = crs.linear_units or "units"
        listed = ", ".join("%s (lat %.6f, lon %.6f, %.1f %s outside)" % (n, p[1], p[0], d, units)
                           for d, n, p in far[:MAX_LISTED])
        problems.append("%d of %d image positions are outside the bounds of %s: %s"
                        % (len(far), len(names), ORTHOPHOTO, listed))

    summary = "%s is in %s and its bounds contain all %d image positions" % (
        ORTHOPHOTO, crs.to_string(), len(names))
    return problems, summary


def main():
    if len(sys.argv) != 2:
        print("Usage: %s <project folder>" % sys.argv[0], file=sys.stderr)
        return 2
    try:
        problems, summary = check(sys.argv[1])
    except Exception as e:
        problems, summary = ["cannot check georeferencing: %s" % e], ""
    if problems:
        for p in problems:
            log("FAIL: %s" % p)
        return 1
    log("ok    %s" % summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
