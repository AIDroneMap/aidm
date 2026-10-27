# AIDM

**The distributed photogrammetry engine of [AIDroneMap](https://github.com/AIDroneMap). Every machine, one command.**

AIDM turns aerial and ground images into georeferenced orthophotos, digital elevation models, classified point clouds and textured 3D models. It is forked from [ODX](https://github.com/WebODM/ODX) and keeps ODX's command line, options and output files, so tools that work with ODX work with AIDM.

> **Status: 0.1.0, the first release.** AIDM 0.1.0 is ODX 3.8.4 packaged as AIDM. Processing and results are the same as ODX 3.8.4, and it makes no performance or accuracy claims yet. The [changelog](CHANGELOG.md) lists what changed and the [roadmap](#roadmap) shows what comes next.

Documentation is at [aidronemap.dev](https://aidronemap.dev): [install and run](https://aidronemap.dev/start/install-and-run/), [hardware](https://aidronemap.dev/start/hardware/), [options](https://aidronemap.dev/engine/options/) and [outputs](https://aidronemap.dev/engine/outputs/). News and the open datasets are on [aidronemap.org](https://aidronemap.org).

## Why AIDM

Drones capture more images, at higher resolution, than ever before, and processing them takes serious computing power. AIDM is built around two goals:

- **Distributed processing.** One job spread across the CPUs and GPUs you can reach: your own machines, a cluster, or cloud capacity billed by the second that stops when the job ends.
- **Simplicity.** One engine image, one command to run it, one URL to point a client at.

AIDM is the engine only. Scheduling across machines and the connectors to cloud providers live in a separate repository, and so does the open web interface. Each piece works on its own.

## What changes compared to ODX

Planned work, in rough order. Each item will only be claimed as done when a public benchmark shows it.

- **Per-stage profiling and benchmarks** against ODX on public datasets, published with every release, covering time, memory, cost and accuracy.
- **Resources known before a job starts:** the engine estimates memory and CPU per stage, so a scheduler can size each machine correctly.
- **Split-merge as the default path for large jobs:** submodels on separate machines, a parallel merge, and a report per submodel.
- **Work that moves between machines:** stage inputs and outputs in S3-compatible storage, so a stage can resume on another machine.
- **More of the pipeline on the GPU,** starting with the slowest stages the profiler finds.
- **Machine-readable progress and results:** structured progress events and a result manifest with a clear status for every output.
- **A camera test suite** with real sample files, to catch metadata bugs before users do.

## Weekly releases

AIDM merges the latest ODX into a test branch every week, runs the test datasets, and publishes a release labelled with the ODX commit it includes. You get ODX's fixes and ours together.

Before an image is published, the [release workflow](.github/workflows/release.yml) processes a real drone survey with each image ([`scripts/smoke-test.sh`](scripts/smoke-test.sh)). The survey is [aidm-vineyard-small](https://huggingface.co/datasets/AIDroneMap/aidm-vineyard-small): 73 RGB images from a DJI Mavic 3M, 5280 x 3956 pixels, with RTK corrected positions, licensed [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) (AIDroneMap, provided by ITALDRONI). Each run checks the SHA-256 of the download, then checks that the orthophoto, DSM, point cloud and textured model exist, and that the orthophoto has a projected coordinate system and covers the GPS position of every image.

The GPU image is tested twice: on a machine without a GPU, where it must fall back to the CPU, and on a real NVIDIA T4 on [Modal](https://modal.com), where the engine must use the GPU ([`scripts/gpu-smoke-modal.py`](scripts/gpu-smoke-modal.py)). The images are only tagged when both pass. On the GitHub runners the smoke test takes 16 to 36 minutes per image, and a whole release run 75 to 105 minutes when it builds the C++ dependencies from scratch, less when they come from the build cache. The GPU test takes about 14 minutes on the T4, costs about USD 0.21 per run, and needs the `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET` secrets and the `MODAL_ENVIRONMENT` variable in the `modal-gpu` environment of the repository.

## Quick start

AIDM runs on Linux, or anywhere Docker runs a Linux container. Pull the image:

```
docker pull ghcr.io/aidronemap/aidm:0.1.0
```

Put your images in a folder named `images`, for example `/home/you/datasets/project/images`, and run:

```
docker run -ti --rm -v /home/you/datasets:/datasets ghcr.io/aidronemap/aidm:0.1.0 --project-path /datasets project
```

Options are passed after the project name, for example `--dsm --orthophoto-resolution 2`. Run `docker run --rm ghcr.io/aidronemap/aidm:0.1.0 --help` for the full list. They are the same as ODX's, and every one is described in [Engine options](https://aidronemap.dev/engine/options/).

### GPU

With an NVIDIA GPU and the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) installed:

```
docker run -ti --rm --gpus all -v /home/you/datasets:/datasets ghcr.io/aidronemap/aidm:0.1.0-gpu --project-path /datasets project
```

Without `--gpus`, or on a machine without an NVIDIA GPU, the GPU image processes on the CPU.

### Build from source

```
git clone https://github.com/AIDroneMap/aidm.git
cd aidm
docker build -t aidm .
docker build -f gpu.Dockerfile -t aidm:gpu .
```

#### Build cache

The first build compiles the C++ dependencies in the SuperBuild step, which is the slow part. After that, `docker build` reuses the SuperBuild layer as long as `configure.sh`, `requirements.txt`, `docker/` and `SuperBuild/` stay the same. A change to Python code, tests or docs recompiles nothing. A change to one of those four inputs rebuilds from the SuperBuild step on.

On CI, the release workflow keeps the layers in the `ghcr.io/aidronemap/aidm-buildcache` package, one tag per variant: `cpu-amd64`, `cpu-arm64` and `gpu-amd64`. The package holds cache layers only, never a published image. Each run, dry run or release, overwrites the tag of its variant. So after a dry run on a branch that changes `SuperBuild/`, the next build of `master` compiles the dependencies again. To clear the cache, delete the package versions in the organization's package settings on GitHub, or delete the whole package. The next run rebuilds everything and fills the cache again.

### Results

When processing finishes, the project folder contains:

```
odm_orthophoto/odm_orthophoto.tif          georeferenced orthophoto (GeoTIFF)
odm_dem/                                   elevation models, when --dsm or --dtm is set
odm_georeferencing/odm_georeferenced_model.laz   point cloud
odm_texturing/odm_textured_model_geo.obj   textured 3D model
```

Open GeoTIFFs in [QGIS](https://qgis.org), point clouds in [CloudCompare](https://www.cloudcompare.org) and meshes in [MeshLab](https://www.meshlab.net). [Outputs](https://aidronemap.dev/engine/outputs/) lists every file the engine writes.

## Platforms

AIDM targets Linux. The CPU image runs on x86-64 and arm64, and the NVIDIA CUDA image on x86-64. Windows and macOS native builds from ODX are not maintained here. On those systems AIDM runs in a Linux container.

## Hardware we build for

AIDM is built for large datasets on modern machines. A normal job is 500 images and 3 GB of data, and the engine does not keep compatibility with CPUs that cannot run such a job. Both images compile every C++ tool, dependencies included, for this minimum CPU:

| Architecture | Minimum | For example |
| --- | --- | --- |
| x86-64 | x86-64-v3, with AVX2, FMA and BMI2 | Intel Haswell (2013) and later, AMD Zen (2017) and later |
| arm64 | ARMv8.2-A | AWS Graviton 2, Ampere Altra, Apple M1, Raspberry Pi 5 |

Older CPUs are not supported. That includes Intel CPUs before Haswell, the Raspberry Pi 4 and AWS Graviton 1, and also some newer Intel Pentium, Celeron and Atom models that ship without AVX2. On such a CPU the engine stops at start with one line:

```
AIDM needs a CPU with AVX2 and FMA (2013 or later). This CPU does not have them.
```

This is a decision, not a bug, and [ADR 0001](docs/adr/0001-build-for-modern-cpus.md) records why. On an x86-64 Linux machine that qualifies, `grep -o -w -e avx2 -e fma /proc/cpuinfo | sort -u` prints both words.

## Roadmap

Target dates, subject to change.

| Phase | Target | Focus |
| --- | --- | --- |
| Foundation | Nov 2026 | Linux images, test datasets, weekly sync with ODX |
| Measure | Dec 2026 to Jan 2027 | Per-stage profiling, baseline numbers against ODX |
| Speed | Feb to Mar 2027 | First speedups on the slowest stages, mixed CPU and GPU jobs |
| 1.0 | Apr to Jun 2027 | AI masking and dataset checks, documentation, first stable release |

## Contributing

Contributions are welcome. Read the [contributing guide](https://github.com/AIDroneMap/.github/blob/main/CONTRIBUTING.md) first. Every commit needs a DCO sign-off (`git commit -s`).

If a fix also applies to ODX, it is welcome there too. Note that ODX only accepts pull requests and issues opened and written by a person, with any AI use disclosed; see their contributing guidelines.

## Licence

AIDM is licensed under the [GNU Affero General Public License v3.0](LICENSE), the same licence as ODX. If you run a modified version of AIDM as a network service, you must offer its source code to the users of that service.

Copyright notices from ODX, OpenDroneMap and their contributors are kept as they are.

## Credits

AIDM exists thanks to the work of the [ODX](https://github.com/WebODM/ODX) and [WebODM](https://github.com/WebODM) maintainers and contributors, and of [OpenDroneMap](https://github.com/OpenDroneMap), where the engine began. It also builds on [OpenSfM](https://github.com/mapillary/OpenSfM), [OpenMVS](https://github.com/cdcseacave/openMVS), [PDAL](https://github.com/PDAL/PDAL), [Entwine](https://entwine.io), [MVS Texturing](https://github.com/nmoehrle/mvs-texturing), [GDAL](https://gdal.org) and [PoissonRecon](https://github.com/mkazhdan/PoissonRecon).

AIDM is an independent project. It is not affiliated with or endorsed by WebODM or OpenDroneMap.

AIDroneMap is maintained by [Dimitri Albino](https://github.com/DimitriAlbino) and sponsored by [ITALDRONI](https://italdroni.it).
