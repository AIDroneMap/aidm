# Layer order. The builder stage first copies only the files the SuperBuild
# step reads, which are configure.sh, requirements.txt, requirements-gpu.txt,
# docker/ and SuperBuild/. It compiles the C++ dependencies, then copies the
# rest of the repository. A change to Python code, tests or docs leaves the
# SuperBuild layer cached. A change to one of those five inputs rebuilds from
# the SuperBuild step on.
# WORKDIR comes after the SuperBuild step on purpose. The release workflow
# sets SOURCE_DATE_EPOCH to the commit time, and BuildKit includes it in
# the cache key of WORKDIR, so a WORKDIR before the build would rebuild
# the dependencies on every commit.
# The final stage copies /code and /usr/local as before, so the image gets
# the same files. Keep Dockerfile in the same order.

# Base images are pinned by the digest of their multi-arch index; the tag
# is kept for readers. Moving a base is its own pull request (AGENTS.md).
FROM nvidia/cuda:12.9.1-devel-ubuntu24.04@sha256:020bc241a628776338f4d4053fed4c38f6f7f3d7eb5919fecb8de313bb8ba47c AS builder

# Env variables
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONPATH="/code/SuperBuild/install/lib/python3.12/dist-packages:/code/SuperBuild/install/bin/opensfm" \
    LD_LIBRARY_PATH="/code/SuperBuild/install/lib"

# Copy only the inputs of the SuperBuild step, so that other changes do not
# invalidate it. These steps create /code. WORKDIR comes later, see the top.
COPY configure.sh requirements.txt requirements-gpu.txt /code/
COPY docker/ /code/docker/
COPY SuperBuild/ /code/SuperBuild/

# Run the build and clean the SuperBuild in one layer, so that the cached
# layer holds only the installed files, not the sources and build trees.
# Compile for the AIDM CPU target (x86-64-v3 or armv8.2-a), not for the
# CPU of the build machine. See docker/README.
# GPU_REQUIREMENTS installs the GPU wheel of ONNX Runtime in place of the
# CPU wheel that requirements.txt pins.
RUN cd /code \
  && CPU_TARGET_INSTALL=YES GPU_REQUIREMENTS=requirements-gpu.txt bash configure.sh install \
  && bash configure.sh clean

# Copy everything else
WORKDIR /code
COPY . ./

### END Builder

### Use a second image for the final asset to reduce the number and
# size of the layers. The cudnn-runtime tag adds cuDNN 9, which the CUDA
# provider of ONNX Runtime loads.
FROM nvidia/cuda:12.9.1-cudnn-runtime-ubuntu24.04@sha256:d02c4310b6d57ca0b16cd80298bdb33a74187baafe2eccd8a6a16180ddc90802

# Image metadata. Keep the version equal to the VERSION file.
LABEL org.opencontainers.image.source="https://github.com/AIDroneMap/aidm" \
      org.opencontainers.image.version="0.2.0" \
      org.opencontainers.image.licenses="AGPL-3.0" \
      org.opencontainers.image.title="AIDM GPU" \
      org.opencontainers.image.description="Photogrammetry engine that turns drone images into orthophotos, elevation models, point clouds and textured 3D models, with NVIDIA CUDA support. Based on ODX."

# Env variables
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}/code/SuperBuild/install/lib/python3.12:/code/SuperBuild/install/bin/opensfm" \
    LD_LIBRARY_PATH="${LD_LIBRARY_PATH:+$LD_LIBRARY_PATH:}/code/SuperBuild/install/lib" \
    PDAL_DRIVER_PATH="/code/SuperBuild/install/bin"

WORKDIR /code

# Copy everything we built from the builder
COPY --from=builder /code /code

# Copy the Python libraries installed via pip from the builder
COPY --from=builder /usr/local /usr/local

RUN apt-get update -y \
 && apt-get install -y ffmpeg libtbbmalloc2

# OpenMVS is linked against libcuda.so.1, the NVIDIA driver library. The
# NVIDIA Container Toolkit mounts it into the container when docker runs with
# --gpus. Without it the OpenMVS binaries cannot start, even in CPU mode.
# Install the CUDA stub library as a fallback, in a directory that ldconfig
# lists after the system directories: the real driver wins when it is
# mounted, and the image still processes on the CPU when it is not.
RUN mkdir -p /usr/local/lib/aidm-cuda-stub \
  && cp /usr/local/cuda/lib64/stubs/libcuda.so /usr/local/lib/aidm-cuda-stub/libcuda.so.1 \
  && echo /usr/local/lib/aidm-cuda-stub > /etc/ld.so.conf.d/zz-aidm-cuda-stub.conf \
  && ldconfig

# Install shared libraries that we depend on via APT, but *not*
# the -dev packages to save space!
# Also run a smoke test on AIDM and OpenSfM, and check that OpenMVS
# can find all its shared libraries without a GPU. Then check that only the
# GPU wheel of ONNX Runtime is installed, that it offers the CUDA provider,
# and that the CUDA provider finds all its shared libraries, cuDNN included.
RUN bash configure.sh installruntimedeps \
  && apt-get clean \
  && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/* \
  && bash run.sh --help \
  && bash -c "eval $(python3 /code/modules/context.py) && python3 -c 'from opensfm import io, pymap'" \
  && ! ldd /code/SuperBuild/install/bin/OpenMVS/DensifyPointCloud | grep "not found" \
  && python3 -c "import importlib.metadata as m, onnxruntime as o; names = {(d.metadata['Name'] or '').lower().replace('_', '-') for d in m.distributions()}; assert 'onnxruntime' not in names, 'CPU wheel installed'; assert 'onnxruntime-gpu' in names, 'GPU wheel missing'; assert 'CUDAExecutionProvider' in o.get_available_providers()" \
  && test -f /usr/local/lib/python3.12/dist-packages/onnxruntime/capi/libonnxruntime_providers_cuda.so \
  && ! ldd /usr/local/lib/python3.12/dist-packages/onnxruntime/capi/libonnxruntime_providers_cuda.so | grep "not found"

# Entry point
ENTRYPOINT ["python3", "/code/run.py"]
