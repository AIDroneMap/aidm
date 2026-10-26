"""Stops the engine on CPUs that cannot run the compiled tools.

The images compile every C++ tool for x86-64-v3 on x86-64 and for armv8.2-a
on arm64 (see docker/README). On an older CPU these tools die with an illegal
instruction, sometimes hours into a job. run.py calls check() before it
imports anything compiled, so the engine stops at once with one plain line.

This module must only import the standard library.
"""

import platform
import sys

# For each machine type: the cpuinfo field that lists the CPU features, the
# features the compiled tools need, and the message shown when one is missing.
REQUIREMENTS = {
    "x86_64": (
        "flags",
        ("avx2", "fma"),
        "AIDM needs a CPU with AVX2 and FMA (2013 or later). This CPU does not have them.",
    ),
    # GCC builds armv8.2-a code with the ARMv8.1 instructions: LSE atomics,
    # RDMA and CRC32. Linux lists them as atomics, asimdrdm and crc32. No
    # cpuinfo feature names ARMv8.2 itself, so the check looks for these.
    "aarch64": (
        "Features",
        ("atomics", "asimdrdm", "crc32"),
        "AIDM needs an ARMv8.2-A CPU, such as AWS Graviton 2, Apple M1 or "
        "Raspberry Pi 5. This CPU does not support ARMv8.2-A.",
    ),
}

# platform.machine() names on other systems
MACHINE_ALIASES = {"amd64": "x86_64", "arm64": "aarch64"}


def requirements(machine):
    """Returns (field, features, message) for a platform.machine() name, or
    None for a machine type the images are not built for."""
    machine = machine.lower()
    return REQUIREMENTS.get(MACHINE_ALIASES.get(machine, machine))


def cpu_features(cpuinfo, field):
    """Returns the features on the first `field` line of a /proc/cpuinfo
    text, or None if there is no such line."""
    for line in cpuinfo.splitlines():
        name, sep, value = line.partition(":")
        if sep and name.strip() == field:
            return set(value.split())
    return None


def missing_features(machine, cpuinfo):
    """Returns the required features that cpuinfo does not list. Returns an
    empty list when the machine type is unknown or cpuinfo has no feature
    line for it (for example under QEMU user emulation): the check only
    stops a CPU that it can read."""
    req = requirements(machine)
    if req is None:
        return []
    field, required, _ = req
    features = cpu_features(cpuinfo, field)
    if features is None:
        return []
    return [f for f in required if f not in features]


def check(machine=None, cpuinfo_path="/proc/cpuinfo"):
    """Exits with a message if this CPU is below the minimum AIDM is built for."""
    machine = machine or platform.machine()
    try:
        with open(cpuinfo_path) as f:
            cpuinfo = f.read()
    except OSError:
        # Not Linux, or no /proc: nothing to check
        return
    if missing_features(machine, cpuinfo):
        sys.exit(requirements(machine)[2])
