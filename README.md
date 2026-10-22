# AIDM

**The distributed photogrammetry engine of [AIDroneMap](https://github.com/AIDroneMap). Every machine, one command.**

AIDM turns aerial and ground images into georeferenced orthophotos, digital elevation models, classified point clouds and textured 3D models. It is forked from [ODX](https://github.com/WebODM/ODX) and keeps ODX's command line, options and output files, so tools that work with ODX work with AIDM.

> **Status: 0.1.0, the first release.** AIDM 0.1.0 is ODX 3.8.4 packaged as AIDM. Processing and results are the same as ODX 3.8.4, and it makes no performance or accuracy claims yet. The [changelog](CHANGELOG.md) lists what changed and the [roadmap](#roadmap) shows what comes next.

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

## Quick start

AIDM runs on Linux, or anywhere Docker runs a Linux container. Pull the image:

```
docker pull ghcr.io/aidronemap/aidm:0.1.0
```

Put your images in a folder named `images`, for example `/home/you/datasets/project/images`, and run:

```
docker run -ti --rm -v /home/you/datasets:/datasets ghcr.io/aidronemap/aidm:0.1.0 --project-path /datasets project
```

Options are passed after the project name, for example `--dsm --orthophoto-resolution 2`. Run `docker run --rm ghcr.io/aidronemap/aidm:0.1.0 --help` for the full list. They are the same as ODX's, documented at [docs.webodm.org](https://docs.webodm.org/options-flags/), until AIDM's own documentation is ready at [aidronemap.dev](https://aidronemap.dev).

### GPU

With an NVIDIA GPU and the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) installed:

```
docker run -ti --rm --gpus all -v /home/you/datasets:/datasets ghcr.io/aidronemap/aidm:0.1.0-gpu --project-path /datasets project
```

### Build from source

```
git clone https://github.com/AIDroneMap/aidm.git
cd aidm
docker build -t aidm .
docker build -f gpu.Dockerfile -t aidm:gpu .
```

### Results

When processing finishes, the project folder contains:

```
odm_orthophoto/odm_orthophoto.tif          georeferenced orthophoto (GeoTIFF)
odm_dem/                                   elevation models, when --dsm or --dtm is set
odm_georeferencing/odm_georeferenced_model.laz   point cloud
odm_texturing/odm_textured_model_geo.obj   textured 3D model
```

Open GeoTIFFs in [QGIS](https://qgis.org), point clouds in [CloudCompare](https://www.cloudcompare.org) and meshes in [MeshLab](https://www.meshlab.net).

## Platforms

AIDM targets Linux. The CPU image runs on x86-64 and arm64, and the NVIDIA CUDA image on x86-64. Windows and macOS native builds from ODX are not maintained here. On those systems AIDM runs in a Linux container.

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
