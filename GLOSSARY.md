# AIDM

AIDM is the photogrammetry engine of AIDroneMap. This glossary fixes the words the documentation, the specs and the pull requests use for how the engine is built, tested and released.

## Language

### Tests and runs

**Unit test**:
The tests under `tests/` on small synthetic inputs, run by `./test.sh` and by CI on every push.

**Smoke test**:
One pipeline run on real images that checks the outputs exist and are georeferenced.
_Avoid_: integration test, end-to-end test

**Dry run**:
A run of the release workflow started by hand on a branch with `publish=false`. It builds the three images, runs the smoke test and the GPU test on the CI dataset, and publishes nothing. It is how the `full` test depth runs.
_Avoid_: release test, test run

**Nightly**:
The scheduled run of the release workflow on master at 00:00 UTC, with no build cache, on the full dataset.
_Avoid_: nightly build

**Tag run**:
The run of the release workflow started by a version tag. It builds, tests and publishes the images of a release on the full dataset.
_Avoid_: release run, publish run

**CI dataset**:
The 32-image set used by smoke tests of pull requests, dry runs and local runs. It only proves the code runs.
_Avoid_: small dataset, subset

**Full dataset**:
The 73-image set used by the nightly, the tag run, the golden input and every benchmark.
_Avoid_: default dataset

**Baseline**:
A named engine version and API version pair measured by the benchmark process, which every later measurement compares against.
_Avoid_: reference run

**Build input**:
A file whose change needs the images rebuilt from source: the Dockerfiles, `SuperBuild/`, `requirements.txt`, `configure.sh`, `docker/` and the release workflow.

**CI code**:
Everything under `.github/` and `scripts/`. A dry run executes the branch's copy of it with the repository's credentials.

**Test depth**:
The declaration in a pull request of which test runs the changed code: `unit`, `smoke`, `full` or `workflow`.

**Coverage gap**:
The line in a pull request that names a changed code path no test runs on real images.

### GPU and outputs

**GPU path**:
A stage, or a step of a stage, that runs on the GPU when the GPU image finds one, and on the CPU otherwise.
_Avoid_: GPU feature, GPU acceleration

**Proof of GPU use**:
The log line a GPU path writes only when it ran on the GPU, which the GPU test asserts.
_Avoid_: GPU check

**Golden input**:
A stored project folder of a run of the full dataset through densification, published with the engine commit that produced it, from which the golden comparison starts.
_Avoid_: stored reconstruction, fixture

**Golden output**:
An output file of the stages after densification whose hash is kept in the repository.
_Avoid_: reference output, expected output

**Golden comparison**:
The step that runs the engine from the golden input and compares every golden output with its stored hash.
_Avoid_: golden test, regression test
