import unittest
from unittest import mock

from modules import bgfilter
from modules.bgfilter import BgFilter
from modules.skyremoval import skyfilter
from modules.skyremoval.skyfilter import SkyFilter

CUDA = "CUDAExecutionProvider"
CPU = "CPUExecutionProvider"


class FakeSession:
    """Stands in for ort.InferenceSession. It runs on the provider given as
    chosen, whatever the filter asked for, as ONNX Runtime does when it falls
    back to the CPU."""

    def __init__(self, chosen):
        self.chosen = chosen

    def get_providers(self):
        return [self.chosen, CPU] if self.chosen != CPU else [CPU]


class TestProviderLine(unittest.TestCase):
    FILTERS = [
        ("Background removal", bgfilter, BgFilter),
        ("Sky removal", skyfilter, SkyFilter),
    ]

    def provider_lines(self, module, filter_class, requested, chosen):
        """Create the filter with a fake session; return the provider lines it logs."""
        session = FakeSession(chosen)
        with mock.patch.object(module, "provider", requested), \
                mock.patch.object(module.ort, "InferenceSession", return_value=session) as create, \
                mock.patch.object(module.log, "INFO") as info:
            created = filter_class(model="/dummy/model.onnx")
        create.assert_called_once_with("/dummy/model.onnx", providers=[requested])
        self.assertIs(created.session, session)
        messages = [c.args[0] for c in info.call_args_list if c.args]
        return [m for m in messages if "ONNX Runtime provider" in m]

    def test_logs_the_provider_the_session_chose(self):
        cases = [
            (CUDA, CUDA),
            (CPU, CPU),
            # The session fell back to the CPU: the line says so
            (CUDA, CPU),
        ]
        for name, module, filter_class in self.FILTERS:
            for requested, chosen in cases:
                with self.subTest(filter=name, requested=requested, chosen=chosen):
                    lines = self.provider_lines(module, filter_class, requested, chosen)
                    self.assertEqual(lines, ["%s: ONNX Runtime provider %s" % (name, chosen)])


if __name__ == "__main__":
    unittest.main()
