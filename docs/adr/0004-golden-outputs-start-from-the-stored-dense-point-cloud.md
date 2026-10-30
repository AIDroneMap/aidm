# ADR 0004: Golden outputs start from the stored dense point cloud

Status: accepted, October 2026

## Context

`AGENTS.md` says a byte comparison of outputs starts from a stored reconstruction, because two full runs are not identical end to end. No such test exists.

Densification is the one stage whose GPU and CPU outputs differ by design (OpenMVS on `--cuda-device -1` against the CPU). A stored input taken before it would need two golden sets or a tolerance on the dense cloud.

Everything 0.2.0 may change after the GPU paths lives after densification: `odm_filterpoints`, `odm_meshing` (dem2mesh, PoissonRecon), texturing, `odm_dem`, `odm_orthophoto` and tiles. The GPU paths are proven by assertions (ADR 0002) and by the sky mask tolerance test in `tests/test_skyfilter.py`.

## Decision

The stored input, called the golden input, is the project folder of a run of the 73-image set (aidm-vineyard-small v1) up to and including `openmvs`. The cpu-amd64 job of a release dry run produces it and uploads it as a run artifact with its SHA-256. The maintainer publishes it as a release of `AIDroneMap/aidm-data`, named after the dataset tag and the engine commit that produced it. `scripts/smoke-test.conf` pins its URL and SHA-256 next to the two image sets.

The golden run starts the engine at `odm_filterpoints` with `--rerun-from` on that folder, with the smoke options. It compares the outputs of every later stage against SHA-256 hashes kept in the repository.

The comparison is a step after the smoke test in the cpu-amd64, cpu-arm64 and gpu-amd64 jobs of `release.yml`, in every mode (dry run, nightly, tag run). It fails the job on any difference and attaches a short report: file, expected hash, actual hash. The GPU job runs it on its CPU runner, since no stage after `openmvs` uses the GPU. That proves the build of the later tools in the GPU image gives the same bytes.

Hashes change only in a pull request that changes an output on purpose and says so in the changelog.

Before the hashes are pinned, a diagnostic establishes which outputs are byte-stable. It makes two runs from the same stored input on amd64 and on arm64, on a scratch machine through the skill `AGENTS.md` names. It is never a second dry run on the same commit, which `AGENTS.md` forbids. An output that is not byte-stable gets a documented tolerance or stays out, and the list is written down. If the diagnostic finds no byte-stable output, the ticket records that verdict and the tag does not wait.

## Consequences

- Each build job runs one more step of engine time on the 73-image input, whatever `SMOKE_DATASET` says, because the hashes belong to one input.
- The first pin needs the maintainer's upload, as every dataset pin does.
- A golden step in `smoke.yml` for Python-only changes to later stages is worth adding after the tag.
- The golden input must be reproduced from a new engine commit whenever a change before `openmvs` is meant to alter the reconstruction. That is a new release in `aidm-data`.
