import ast
import os
import tempfile
import unittest

from modules import cpu

ROOT = os.path.join(os.path.dirname(__file__), "..")

# Excerpts of /proc/cpuinfo, one processor each
HASWELL = """processor\t: 0
vendor_id\t: GenuineIntel
model name\t: Intel(R) Core(TM) i7-4770 CPU @ 3.40GHz
flags\t\t: fpu vme de pse tsc msr pae mce cx8 apic sep mtrr pge mca cmov pat pse36 clflush mmx fxsr sse sse2 ht syscall nx lm constant_tsc pni pclmulqdq ssse3 fma cx16 sse4_1 sse4_2 movbe popcnt aes xsave avx f16c rdrand lahf_lm abm bmi1 avx2 bmi2 erms
"""

# Sandy Bridge: AVX but no AVX2 or FMA
SANDY_BRIDGE = """processor\t: 0
model name\t: Intel(R) Core(TM) i7-2600 CPU @ 3.40GHz
flags\t\t: fpu vme de pse tsc msr pae mce cx8 apic sep mtrr pge mca cmov pat pse36 clflush mmx fxsr sse sse2 ht syscall nx lm constant_tsc pni pclmulqdq ssse3 cx16 sse4_1 sse4_2 popcnt aes xsave avx lahf_lm
"""

# Raspberry Pi 5, Cortex-A76 (ARMv8.2-A)
CORTEX_A76 = """processor\t: 0
BogoMIPS\t: 108.00
Features\t: fp asimd evtstrm aes pmull sha1 sha2 crc32 atomics fphp asimdhp cpuid asimdrdm lrcpc dcpop asimddp
CPU implementer\t: 0x41
CPU part\t: 0xd0b
"""

# Raspberry Pi 4, Cortex-A72 (ARMv8.0-A)
CORTEX_A72 = """processor\t: 0
BogoMIPS\t: 108.00
Features\t: fp asimd evtstrm crc32 cpuid
CPU implementer\t: 0x41
CPU part\t: 0xd08
"""


class TestCpu(unittest.TestCase):
    def test_modern_cpus_pass(self):
        self.assertEqual(cpu.missing_features("x86_64", HASWELL), [])
        self.assertEqual(cpu.missing_features("aarch64", CORTEX_A76), [])

    def test_old_cpus_fail(self):
        self.assertEqual(cpu.missing_features("x86_64", SANDY_BRIDGE), ["avx2", "fma"])
        self.assertEqual(cpu.missing_features("aarch64", CORTEX_A72), ["atomics", "asimdrdm"])

    def test_machine_aliases(self):
        self.assertEqual(cpu.missing_features("AMD64", SANDY_BRIDGE), ["avx2", "fma"])
        self.assertEqual(cpu.missing_features("arm64", CORTEX_A72), ["atomics", "asimdrdm"])

    def test_unreadable_cpuinfo_passes(self):
        # Another machine type, or cpuinfo without the feature line for this
        # one (QEMU user emulation shows the host's cpuinfo)
        self.assertEqual(cpu.missing_features("riscv64", SANDY_BRIDGE), [])
        self.assertEqual(cpu.missing_features("aarch64", HASWELL), [])
        self.assertEqual(cpu.missing_features("x86_64", CORTEX_A72), [])
        self.assertEqual(cpu.missing_features("x86_64", ""), [])

    def check(self, machine, cpuinfo):
        with tempfile.NamedTemporaryFile("w", suffix="cpuinfo") as f:
            f.write(cpuinfo)
            f.flush()
            cpu.check(machine, f.name)

    def test_check_exits_with_one_line(self):
        with self.assertRaises(SystemExit) as e:
            self.check("x86_64", SANDY_BRIDGE)
        self.assertEqual(e.exception.code,
                         "AIDM needs a CPU with AVX2 and FMA (2013 or later). This CPU does not have them.")

        with self.assertRaises(SystemExit) as e:
            self.check("aarch64", CORTEX_A72)
        self.assertIn("ARMv8.2-A", e.exception.code)
        self.assertNotIn("\n", e.exception.code)

    def test_check_passes(self):
        self.check("x86_64", HASWELL)
        self.check("aarch64", CORTEX_A76)
        cpu.check("x86_64", os.path.join(ROOT, "no-such-cpuinfo"))

    def test_run_py_checks_before_other_imports(self):
        # Everything compiled is imported after cpu.check(), so the check
        # must come before every import except sys and modules.cpu
        with open(os.path.join(ROOT, "run.py")) as f:
            body = ast.parse(f.read()).body
        check_line = None
        for node in body:
            if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                    and ast.unparse(node.value) == "cpu.check()"):
                check_line = node.lineno
                break
        self.assertIsNotNone(check_line, "run.py does not call cpu.check()")

        for node in body:
            if isinstance(node, (ast.Import, ast.ImportFrom)) and node.lineno < check_line:
                self.assertIn(ast.unparse(node), ["import sys", "from modules import cpu"])


if __name__ == '__main__':
    unittest.main()
