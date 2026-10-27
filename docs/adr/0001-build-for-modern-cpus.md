# ADR 0001: Build for modern CPUs only

Status: accepted, October 2026

## Context

AIDM is built for large jobs. A normal dataset is 500 images and 3 GB of data, and the work is meant to spread across many machines. A CPU that cannot run such a job in reasonable time is not a target.

AIDM 0.1.0 compiled both images for `-march=nehalem` on x86-64 (2008, no AVX) and `-march=armv8-a` on arm64, through the wrappers in `docker/`. ODX did this only for its portable image. OpenSfM, OpenMVS, Ceres, PoissonRecon and the other C++ tools use Eigen, which vectorises with the compiler's target flags, so they never used AVX2 or FMA. OpenCV and NumPy pick their code paths at run time and were not affected.

## Decision

Both images compile every C++ tool, dependencies included, for one target:

- x86-64: `x86-64-v3` (AVX2, FMA, BMI2). This is Intel Haswell (2013) and AMD Zen (2017) and later. Not `native`, which would bake in the CPU of the build runner. Not `x86-64-v4`, because Intel disabled AVX-512 on its consumer CPUs from the 12th generation, so it would exclude current desktops.
- arm64: `armv8.2-a`. This covers Apple silicon, AWS Graviton 2 and later, Ampere and the Raspberry Pi 5. It drops the Raspberry Pi 4.

The engine checks the CPU before it loads any compiled tool and stops with one line on a CPU below the minimum (`modules/cpu.py`). There is no fallback and no second image for older CPUs.

## Consequences

- A crash or the start-up message on a CPU below the minimum is expected and documented. It is not a bug.
- Pull requests that add compatibility for older CPUs, or that reopen this decision, are declined. `AGENTS.md` says so for agents.
- The new target brings no speed gain. Measured on an AMD Ryzen 9 9900X, full runs take the same time as with 0.1.0, and the openmvs stage is up to 8% slower on a small dataset. A profile of DensifyPointCloud shows its main loop costs the same in both builds; the extra time comes from GCC inlining some small functions differently. The decision rests on the hardware AIDM targets, not on speed.
- Link-time optimisation was tried and left out. It made OpenMVS 7.9% slower on a 524-image dataset and did not make any other stage measurably faster.
- AVX-512 builds and profile-guided optimisation are separate questions, to decide after the profiling work.
