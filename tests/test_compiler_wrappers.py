import os
import stat
import subprocess
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")


def write_script(path, text):
    with open(path, "w") as f:
        f.write(text)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)


class TestCompilerWrappers(unittest.TestCase):
    """docker/gcc and docker/g++ decide which -march flags reach the compiler.

    Each test runs a copy of the wrapper in which the real compiler is a stub
    that prints its arguments, with a fake uname that reports the machine.
    """

    def compile_args(self, wrapper, machine, args):
        with tempfile.TemporaryDirectory() as tmp:
            real = os.path.join(tmp, "real")
            write_script(real, '#!/bin/bash\nprintf "%s\\n" "$@"\n')
            write_script(os.path.join(tmp, "uname"), "#!/bin/bash\necho %s\n" % machine)

            with open(os.path.join(ROOT, "docker", wrapper)) as f:
                text = f.read()
            real_path = "/usr/bin/%s_real" % wrapper
            self.assertIn(real_path, text)
            copy = os.path.join(tmp, wrapper)
            write_script(copy, text.replace(real_path, real))

            env = dict(os.environ, PATH=tmp + os.pathsep + os.environ.get("PATH", ""))
            out = subprocess.run([copy] + args, env=env, check=True,
                                 stdout=subprocess.PIPE, text=True).stdout
            return out.splitlines()

    def test_both_wrappers_force_the_target(self):
        for wrapper in ("gcc", "g++"):
            with self.subTest(wrapper=wrapper):
                self.assertEqual(self.compile_args(wrapper, "x86_64", ["-O2", "-march=native", "-c", "a.c"]),
                                 ["-march=x86-64-v3", "-O2", "-c", "a.c"])
                self.assertEqual(self.compile_args(wrapper, "aarch64", ["-march=native", "-c", "a.c"]),
                                 ["-march=armv8.2-a", "-c", "a.c"])

    def test_opencv_dispatch_flags_pass_on_arm64(self):
        # The flags OpenCV 4.12 uses for its NEON_DOTPROD, NEON_FP16 and NEON_BF16 kernels
        for ext in ("dotprod", "fp16", "bf16"):
            with self.subTest(ext=ext):
                flag = "-march=armv8.2-a+" + ext
                self.assertEqual(self.compile_args("g++", "aarch64", [flag, "-c", "k.cpp"]),
                                 ["-march=armv8.2-a", flag, "-c", "k.cpp"])

    def test_other_march_flags_are_dropped_on_arm64(self):
        for flag in ("-march=armv8.4-a", "-march=armv8-a+dotprod", "-march=armv8.2-a+nosimd",
                     "-march=armv8.2-a+dotprod+nofp", "-march=native", "-march=armv8.2-a"):
            with self.subTest(flag=flag):
                self.assertEqual(self.compile_args("g++", "aarch64", [flag, "-c", "k.cpp"]),
                                 ["-march=armv8.2-a", "-c", "k.cpp"])

    def test_x86_feature_flags_pass_and_march_is_dropped(self):
        self.assertEqual(self.compile_args("g++", "x86_64", ["-march=haswell", "-mavx512f", "-c", "k.cpp"]),
                         ["-march=x86-64-v3", "-mavx512f", "-c", "k.cpp"])


if __name__ == "__main__":
    unittest.main()
