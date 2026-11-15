"""The two Windows variants of configure.py: the variant argument, the CUDA
toolkit check, the import reader and the DLL check on fake install trees.
configure.py runs on Windows only; these functions run on any platform."""

import contextlib
import importlib.util
import io
import os
from pathlib import Path
import struct
import subprocess
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("aidm_configure_variants", ROOT / "configure.py")
configure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(configure)

IMAGE_BASE = 0x140000000
IMAGE_BASE_32 = 0x400000
SECTION_RVA = 0x1000
SECTION_OFFSET = 0x200


def make_pe(imports=(), delay_imports=(), pe32=False, delay_absolute=False):
    """Bytes of a minimal PE file with one section that holds an import
    table naming imports and a delay-load table naming delay_imports."""
    image_base = IMAGE_BASE_32 if pe32 else IMAGE_BASE
    body = bytearray()
    names = {}
    # Descriptors first, then the names. Sizes are known before the names.
    import_size = 20 * (len(imports) + 1) if imports else 0
    delay_size = 32 * (len(delay_imports) + 1) if delay_imports else 0
    name_area = import_size + delay_size
    blob = bytearray()
    for name in list(imports) + list(delay_imports):
        names[name] = SECTION_RVA + name_area + len(blob)
        blob += name.encode("ascii") + b"\0"
    if imports:
        for name in imports:
            body += struct.pack("<5I", 0, 0, 0, names[name], 0)
        body += b"\0" * 20
    if delay_imports:
        for name in delay_imports:
            address = names[name] + image_base if delay_absolute else names[name]
            body += struct.pack("<8I", 0 if delay_absolute else 1, address, 0, 0, 0, 0, 0, 0)
        body += b"\0" * 32
    body += blob
    section = bytes(body)

    directories = [(0, 0)] * 16
    if imports:
        directories[1] = (SECTION_RVA, import_size)
    if delay_imports:
        directories[13] = (SECTION_RVA + import_size, delay_size)
    if pe32:
        optional = bytearray(96 + 16 * 8)
        struct.pack_into("<H", optional, 0, 0x10B)
        struct.pack_into("<I", optional, 28, image_base)
        struct.pack_into("<I", optional, 92, 16)
        start = 96
    else:
        optional = bytearray(112 + 16 * 8)
        struct.pack_into("<H", optional, 0, 0x20B)
        struct.pack_into("<Q", optional, 24, image_base)
        struct.pack_into("<I", optional, 108, 16)
        start = 112
    for i, (rva, size) in enumerate(directories):
        struct.pack_into("<II", optional, start + 8 * i, rva, size)

    dos = bytearray(0x40)
    dos[0:2] = b"MZ"
    struct.pack_into("<I", dos, 0x3C, 0x40)
    coff = struct.pack("<HHIIIHH", 0x8664, 1, 0, 0, 0, len(optional), 0x22)
    section_header = struct.pack("<8sIIIIIIHHI", b".idata", len(section), SECTION_RVA,
                                 len(section), SECTION_OFFSET, 0, 0, 0, 0, 0)
    head = bytes(dos) + b"PE\0\0" + coff + bytes(optional) + section_header
    assert len(head) <= SECTION_OFFSET
    return head + b"\0" * (SECTION_OFFSET - len(head)) + section


class FakeTree:
    """A fake package: SuperBuild/install with its bin, and python/."""

    def __init__(self, folder):
        self.install = os.path.join(folder, "install")
        self.bin = os.path.join(self.install, "bin")
        self.python = os.path.join(folder, "python")
        os.makedirs(self.bin)
        os.makedirs(os.path.join(self.python, "Lib", "site-packages"))
        self.roots = [self.install, self.python]

    def add(self, relative, data=b"not a PE file"):
        path = os.path.join(os.path.dirname(self.install), relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return path


def a_cpu_tree(tree):
    """What a cpu build holds: tools and DLLs that import no CUDA."""
    tree.add("install/bin/DensifyPointCloud.exe", make_pe(["KERNEL32.dll", "gdal.dll", "VCOMP140.DLL"]))
    tree.add("install/bin/gdal.dll", make_pe(["KERNEL32.dll", "sqlite3.dll"]))
    tree.add("install/bin/sqlite3.dll", make_pe(["KERNEL32.dll"]))
    tree.add("install/bin/opensfm/opensfm/pyfeatures.cp312-win_amd64.pyd", make_pe(["python312.dll"]))
    tree.add("python/Lib/site-packages/cv2/cv2.pyd", make_pe(["python312.dll"]))
    tree.add("install/share/proj/proj.db", b"data")


def quietly(function, *args, **kwargs):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        result = function(*args, **kwargs)
    return result, out.getvalue()


class TestPeImports(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write(self, data, name="tool.exe"):
        path = os.path.join(self.tmp.name, name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def test_import_table_names_in_order(self):
        path = self.write(make_pe(["KERNEL32.dll", "nvcuda.dll", "cudart64_12.dll"]))
        self.assertEqual(configure.pe_imports(path), ["KERNEL32.dll", "nvcuda.dll", "cudart64_12.dll"])

    def test_delay_loaded_imports_are_read(self):
        path = self.write(make_pe(["KERNEL32.dll"], delay_imports=["cublas64_12.dll"]))
        self.assertEqual(configure.pe_imports(path), ["KERNEL32.dll", "cublas64_12.dll"])

    def test_delay_load_names_by_absolute_address(self):
        # The old form of the delay-load table, from 32 bit linkers.
        path = self.write(make_pe(delay_imports=["cudart64_12.dll"], delay_absolute=True, pe32=True))
        self.assertEqual(configure.pe_imports(path), ["cudart64_12.dll"])

    def test_pe32_file(self):
        path = self.write(make_pe(["KERNEL32.dll"], delay_imports=["nvcuda.dll"], pe32=True))
        self.assertEqual(configure.pe_imports(path), ["KERNEL32.dll", "nvcuda.dll"])

    def test_no_imports(self):
        self.assertEqual(configure.pe_imports(self.write(make_pe())), [])

    def test_a_file_that_is_not_pe(self):
        self.assertIsNone(configure.pe_imports(self.write(b"#!/bin/sh\n", "script.exe")))
        self.assertIsNone(configure.pe_imports(self.write(b"MZ" + b"\0" * 0x3A + struct.pack("<I", 0x40)
                                                          + b"NE\0\0", "old.exe")))

    def test_a_truncated_pe_file_stops(self):
        data = make_pe(["KERNEL32.dll"])
        with self.assertRaisesRegex(configure.DepsError, "truncated"):
            configure.pe_imports(self.write(data[:0x120]))


class TestCudaDllNames(unittest.TestCase):
    def test_cuda_names(self):
        for name in ("nvcuda.dll", "NVCUDA.DLL", "cudart64_12.dll", "cublas64_12.dll", "cublasLt64_12.dll",
                     "cufft64_11.dll", "cufftw64_11.dll", "curand64_10.dll", "cusolver64_11.dll",
                     "cusolverMg64_11.dll", "cusparse64_12.dll", "nvrtc64_120_0.dll", "nvrtc-builtins64_129.dll",
                     "nvJitLink_120_0.dll", "nvjpeg64_12.dll", "nppc64_12.dll", "nppial64_12.dll",
                     "nvblas64_12.dll", "nvToolsExt64_1.dll", "cudnn64_9.dll", "cudnn_ops64_9.dll",
                     "nvfatbin_120_0.dll", "cupti64_2025.2.1.dll", "C:/x/bin/cudart64_12.dll"):
            self.assertTrue(configure.is_cuda_dll(name), name)

    def test_other_names(self):
        for name in ("KERNEL32.dll", "gdal.dll", "VCOMP140.DLL", "opencv_core4120.dll", "python312.dll",
                     "cudart64_12.lib", "nvcuda.pdb", "nvml.dll", "onnxruntime.dll", "cuda_helper.dll"):
            self.assertFalse(configure.is_cuda_dll(name), name)


class TestCheckVariantDlls(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tree = FakeTree(self.tmp.name)
        a_cpu_tree(self.tree)

    def check(self, variant):
        return quietly(configure.check_variant_dlls, variant, self.tree.roots, self.tree.bin)[1]

    def failing(self, variant):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with self.assertRaisesRegex(configure.DepsError, "the %s variant failed its DLL check" % variant):
                configure.check_variant_dlls(variant, self.tree.roots, self.tree.bin)
        return out.getvalue()

    def add_cuda_tools(self):
        self.tree.add("install/bin/DensifyPointCloud.exe",
                      make_pe(["KERNEL32.dll", "nvcuda.dll", "cudart64_12.dll", "gdal.dll"]))
        self.tree.add("install/bin/opensfm/opensfm/pyfeatures.cp312-win_amd64.pyd",
                      make_pe(["python312.dll"], delay_imports=["cublas64_12.dll"]))

    def test_a_clean_cpu_tree_passes_and_lists_its_dlls(self):
        out = self.check("cpu")
        self.assertIn("DLLs in %s:" % self.tree.bin, out)
        self.assertIn("  gdal.dll\n", out)
        self.assertIn("  sqlite3.dll\n", out)
        self.assertIn("The cpu variant passed its DLL check. Files that import CUDA: 0", out)

    def test_cpu_tree_holding_a_cuda_runtime_dll_fails(self):
        self.tree.add("install/bin/cudart64_12.dll", make_pe(["KERNEL32.dll"]))
        out = self.failing("cpu")
        self.assertIn("  cudart64_12.dll (CUDA)\n", out)
        self.assertIn("cudart64_12.dll is a CUDA DLL in the cpu variant", out)

    def test_cpu_tree_holding_a_cuda_dll_in_python_fails(self):
        self.tree.add("python/Lib/site-packages/onnxruntime/capi/cudnn64_9.dll", make_pe())
        self.assertIn("cudnn64_9.dll is a CUDA DLL in the cpu variant", self.failing("cpu"))

    def test_cpu_tree_holding_the_stub_driver_fails(self):
        self.tree.add("install/bin/nvcuda.dll", make_pe(["KERNEL32.dll"]))
        out = self.failing("cpu")
        self.assertIn("nvcuda.dll is the NVIDIA driver's DLL", out)

    def test_cpu_tool_importing_the_driver_fails(self):
        self.tree.add("install/bin/DensifyPointCloud.exe", make_pe(["KERNEL32.dll", "nvcuda.dll"]))
        out = self.failing("cpu")
        self.assertIn("DensifyPointCloud.exe imports nvcuda.dll in the cpu variant", out)

    def test_cpu_module_delay_loading_cuda_fails(self):
        self.tree.add("install/bin/opensfm/opensfm/pyfeatures.cp312-win_amd64.pyd",
                      make_pe(["python312.dll"], delay_imports=["cudart64_12.dll"]))
        self.assertIn("pyfeatures.cp312-win_amd64.pyd imports cudart64_12.dll in the cpu variant",
                      self.failing("cpu"))

    def test_a_cuda_tree_with_its_runtime_passes(self):
        self.add_cuda_tools()
        self.tree.add("install/bin/cudart64_12.dll", make_pe(["KERNEL32.dll"]))
        self.tree.add("install/bin/cublas64_12.dll", make_pe(["KERNEL32.dll", "cublasLt64_12.dll"]))
        self.tree.add("install/bin/cublasLt64_12.dll", make_pe(["KERNEL32.dll"]))
        out = self.check("cuda")
        self.assertIn("DensifyPointCloud.exe imports cudart64_12.dll, nvcuda.dll", out)
        self.assertIn("The cuda variant passed its DLL check. Files that import CUDA: 3", out)

    def test_a_cuda_tree_that_lacks_an_imported_runtime_dll_fails(self):
        self.add_cuda_tools()
        self.tree.add("install/bin/cudart64_12.dll", make_pe(["KERNEL32.dll"]))
        out = self.failing("cuda")
        self.assertIn("pyfeatures.cp312-win_amd64.pyd imports cublas64_12.dll, which is not in %s"
                      % self.tree.bin, out)
        self.assertNotIn("imports nvcuda.dll, which is not in", out)

    def test_a_cuda_tree_built_without_cuda_fails(self):
        out = self.failing("cuda")
        self.assertIn("no file below", out)
        self.assertIn("the cuda variant was built without CUDA", out)

    def test_a_cuda_tree_with_only_the_driver_import_passes(self):
        # The CUDA runtime may be linked statically: then the tools import the driver only.
        self.tree.add("install/bin/DensifyPointCloud.exe", make_pe(["KERNEL32.dll", "nvcuda.dll"]))
        self.assertIn("Files that import CUDA: 1", self.check("cuda"))

    def test_a_cuda_tree_shipping_the_driver_fails(self):
        self.add_cuda_tools()
        self.tree.add("install/bin/cudart64_12.dll", make_pe())
        self.tree.add("install/bin/cublas64_12.dll", make_pe())
        self.tree.add("install/bin/nvcuda.dll", make_pe())
        self.assertIn("nvcuda.dll is the NVIDIA driver's DLL", self.failing("cuda"))

    def test_the_import_check_reads_names_without_case(self):
        self.add_cuda_tools()
        self.tree.add("install/bin/CUDART64_12.DLL", make_pe())
        self.tree.add("install/bin/CUBLAS64_12.DLL", make_pe())
        self.check("cuda")

    def test_unknown_variant_stops(self):
        with self.assertRaisesRegex(configure.DepsError, "Unknown variant 'gpu'"):
            configure.check_variant_dlls("gpu", self.tree.roots, self.tree.bin)


class TestBundleCudaRuntime(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tree = FakeTree(os.path.join(self.tmp.name, "package"))
        a_cpu_tree(self.tree)
        self.cuda_bin = os.path.join(self.tmp.name, "cuda", "bin")
        os.makedirs(self.cuda_bin)
        for name, imports in (("cudart64_12.dll", ["KERNEL32.dll"]),
                              ("cublas64_12.dll", ["KERNEL32.dll", "cublasLt64_12.dll"]),
                              ("cublasLt64_12.dll", ["KERNEL32.dll"]),
                              ("cufft64_11.dll", ["KERNEL32.dll"])):
            with open(os.path.join(self.cuda_bin, name), "wb") as f:
                f.write(make_pe(imports))
        with open(os.path.join(self.cuda_bin, "nvcc.exe"), "wb") as f:
            f.write(make_pe(["KERNEL32.dll"]))

    def bundle(self):
        return quietly(configure.bundle_cuda_runtime, self.tree.bin, self.tree.roots, self.cuda_bin)[0]

    def test_copies_what_the_tools_import_and_what_those_import(self):
        self.tree.add("install/bin/DensifyPointCloud.exe", make_pe(["KERNEL32.dll", "nvcuda.dll", "CUDART64_12.dll"]))
        self.tree.add("python/Lib/site-packages/mod.pyd", make_pe(delay_imports=["cublas64_12.dll"]))
        # One pass per level of imports, each in name order.
        self.assertEqual(self.bundle(), ["cublas64_12.dll", "cudart64_12.dll", "cublasLt64_12.dll"])
        present = set(os.listdir(self.tree.bin))
        self.assertTrue({"cudart64_12.dll", "cublas64_12.dll", "cublasLt64_12.dll"} <= present)
        self.assertNotIn("cufft64_11.dll", present)
        self.assertNotIn("nvcuda.dll", present)
        quietly(configure.check_variant_dlls, "cuda", self.tree.roots, self.tree.bin)

    def test_copies_nothing_when_no_tool_imports_the_toolkit(self):
        self.tree.add("install/bin/DensifyPointCloud.exe", make_pe(["KERNEL32.dll", "nvcuda.dll"]))
        self.assertEqual(self.bundle(), [])

    def test_a_present_copy_is_kept(self):
        self.tree.add("install/bin/DensifyPointCloud.exe", make_pe(["cudart64_12.dll"]))
        self.tree.add("install/bin/cudart64_12.dll", make_pe())
        self.assertEqual(self.bundle(), [])


class TestCudaToolkit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = os.path.join(self.tmp.name, "CUDA", "v12.9")
        os.makedirs(os.path.join(self.folder, "bin"))
        os.makedirs(os.path.join(self.folder, configure.CUDA_MSBUILD_EXTENSIONS))
        with open(os.path.join(self.folder, "bin", "nvcc.exe"), "wb") as f:
            f.write(b"")
        self.calls = []

    def runner(self, stdout, returncode=0):
        def run(cmd, **_kwargs):
            self.calls.append(cmd)
            return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")
        return run

    NVCC = ("nvcc: NVIDIA (R) Cuda compiler driver\nCopyright (c) 2005-2025 NVIDIA Corporation\n"
            "Cuda compilation tools, release 12.9, V12.9.86\nBuild cuda_12.9.r12.9/compiler.36037853_0\n")

    def test_the_folder_of_cuda_path(self):
        folder, out = quietly(configure.cuda_toolkit, {"Cuda_Path": self.folder}, runner=self.runner(self.NVCC))
        self.assertEqual(folder, self.folder)
        self.assertEqual(self.calls, [[os.path.join(self.folder, "bin", "nvcc.exe"), "--version"]])
        self.assertIn("nvcc V12.9.86", out)

    def test_no_cuda_path_stops(self):
        with self.assertRaisesRegex(configure.DepsError, r"CUDA_PATH \(not set\) names no folder"):
            configure.cuda_toolkit({}, runner=self.runner(self.NVCC))

    def test_no_nvcc_stops(self):
        os.remove(os.path.join(self.folder, "bin", "nvcc.exe"))
        with self.assertRaisesRegex(configure.DepsError, "has no nvcc.exe"):
            configure.cuda_toolkit({"CUDA_PATH": self.folder}, runner=self.runner(self.NVCC))

    def test_no_msbuild_files_stops(self):
        os.rmdir(os.path.join(self.folder, configure.CUDA_MSBUILD_EXTENSIONS))
        with self.assertRaisesRegex(configure.DepsError, "Visual Studio integration subpackage"):
            configure.cuda_toolkit({"CUDA_PATH": self.folder}, runner=self.runner(self.NVCC))

    def test_another_release_stops(self):
        with self.assertRaisesRegex(configure.DepsError, "is CUDA 12.8. The cuda variant builds with CUDA 12.9"):
            configure.cuda_toolkit({"CUDA_PATH": self.folder},
                                   runner=self.runner(self.NVCC.replace("release 12.9, V12.9.86", "release 12.8, V12.8.93")))

    def test_nvcc_that_prints_no_release_stops(self):
        with self.assertRaisesRegex(configure.DepsError, "did not print a release"):
            configure.cuda_toolkit({"CUDA_PATH": self.folder}, runner=self.runner("", returncode=1))


class TestVariantArguments(unittest.TestCase):
    def test_build_without_a_variant_is_cpu(self):
        args = configure.parse_args(["build"])
        self.assertEqual((args.action, args.variant), ("build", "cpu"))

    def test_both_variants_are_accepted(self):
        self.assertEqual(configure.parse_args(["build", "cpu"]).variant, "cpu")
        self.assertEqual(configure.parse_args(["build", "cuda"]).variant, "cuda")

    def test_another_variant_is_refused(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                configure.parse_args(["build", "gpu"])

    def test_cpu_configure_args(self):
        self.assertEqual(configure.superbuild_variant_args("cpu"), ["-DAIDM_WINDOWS_VARIANT=cpu"])

    def test_cuda_configure_args_name_the_toolkit_and_the_toolset(self):
        folder = "C:\\Program Files\\NVIDIA GPU Computing Toolkit\\CUDA\\v12.9\\"
        self.assertEqual(configure.superbuild_variant_args("cuda", folder),
                         ["-DAIDM_WINDOWS_VARIANT=cuda",
                          "-DAIDM_CUDA_TOOLKIT=C:/Program Files/NVIDIA GPU Computing Toolkit/CUDA/v12.9",
                          "-T", "cuda=C:/Program Files/NVIDIA GPU Computing Toolkit/CUDA/v12.9"])

    def test_cuda_without_a_toolkit_stops(self):
        with self.assertRaisesRegex(configure.DepsError, "needs the folder of the CUDA toolkit"):
            configure.superbuild_variant_args("cuda")

    def test_configure_command_carries_the_variant(self):
        calls = []

        def runner(cmd, **kwargs):
            calls.append(cmd)
            if cmd[1:] == ["--version"]:
                return SimpleNamespace(returncode=0, stdout="cmake version 4.4.5\n", stderr="")
            if cmd[1:] == ["--help"]:
                return SimpleNamespace(returncode=0, stdout="* Visual Studio 18 2026 = Generates\n", stderr="")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as folder:
            quietly(configure.build_superbuild, "cmake", "4.4.5", os.path.join(folder, "build"), {},
                    runner=runner, configure_args=["-DAIDM_WINDOWS_VARIANT=cpu"])
        self.assertEqual(calls[2], ["cmake", "..", "-DAIDM_WINDOWS_VARIANT=cpu"])


class TestBuildVariantMarker(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.build_dir = os.path.join(self.tmp.name, "build")

    def test_a_new_build_folder_takes_either_variant(self):
        configure.check_build_variant(self.build_dir, "cpu")
        configure.check_build_variant(self.build_dir, "cuda")

    def test_the_recorded_variant_builds_again(self):
        configure.record_build_variant(self.build_dir, "cuda")
        configure.check_build_variant(self.build_dir, "cuda")

    def test_another_variant_stops(self):
        configure.record_build_variant(self.build_dir, "cpu")
        with self.assertRaisesRegex(configure.DepsError, "holds a build of the cpu variant, not of the cuda variant"):
            configure.check_build_variant(self.build_dir, "cuda")

    def test_a_build_folder_with_no_recorded_variant_stops(self):
        os.makedirs(self.build_dir)
        with self.assertRaisesRegex(configure.DepsError, "holds a build of no recorded variant"):
            configure.check_build_variant(self.build_dir, "cpu")


if __name__ == "__main__":
    unittest.main()
