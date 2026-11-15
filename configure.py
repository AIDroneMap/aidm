"""Windows build script of AIDM.

`build` downloads the Windows dependencies release pinned in
SuperBuild/windows-deps.json, checks the SHA-256 of every file against the
pin, unpacks the deps export into vcpkg/, lays out the package's Python in
python/, installs the CMake pinned in SuperBuild/windows-cmake.txt and
builds the SuperBuild with that CMake and MSVC for AVX2. `clean` removes
what `build` made.

`build` takes a variant, `cpu` (the default) or `cuda` (ADR 0006). The cpu
variant builds OpenMVS and OpenSfM without CUDA. The cuda variant builds
them with the CUDA toolkit that CUDA_PATH names, then copies into the
install folder the CUDA runtime DLLs the built tools import. After the
build, both variants list the DLLs of the install folder and read the
imports of every executable and module the package holds. A cpu build stops
when it holds or imports any CUDA DLL. A cuda build stops when a tool
imports a CUDA runtime DLL the install folder lacks, or when no tool
imports CUDA at all. Neither ships the NVIDIA driver's nvcuda.dll.

The package's Python is the embeddable package of the deps release. Its
._pth file is edited to import site and to list Lib\\site-packages, and the
wheels of requirements.txt are installed there with the Python that runs
this script, of the same version and architecture. No virtual environment.
The GDAL Python bindings are the wheel of the deps release, installed there
from its checked download. GDAL itself, PROJ and their DLLs are the export's.

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
# Present from the moment python/ is laid out anew until a SuperBuild has
# run after it: the SuperBuild installs Python modules, such as cv2, into
# python/, and a new layout has lost them.
PYTHON_REINSTALL_MARKER = ".aidm-superbuild-reinstall"
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
# The file name of the GDAL wheel of the deps release, with its version.
_GDAL_WHEEL = re.compile(r"^gdal-(\d+\.\d+\.\d+)-(cp\d+)-(cp\d+)-win_amd64\.whl$", re.IGNORECASE)
# One pin of requirements.txt: a name, optional extras, one exact version,
# and optionally one sys_platform marker.
_REQUIREMENT = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[A-Za-z0-9,._-]+\])?==([A-Za-z0-9.+!-]+)"
                          r"\s*(?:;\s*sys_platform\s*==\s*['\"]([a-z0-9]+)['\"])?$")
# One source pin: a name, one exact version, then one or more hashes.
_SOURCE_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9.+!-]+)(?:\s+--hash=sha256:[0-9a-f]{64})+$")

# The two Windows variants of ADR 0006. A build without a variant is cpu.
VARIANTS = ("cpu", "cuda")
DEFAULT_VARIANT = "cpu"
# Written in the SuperBuild's build folder: a build folder serves one variant.
VARIANT_MARKER = ".aidm-variant"
# The CUDA release the cuda variant builds with (docs/specs/0113-windows-engine-0.3.0.md).
CUDA_RELEASE = "12.9"
# The MSBuild files of the toolkit itself. CMake's toolset cuda=<folder>
# reads them there, so the build needs no CUDA integration in Visual Studio.
CUDA_MSBUILD_EXTENSIONS = os.path.join("extras", "visual_studio_integration", "MSBuildExtensions")
# The NVIDIA driver's DLL. The machine supplies it; a package that ships
# one shadows the real driver, as the stub of the inherited build did.
DRIVER_DLLS = ("nvcuda.dll",)
# The DLL names of the CUDA driver, the CUDA toolkit's runtime libraries and
# cuDNN, with their 64 and version suffixes: cudart64_12.dll,
# cublasLt64_12.dll, nvrtc64_120_0.dll, nppial64_12.dll, nvJitLink_120_0.dll.
_CUDA_DLL = re.compile(r"^(nvcuda|cudart|cublas|cublaslt|cufft|cufftw|curand|cusolver|cusolvermg|cusparse"
                       r"|nvrtc|nvrtc-builtins|nvjitlink|nvjpeg|npp[a-z]*|nvblas|cupti|nvtoolsext|cudnn[a-z_]*"
                       r"|nvfatbin)(?:32|64)?(?:_[0-9.]+)*\.dll$", re.IGNORECASE)
# The files whose imports the DLL check reads: programs, DLLs and Python modules.
PE_SUFFIXES = (".exe", ".dll", ".pyd")
# Data directory entries of a PE file: the import table and the delay-load import table.
_PE_IMPORT_DIRECTORY = 1
_PE_DELAY_IMPORT_DIRECTORY = 13


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


def build_superbuild(cmake, pinned, build_dir, env, runner=subprocess.run, configure_args=()):
    """Print the CMake the SuperBuild uses, then configure and build it with that CMake.

    pinned is the version of the CMake pin. A wheel version may carry a
    fourth number for a rebuild of the same CMake, which cmake does not print.
    configure_args go to the configure command, after the source folder.
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
    for cmd in ([cmake, ".."] + list(configure_args),
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


def gdal_wheel_version(pin, requirements=REQUIREMENTS_FILE):
    """Return the GDAL version of the pinned wheel, from its file name.

    Stop unless the wheel is built for the package's Python and its version
    is the one requirements.txt pins for Linux: the export's GDAL is the
    version the Linux images carry.
    """
    name = pin["assets"]["gdal-wheel"]["file"]
    python_tag = "cp" + "".join(embeddable_python_version(pin).split(".")[:2])
    match = _GDAL_WHEEL.match(name)
    if not match or match.group(2) != python_tag or match.group(3) != python_tag:
        raise DepsError("%s: the gdal-wheel asset %s is not named gdal-X.Y.Z-%s-%s-win_amd64.whl"
                        % (PIN_FILE, name, python_tag, python_tag))
    version = match.group(1)
    linux = {canonical_name(n): v for n, v in read_requirements(requirements, platform_name="linux")}
    if linux.get("gdal") != version:
        raise DepsError("%s pins the GDAL wheel %s, but %s pins %s for Linux"
                        % (PIN_FILE, version, requirements,
                           "gdal==%s" % linux["gdal"] if "gdal" in linux else "no gdal"))
    return version


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


def pip_gdal_wheel_command(python, site_packages, wheel):
    """The pip command that installs the GDAL wheel from its checked download
    and nothing else. numpy, which it imports, is a pin of requirements.txt."""
    return _pip_install(python, site_packages) + ["--no-deps", "--no-index", wheel]


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

# One process holds one SQLite: the version GDAL reports must be the one
# the stdlib sqlite3 runs.
if "osgeo" in sys.modules:
    try:
        from osgeo import gdal, ogr
        database = ogr.GetDriverByName("SQLite").CreateDataSource(":memory:")
        result = database.ExecuteSQL("SELECT sqlite_version()")
        gdal_sqlite = result.GetNextFeature().GetField(0)
        database.ReleaseResultSet(result)
        database = None
        print("GDAL %s reports SQLite %s" % (gdal.__version__, gdal_sqlite))
        if gdal_sqlite != sqlite3.sqlite_version:
            failures.append("GDAL reports SQLite %s, the stdlib sqlite3 runs %s"
                            % (gdal_sqlite, sqlite3.sqlite_version))
    except Exception as e:
        failures.append("GDAL cannot report its SQLite version: %s: %s" % (type(e).__name__, e))

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
# wheel most of the engine loads, osgeo the GDAL wheel of the deps release,
# which loads the export's DLLs. After the SuperBuild, cv2 and pdal too:
# OpenCV and the PDAL Python bindings install their modules into the
# package's site-packages. pdal comes after osgeo: its DLLs are found in the
# folders osgeo adds from PATH. codem last: the engine imports it, and it
# imports cv2, pdal and distutils, which setuptools provides on Windows.
EMBEDDED_PYTHON_IMPORTS = ("numpy", "osgeo")
SUPERBUILD_PYTHON_IMPORTS = ("numpy", "cv2", "osgeo", "pdal", "codem")
SUPERBUILD_INSTALL = os.path.join(ROOT, "SuperBuild", "install")
SUPERBUILD_BIN = os.path.join(SUPERBUILD_INSTALL, "bin")
# The data folders GDAL and PROJ read at run time, which the SuperBuild
# copies from the export into its install folder, each with a file it must hold.
DATA_FOLDERS = ((os.path.join("share", "gdal"), "gdalvrt.xsd"), (os.path.join("share", "proj"), "proj.db"))
# Read by the osgeo package on Windows: it adds every folder of PATH to the
# folders its DLLs, and so the DLLs of every module after it, load from.
GDAL_DLL_SEARCH = {"USE_PATH_FOR_GDAL_PYTHON": "YES"}


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
    env = minimal_path_environment(env, folders)
    env.update(GDAL_DLL_SEARCH)
    result = runner(cmd, cwd=python_dir, env=env)
    if result.returncode != 0:
        raise DepsError("the package's Python in %s failed its check, exit code %s"
                        % (python_dir, result.returncode))


def check_data_folders(install_dir=SUPERBUILD_INSTALL):
    """Stop unless the install folder holds the GDAL and PROJ data folders; print each."""
    for folder, required in DATA_FOLDERS:
        path = os.path.join(install_dir, folder)
        if not os.path.isfile(os.path.join(path, required)):
            raise DepsError("%s has no %s: the SuperBuild copies it from the deps export" % (path, required))
        print("Data folder %s present, with %d files, %s among them"
              % (path, sum(len(files) for _, _, files in os.walk(path)), required))


def python_marker(pin, requirements=REQUIREMENTS_FILE, source_pins=SOURCE_PINS_FILE):
    return "%s %s %s %s %s\n" % (pin["tag"], pin["assets"]["python"]["sha256"],
                                  pin["assets"]["gdal-wheel"]["sha256"],
                                  sha256_of(requirements), sha256_of(source_pins))


def prepare_python(pin, archive, gdal_wheel, export_dir=EXPORT_DIR, python_dir=PYTHON_DIR,
                   requirements=REQUIREMENTS_FILE, source_pins_file=SOURCE_PINS_FILE,
                   python=None, env=None, runner=subprocess.run):
    """Lay out the package's Python in python_dir and check it.

    Unpacks the embeddable package, edits its ._pth file, installs the
    wheels of requirements, builds the source pins and installs the GDAL
    wheel, unless a marker says python_dir already holds this pin's package
    and wheel with these requirements. A
    new layout also leaves PYTHON_REINSTALL_MARKER, so the SuperBuild
    installs its Python modules into it again. Then replaces its SQLite
    with the export's and runs the check in the package's Python.
    """
    env = os.environ if env is None else env
    version = embeddable_python_version(pin)
    pins = read_requirements(requirements, platform_name="win32")
    source_pins = load_source_pins(source_pins_file)
    check_source_pins(source_pins, pins, source_pins_file, requirements)
    gdal_version = gdal_wheel_version(pin, requirements)
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
        with open(os.path.join(python_dir, PYTHON_REINSTALL_MARKER), "w", encoding="utf-8") as f:
            f.write("The SuperBuild has not installed its Python modules into this layout yet.\n")
        edit_pth_file(python_dir)
        os.makedirs(site_packages, exist_ok=True)
        python = python or sys.executable
        with tempfile.TemporaryDirectory(prefix="aidm-requirements-") as folder:
            wheels = write_wheel_requirements(requirements, source_pins,
                                              os.path.join(folder, "requirements-wheels.txt"))
            for cmd, source in ((pip_wheels_command(python, site_packages, wheels), requirements),
                                (pip_source_command(python, site_packages, source_pins, source_pins_file),
                                 source_pins_file),
                                (pip_gdal_wheel_command(python, site_packages, gdal_wheel), gdal_wheel)):
                print("Running %s" % subprocess.list2cmdline(cmd))
                result = runner(cmd, env=env)
                if result.returncode != 0:
                    raise DepsError("pip did not install %s into %s, exit code %s"
                                    % (source, site_packages, result.returncode))
        with open(marker_path, "w", encoding="utf-8") as f:
            f.write(marker)

    export_bin = os.path.join(export_dir, "installed", pin["triplet"], "bin")
    replace_sqlite(python_dir, export_bin)
    check_embedded_python(python_dir, export_bin, pins + [("gdal", gdal_version)], version, env, runner=runner)
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


def build(variant=DEFAULT_VARIANT):
    print("Windows variant: %s" % variant)
    build_dir = os.path.join(ROOT, "SuperBuild", "build")
    check_build_variant(build_dir, variant)
    cuda_folder = cuda_toolkit(os.environ) if variant == "cuda" else None
    configure_args = superbuild_variant_args(variant, cuda_folder)

    pin = load_pin()
    assets = fetch_pinned_assets(pin)
    unpack_export(pin, assets["export"])
    check_build_python(pin)
    prepare_python(pin, assets["python"], assets["gdal-wheel"])
    site_packages = os.path.join(PYTHON_DIR, SITE_PACKAGES)

    cmake = install_cmake()
    env = superbuild_environment(os.environ, cmake, sys.executable, site_packages)
    print("CL=%s" % env["CL"])
    print("PYTHONPATH=%s" % env["PYTHONPATH"])
    report_toolset(env)

    def superbuild():
        if superbuild_needed(PYTHON_DIR, build_dir, os.path.join(ROOT, "SuperBuild", "install")):
            record_build_variant(build_dir, variant)
            build_superbuild(cmake, load_cmake_pin(), build_dir, env, configure_args=configure_args)
            superbuild_done(PYTHON_DIR)

    def check():
        pins = read_requirements(platform_name="win32") + [("gdal", gdal_wheel_version(pin))]
        check_embedded_python(PYTHON_DIR, os.path.join(EXPORT_DIR, "installed", pin["triplet"], "bin"),
                              pins, embeddable_python_version(pin),
                              os.environ, imports=SUPERBUILD_PYTHON_IMPORTS, dll_folders=[SUPERBUILD_BIN])
        check_data_folders()

    build_then_check(superbuild, check)

    # The package holds the install folder and python/. Their imports decide
    # which CUDA runtime DLLs the cuda variant needs.
    roots = [SUPERBUILD_INSTALL, PYTHON_DIR]
    if variant == "cuda":
        bundle_cuda_runtime(SUPERBUILD_BIN, roots, os.path.join(cuda_folder, "bin"))
    check_variant_dlls(variant, roots, SUPERBUILD_BIN)


def _inside(path, folder):
    """Whether path is folder or below it, as the file system compares them."""
    path = os.path.normcase(os.path.normpath(path))
    folder = os.path.normcase(os.path.normpath(folder))
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


def python_module_projects(build_dir, python_dir):
    """Return the folders under build_dir of the external projects whose
    install wrote into python_dir, by their CMake install manifests.

    Each external project of the SuperBuild has its folder build_dir/<name>,
    with its stamps in a stamp folder there and its install_manifest.txt in
    its binary folder below it. The manifest lists every file the install
    wrote, so it still names them after python_dir has been laid out anew.
    """
    projects = []
    if not os.path.isdir(build_dir):
        return projects
    for name in sorted(os.listdir(build_dir)):
        project = os.path.join(build_dir, name)
        if not os.path.isdir(os.path.join(project, "stamp")):
            continue
        for folder, _dirs, files in os.walk(project):
            if "install_manifest.txt" not in files:
                continue
            with open(os.path.join(folder, "install_manifest.txt"), encoding="utf-8", errors="replace") as f:
                if any(line.strip() and _inside(line.strip(), python_dir) for line in f):
                    projects.append(project)
                    break
    return projects


def remove_install_stamps(project):
    """Remove the install stamp of one external project, so the next build
    runs its install step again; return the removed paths.

    ExternalProject marks each finished step with a file <name>-<step> in
    the project's stamp folder, and with a multi-config generator in a
    folder per configuration below it. Its build stamp stays, so nothing is
    compiled again.
    """
    removed = []
    for folder, _dirs, files in os.walk(os.path.join(project, "stamp")):
        for name in files:
            if name.endswith("-install"):
                os.remove(os.path.join(folder, name))
                removed.append(os.path.join(folder, name))
    return sorted(removed)


def superbuild_needed(python_dir, build_dir, install_dir):
    """Decide whether build() runs the SuperBuild.

    It runs on a tree that has not built yet, and after python_dir was laid
    out anew. Then the install stamps of the projects whose install wrote
    into python_dir, such as OpenCV with cv2, are removed first, so the
    SuperBuild installs their Python modules into the new layout.
    """
    if os.path.isfile(os.path.join(python_dir, PYTHON_REINSTALL_MARKER)):
        projects = python_module_projects(build_dir, python_dir)
        for project in projects:
            remove_install_stamps(project)
        print("python/ was laid out anew: the SuperBuild installs again %s"
              % (", ".join(os.path.basename(p) for p in projects) or "no project, none installed into it yet"))
        return True
    return not os.path.exists(build_dir) or not os.path.exists(install_dir)


def superbuild_done(python_dir):
    """Record that the SuperBuild has installed its Python modules into python/."""
    safe_remove(os.path.join(python_dir, PYTHON_REINSTALL_MARKER))


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


def cuda_toolkit(env, runner=subprocess.run):
    """Return the CUDA toolkit folder that CUDA_PATH names, or raise DepsError.

    The CUDA installer sets CUDA_PATH. The folder must hold nvcc of
    CUDA_RELEASE and the toolkit's own MSBuild files, which the toolset
    cuda=<folder> reads.
    """
    by_name = {key.upper(): value for key, value in env.items()}
    folder = by_name.get("CUDA_PATH", "")
    if not folder or not os.path.isdir(folder):
        raise DepsError("The cuda variant needs the CUDA %s toolkit, and CUDA_PATH (%s) names no folder. "
                        "Install the toolkit, which sets CUDA_PATH, and run configure.py again."
                        % (CUDA_RELEASE, folder or "not set"))
    nvcc = os.path.join(folder, "bin", "nvcc.exe")
    if not os.path.isfile(nvcc):
        raise DepsError("%s has no nvcc.exe. The cuda variant needs the CUDA compiler of the toolkit." % os.path.dirname(nvcc))
    extensions = os.path.join(folder, CUDA_MSBUILD_EXTENSIONS)
    if not os.path.isdir(extensions):
        raise DepsError("%s does not exist. The cuda variant builds through the toolkit's own MSBuild files: "
                        "install the toolkit with its Visual Studio integration subpackage." % extensions)
    result = runner([nvcc, "--version"], capture_output=True, text=True)
    match = re.search(r"release (\d+\.\d+), (V\S+)", result.stdout or "")
    if result.returncode != 0 or not match:
        raise DepsError("%s --version did not print a release" % nvcc)
    if match.group(1) != CUDA_RELEASE:
        raise DepsError("%s is CUDA %s. The cuda variant builds with CUDA %s." % (nvcc, match.group(1), CUDA_RELEASE))
    print("CUDA toolkit: %s, nvcc %s" % (folder, match.group(2)))
    return folder


def superbuild_variant_args(variant, cuda_folder=None):
    """The arguments of the SuperBuild's configure command for variant.

    The cuda variant also names the toolkit and selects the toolset
    cuda=<toolkit>, which the external projects of the SuperBuild take on.
    """
    if variant not in VARIANTS:
        raise DepsError("Unknown variant %r: expected %s" % (variant, " or ".join(VARIANTS)))
    args = ["-DAIDM_WINDOWS_VARIANT=%s" % variant]
    if variant == "cuda":
        if not cuda_folder:
            raise DepsError("The cuda variant needs the folder of the CUDA toolkit")
        folder = cuda_folder.replace("\\", "/").rstrip("/")
        args += ["-DAIDM_CUDA_TOOLKIT=%s" % folder, "-T", "cuda=%s" % folder]
    return args


def check_build_variant(build_dir, variant):
    """Stop when build_dir holds a build of another variant, or of no recorded variant.

    A build folder keeps its toolset and its CUDA settings, so a cpu build
    and a cuda build never share one.
    """
    if not os.path.isdir(build_dir):
        return
    marker = os.path.join(build_dir, VARIANT_MARKER)
    built = ""
    if os.path.isfile(marker):
        with open(marker, encoding="utf-8") as f:
            built = f.read().strip()
    if built != variant:
        raise DepsError("%s holds a build of %s, not of the %s variant. Run python configure.py clean first."
                        % (build_dir, "the %s variant" % built if built else "no recorded variant", variant))


def record_build_variant(build_dir, variant):
    os.makedirs(build_dir, exist_ok=True)
    with open(os.path.join(build_dir, VARIANT_MARKER), "w", encoding="utf-8") as f:
        f.write(variant + "\n")


def is_cuda_dll(name):
    """Whether name is the file name of a CUDA driver, CUDA runtime or cuDNN DLL."""
    return bool(_CUDA_DLL.match(os.path.basename(name)))


def _read_at(f, offset, size, path):
    f.seek(offset)
    data = f.read(size)
    if len(data) != size:
        raise DepsError("%s: truncated PE file, no %d bytes at offset %d" % (path, size, offset))
    return data


def _read_name(f, offset, path):
    f.seek(offset)
    data = f.read(512)
    end = data.find(b"\0")
    if end <= 0:
        raise DepsError("%s: no DLL name at offset %d" % (path, offset))
    return data[:end].decode("ascii", "replace")


def pe_imports(path):
    """Return the names of the DLLs that the PE file path imports, the
    delay-loaded ones included, in the order of its tables. Return None
    when path is not a PE file. Raise DepsError on a broken one.
    """
    with open(path, "rb") as f:
        if f.read(2) != b"MZ":
            return None
        pe_offset = struct.unpack("<I", _read_at(f, 0x3C, 4, path))[0]
        if _read_at(f, pe_offset, 4, path) != b"PE\0\0":
            return None
        sections_count, optional_size = struct.unpack("<2xH12xH2x", _read_at(f, pe_offset + 4, 20, path))
        optional_offset = pe_offset + 24
        optional = _read_at(f, optional_offset, optional_size, path)
        magic = struct.unpack_from("<H", optional, 0)[0]
        if magic == 0x20B:
            image_base = struct.unpack_from("<Q", optional, 24)[0]
            directories_offset = 112
        elif magic == 0x10B:
            image_base = struct.unpack_from("<I", optional, 28)[0]
            directories_offset = 96
        else:
            raise DepsError("%s: unknown PE optional header magic 0x%x" % (path, magic))
        directories_count = struct.unpack_from("<I", optional, directories_offset - 4)[0]

        def directory(index):
            if index >= directories_count or directories_offset + 8 * index + 8 > len(optional):
                return 0, 0
            return struct.unpack_from("<II", optional, directories_offset + 8 * index)

        sections = []
        table = optional_offset + optional_size
        for i in range(sections_count):
            virtual_size, virtual_address, raw_size, raw_offset = struct.unpack(
                "<8xIIII16x", _read_at(f, table + 40 * i, 40, path))
            sections.append((virtual_address, max(virtual_size, raw_size), raw_offset))

        def offset_of(rva):
            for virtual_address, size, raw_offset in sections:
                if virtual_address <= rva < virtual_address + size:
                    return rva - virtual_address + raw_offset
            raise DepsError("%s: address 0x%x is in no section" % (path, rva))

        names = []
        rva, _size = directory(_PE_IMPORT_DIRECTORY)
        if rva:
            position = offset_of(rva)
            while True:
                descriptor = struct.unpack("<5I", _read_at(f, position, 20, path))
                if not any(descriptor):
                    break
                names.append(_read_name(f, offset_of(descriptor[3]), path))
                position += 20
        rva, _size = directory(_PE_DELAY_IMPORT_DIRECTORY)
        if rva:
            position = offset_of(rva)
            while True:
                descriptor = struct.unpack("<8I", _read_at(f, position, 32, path))
                if not any(descriptor):
                    break
                attributes, name_address = descriptor[0], descriptor[1]
                # Bit 0 set: the name is an address relative to the image. Clear: an absolute one.
                names.append(_read_name(f, offset_of(name_address if attributes & 1 else name_address - image_base),
                                        path))
                position += 32
        return names


def pe_files(roots):
    """Every program, DLL and Python module below roots, in a fixed order."""
    found = []
    for root in roots:
        for folder, _dirs, files in os.walk(root):
            found += [os.path.join(folder, name) for name in files if name.lower().endswith(PE_SUFFIXES)]
    return sorted(found)


def read_imports(roots):
    """Map each PE file below roots to the DLLs it imports."""
    imports = {}
    for path in pe_files(roots):
        names = pe_imports(path)
        if names is not None:
            imports[path] = names
    return imports


def bundle_cuda_runtime(install_bin, roots, cuda_bin):
    """Copy into install_bin each DLL of the CUDA toolkit's bin folder that a
    PE file below roots imports, and those the copies import in turn.
    Return the names copied.
    """
    toolkit = {name.lower(): name for name in os.listdir(cuda_bin) if name.lower().endswith(".dll")}
    copied = []
    while True:
        present = {name.lower() for name in os.listdir(install_bin)}
        wanted = sorted({name.lower() for names in read_imports(roots).values() for name in names
                         if name.lower() in toolkit and name.lower() not in present})
        if not wanted:
            break
        for name in wanted:
            source = os.path.join(cuda_bin, toolkit[name])
            print("Copying %s to %s" % (source, install_bin))
            shutil.copyfile(source, os.path.join(install_bin, toolkit[name]))
            copied.append(toolkit[name])
    return copied


def check_variant_dlls(variant, roots, install_bin):
    """List the DLLs of install_bin, read the imports of every PE file below
    roots, and raise DepsError when the tree does not fit variant.

    Both variants: no file is the NVIDIA driver's DLL. cpu: no file is a
    CUDA DLL and no file imports one. cuda: every CUDA DLL a file imports,
    other than the driver's, is in install_bin, and at least one file
    imports CUDA, so the tools were really built with it.
    """
    if variant not in VARIANTS:
        raise DepsError("Unknown variant %r: expected %s" % (variant, " or ".join(VARIANTS)))
    present = {name.lower() for name in os.listdir(install_bin)} if os.path.isdir(install_bin) else set()
    print("DLLs in %s:" % install_bin)
    for name in sorted(present):
        if name.endswith(".dll"):
            print("  %s%s" % (name, " (CUDA)" if is_cuda_dll(name) else ""))

    failures = []
    driver = {name.lower() for name in DRIVER_DLLS}
    for path in pe_files(roots):
        name = os.path.basename(path).lower()
        if name in driver:
            failures.append("%s is the NVIDIA driver's DLL, which the machine supplies; no package ships it" % path)
        elif variant == "cpu" and is_cuda_dll(name):
            failures.append("%s is a CUDA DLL in the cpu variant" % path)

    importers = 0
    for path, names in sorted(read_imports(roots).items()):
        cuda = sorted({name for name in names if is_cuda_dll(name)}, key=str.lower)
        if not cuda:
            continue
        importers += 1
        print("%s imports %s" % (path, ", ".join(cuda)))
        for name in cuda:
            if variant == "cpu":
                failures.append("%s imports %s in the cpu variant" % (path, name))
            elif name.lower() not in driver and name.lower() not in present:
                failures.append("%s imports %s, which is not in %s" % (path, name, install_bin))
    if variant == "cuda" and not importers:
        failures.append("no file below %s imports a CUDA DLL: the cuda variant was built without CUDA"
                        % ", ".join(roots))

    for failure in failures:
        print("Error: %s" % failure)
    if failures:
        raise DepsError("the %s variant failed its DLL check, %d failure%s"
                        % (variant, len(failures), "" if len(failures) == 1 else "s"))
    print("The %s variant passed its DLL check. Files that import CUDA: %d" % (variant, importers))


def clean():
    safe_remove(EXPORT_DIR)
    safe_remove(EXPORT_DIR + ".unpack")
    safe_remove(PYTHON_DIR)
    safe_remove(os.path.join(ROOT, "SuperBuild", "build"))
    safe_remove(os.path.join(ROOT, "SuperBuild", "download"))
    safe_remove(os.path.join(ROOT, "SuperBuild", "src"))
    safe_remove(os.path.join(ROOT, "SuperBuild", "install"))


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="AIDM Windows build script")
    parser.add_argument("action", type=str, choices=["build", "clean"],
                        help="Action: %(choices)s")
    parser.add_argument("variant", type=str, nargs="?", choices=VARIANTS, default=DEFAULT_VARIANT,
                        help="Variant that build makes: %(choices)s. Default: %(default)s")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
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
            build(args.variant)
        else:
            clean()
    except DepsError as e:
        print("Error: %s" % e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
