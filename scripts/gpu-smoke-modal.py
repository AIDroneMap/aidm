#!/usr/bin/env python3
#
# GPU smoke test of the AIDM GPU image on a real NVIDIA GPU, on Modal.
#
# Starts the image by digest in a Modal Sandbox with one GPU, processes the
# same dataset as scripts/smoke-test.sh, and checks that:
#   - the engine found the GPU ("nvidia-smi detected");
#   - every DensifyPointCloud run used the GPU (--cuda-device -1, never -2);
#   - the outputs listed in scripts/smoke-test.conf exist and are not empty.
# It prints the engine log, the GPU, the driver, the wall time and a cost
# estimate, and exits non-zero on any failure.
#
# Usage:
#   scripts/gpu-smoke-modal.py ghcr.io/aidronemap/aidm@sha256:<digest>
#
# Environment:
#   MODAL_TOKEN_ID, MODAL_TOKEN_SECRET   Modal credentials (required)
#   MODAL_ENVIRONMENT                    Modal environment to run in (required)
#   REGISTRY_USERNAME, REGISTRY_PASSWORD credentials to pull the image, for
#                                        example a short-lived GITHUB_TOKEN
#                                        (optional for public images)
#   SMOKE_GPU        Modal GPU type (default: T4, the cheapest)
#   SMOKE_CPU        CPU cores for the Sandbox (default: 4)
#   SMOKE_MEMORY     memory for the Sandbox in MiB (default: 16384)
#   SMOKE_TIMEOUT    Sandbox lifetime in seconds (default: 2400)
#   SMOKE_OPTIONS    engine options (default: DEFAULT_OPTIONS in smoke-test.conf)
#   SMOKE_CACHE      where the dataset is downloaded once
#                    (default: ~/.cache/aidm-smoke-test)
#
# Everything it creates on Modal is ephemeral: one app that stops when the
# script ends, one Sandbox, and a registry secret that exists for this run
# only. It creates no deployed apps, volumes or named secrets.
#
# Needs the modal Python package; the release workflow pins its version.

import os
import re
import shlex
import sys
import tarfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CONF = os.path.join(HERE, "smoke-test.conf")

APP_NAME = "aidm-gpu-smoke"
PROJECT = "/datasets/project"
ENGINE = ["/usr/bin/python3", "/code/run.py"]

# Modal list prices in USD per second, from https://modal.com/pricing
# (checked 2026-10-25). Used only for the cost estimate in the log.
GPU_PRICES = {
    "T4": 0.000164,
    "L4": 0.000222,
    "A10": 0.000306,
    "L40S": 0.000542,
}
CPU_PRICE = 0.0000131  # per core
MEMORY_PRICE = 0.00000222  # per GiB


def log(msg):
    print("[gpu-smoke] %s" % msg, flush=True)


def read_config(path=CONF):
    """Read the KEY="value" lines of smoke-test.conf into a dict."""
    config = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, sep, value = line.partition("=")
            if not sep or not key.isidentifier():
                raise ValueError("%s: cannot parse line: %s" % (path, line))
            config[key] = " ".join(shlex.split(value))
    return config


def check_engine_log(text):
    """Return a list of reasons why the engine log does not prove GPU use.

    An empty list means the log shows that the engine found the GPU and ran
    every DensifyPointCloud on it.
    """
    lines = text.splitlines()
    problems = []

    if any("No nvidia-smi detected" in l for l in lines):
        problems.append("the engine logged 'No nvidia-smi detected'")
    elif not any("nvidia-smi detected" in l for l in lines):
        problems.append("the engine did not log 'nvidia-smi detected'")

    densify = [l for l in lines if "running" in l and "DensifyPointCloud" in l]
    if not densify:
        problems.append("DensifyPointCloud did not run")
    for l in densify:
        if "--cuda-device -1" not in l:
            problems.append("DensifyPointCloud ran without --cuda-device -1: %s" % l.strip())

    if any("OpenMVS failed with GPU" in l for l in lines):
        problems.append("OpenMVS failed on the GPU and fell back to the CPU")

    return problems


def check_nvidia_smi(code, output):
    """Return a list of problems with the nvidia-smi GPU query.

    Only the query decides pass or fail. The CUDA version line in the
    nvidia-smi header is printed for information and never checked.
    """
    if code != 0:
        return ["nvidia-smi failed in the Sandbox (exit code %s)" % code]
    if not output.strip():
        return ["nvidia-smi listed no GPU in the Sandbox"]
    return []


def cuda_version(header):
    """Return the CUDA version shown by nvidia-smi, or None if not found."""
    match = re.search(r"CUDA(?: Driver)? Version:\s*([0-9][0-9.]*)", header)
    return match.group(1) if match else None


def fetch_dataset(config, cache):
    """Download the dataset images once, at the pinned commit."""
    commit = config["DATASET_COMMIT"]
    expected = int(config["DATASET_IMAGES"])
    folder = os.path.join(cache, "brighton_beach-%s" % commit)
    images = os.path.join(folder, "images")

    if not os.path.isdir(images):
        url = "%s/archive/%s.tar.gz" % (config["DATASET_REPO"], commit)
        log("Downloading dataset to %s" % folder)
        tmp = folder + ".tmp"
        os.makedirs(os.path.join(tmp, "images"), exist_ok=True)
        with urllib.request.urlopen(url) as response:
            with tarfile.open(fileobj=response, mode="r|gz") as archive:
                for member in archive:
                    parts = member.name.split("/")
                    if member.isfile() and len(parts) == 3 and parts[1] == "images":
                        with archive.extractfile(member) as src:
                            with open(os.path.join(tmp, "images", parts[2]), "wb") as dst:
                                dst.write(src.read())
        os.rename(tmp, folder)

    files = sorted(f for f in os.listdir(images) if f.lower().endswith(".jpg"))
    if len(files) != expected:
        raise RuntimeError("expected %d images in %s, found %d" % (expected, images, len(files)))
    return [os.path.join(images, f) for f in files]


def run(sb, cmd, echo=True, timeout=None):
    """Run a shell command in the Sandbox, print its output, return (code, output)."""
    p = sb.exec("bash", "-c", cmd + " 2>&1", timeout=timeout)
    out = []
    for line in p.stdout:
        out.append(line)
        if echo:
            print(line, end="", flush=True)
    p.wait()
    return p.returncode, "".join(out)


def env_required(name):
    value = os.environ.get(name, "")
    if not value:
        log("FAIL: %s is not set" % name)
        sys.exit(2)
    return value


def main():
    if len(sys.argv) != 2 or "@sha256:" not in sys.argv[1]:
        print("Usage: %s <image>@sha256:<digest>" % sys.argv[0], file=sys.stderr)
        return 2
    image_ref = sys.argv[1]

    # Never fall back to the default Modal environment
    environment = env_required("MODAL_ENVIRONMENT")
    env_required("MODAL_TOKEN_ID")
    env_required("MODAL_TOKEN_SECRET")

    config = read_config()
    gpu = os.environ.get("SMOKE_GPU", "T4")
    cpu = float(os.environ.get("SMOKE_CPU", "4"))
    memory = int(os.environ.get("SMOKE_MEMORY", "16384"))
    timeout = int(os.environ.get("SMOKE_TIMEOUT", "2400"))
    options = os.environ.get("SMOKE_OPTIONS") or config["DEFAULT_OPTIONS"]
    cache = os.environ.get("SMOKE_CACHE") or os.path.expanduser("~/.cache/aidm-smoke-test")
    outputs = config["OUTPUTS"].split()

    images = fetch_dataset(config, cache)

    import modal

    registry_secret = None
    if os.environ.get("REGISTRY_USERNAME") and os.environ.get("REGISTRY_PASSWORD"):
        # Exists for this run only, never stored as a named Modal secret
        registry_secret = modal.Secret.from_dict({
            "REGISTRY_USERNAME": os.environ["REGISTRY_USERNAME"],
            "REGISTRY_PASSWORD": os.environ["REGISTRY_PASSWORD"],
        })

    # Without add_python, Modal adds nothing to the image, so the engine runs
    # with its own Python. Clearing the entrypoint lets the Sandbox run our
    # command instead of run.py; it changes no files.
    image = modal.Image.from_registry(
        image_ref,
        secret=registry_secret,
        setup_dockerfile_commands=["ENTRYPOINT []"],
    )

    log("Image %s" % image_ref)
    log("Modal: GPU %s, %g CPU cores, %d MiB memory, timeout %d s" % (gpu, cpu, memory, timeout))

    app = modal.App(APP_NAME)
    problems = []
    sandbox_seconds = 0.0
    engine_seconds = 0.0

    smi = ""

    with modal.enable_output(), app.run(environment_name=environment):
        sb = modal.Sandbox.create(
            "sleep", str(timeout),
            app=app,
            image=image,
            gpu=gpu,
            cpu=cpu,
            memory=memory,
            timeout=timeout,
        )
        # Count Sandbox time from here: the image build above is not GPU time
        started = time.monotonic()
        try:
            log("Sandbox %s started" % sb.object_id)

            log("GPU and driver:")
            code, smi = run(sb, "nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader")
            problems += check_nvidia_smi(code, smi)
            # Information only: the header format changes between drivers
            _, header = run(sb, "nvidia-smi", echo=False)
            log("CUDA version: %s" % (cuda_version(header) or "not shown by nvidia-smi"))

            # Shows which libcuda.so.1 OpenMVS loads: the driver, or the stub
            # from gpu.Dockerfile, which must lose when a driver is present
            log("Environment and CUDA driver library:")
            run(sb, "echo \"python3: $(command -v python3)\"; echo \"LD_LIBRARY_PATH=$LD_LIBRARY_PATH\";"
                    " ldconfig -p | grep 'libcuda\\.so'; "
                    "ldd /code/SuperBuild/install/bin/OpenMVS/DensifyPointCloud | grep 'libcuda\\.so'")

            log("Uploading %d images" % len(images))
            for path in images:
                sb.filesystem.copy_from_local(path, "%s/images/%s" % (PROJECT, os.path.basename(path)))

            cmd = ENGINE + ["--project-path", os.path.dirname(PROJECT), os.path.basename(PROJECT)]
            cmd += shlex.split(options) + ["--max-concurrency", str(max(1, int(cpu)))]
            log("Running %s" % " ".join(shlex.quote(c) for c in cmd))
            engine_started = time.monotonic()
            code, engine_log = run(sb, "exec " + " ".join(shlex.quote(c) for c in cmd))
            engine_seconds = time.monotonic() - engine_started
            if code != 0:
                problems.append("the engine exited with code %s" % code)

            problems += check_engine_log(engine_log)

            for out in outputs:
                try:
                    size = sb.filesystem.stat("%s/%s" % (PROJECT, out)).size
                except Exception:
                    size = 0
                if size > 0:
                    log("ok    %s (%d bytes)" % (out, size))
                else:
                    log("MISSING OR EMPTY  %s" % out)
                    problems.append("%s is missing or empty" % out)
        finally:
            sb.terminate()
            sandbox_seconds = time.monotonic() - started

    gpu_line = smi.strip().splitlines()[0] if smi.strip() else "unknown"
    log("GPU: %s" % gpu_line)
    log("Engine time %d s, Sandbox time %d s" % (engine_seconds, sandbox_seconds))
    if gpu in GPU_PRICES:
        per_second = GPU_PRICES[gpu] + cpu * CPU_PRICE + memory / 1024 * MEMORY_PRICE
        log("Estimated cost: USD %.2f (list prices x Sandbox time, image build not included)"
            % (per_second * sandbox_seconds))

    if problems:
        for p in problems:
            log("FAIL: %s" % p)
        return 1
    log("PASS: the engine ran on the GPU and produced every output")
    return 0


if __name__ == "__main__":
    sys.exit(main())
