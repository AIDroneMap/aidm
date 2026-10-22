# Changelog

AIDM uses [Semantic Versioning](https://semver.org). Each release names the ODX version it is based on.

## 0.1.0

The first AIDM release. It is the ODX 3.8.4 engine, packaged and named as AIDM. Processing is unchanged, so results match ODX 3.8.4. This release makes no performance or accuracy claims.

### Changed

- The engine calls itself AIDM. The log banner, `--version`, `--help`, the PDF report and the Software tag of GeoTIFF outputs show `AIDM 0.1.0 (ODX 3.8.4)`.
- `VERSION` holds the AIDM version, `0.1.0`. The ODX version it is based on is in the new `UPSTREAM_VERSION` file.
- `log.json` reports `"engine": "AIDM"` and `0.1.0` in `version` and `odmVersion`. A new field, `upstreamVersion`, holds `ODX 3.8.4`.
- A new `ENGINE` file holds `aidm`. AIDM API reports it as the engine name in `GET /info`.
- `benchmark.txt` and the EXIF Software tag of frames extracted from video say AIDM.
- The CPU image is built for a generic CPU, as the GPU image already was. It runs on machines older than the one that built it.
- Both images carry OCI labels for source, version, licence, title and description.

### Removed

- The Windows and macOS build files inherited from ODX, which were not maintained: `*.bat`, `innosetup.iss`, `configure_macos.sh`, `portable.Dockerfile`, `vcpkg.json` and `vcpkg-requirements.txt`. The CPU `Dockerfile` now builds what `portable.Dockerfile` built.

### Not changed

- Command-line options, `settings.yaml` keys and defaults, output files and folders, stage names and Python modules are the same as in ODX 3.8.4.

### Known limitations

- No performance or accuracy changes compared to ODX 3.8.4 yet.
- Images are for Linux on x86-64. arm64 images will follow.
- `configure.py`, the Windows configure script from ODX, is still in the repository. It no longer works, because the vcpkg files it reads are gone.
