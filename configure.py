"""Windows build script of AIDM.

`build` downloads the Windows dependencies release pinned in
SuperBuild/windows-deps.json, checks the SHA-256 of every file against the
pin, unpacks the deps export into vcpkg/, lays out the package's Python in
python/, installs the CMake pinned in SuperBuild/windows-cmake.txt and
builds the SuperBuild with that CMake and MSVC for AVX2. `clean` removes
what `build` made.

The package's Python is the embeddable package of the deps release. Its
._pth file is edited to import site and to list Lib\\site-packages, and the
wheels of requirements.txt are installed there with the Python that runs
this script, of the same version and architecture. No virtual environment.

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
import struct
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
PIN_FILE = os.path.join(ROOT, "SuperBuild", "windows-deps.json")
DOWNLOAD_DIR = os.path.join(ROOT, "SuperBuild", "download", "windows-deps")
EXPORT_DIR = os.path.join(ROOT, "vcpkg")
EXPORT_MARKER = ".aidm-deps-export"
CMAKE_PIN_FILE = os.path.join(ROOT, "SuperBuild", "windows-cmake.txt")
CMAKE_DIR = os.path.join(ROOT, "SuperBuild", "download", "cmake")

# The oldest CMake with a generator for Visual Studio 18 2026, the Visual
# Studio of the hosted Windows runner.
CMAKE_MINIMUM = (4, 2)

# CMake 4 refuses a project whose cmake_minimum_required is below 3.5, and
# some tools of the SuperBuild declare one. CMake reads this environment
# variable each time it creates a build tree, and every external project of
# the SuperBuild has its own, so those tools configure with their
# CMakeLists.txt unchanged. Only the Windows build sets it: configure.py runs
# on Windows only.
CMAKE_POLICY_VERSION_MINIMUM = "3.5"

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

# The package's own Python: the embeddable package of the deps release with
# the wheels of requirements.txt in its Lib\site-packages.
PYTHON_DIR = os.path.join(ROOT, "python")
PYTHON_MARKER = ".aidm-python"
SITE_PACKAGES = os.path.join("Lib", "site-packages")
REQUIREMENTS_FILE = os.path.join(ROOT, "requirements.txt")
# The lines the ._pth file needs, in the spelling of its publisher.
PTH_SITE_PACKAGES = "Lib\\site-packages"
PTH_IMPORT_SITE = "import site"

# Pins of requirements.txt published on PyPI as source only, each with the
# SHA-256 of its sdist. Every other pin must come as a wheel.
SOURCE_PINS_FILE = os.path.join(ROOT, "SuperBuild", "windows-python-source.txt")

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
# One pip requirement: cmake at an exact version, then one or more hashes.
_CMAKE_REQUIREMENT = re.compile(r"^cmake==(\d+)\.(\d+)\.(\d+)((?:\.\d+)?)(?:\s+--hash=sha256:[0-9a-f]{64})+$")
# The file name of the Python embeddable package for 64-bit x86.
_EMBEDDABLE_PYTHON = re.compile(r"^python-(\d+\.\d+\.\d+)-embed-amd64\.zip$")
# One pin of requirements.txt: a name, optional extras, one exact version,
# and optionally one sys_platform marker.
_REQUIREMENT = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[A-Za-z0-9,._-]+\])?==([A-Za-z0-9.+!-]+)"
                          r"\s*(?:;\s*sys_platform\s*==\s*['\"]([a-z0-9]+)['\"])?$")
# One source pin: a name, one exact version, then one or more hashes.
_SOURCE_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9.+!-]+)(?:\s+--hash=sha256:[0-9a-f]{64})+$")


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


def load_cmake_pin(path=CMAKE_PIN_FILE):
    """Return the CMake version pinned in path, or raise DepsError naming the file.

    The pin is a pip requirements file with one line: cmake==X.Y.Z and the
    SHA-256 of each wheel pip may install. pip reads the hashes itself.
    """
    try:
        with open(path, encoding="utf-8") as f:
            lines = [line.strip() for line in f]
    except OSError as e:
        raise DepsError("%s: cannot read the CMake pin: %s" % (path, e))
    requirements = [line for line in lines if line and not line.startswith("#")]
    if len(requirements) != 1:
        raise DepsError("%s: expected one requirement line, found %d" % (path, len(requirements)))
    match = _CMAKE_REQUIREMENT.match(requirements[0])
    if not match:
        raise DepsError("%s: the line must be cmake==X.Y.Z followed by --hash=sha256: "
                        "and 64 lowercase hex digits" % path)
    major, minor = int(match.group(1)), int(match.group(2))
    if (major, minor) < CMAKE_MINIMUM:
        raise DepsError("%s: CMake %d.%d has no generator for the Visual Studio of the hosted runner. "
                        "Pin %d.%d or later." % ((path, major, minor) + CMAKE_MINIMUM))
    return "%s.%s.%s%s" % match.groups()


def cmake_executable(folder):
    """The cmake program of the PyPI wheel installed into folder."""
    name = "cmake.exe" if sys.platform == "win32" else "cmake"
    return os.path.join(folder, "cmake", "data", "bin", name)


def install_cmake(pin_file=CMAKE_PIN_FILE, cmake_dir=CMAKE_DIR, python=None, runner=subprocess.run):
    """Install the pinned CMake from PyPI and return the path of its cmake program.

    pip runs with --require-hashes, so a wheel whose SHA-256 is not in the
    pin stops the build. Each build installs into a fresh folder, so a CMake
    left by an earlier build is never used unchecked.
    """
    version = load_cmake_pin(pin_file)
    target = os.path.join(cmake_dir, version)
    safe_remove(target)
    cmd = [python or sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
           "--require-hashes", "--only-binary", ":all:", "--no-deps",
           "--target", target, "-r", pin_file]
    print("Installing CMake %s from PyPI, pinned in %s" % (version, pin_file))
    result = runner(cmd)
    cmake = cmake_executable(target)
    if result.returncode != 0:
        safe_remove(target)
        raise DepsError("%s: pip did not install CMake %s with a pinned SHA-256, exit code %s"
                        % (pin_file, version, result.returncode))
    if not os.path.isfile(cmake):
        raise DepsError("%s: the CMake wheel has no %s" % (pin_file, os.path.relpath(cmake, target)))
    return cmake


def cmake_version(cmake, runner=subprocess.run):
    """Return the version cmake --version prints, or raise DepsError."""
    result = runner([cmake, "--version"], capture_output=True, text=True)
    first = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    match = re.match(r"^cmake version (\S+)$", first)
    if result.returncode != 0 or not match:
        raise DepsError("%s --version did not print a version: %r" % (cmake, first))
    return match.group(1)


def default_generator(cmake, env, runner=subprocess.run):
    """Return the generator cmake picks when none is given, or raise DepsError.

    cmake --help marks it with a star in its list of generators. The
    SuperBuild needs a Visual Studio generator: its PoissonRecon step runs
    MSBuild on a Visual Studio project.
    """
    result = runner([cmake, "--help"], env=env, capture_output=True, text=True)
    starred = [line[1:].split("=")[0].strip() for line in result.stdout.splitlines() if line.startswith("*")]
    if result.returncode != 0 or len(starred) != 1:
        raise DepsError("%s --help did not name a default generator" % cmake)
    generator = starred[0]
    if not generator.startswith("Visual Studio "):
        raise DepsError("%s would use the generator %s. The SuperBuild needs a Visual Studio generator: "
                        "run configure.py from a Visual Studio developer shell, with a Visual Studio "
                        "that this CMake knows." % (cmake, generator))
    return generator


def superbuild_environment(env, cmake, python=None, site_packages=None):
    """Return a copy of env for the SuperBuild: the AVX2 flag, the policy
    minimum, the pinned cmake first on PATH for any step that calls cmake
    by name, and in AIDM_PYTHON_EXECUTABLE the Python that tools compile
    against. The embeddable package has no headers and no import library,
    so they use the Python that runs this script, of the same version.

    That Python has none of the wheels of the package. PYTHONPATH gives it
    the package's site-packages, so a build step that imports numpy, as
    OpenCV's Python detection does, finds the one the package ships.
    """
    env = msvc_environment(env)
    env["CMAKE_POLICY_VERSION_MINIMUM"] = CMAKE_POLICY_VERSION_MINIMUM
    if python:
        env["AIDM_PYTHON_EXECUTABLE"] = python
    if site_packages:
        for key in [key for key in env if key.upper() == "PYTHONPATH"]:
            del env[key]
        env["PYTHONPATH"] = site_packages
    # A Windows environment copied into a dict may spell it Path.
    path_key = next((key for key in env if key.upper() == "PATH"), "PATH")
    current = env.get(path_key, "")
    env[path_key] = os.path.dirname(cmake) + (os.pathsep + current if current else "")
    return env


def build_superbuild(cmake, pinned, build_dir, env, runner=subprocess.run):
    """Print the CMake the SuperBuild uses, then configure and build it with that CMake.

    pinned is the version of the CMake pin. A wheel version may carry a
    fourth number for a rebuild of the same CMake, which cmake does not print.
    """
    version = cmake_version(cmake, runner=runner)
    if version != ".".join(pinned.split(".")[:3]):
        raise DepsError("%s reports version %s, but %s pins %s" % (cmake, version, CMAKE_PIN_FILE, pinned))
    generator = default_generator(cmake, env, runner=runner)
    print("CMake: %s" % cmake)
    print("CMake version: %s" % version)
    print("CMake generator: %s" % generator)
    print("CMAKE_POLICY_VERSION_MINIMUM=%s" % env.get("CMAKE_POLICY_VERSION_MINIMUM", "(not set)"))

    print("Compiling SuperBuild")
    os.makedirs(build_dir, exist_ok=True)
    for cmd in ([cmake, ".."],
                [cmake, "--build", ".", "--config", "Release", "-j%d" % os.cpu_count()]):
        print("Running %s" % subprocess.list2cmdline(cmd))
        result = runner(cmd, cwd=build_dir, env=env)
        if result.returncode != 0:
            raise DepsError("%s returned %s" % (subprocess.list2cmdline(cmd), result.returncode))


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


def embeddable_python_version(pin):
    """Return the Python version of the pinned embeddable package, from its file name."""
    name = pin["assets"]["python"]["file"]
    match = _EMBEDDABLE_PYTHON.match(name)
    if not match:
        raise DepsError("%s: the python asset %s is not named python-X.Y.Z-embed-amd64.zip"
                        % (PIN_FILE, name))
    return match.group(1)


def check_build_python(pin, version=None, machine=None, pointer_bits=None):
    """Stop unless this script runs on the Python version and architecture of
    the embeddable package: the wheels it installs are chosen for its own."""
    expected = embeddable_python_version(pin)
    version = version or platform.python_version()
    machine = machine or platform.machine()
    pointer_bits = pointer_bits or struct.calcsize("P") * 8
    if version != expected or machine.upper() != "AMD64" or pointer_bits != 64:
        raise DepsError("configure.py runs on Python %s for %s, %d-bit. The package's Python is %s for AMD64, "
                        "64-bit, pinned in %s. Run configure.py with a Python of that version and architecture."
                        % (version, machine, pointer_bits, expected, PIN_FILE))


def enable_site_packages(text):
    """Return the text of a ._pth file that imports site and lists Lib\\site-packages.

    The embeddable package ships the line "#import site" commented out. This
    uncomments it, adds Lib\\site-packages after the last path line, and keeps
    the line endings of the file. A file that already has both is returned
    unchanged.
    """
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    stripped = [line.strip() for line in lines]
    if PTH_SITE_PACKAGES.lower() not in (line.lower() for line in stripped):
        paths = [i for i, line in enumerate(stripped)
                 if line and not line.startswith("#") and not line.startswith("import ")]
        lines.insert(paths[-1] + 1 if paths else 0, PTH_SITE_PACKAGES)
        stripped = [line.strip() for line in lines]
    if PTH_IMPORT_SITE not in stripped:
        commented = [i for i, line in enumerate(stripped)
                     if line.startswith("#") and line[1:].strip() == PTH_IMPORT_SITE]
        if commented:
            lines[commented[0]] = PTH_IMPORT_SITE
        else:
            lines.append(PTH_IMPORT_SITE)
    return newline.join(lines) + newline


def edit_pth_file(python_dir):
    """Edit the one ._pth file of python_dir with enable_site_packages; return its path."""
    found = [name for name in os.listdir(python_dir) if name.lower().endswith("._pth")]
    if len(found) != 1:
        raise DepsError("%s: expected one ._pth file, found %s" % (python_dir, ", ".join(found) or "none"))
    path = os.path.join(python_dir, found[0])
    with open(path, encoding="utf-8", newline="") as f:
        text = f.read()
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(enable_site_packages(text))
    print("Edited %s to import site and list %s" % (path, PTH_SITE_PACKAGES))
    return path


def _one_file(folder, name):
    """The file called name in folder, by the case-insensitive match Windows makes."""
    found = [entry for entry in os.listdir(folder) if entry.lower() == name.lower()] if os.path.isdir(folder) else []
    if not found:
        raise DepsError("%s has no %s" % (folder, name))
    return os.path.join(folder, found[0])


def replace_sqlite(python_dir, export_bin):
    """Copy the export's sqlite3.dll over the embedded Python's own.

    Windows loads a DLL's dependencies from the folder of the program first,
    so the stdlib sqlite3 of the embeddable package would load its own
    SQLite, and GDAL, which imports functions that SQLite lacks, could not
    load in the same process. One process holds one SQLite: the export's.
    """
    source = _one_file(export_bin, "sqlite3.dll")
    target = _one_file(python_dir, "sqlite3.dll")
    shutil.copyfile(source, target)
    print("Replaced %s with %s" % (target, source))
    return target


def windows_folders(env):
    """The folders of Windows itself, from SystemRoot."""
    by_name = {key.upper(): value for key, value in env.items()}
    root = by_name.get("SYSTEMROOT") or "C:\\Windows"
    return [root + "\\System32", root, root + "\\System32\\Wbem"]


def minimal_path_environment(env, folders):
    """Return a copy of env whose PATH is folders, then the Windows folders.

    Every process that loads the export's DLLs runs with this PATH, so no
    runtime DLL from the folder of another program on the user's PATH is
    loaded first.
    """
    env = {key: value for key, value in env.items() if key.upper() != "PATH"}
    env["PATH"] = ";".join(list(folders) + windows_folders(env))
    return env


def read_requirements(path=REQUIREMENTS_FILE, platform_name=None):
    """Return (name, version) of every pin of path that applies to platform_name.

    Each line must be name==version, with optional extras and optionally one
    sys_platform marker. Any other line stops the build, so a pin is never
    left out of the check of the installed wheels unnoticed.
    """
    platform_name = platform_name or sys.platform
    try:
        with open(path, encoding="utf-8") as f:
            lines = [line.split("#", 1)[0].strip() for line in f]
    except OSError as e:
        raise DepsError("%s: cannot read the requirements: %s" % (path, e))
    pins = []
    for number, line in enumerate(lines, 1):
        if not line:
            continue
        match = _REQUIREMENT.match(line)
        if not match:
            raise DepsError("%s line %d: expected name==version with at most one sys_platform marker: %s"
                            % (path, number, line))
        name, version, marker = match.groups()
        if marker is None or marker == platform_name:
            pins.append((name, version))
    return pins


def canonical_name(name):
    """The name of a distribution as PyPI compares it."""
    return re.sub(r"[-_.]+", "-", name).lower()


def load_source_pins(path=SOURCE_PINS_FILE):
    """Return (name, version) of every source pin in path, or raise DepsError naming the file.

    Each line is name==version followed by --hash=sha256: and the SHA-256 of
    the sdist. pip reads the hashes itself.
    """
    try:
        with open(path, encoding="utf-8") as f:
            lines = [line.strip() for line in f]
    except OSError as e:
        raise DepsError("%s: cannot read the source pins: %s" % (path, e))
    pins = []
    for number, line in enumerate(lines, 1):
        if not line or line.startswith("#"):
            continue
        match = _SOURCE_PIN.match(line)
        if not match:
            raise DepsError("%s line %d: expected name==version followed by --hash=sha256: "
                            "and 64 lowercase hex digits" % (path, number))
        pins.append(match.groups())
    return pins


def check_source_pins(source_pins, pins, path=SOURCE_PINS_FILE, requirements=REQUIREMENTS_FILE):
    """Stop unless every source pin is a pin of requirements.txt at the same version."""
    versions = {canonical_name(name): version for name, version in pins}
    for name, version in source_pins:
        if versions.get(canonical_name(name)) != version:
            raise DepsError("%s pins %s==%s, but %s pins %s" % (
                path, name, version, requirements,
                "%s==%s" % (name, versions[canonical_name(name)]) if canonical_name(name) in versions
                else "no %s for Windows" % name))


def write_wheel_requirements(requirements, source_pins, dest):
    """Write to dest the lines of requirements without the source pins."""
    names = {canonical_name(name) for name, _ in source_pins}
    with open(requirements, encoding="utf-8") as f:
        lines = f.read().splitlines()
    kept = []
    for line in lines:
        match = _REQUIREMENT.match(line.split("#", 1)[0].strip())
        if not match or canonical_name(match.group(1)) not in names:
            kept.append(line)
    with open(dest, "w", encoding="utf-8") as f:
        f.write("\n".join(kept) + "\n")
    return dest


def _pip_install(python, site_packages):
    # --target installs into that folder and ignores what the Python running
    # pip already has. --no-compile writes no bytecode: the engine keeps its
    # bytecode cache outside the package.
    return [python, "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
            "--no-compile", "--target", site_packages]


def pip_wheels_command(python, site_packages, requirements):
    """The pip command that installs requirements, and what they need, from wheels only."""
    return _pip_install(python, site_packages) + ["--only-binary", ":all:", "-r", requirements]


def pip_source_command(python, site_packages, source_pins, path=SOURCE_PINS_FILE):
    """The pip command that builds and installs the source pins, each checked
    against the SHA-256 of its sdist. Each is pure Python and needs nothing
    else, so pip builds it without a compiler and installs nothing more."""
    cmd = _pip_install(python, site_packages) + ["--require-hashes", "--no-deps"]
    for name, _ in source_pins:
        cmd += ["--no-binary", name]
    return cmd + ["-r", path]


# Runs in the package's Python, which loads the export's DLLs, so it runs
# with the minimal PATH. Its one argument is a JSON object: the Python
# version it must be, its site-packages, the pins of requirements.txt, the
# modules to import and the sqlite3.dll it must load. Prints one line per
# pin and exits 1 with an Error line for each failure.
EMBEDDED_PYTHON_CHECK = r"""
import importlib
import importlib.metadata
import json
import os
import platform
import sqlite3
import sys

args = json.loads(sys.argv[1])
failures = []


def same(a, b):
    return os.path.normcase(os.path.realpath(str(a))) == os.path.normcase(os.path.realpath(str(b)))


print("Python %s at %s" % (platform.python_version(), sys.executable))
if platform.python_version() != args["version"]:
    failures.append("Python is %s, not %s" % (platform.python_version(), args["version"]))
site_packages = args["site_packages"]
if not any(same(entry, site_packages) for entry in sys.path):
    failures.append("%s is not on sys.path: %s" % (site_packages, sys.path))

for name, version in args["pins"]:
    try:
        dist = importlib.metadata.distribution(name)
    except importlib.metadata.PackageNotFoundError:
        failures.append("%s==%s is not installed" % (name, version))
        continue
    location = dist.locate_file("")
    if dist.version != version:
        failures.append("%s is %s, but requirements.txt pins %s" % (name, dist.version, version))
    elif not same(location, site_packages):
        failures.append("%s==%s is in %s, not in %s" % (name, version, location, site_packages))
    else:
        print("Installed %s==%s in %s" % (name, dist.version, location))

for module in args["imports"]:
    try:
        importlib.import_module(module)
        print("Imported %s" % module)
    except Exception as e:
        failures.append("import %s failed: %s: %s" % (module, type(e).__name__, e))

try:
    with sqlite3.connect(":memory:") as connection:
        connection.execute("CREATE TABLE t (x INTEGER)")
        connection.execute("INSERT INTO t VALUES (17)")
        if connection.execute("SELECT x FROM t").fetchone() != (17,):
            failures.append("the stdlib sqlite3 did not read back what it wrote")
    print("SQLite %s opened a database" % sqlite3.sqlite_version)
except sqlite3.Error as e:
    failures.append("the stdlib sqlite3 cannot open a database: %s" % e)

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    kernel32.GetModuleFileNameW.argtypes = [wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
    kernel32.GetModuleFileNameW.restype = wintypes.DWORD
    handle = kernel32.GetModuleHandleW("sqlite3.dll")
    buffer = ctypes.create_unicode_buffer(32768)
    loaded = buffer.value if handle and kernel32.GetModuleFileNameW(handle, buffer, len(buffer)) else ""
    print("SQLite DLL loaded from %s" % (loaded or "(not loaded)"))
    if not loaded or not same(loaded, args["sqlite_dll"]):
        failures.append("the process loaded SQLite from %s, not %s" % (loaded or "nowhere", args["sqlite_dll"]))

for failure in failures:
    print("Error: %s" % failure)
sys.exit(1 if failures else 0)
"""

# Modules the check imports in the package's Python: numpy is the compiled
# wheel most of the engine loads. After the SuperBuild, cv2 too: OpenCV
# installs its Python module into the package's site-packages.
EMBEDDED_PYTHON_IMPORTS = ("numpy",)
SUPERBUILD_PYTHON_IMPORTS = ("numpy", "cv2")
SUPERBUILD_BIN = os.path.join(ROOT, "SuperBuild", "install", "bin")


def check_embedded_python(python_dir, export_bin, pins, version, env, runner=subprocess.run,
                          imports=EMBEDDED_PYTHON_IMPORTS, dll_folders=()):
    """Run EMBEDDED_PYTHON_CHECK in the package's Python with the minimal PATH:
    python_dir, then dll_folders, then the export's bin and Windows."""
    args = {
        "version": version,
        "site_packages": os.path.join(python_dir, SITE_PACKAGES),
        "pins": [list(pin) for pin in pins],
        "imports": list(imports),
        "sqlite_dll": os.path.join(python_dir, "sqlite3.dll"),
    }
    cmd = [os.path.join(python_dir, "python.exe"), "-c", EMBEDDED_PYTHON_CHECK, json.dumps(args)]
    folders = [python_dir] + list(dll_folders) + [export_bin]
    print("Checking the package's Python with PATH limited to %s and Windows" % ", ".join(folders))
    result = runner(cmd, cwd=python_dir, env=minimal_path_environment(env, folders))
    if result.returncode != 0:
        raise DepsError("the package's Python in %s failed its check, exit code %s"
                        % (python_dir, result.returncode))


def python_marker(pin, requirements=REQUIREMENTS_FILE, source_pins=SOURCE_PINS_FILE):
    return "%s %s %s %s\n" % (pin["tag"], pin["assets"]["python"]["sha256"],
                               sha256_of(requirements), sha256_of(source_pins))


def prepare_python(pin, archive, export_dir=EXPORT_DIR, python_dir=PYTHON_DIR,
                   requirements=REQUIREMENTS_FILE, source_pins_file=SOURCE_PINS_FILE,
                   python=None, env=None, runner=subprocess.run):
    """Lay out the package's Python in python_dir and check it.

    Unpacks the embeddable package, edits its ._pth file, installs the
    wheels of requirements and builds the source pins, unless a marker says python_dir already holds
    this pin's package with these requirements. Then replaces its SQLite
    with the export's and runs the check in the package's Python.
    """
    env = os.environ if env is None else env
    version = embeddable_python_version(pin)
    pins = read_requirements(requirements, platform_name="win32")
    source_pins = load_source_pins(source_pins_file)
    check_source_pins(source_pins, pins, source_pins_file, requirements)
    site_packages = os.path.join(python_dir, SITE_PACKAGES)
    marker = python_marker(pin, requirements, source_pins_file)
    marker_path = os.path.join(python_dir, PYTHON_MARKER)
    current = ""
    if os.path.isfile(marker_path):
        with open(marker_path, encoding="utf-8") as f:
            current = f.read()
    if current == marker:
        print("Using the package's Python in %s" % python_dir)
    else:
        safe_remove(python_dir)
        print("Extracting %s --> %s" % (archive, python_dir))
        with zipfile.ZipFile(archive) as z:
            z.extractall(python_dir)
        edit_pth_file(python_dir)
        os.makedirs(site_packages, exist_ok=True)
        python = python or sys.executable
        with tempfile.TemporaryDirectory(prefix="aidm-requirements-") as folder:
            wheels = write_wheel_requirements(requirements, source_pins,
                                              os.path.join(folder, "requirements-wheels.txt"))
            for cmd, source in ((pip_wheels_command(python, site_packages, wheels), requirements),
                                (pip_source_command(python, site_packages, source_pins, source_pins_file),
                                 source_pins_file)):
                print("Running %s" % subprocess.list2cmdline(cmd))
                result = runner(cmd, env=env)
                if result.returncode != 0:
                    raise DepsError("pip did not install %s into %s, exit code %s"
                                    % (source, site_packages, result.returncode))
        with open(marker_path, "w", encoding="utf-8") as f:
            f.write(marker)

    export_bin = os.path.join(export_dir, "installed", pin["triplet"], "bin")
    replace_sqlite(python_dir, export_bin)
    check_embedded_python(python_dir, export_bin, pins, version, env, runner=runner)
    return python_dir


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
    check_build_python(pin)
    prepare_python(pin, assets["python"])
    site_packages = os.path.join(PYTHON_DIR, SITE_PACKAGES)

    cmake = install_cmake()
    env = superbuild_environment(os.environ, cmake, sys.executable, site_packages)
    print("CL=%s" % env["CL"])
    print("PYTHONPATH=%s" % env["PYTHONPATH"])
    report_toolset(env)

    build_dir = os.path.join(ROOT, "SuperBuild", "build")

    def superbuild():
        if not os.path.exists(build_dir) or not os.path.exists(os.path.join(ROOT, "SuperBuild", "install")):
            build_superbuild(cmake, load_cmake_pin(), build_dir, env)

    def check():
        check_embedded_python(PYTHON_DIR, os.path.join(EXPORT_DIR, "installed", pin["triplet"], "bin"),
                              read_requirements(platform_name="win32"), embeddable_python_version(pin),
                              os.environ, imports=SUPERBUILD_PYTHON_IMPORTS, dll_folders=[SUPERBUILD_BIN])

    build_then_check(superbuild, check)


def build_then_check(build_step, check_step):
    """Run build_step, then check_step even when build_step failed, so the
    log shows what the SuperBuild installed in the package's Python before
    it stopped. The first failure is the one raised."""
    failure = None
    try:
        build_step()
    except DepsError as e:
        failure = e
        print("Error: %s" % e)
        print("Checking the package's Python anyway, for the modules the SuperBuild installed")
    try:
        check_step()
    except DepsError as e:
        if failure is None:
            raise
        print("Error: %s" % e)
    if failure is not None:
        raise failure


def clean():
    safe_remove(EXPORT_DIR)
    safe_remove(EXPORT_DIR + ".unpack")
    safe_remove(PYTHON_DIR)
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
