# Engine 0.2.0: what the release ships and how it is proven

This note records the decisions of the grilling session of 30 October 2026 with the maintainer, the facts they rest on, and what the spec and the tickets take from it. It does not replace the spec. The words it uses are defined in `GLOSSARY.md`.

## What 0.2.0 is

0.2.0 is one coordinated AIDroneMap release: engine 0.2.0, aidm-api 2.7.0 and the first aidm-ui tag. The three changelogs tell one story.

There is no 0.1.1. Everything under `## Unreleased` in `CHANGELOG.md` ships in 0.2.0.

The engine tag is the first benchmark baseline. Every performance number comes from the benchmark process after the tag, never from a coding session.

The engine goal for the release: every GPU path the engine has is proven by the release test, and one new path is added.

## What master has today

Verified on 30 October 2026, master at 053ad8ad.

- `VERSION` is 0.1.0.
- GPU: densification runs on the GPU and is asserted. The OpenSfM GPU matcher runs on the GPU on Modal (run 40973807769) and is not asserted. ONNX is the CPU wheel in both images.
- `External-Ceres.cmake` line 25 has `-DUSE_CUDA=OFF`. `External-OpenCV.cmake` line 63 has `-DWITH_CUDA=OFF`.
- The GPU image is built from `nvidia/cuda:12.9.1` devel and runtime images, both pinned by digest.
- Sizes from the registry manifests: the published GPU image is 5.25 GB compressed, the CPU image 490 MiB (amd64) and 431 MiB (arm64).
- arm64: PoissonRecon is built with `SANITIZED_PR`, the smoke test fails on any PoissonRecon retry, OpenMVS detects 64-bit, and OpenCV's NEON kernels are on.
- No golden-output test exists.
- CI has four workflows. `tests.yml` runs the unit tests on every push and pull request. `smoke.yml` runs the cpu-amd64 smoke test on the `smoke` label. `release.yml` runs on tag pushes, by hand as a dry run, and as the nightly at 00:00 UTC with `scripts/nightly-merge.py`. `boat-build.yml` runs a no-cache build on a sandbox, on a label that does not exist.
- No scheduled run of `release.yml` had happened by 02:16 UTC on 30 October. The run on master at 01:19 was a dispatch.
- Open issues: #49 (bug, a fix in progress), #34 (dem2mesh, under test), #15 (native packages, postponed). #18 (LTO) is closed under the compiler-flags decision.
- `docs/specs/` holds 0013, 0045 and 0053. `docs/adr/` held 0001 before this note.
- aidm-api reads `UPSTREAM_VERSION` and returns "?" when it is missing (`libs/odmRunner.js` lines 96 to 101). `/info` shows "?" for the upstream version until aidm-api 2.7.0 drops the field.
- aidm-api builds `FROM ghcr.io/aidronemap/aidm:<version>`. The aidm-ui manifests pin the aidm-api image by digest per platform.

## Decisions

### 1. The GPU goal (ADR 0002)

Two tickets: the matcher assertion and ONNX on the GPU. Ceres with CUDA and OpenCV with CUDA are recorded as "no consumer, not enabled" and get no ticket. At the tag, three paths use the GPU: matcher, densification and ONNX.

The matcher assertion requires "Using GPU for image matching" (`modules/osfm.py`) and "Computing pair matching on GPU" (OpenSfM). It fails on "Falling back to CPU FLANN".

### 2. ONNX on the GPU (ADR 0003)

The GPU image installs `onnxruntime-gpu==1.22.0` from a GPU-only requirements file that `gpu.Dockerfile` passes to `configure.sh`. `requirements.txt` and the CPU image do not change. cuDNN comes from the base image: the runtime stage moves to `12.9.1-cudnn-runtime-ubuntu24.04`, pinned by digest. The pull request records the image size from the dry run and stops to ask if growth passes 2 GB.

### 3. The proof of the ONNX path (ADR 0003)

Both filters log the provider they chose when they create their session. The Modal test makes a second engine run, `--bg-removal --end-with dataset`, on the same downloaded set. It asserts the CUDA provider line and the absence of "Cannot load AI model".

`--sky-removal` cannot serve, because it skips nadir images (`stages/dataset.py` line 213). `--bg-removal` cannot go into the main run, because it masks every image and changes every output.

### 4. Golden outputs (ADR 0004)

The golden input is the project folder of a run of the 73-image set up to and including `openmvs`. The cpu-amd64 job of a dry run produces it and uploads it as an artifact with its SHA-256. The maintainer publishes it as a release of `AIDroneMap/aidm-data`. `scripts/smoke-test.conf` pins its URL and SHA-256.

The golden comparison starts the engine at `odm_filterpoints` with `--rerun-from` and compares the outputs of every later stage with hashes kept in the repository. It is a step after the smoke test in the cpu-amd64, cpu-arm64 and gpu-amd64 jobs of `release.yml`, in every mode.

A diagnostic comes first. It runs the engine twice from the same stored input on amd64 and on arm64, on a scratch machine through the skill `AGENTS.md` names, to find which outputs are byte-stable. If it finds none, the ticket records that verdict and the tag does not wait.

### 5. dem2mesh (#34) is not touched in 0.2.0

Agents could not reproduce the crash. Issue #31 was closed after 60 replays of the 0.1.0 image on two machine shapes all exited 0, and #34 is under test with the same expectation. The release's own runs (dry runs, nightly, golden step, tag run) are the watch.

Facts kept for whoever picks it up:

- #34 failed at every concurrency from 16 down to 1 on the same run. The crash is deterministic for a given DSM and only looks random because the reconstruction varies between runs.
- The existing retry in `modules/mesh.py` lines 90 to 112 halves concurrency down to 1, so a concurrency bound would not help.
- The lead is the reference fix in OpenDroneMap/dem2mesh pull request 8, merged 5 August 2026. It stops a null edge from being carried into the next step of a vertex ring traversal on the edge-swap path, which AIDM enables with `-edgeSwapThreshold 0.15`.
- AIDM pins `WebODM/dem2mesh` at 623c8536, which does not have that fix. The two forks have diverged.
- The mechanism for an AIDM-owned fix is a build-time patch, as `docker/patch_openmvs.py` does. The idea is credited and the code is not copied.

If it ever fails a tag run, the maintainer decides at the time.

### 6. #49 is a precondition

A fix is in progress and lands before this sprint starts. The taggable minimum reads "#49 merged".

### 7. Found items

The `copy-to` slip is a `unit` ticket in 0.2.0. `modules/osfm.py` line 650 lists `'copy-to'` in `remove_always`, but the set keys use underscores (`copy_to`). Every submodel of a split run therefore receives `--copy-to` and copies its own outputs there in `odm_postprocess`. No test covers it.

Three issues, out of 0.2.0:

- `BIG_DATA` for PoissonRecon. AIDM builds with 32-bit node indices: `SuperBuild/CMakeLists.txt` line 193 sets only `SANITIZED_PR`. No overflow report exists in the reference projects. The default `--mesh-octree-depth` is 11. The decision needs a reproduction.
- Hosting the sky and background model weights under AIDroneMap after a licence check. Today `stages/dataset.py` lines 218 and 265 download them at run time from a release of a reference project, and the ONNX proof depends on that download.
- The per-band alignment idea for multispectral. It needs the multispectral dataset, which is not published.

### 8. No provider names in public CI

`boat-build.yml`, `.github/scripts/boat_build.py` and `.github/scripts/test_boat_build.py` are removed in a `workflow` pull request. The maintainer deletes the secret. The mechanism stays in the private skill that gives agents a scratch machine.

The workflow proved nothing the nightly does not: a no-cache cpu-amd64 build and smoke test on the CI set.

### 9. Lint before the tag

`lint.yml` runs on every pull request and checks:

- em dashes in tracked text files (`.md`, `.py`, `.yml`, `.sh`, `.cmake`, `.txt`);
- the `Test depth:` line and the AI disclosure in the pull request body;
- Conventional Commits on the title;
- DCO sign-off on every commit.

It is described in `AGENTS.md` in the same pull request.

A private-name check cannot live in a public workflow, since the list would publish the names. It stays in the review outside the repository.

`.github/ci-paths.yml`, the change-class matrix and the arm64 smoke test on pull requests wait until after the tag, as issues.

### 10. Version and changelog

One pull request at the end bumps `VERSION` and the four hard-coded copies: the `org.opencontainers.image.version` label in `Dockerfile` and `gpu.Dockerfile`, the `docker run` examples in `README.md`, and the usage line of `scripts/smoke-test.sh`. It renames `## Unreleased` to `## 0.2.0` with the date. Its depth is `full`, because it edits both Dockerfiles, and it is CI code, because it edits a file under `scripts/`: the maintainer starts its dry run, which builds the three images with the 0.2.0 label as a rehearsal of the tag run, and merges it by hand, since the nightly never merges a pull request that changes CI code.

It adds one paragraph that tells the release as a whole:

- engine 0.2.0 ships with aidm-api 2.7.0 and the first aidm-ui tag;
- the engine reports only its own version and no longer writes `UPSTREAM_VERSION`;
- the GPU image carries cuDNN and ONNX Runtime on the GPU and is larger by the size recorded in its pull request;
- three stages use the GPU, as the Modal test asserts.

The "Performance has not been measured" lines stay. The benchmark process's own pull requests replace them after the baseline.

`release.yml` requires the tag to equal `v$VERSION` and `## $VERSION` in the changelog.

The `IMAGE: ghcr.io/aidronemap/aidm:0.1.0` pins in `tests.yml` and `smoke.yml` are the last published image. They move to 0.2.0 in a `workflow` pull request after the tag run.

`## Unreleased` is re-added with the first change after the tag.

### 11. Benchmarks

No benchmark request is filed in this sprint. The maintainer asks for the baseline on engine 0.2.0, paired with aidm-api 2.7.0, after the tag, when the maintainer wants it.

No ticket of this release measures anything. The only numbers a ticket may quote are image sizes from the registry.

### 12. Tag timing

v0.2.0 is tagged as soon as the taggable minimum holds on master. The API image and the UI manifest follow, since the API builds from the engine image and the UI pins the API image. The site update and any announcement wait until all three exist. The engine's 0.2.0 entry is not edited afterwards to add their dates.

## The taggable minimum

1. #49 is merged.
2. The Modal test asserts the OpenSfM GPU matcher.
3. The GPU image has ONNX Runtime on the GPU, and the Modal test asserts it with the second dataset-stage run.
4. The golden comparison runs in the three build jobs of `release.yml` and is green, with its input pinned in `aidm-data` and its hashes in the repository. Or the diagnostic's written verdict says no output is byte-stable.
5. `lint.yml` is live, and `boat-build.yml` and its scripts are gone.
6. The `copy-to` fix is merged.
7. The nightly has run green on master at least once, on the full dataset, with the golden step in place.
8. The release pull request is merged, then the tag is pushed: the three images are built from source, the smoke test passes on the 73 images on each, and the Modal GPU test is green.

There is no "if in time" list. Each item is in the minimum or out of the release.

## Out of the release

- dem2mesh: #34 is watched, not touched.
- Ceres with CUDA and OpenCV with CUDA: no consumer.
- `BIG_DATA` for PoissonRecon.
- The model-weights host.
- The band-alignment idea.
- The change-class matrix and `.github/ci-paths.yml`.
- The arm64 smoke test on pull requests.
- The golden step in `smoke.yml`.
- The in-memory data path and VRAM residency: analysis items with a written verdict, kept separate.
- Cutline routing.
- Orthophoto second-pass decisions that need crops beyond #13.
- Split-merge across machines and provider connectors.
- Native Windows and macOS builds (#15, postponed).
- #18, closed under the compiler-flags decision: no AVX-512 image, no PGO, no LTO.
- Any change taken from the reference projects.
- aidm-api 2.7.0 and the aidm-ui tag themselves. They have their own sessions. This note states only what the engine owes them: version strings without the upstream version, no `UPSTREAM_VERSION` file, and the published images by tag and digest.

## Tickets, depth and runs

| Ticket | Depth | Dry runs | Modal | Who starts the dry run |
| --- | --- | --- | --- | --- |
| Matcher assertion (`scripts/gpu-smoke-modal.py`, `tests/test_gpu_smoke_modal.py`) | full | 1 | yes | maintainer (CI code) |
| ONNX GPU (runtime base digest, GPU requirements file, `configure.sh` switch, provider log line, unit test) | full | 1, at most 2 | yes | the builder |
| ONNX Modal assertion (second run) | full | 1 | yes | maintainer (CI code) |
| Golden input artifact step (`release.yml`) | full | 1 | in the run | maintainer (CI code) |
| Golden pin, hashes, compare step (`smoke-test.conf`, a compare script, `release.yml`) | full | 1, at most 2 | in the run | maintainer (CI code) |
| `lint.yml` | workflow | 0 | no | nobody |
| Remove `boat-build.yml` | workflow | 0 | no | nobody |
| `copy-to` fix | unit | 0 | no | nobody |
| Release pull request (`VERSION`, both Dockerfiles, `README.md`, `scripts/smoke-test.sh`, `CHANGELOG.md`) | full | 1 | in the run | maintainer (CI code), then the maintainer's tag push |

Order and dependencies:

- Lint, the removal of the sandbox workflow, the matcher assertion and the `copy-to` fix are independent.
- The ONNX assertion follows the ONNX GPU merge.
- The golden compare step follows the artifact step and the maintainer's upload.
- The release pull request is last. Its dry run is the rehearsal of the tag run.
- The nightly must have run green before the tag.

Six of the nine tickets are CI code (everything under `.github/` and `scripts/`). The maintainer starts their dry run, and the review loop hands them to the maintainer.

Runner cost: six to eight dry runs, 35 to 110 minutes each by the recent runs, three in parallel at most, plus the tag run. Run 40973807769 took 34 minutes with a warm cache. Runs with a full dependency build took 66 to 108 minutes.

Every pull request keeps the house rules: sign-off, Conventional Commits, the AI disclosure, the `Test depth:` line, and a `Coverage gap:` line where no test reaches the code on real images. The `copy-to` fix carries one for split-merge.

## What only the maintainer does

- Publish the golden input to `aidm-data` and approve its pin.
- Start the dry runs of the six CI-code tickets and merge them.
- Delete the sandbox secret.
- Decide #34 if it ever fails a tag run.
- Tag v0.2.0.
- Ask for the baseline after the tag.
- Re-run the Mac test only if meshing changes. It does not in 0.2.0.

## Terms

`GLOSSARY.md` at the repository root defines the words this note uses: GPU path, proof of GPU use, golden input, golden output, golden comparison, dry run, nightly, tag run and baseline.
