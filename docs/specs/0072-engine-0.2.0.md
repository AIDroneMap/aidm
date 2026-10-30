# Engine 0.2.0: what the release ships and how it is proven

Issue: https://github.com/AIDroneMap/aidm/issues/72

Written from the design note `docs/design/engine-0.2.0.md`, ADR 0002, ADR 0003 and ADR 0004, and the words in `GLOSSARY.md`. The note records the decisions and the facts; this spec says what has to be true on `master` for v0.2.0 to be tagged, and how each part is proven. The tickets are cut from the "Implementation decisions" and "Testing decisions" sections, one pull request each.

## Problem statement

A user who runs the GPU image today gets densification on the GPU and, without knowing it, feature matching on the GPU as well. Nothing in the release test asserts the second path, so a build that silently dropped it would still be published. Sky removal and background removal run on the CPU in the GPU image, because both images install the CPU wheel of ONNX Runtime. A user who runs a split dataset with `--copy-to` gets every submodel copying its own outputs into the target folder. And nobody can prove that a change to meshing, the DEM or the orthophoto left the outputs unchanged: the release test checks that outputs exist and are georeferenced, not what they contain, and two full runs are never identical, so there is no reference to compare against.

For the maintainer, 0.2.0 is also the first benchmark baseline. A baseline on a release whose GPU use is unasserted and whose outputs have no reference is a number without a meaning.

## Solution

0.2.0 makes every GPU path the engine has provable and adds one: ONNX Runtime on the GPU. The Modal GPU test asserts, from the engine log, that the matcher, densification and the ONNX filters ran on the GPU, and fails the release on a silent fall back to the CPU. A golden comparison, started from a stored run of the full dataset through densification, compares every later output by hash in the three build jobs of the release workflow, so a change to meshing, DEM, orthophoto or tiles proves "outputs unchanged" or declares the change. The `copy-to` slip is fixed. The public CI loses the sandbox workflow and gains a lint job for the writing rules every pull request must follow. The release pull request bumps the version, writes the 0.2.0 changelog as one story with aidm-api 2.7.0 and the first aidm-ui tag, and its dry run rehearses the tag run.

## User stories

1. As a user of the GPU image, I want feature matching, densification and the ONNX filters to run on the GPU when one is present, so that the image I pulled for its GPU uses it.
2. As a user of the GPU image, I want one log line per GPU path that says the path ran on the GPU, or fell back to the CPU, so that I can tell from the log alone what my hardware did.
3. As a user of the GPU image with `--sky-removal` or `--bg-removal`, I want the masks computed on the GPU, so that the dataset stage uses the hardware I run on.
4. As a user of the CPU image, I want its inputs and behaviour unchanged by the GPU work, so that an engine I already run keeps giving the same outputs.
5. As a user of either image on a CPU without a GPU, I want the GPU image to keep running on the CPU with every filter and matcher available, so that one image serves both machines.
6. As a user who runs a split dataset with `--copy-to`, I want only the merged results copied to the target folder, so that submodel outputs do not land there.
7. As a user who reads the changelog, I want the 0.2.0 entry to say which stages use the GPU and that the claim is asserted by the release test, so that I know what "GPU" means for this release.
8. As a user who reads the changelog, I want the lines that say "Performance has not been measured" to stay until the benchmark process writes a number, so that I never read a speed claim nobody measured.
9. As a user of aidm-api, I want the engine's version reported without an upstream version and without an `UPSTREAM_VERSION` file, so that `/info` shows the engine as what it is.
10. As a maintainer, I want the Modal GPU test to fail when any GPU path falls back to the CPU, so that I never tag a release whose GPU use regressed.
11. As a maintainer, I want the golden comparison to run in every mode of the release workflow on all three images, so that a dry run tells a builder, before merge, whether the outputs changed.
12. As a maintainer, I want the golden input published as a release of the dataset repository and pinned by URL and SHA-256, so that a comparison is reproducible from a pin and never from a file on someone's machine.
13. As a maintainer, I want the output hashes in the repository, changed only by a pull request that says why, so that every deliberate output change has a record.
14. As a maintainer, I want to know which outputs are byte-stable on amd64 and on arm64 before the hashes are pinned, so that the comparison does not fail on noise.
15. As a maintainer, I want every pull request checked for em dashes, the test depth line, the disclosure footnote, Conventional Commits and the sign-off by a workflow, so that the reviewer spends its rounds on the change.
16. As a maintainer, I want no provider of private infrastructure named in the public repository, so that the public record holds only what is public.
17. As a maintainer, I want the release pull request to bump the version everywhere it is copied, and its dry run to build the images with the new label, so that the tag run holds no surprise.
18. As a maintainer, I want the nightly to have run green at least once on the full dataset before I tag, so that the tag run repeats a path that already passed.
19. As a maintainer, I want the taggable minimum written down, so that "done" is a list I can check and not a feeling.
20. As a builder, I want each ticket to name its test depth, the seam its test uses and whether a maintainer starts its dry run, so that I do not start runs that prove nothing and do not wait on nobody.
21. As a builder, I want the diagnostic runs of the golden ticket to happen on a scratch machine and never as a second dry run on the same commit, so that I follow the rules on runner time.
22. As the independent reviewer, I want each pull request to carry the evidence its depth requires, so that I can approve from the record.
23. As the benchmark process, I want the tag run to have asserted every GPU path and the golden comparison to have passed, so that the baseline on 0.2.0 measures a release whose outputs and GPU use are known.
24. As the aidm-api 2.7.0 session, I want to know that the engine no longer writes `UPSTREAM_VERSION` and that the images are published by tag and digest, so that the API drops the field and builds from the tag.
25. As the aidm-ui session, I want the published aidm-api image to exist before the first UI tag, so that the manifest pins a real digest.

## Implementation decisions

Decisions come from the design note; the ticket that implements one does not reopen it.

### GPU paths (ADR 0002)

- The engine has three GPU paths at the tag: the OpenSfM Hamming matcher, OpenMVS densification, and the ONNX filters for sky removal and background removal. Ceres with CUDA and OpenCV with CUDA are not enabled: no code of the engine would call them, and the design note records why.
- The Modal GPU test asserts every path from the engine log. For the matcher it requires the engine's "Using GPU for image matching" line and OpenSfM's "Computing pair matching on GPU" line, and fails on "Falling back to CPU FLANN". The densification assertion stays as it is. The ONNX assertion is described below.
- A new GPU path lands with its assertion in the same release. The changelog counts a stage as "on the GPU" only once the Modal test asserts it.
- The matcher path is reached on the CI dataset because that set has exactly the number of images at which the engine turns the GPU matcher on. The dataset pins do not change.

### ONNX Runtime on the GPU (ADR 0003)

- The GPU image installs the GPU wheel of ONNX Runtime, version 1.22.0, in place of the CPU wheel. The pin lives in a GPU-only requirements file that the GPU Dockerfile hands to the configure script. The shared requirements file and the CPU image do not change.
- cuDNN comes from the base image: the runtime stage of the GPU image moves to the cuDNN runtime tag of the same CUDA 12.9.1 image family, pinned by digest. No pip-provided CUDA or cuDNN libraries.
- Both filters log the provider they chose when they create their session, in one line that names the filter and the provider. The line appears in every run, CPU or GPU.
- The pull request records the compressed size of the GPU image from its dry run next to the size of the published 0.1.0 GPU image, and stops to ask the maintainer if the growth passes 2 GB.
- The Modal GPU test makes a second engine run in the same sandbox on the same downloaded set with `--bg-removal --end-with dataset`, after the main run. It asserts the CUDA provider line of the background filter and the absence of "Cannot load AI model". The main run's options and outputs do not change. Sky removal cannot serve as the proof because it skips nadir images and the pinned sets are nadir.
- The models keep being downloaded at run time from their current URL. Hosting them under AIDroneMap is an issue outside this release.

### Golden comparison (ADR 0004)

- The golden input is the project folder of a run of the full dataset up to and including `openmvs`, produced by the cpu-amd64 job of a release dry run when a workflow input asks for it, and uploaded as a run artifact with its SHA-256. The maintainer publishes it as a release of the dataset repository, named after the dataset tag and the engine commit that produced it. The smoke configuration pins its URL and SHA-256 next to the two image sets, and the download helper that already handles the image sets downloads it.
- The golden comparison starts the engine at `odm_filterpoints` on that folder, with the smoke options, and compares the outputs of every later stage with hashes kept in the repository, one list per architecture if the diagnostic shows they differ. It is one step after the smoke test in the cpu-amd64, cpu-arm64 and gpu-amd64 jobs of the release workflow, in every mode. It fails the job on any difference and writes a short report: file, expected hash, actual hash. The GPU job runs it on its CPU runner.
- Before the hashes are pinned, the golden ticket runs a diagnostic on a scratch machine: two runs from the same golden input on amd64 and on arm64, to learn which outputs are byte-stable. An output that is not byte-stable gets a documented tolerance or stays out, and the list is written in the ticket and in the compare script. If nothing is byte-stable, the ticket records that verdict and the tag does not wait.
- Hashes change only in a pull request that changes an output on purpose and says so in the changelog. A change before `openmvs` that is meant to alter the reconstruction needs a new golden input, which is a new release in the dataset repository.

### Split-merge `copy-to`

- The list of options the engine strips before it starts a submodel names the copy target with a hyphen, while the keys it is matched against use an underscore, so the entry never matches. The fix makes the entry match. Each submodel then runs without a copy target, and only the parent copies the merged results.

### CI

- A lint workflow runs on every pull request and checks: no em dash in tracked text files (`.md`, `.py`, `.yml`, `.sh`, `.cmake`, `.txt`); the `Test depth:` line and the disclosure footnote in the pull request body; Conventional Commits on the title; the sign-off on every commit. It describes itself in the agent rules in the same pull request. A private-name check stays outside the repository.
- The sandbox build workflow and its two scripts are removed. The maintainer deletes its secret. The nightly already does what it did.
- The change-class matrix, path filters and the arm64 smoke test on pull requests wait until after the tag.

### Release

- One pull request bumps `VERSION` and its four hard-coded copies (the image version label in both Dockerfiles, the run examples in the README, the usage line of the smoke script), renames `## Unreleased` to `## 0.2.0` with the date, and adds one paragraph that tells the release as a whole: engine 0.2.0 ships with aidm-api 2.7.0 and the first aidm-ui tag; the engine reports only its own version and no longer writes `UPSTREAM_VERSION`; the GPU image carries cuDNN and ONNX Runtime on the GPU and is larger by the size recorded in its pull request; three stages use the GPU, as the Modal test asserts.
- The "Performance has not been measured" lines stay. The benchmark process replaces them after the baseline, in its own pull requests.
- The pull request is `full` and CI code: the maintainer starts its dry run, which builds the three images with the 0.2.0 label as the rehearsal of the tag run, and merges it by hand.
- The last-published-image pins of the unit and smoke workflows move to 0.2.0 after the tag run, in a `workflow` pull request.
- v0.2.0 is tagged as soon as the taggable minimum holds. The API image and the UI manifest follow; the announcement waits for all three.

### The taggable minimum

1. #49 merged.
2. The Modal test asserts the OpenSfM GPU matcher.
3. The GPU image has ONNX Runtime on the GPU and the Modal test asserts it.
4. The golden comparison runs green in the three build jobs of the release workflow, with its input pinned and its hashes in the repository, or the diagnostic's written verdict that no output is byte-stable.
5. The lint workflow is live; the sandbox workflow and its scripts are gone.
6. The `copy-to` fix is merged.
7. The nightly has run green on `master` at least once, on the full dataset, with the golden step in place.
8. The release pull request is merged, then the tag is pushed and the tag run is green on the three images and the Modal GPU test.

## Testing decisions

A good test here checks what a user or the release sees: a log line, an output file, a hash, a workflow verdict. It does not check how the code got there. Nothing in this spec measures speed or memory.

### Seams

Every ticket uses one of five seams. Four exist today; one is new.

1. **The Modal GPU test** (`scripts/gpu-smoke-modal.py`). Existing. It parses the engine log and fails on missing or forbidden lines. The matcher and ONNX assertions are new checks in the same parser, with unit tests next to the existing ones in `tests/test_gpu_smoke_modal.py`, which feed the parser log excerpts and check the verdict. Prior art: the `--cuda-device -1` check and its tests.
2. **The build jobs of the release workflow**. Existing. The golden input step and the golden comparison step are steps in these jobs, proven by a dry run that a maintainer starts. Prior art: the smoke test step and the pushed-image check.
3. **Unit tests under `tests/`**. Existing. The `copy-to` fix adds a case to the existing tests of the submodel argument builder (`tests/test_osfm.py`). The provider log line gets a test that creates a filter with a fake session and reads the log. The release pull request is covered by `tests/test_version.py`, which already checks that the image labels match `VERSION`. Prior art: those files.
4. **A workflow's own run on the pull request**. Existing. The lint workflow and the removal of the sandbox workflow are proven by their run on the pull request that adds or removes them, as the `workflow` depth says.
5. **The golden compare script**. New, under `scripts/`. It takes a folder and a hash list and prints the report. Its unit test, next to the other script tests, feeds it a synthetic folder with one changed file and checks the report and the exit code. It is the only new seam, and it sits as high as the comparison can be tested without an engine run.

### Depth per ticket

| Ticket | Depth | Seam | Dry run started by |
| --- | --- | --- | --- |
| Matcher assertion | full | 1 | maintainer (CI code) |
| ONNX Runtime on the GPU | full | 3 for the log line, 1 for the evidence in the dry run log | the builder |
| ONNX Modal assertion | full | 1 | maintainer (CI code) |
| Golden input artifact step | full | 2 | maintainer (CI code) |
| Golden pin, hashes and compare step | full | 5, then 2 | maintainer (CI code) |
| Lint workflow | workflow | 4 | nobody |
| Remove the sandbox workflow | workflow | 4 | nobody |
| `copy-to` fix | unit | 3, with a `Coverage gap:` line for split-merge | nobody |
| Release pull request | full | 3, then 2 | maintainer (CI code), then the tag push |

Six to eight dry runs for the release, three in parallel at most, plus the tag run. The diagnostic of the golden ticket runs on a scratch machine, never as a second dry run on the same commit.

## Out of scope

- dem2mesh (#34): watched by the release's own runs, not touched. The design note keeps the lead and the mechanism.
- Ceres with CUDA and OpenCV with CUDA: no consumer, not enabled.
- `BIG_DATA` for PoissonRecon, hosting the model weights under AIDroneMap, the multispectral band-alignment idea: issues outside the release.
- The change-class matrix, path filters, the arm64 smoke test on pull requests, a golden step in the smoke workflow: after the tag.
- The in-memory data path and VRAM residency: analysis items with a written verdict, separate.
- Cutline routing, orthophoto second-pass decisions that need crops, split-merge across machines and provider connectors.
- Native Windows and macOS builds (#15, postponed). Compiler flags (#18 closed: no AVX-512 image, no PGO, no LTO).
- Any change taken from the reference projects.
- aidm-api 2.7.0 and the aidm-ui tag themselves. The engine owes them: version strings without the upstream version, no `UPSTREAM_VERSION` file, the published images by tag and digest.
- Benchmark requests. None is filed in this sprint; the maintainer asks for the baseline after the tag.

## Further notes

- The design note corrects the brief this release started from: the OpenSfM GPU matcher already runs on the GPU on Modal and nothing asserts it. That is why the GPU work is one assertion and one new path, not four flags.
- Six of the nine tickets are CI code. The maintainer starts their dry runs and merges them by hand; the nightly never merges CI code.
- Every pull request carries the house rules: sign-off, Conventional Commits, the disclosure footnote, the `Test depth:` line, a `Coverage gap:` line where no test reaches the code on real images, and no number for speed or memory.
- What only the maintainer does: publish the golden input and approve its pin; start and merge the six CI-code pull requests; delete the sandbox secret; decide #34 if it ever fails a tag run; tag v0.2.0; ask for the baseline.
