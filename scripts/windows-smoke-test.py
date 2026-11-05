#!/usr/bin/env python3
"""Check a Windows engine package through the released task API.

Run with the runner's Python and pyodm pinned by requirements.txt:
  python scripts/windows-smoke-test.py <package folder> [smoke|start]

The package folder contains engine/. SMOKE_DATASET selects ci or full.
SMOKE_CACHE holds checked downloads. The API and results live in a fresh
temporary folder. No engine options or output paths differ from the Linux test.
"""

import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

HERE = Path(__file__).resolve().parent
CONF = HERE / "smoke-test.conf"
GEOREF_CHECK = HERE / "check-georeferencing.py"


@contextlib.contextmanager
def step(name):
    print("[windows-smoke] %s" % name, flush=True)
    try:
        yield
    except Exception as exc:
        raise RuntimeError("%s: %s" % (name, exc)) from exc


def read_config(path=CONF):
    config = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        values = shlex.split(value)
        if not sep or not key.isidentifier() or len(values) != 1:
            raise ValueError("cannot parse setting: %s" % line)
        config[key] = values[0]
    return config


def select_dataset(config, name):
    if name not in ("ci", "full"):
        raise ValueError("SMOKE_DATASET must be ci or full")
    prefix = name.upper() + "_DATASET_"
    return {"url": config[prefix + "URL"],
            "sha256": config[prefix + "SHA256"],
            "images": int(config[prefix + "IMAGES"])}


def fetch(url, digest, cache):
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("invalid SHA-256 pin")
    if not url.startswith("https://"):
        raise ValueError("download URL must use HTTPS")
    path = Path(cache) / digest / url.rsplit("/", 1)[-1]
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with urllib.request.urlopen(url, timeout=60) as response, \
                path.with_suffix(".tmp").open("wb") as target:
            shutil.copyfileobj(response, target)
        path.with_suffix(".tmp").replace(path)
    actual = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            actual.update(block)
    if actual.hexdigest() != digest:
        path.unlink()
        raise RuntimeError("SHA-256 mismatch for %s" % path.name)
    return path


def relative_path(name):
    path = PureWindowsPath(name)
    if (not path.parts or path.drive or path.root or ".." in path.parts
            or any(":" in part or part.endswith((".", " ")) for part in path.parts)
            or path.is_reserved()):
        raise ValueError("unsafe archive path: %s" % name)
    return Path(*path.parts)


def unpack(archive, folder):
    # Validate before extracting, including drive paths and links on Windows.
    with zipfile.ZipFile(archive) as source:
        for member in source.infolist():
            relative_path(member.filename)
            if (member.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("archive contains a link: %s" % member.filename)
        source.extractall(folder)


def fetch_dataset(dataset, cache, folder):
    unpack(fetch(dataset["url"], dataset["sha256"], cache), folder)
    images = sorted(p for p in Path(folder).iterdir()
                    if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg"))
    if len(images) != dataset["images"]:
        raise RuntimeError("expected %d images, found %d" % (dataset["images"], len(images)))
    return [str(p) for p in images]


def engine_options(text):
    """Turn the shared command-line options into the task API's option map."""
    tokens = shlex.split(text)
    options = {}
    while tokens:
        key = tokens.pop(0)
        if not key.startswith("--") or len(key) == 2:
            raise ValueError("expected an engine option, found %s" % key)
        name, sep, value = key[2:].partition("=")
        if not sep:
            value = tokens.pop(0) if tokens and not tokens[0].startswith("--") else True
        options[name] = value
    return options


def api_config(engine, token, port):
    return {"token": token, "port": port, "parallelQueueProcessing": 1,
            "odx_path": str(Path(engine).resolve())}


def check_outputs(project, outputs):
    problems = []
    for name in outputs.split():
        path = Path(project) / relative_path(name)
        if not path.is_file() or path.stat().st_size == 0:
            problems.append("missing or empty output: %s" % name)
    if not outputs.split():
        problems.append("the output list is empty")
    return problems


def task_finished(status):
    # TaskStatus values in pyodm 1.5.12 and the released task API.
    code = getattr(status, "value", status)
    if code in (10, 20):
        return False
    if code == 40:
        return True
    if code in (30, 50):
        raise RuntimeError("task %s" % ("failed" if code == 30 else "was canceled"))
    raise RuntimeError("unknown task status: %s" % code)


def wait_task(task, process, timeout=7200, interval=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("task API exited with code %s" % process.returncode)
        info = task.info()
        if task_finished(info.status):
            return
        time.sleep(interval)
    raise RuntimeError("task did not finish within %s seconds" % timeout)


def check_georeferencing(code, output):
    if code != 0:
        return ["georeferencing check exited with code %s: %s" % (code, output.strip())]
    if "[georef] FAIL:" in output or not any(
            line.startswith("[georef] ok    ") and "bounds contain all " in line
            for line in output.splitlines()):
        return ["georeferencing check did not report a passing verdict"]
    return []


def check_start(info, version, code, output):
    problems = []
    if not version or info.engine != "aidm" or info.engine_version != version:
        problems.append("the task API did not report the package's engine version")
    lines = re.sub(r"\x1b\[[0-9;]*m", "", output).splitlines()
    if code != 0:
        problems.append("GPU detection exited with code %s" % code)
    if "[INFO] No CUDA drivers detected" not in [line.strip() for line in lines]:
        problems.append("GPU detection did not report 'No CUDA drivers detected'")
    if any("CUDA drivers detected" in line and "No CUDA drivers detected" not in line
           for line in lines):
        problems.append("GPU detection found a CUDA driver")
    return problems


def require_ok(problems):
    if problems:
        raise RuntimeError("; ".join(problems))


def package_python(engine, args, timeout=120):
    # cmd.exe needs its own quoting. Capture the batch environment first,
    # then pass Python arguments directly, so code and paths avoid the shell.
    command = 'cmd.exe /d /u /s /c "call "%s" >nul && set"' % (engine / "win32env.bat")
    setup = subprocess.run(command, cwd=engine, capture_output=True, text=True,
                           encoding="utf-16-le", errors="replace", timeout=timeout)
    if setup.returncode != 0:
        return setup
    environment = {}
    for line in setup.stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep and key:
            environment[key] = value
    return subprocess.run([str(engine / "python/python.exe"), "-X", "utf8",
                           *[str(arg) for arg in args]], cwd=engine, env=environment,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)


def start_api(executable, config_path, log):
    return subprocess.Popen([str(executable), "--config", str(config_path)],
                            cwd=executable.parent, stdout=log, stderr=subprocess.STDOUT)


def stop_api(process):
    # Kill the whole tree, including an engine still running after a failure.
    result = subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                            capture_output=True, timeout=30)
    if result.returncode != 0 and process.poll() is None:
        raise RuntimeError("cannot stop task API process tree")
    process.wait(timeout=30)


def wait_api(node, process, timeout=90):
    from pyodm.exceptions import NodeConnectionError

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("task API exited with code %s" % process.returncode)
        try:
            return node.info()
        except NodeConnectionError:
            time.sleep(1)
    raise RuntimeError("task API did not answer within %s seconds" % timeout)


def run(package, mode, config, work, cache):
    from pyodm import Node

    engine = Path(package).resolve() / "engine"
    work = Path(work)
    with step("package"):
        for name in ("VERSION", "ENGINE", "win32env.bat", "run.bat", "python/python.exe"):
            if not (engine / name).is_file():
                raise RuntimeError("engine package lacks %s" % name)
        version = (engine / "VERSION").read_text(encoding="utf-8").strip()
    with step("download task API"):
        api_folder = work / "api"
        unpack(fetch(config["WINDOWS_API_URL"], config["WINDOWS_API_SHA256"], cache), api_folder)
        executables = list(api_folder.glob("*/aidm-api.exe"))
        if len(executables) != 1:
            raise RuntimeError("expected one task API executable")
        executable = executables[0]
    with step("configure task API"):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        token = secrets.token_hex(24)
        config_path = work / "api-config.json"
        config_path.write_text(json.dumps(api_config(engine, token, port)), encoding="utf-8")
        node = Node("127.0.0.1", port, token=token, timeout=10)
    log_path = work / "api.log"
    with log_path.open("w", encoding="utf-8") as log:
        with step("start task API"):
            process = start_api(executable, config_path, log)
        try:
            with step("task API information"):
                info = wait_api(node, process)
            if mode == "start":
                with step("start check"):
                    result = package_python(engine, ["-c", "import os, sys; "
                        "sys.path.insert(0, os.getcwd()); from types import SimpleNamespace; "
                        "from modules import gpu; gpu.has_gpu(SimpleNamespace(no_gpu=False))"])
                    print(result.stdout, end="", flush=True)
                    require_ok(check_start(info, version, result.returncode,
                                           result.stdout + result.stderr))
                return
            project = work / "project"
            images_folder = project / "images"
            images_folder.mkdir(parents=True)
            with step("dataset"):
                images = fetch_dataset(select_dataset(config, os.environ.get("SMOKE_DATASET", "ci")),
                                       cache, images_folder)
            with step("submit task"):
                task = node.create_task(images, engine_options(config["DEFAULT_OPTIONS"]),
                                        name="Windows smoke test")
            with step("wait for task"):
                wait_task(task, process)
            with step("download results"):
                archive = task.download_zip(str(work / "results"), parallel_downloads=1)
                unpack(archive, project)
            with step("outputs"):
                require_ok(check_outputs(project, config["OUTPUTS"]))
            with step("georeferencing"):
                result = package_python(engine, [GEOREF_CHECK, project])
                print(result.stdout, end="", flush=True)
                require_ok(check_georeferencing(result.returncode, result.stdout + result.stderr))
        except Exception as exc:
            raise RuntimeError(str(exc).replace(token, "[redacted]")) from None
        finally:
            try:
                with step("stop task API"):
                    stop_api(process)
            finally:
                # The generated token stays out of the diagnostic log.
                text = log_path.read_text(encoding="utf-8", errors="replace")
                print(text.replace(token, "[redacted]"), end="", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("mode", choices=("smoke", "start"), nargs="?", default="smoke")
    args = parser.parse_args()
    try:
        if sys.platform != "win32":
            raise RuntimeError("run this test on Windows")
        with step("configuration"):
            config = read_config()
        cache = os.environ.get("SMOKE_CACHE", str(Path.home() / ".cache" / "aidm-smoke-test"))
        with tempfile.TemporaryDirectory(prefix="aidm-windows-smoke-") as work:
            run(args.package, args.mode, config, work, cache)
    except Exception as exc:
        print("[windows-smoke] FAIL: %s" % exc, file=sys.stderr, flush=True)
        return 1
    print("[windows-smoke] PASS", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
