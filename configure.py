"""Windows build script of AIDM.

`build` downloads the Windows dependencies release pinned in
SuperBuild/windows-deps.json, checks the SHA-256 of every file against the
pin, unpacks the deps export into vcpkg/ and builds the SuperBuild with MSVC
for AVX2. `clean` removes what `build` made.

The functions that read the pin and check the downloads run on any platform,
so the unit tests in tests/test_configure.py import this file on Linux.
"""

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
PIN_FILE = os.path.join(ROOT, "SuperBuild", "windows-deps.json")
DOWNLOAD_DIR = os.path.join(ROOT, "SuperBuild", "download", "windows-deps")
EXPORT_DIR = os.path.join(ROOT, "vcpkg")
EXPORT_MARKER = ".aidm-deps-export"

# The only place the Windows build downloads from. The tag and the file
# names come from the pin file; the hashes decide whether a file is used.
DEPS_RELEASE_URL = "https://github.com/AIDroneMap/aidm-windows-deps/releases/download/{tag}/{file}"

# Every asset the Windows build and package steps read from the deps release.
PINNED_ASSETS = ("export", "gdal-wheel", "python", "exiftool", "licenses")

# Prepended to every MSVC compile through the CL environment variable, the
# Windows form of the docker/gcc wrappers (ADR 0001). A project's own /arch
# flag on a file comes later on the command line and wins for that file, as
# OpenCV's per-file dispatch flags do on Linux.
MSVC_ARCH_FLAG = "/arch:AVX2"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")


class DepsError(Exception):
    """A pin that cannot be used, a download that does not match it, or a failed step."""


def load_pin(path=PIN_FILE):
    """Read the pin file and return it, or raise DepsError naming the file."""
    try:
        with open(path, encoding="utf-8") as f:
            pin = json.load(f)
    except (OSError, ValueError) as e:
        raise DepsError("%s: cannot read the pin: %s" % (path, e))

    def fail(message):
        raise DepsError("%s: %s" % (path, message))

    if not isinstance(pin, dict):
        fail("the pin must be a JSON object")
    for key in ("tag", "triplet"):
        value = pin.get(key)
        if not isinstance(value, str) or not _FILE_NAME.match(value):
            fail("'%s' must be a name of letters, digits, dots, dashes or underscores" % key)
    assets = pin.get("assets")
    if not isinstance(assets, dict):
        fail("'assets' must be an object")
    missing = [role for role in PINNED_ASSETS if role not in assets]
    if missing:
        fail("no pin for %s" % ", ".join(missing))
    unknown = sorted(set(assets) - set(PINNED_ASSETS))
    if unknown:
        fail("unknown assets %s" % ", ".join(unknown))
    for role in PINNED_ASSETS:
        asset = assets[role]
        if not isinstance(asset, dict):
            fail("asset '%s' must be an object with 'file' and 'sha256'" % role)
        name = asset.get("file")
        if not isinstance(name, str) or not _FILE_NAME.match(name):
            fail("asset '%s' needs a plain file name" % role)
        digest = asset.get("sha256")
        if not isinstance(digest, str) or not _SHA256.match(digest):
            fail("asset '%s' needs a SHA-256 of 64 lowercase hex digits" % role)
    return pin


def asset_url(pin, role):
    return DEPS_RELEASE_URL.format(tag=pin["tag"], file=pin["assets"][role]["file"])


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch_asset(url, dest, expected_sha256, opener=None):
    """Download url to dest unless dest already has the expected SHA-256.

    The download goes to dest + ".part" and becomes dest only when its hash
    matches. On a mismatch the partial file is removed and DepsError says
    which file, which hash was expected and which one came.
    """
    if opener is None:
        opener = lambda u: urllib.request.urlopen(u, timeout=60)

    if os.path.isfile(dest):
        if sha256_of(dest) == expected_sha256:
            print("Using %s, SHA-256 matches the pin" % dest)
            return dest
        print("Removing %s: its SHA-256 does not match the pin" % dest)
        os.remove(dest)

    os.makedirs(os.path.dirname(dest), exist_ok=True)
    partial = dest + ".part"
    digest = hashlib.sha256()
    print("Downloading %s" % url)
    try:
        with opener(url) as response, open(partial, "wb") as out:
            for block in iter(lambda: response.read(1 << 20), b""):
                digest.update(block)
                out.write(block)
    except BaseException:
        if os.path.exists(partial):
            os.remove(partial)
        raise

    actual = digest.hexdigest()
    if actual != expected_sha256:
        os.remove(partial)
        raise DepsError("%s: SHA-256 %s does not match the pin %s in %s"
                        % (url, actual, expected_sha256, PIN_FILE))
    os.replace(partial, dest)
    return dest


def fetch_pinned_assets(pin, download_dir=DOWNLOAD_DIR, opener=None):
    """Download every pinned asset of the deps release; return role -> path."""
    folder = os.path.join(download_dir, pin["tag"])
    paths = {}
    for role in PINNED_ASSETS:
        asset = pin["assets"][role]
        paths[role] = fetch_asset(asset_url(pin, role),
                                  os.path.join(folder, asset["file"]),
                                  asset["sha256"], opener=opener)
    return paths


def unpack_export(pin, archive, export_dir=EXPORT_DIR):
    """Unpack the deps export into export_dir, unless it holds this pin's export.

    The export zip has one top folder. A marker file in export_dir records
    the tag and hash it came from, so a new pin replaces an old export.
    """
    marker = "%s %s\n" % (pin["tag"], pin["assets"]["export"]["sha256"])
    marker_path = os.path.join(export_dir, EXPORT_MARKER)
    if os.path.isfile(marker_path):
        with open(marker_path, encoding="utf-8") as f:
            if f.read() == marker:
                print("Using the deps export in %s" % export_dir)
                return export_dir
    safe_remove(export_dir)

    # Unpack next to export_dir, not in the system temporary folder, and on
    # a short path: the export's longest file name is close to 160 characters.
    staging = export_dir + ".unpack"
    safe_remove(staging)
    print("Extracting %s --> %s" % (archive, export_dir))
    with zipfile.ZipFile(archive) as z:
        tops = {name.split("/")[0] for name in z.namelist()}
        if len(tops) != 1:
            raise DepsError("%s: expected one top folder, found %s" % (archive, ", ".join(sorted(tops))))
        z.extractall(staging)
    os.replace(os.path.join(staging, tops.pop()), export_dir)
    safe_remove(staging)

    toolchain = os.path.join(export_dir, "scripts", "buildsystems", "vcpkg.cmake")
    triplet_dir = os.path.join(export_dir, "installed", pin["triplet"])
    for required in (toolchain, triplet_dir):
        if not os.path.exists(required):
            raise DepsError("%s: the deps export has no %s" % (archive, os.path.relpath(required, export_dir)))
    with open(marker_path, "w", encoding="utf-8") as f:
        f.write(marker)
    return export_dir


def msvc_environment(env):
    """Return a copy of env whose CL variable starts with the AVX2 flag."""
    env = dict(env)
    current = env.get("CL", "").strip()
    if MSVC_ARCH_FLAG not in current.split():
        env["CL"] = (MSVC_ARCH_FLAG + " " + current).strip()
    return env


def report_toolset(env, runner=subprocess.run):
    """Print the Visual Studio and MSVC versions of the developer shell."""
    # On Windows os.environ, and so a copy of it, holds names in upper case.
    by_name = {key.upper(): value for key, value in env.items()}
    for name in ("VSCMD_VER", "VisualStudioVersion", "VCToolsVersion"):
        print("%s=%s" % (name, by_name.get(name.upper(), "(not set)")))
    try:
        banner = runner(["cl"], env=env, capture_output=True, text=True).stderr
        print(banner.strip().splitlines()[0] if banner.strip() else "cl printed no version")
    except OSError as e:
        print("Cannot run cl: %s" % e)


def run(cmd, cwd=ROOT, env=None):
    print("Running %s" % cmd)
    result = subprocess.run(cmd, shell=True, cwd=cwd, env=env)
    if result.returncode != 0:
        raise DepsError("%s returned %s" % (cmd, result.returncode))


def _remove_read_only(func, path, _error):
    # Git and the export leave read-only files that Windows will not delete.
    os.chmod(path, stat.S_IWRITE)
    func(path)


def safe_remove(path):
    if os.path.isdir(path) and not os.path.islink(path):
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=_remove_read_only)
        else:
            shutil.rmtree(path, onerror=_remove_read_only)
    elif os.path.lexists(path):
        os.remove(path)


def build():
    pin = load_pin()
    assets = fetch_pinned_assets(pin)
    unpack_export(pin, assets["export"])

    env = msvc_environment(os.environ)
    print("CL=%s" % env["CL"])
    report_toolset(env)

    # Create python virtual env
    venv = os.path.join(ROOT, "venv")
    if not os.path.isdir(venv):
        print("Creating virtual env --> venv/")
        from venv import EnvBuilder
        EnvBuilder(with_pip=True).create(venv)

    run("venv\\Scripts\\pip install setuptools", env=env)
    run("venv\\Scripts\\pip install --ignore-installed -r requirements.txt", env=env)

    build_dir = os.path.join(ROOT, "SuperBuild", "build")
    if not os.path.exists(build_dir) or not os.path.exists(os.path.join(ROOT, "SuperBuild", "install")):
        print("Compiling SuperBuild")
        os.makedirs(build_dir, exist_ok=True)
        run("cmake ..", cwd=build_dir, env=env)
        run("cmake --build . --config Release -j%d" % os.cpu_count(), cwd=build_dir, env=env)


def clean():
    safe_remove(EXPORT_DIR)
    safe_remove(EXPORT_DIR + ".unpack")
    safe_remove(os.path.join(ROOT, "venv"))
    safe_remove(os.path.join(ROOT, "SuperBuild", "build"))
    safe_remove(os.path.join(ROOT, "SuperBuild", "download"))
    safe_remove(os.path.join(ROOT, "SuperBuild", "src"))
    safe_remove(os.path.join(ROOT, "SuperBuild", "install"))


def main(argv=None):
    parser = argparse.ArgumentParser(description="AIDM Windows build script")
    parser.add_argument("action", type=str, choices=["build", "clean"],
                        help="Action: %(choices)s")
    args = parser.parse_args(argv)
    # Keep this script's lines in order with the output of the commands it
    # runs when both go to one log file.
    sys.stdout.reconfigure(line_buffering=True)

    if sys.platform != "win32":
        print("This script is for Windows only. Use configure.sh instead.")
        return 1
    if sys.version_info.major != 3 or sys.version_info.minor != 12:
        print("You need to use Python 3.12.x (due to the requirements.txt). You are using %s instead."
              % platform.python_version())
        return 1

    try:
        if args.action == "build":
            build()
        else:
            clean()
    except DepsError as e:
        print("Error: %s" % e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
