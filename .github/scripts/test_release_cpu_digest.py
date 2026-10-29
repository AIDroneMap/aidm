from pathlib import Path
import unittest


WORKFLOW = Path(__file__).resolve().parents[1] / "workflows/release.yml"
STEPS = ("Push by digest", "Check that the pushed image is the tested one")


def step_condition(source, name):
    selected = False
    for line in source.splitlines():
        if line.startswith("      - name: "):
            selected = line.removeprefix("      - name: ") == name
        if selected and line.startswith("        if: "):
            return line.removeprefix("        if: ")
    raise AssertionError(f"No condition found for {name}")


def selected(condition, publish, image):
    values = {"needs.check.outputs.publish": publish, "matrix.name": image}
    matches = []
    for clause in condition.split(" || "):
        variable, operator, literal = clause.partition(" == ")
        if operator != " == " or variable not in values:
            raise AssertionError(f"Unsupported image selection clause: {clause}")
        if not (literal.startswith("'") and literal.endswith("'")):
            raise AssertionError(f"Expected a quoted value in {clause}")
        matches.append(values[variable] == literal[1:-1])
    return any(matches)


class TestReleaseCPUDigest(unittest.TestCase):
    def setUp(self):
        self.source = WORKFLOW.read_text()

    def test_dry_run_retains_and_verifies_both_amd64_images(self):
        for step in STEPS:
            condition = step_condition(self.source, step)
            for image in ("cpu-amd64", "gpu-amd64"):
                with self.subTest(step=step, image=image):
                    self.assertTrue(selected(condition, "false", image))

    def test_dry_run_does_not_push_arm64(self):
        for step in STEPS:
            with self.subTest(step=step):
                self.assertFalse(selected(step_condition(self.source, step), "false", "cpu-arm64"))

    def test_release_still_retains_and_verifies_every_image(self):
        for step in STEPS:
            condition = step_condition(self.source, step)
            for image in ("cpu-amd64", "cpu-arm64", "gpu-amd64"):
                with self.subTest(step=step, image=image):
                    self.assertTrue(selected(condition, "true", image))

    def test_push_and_verification_select_the_same_images(self):
        self.assertEqual(*(step_condition(self.source, step) for step in STEPS))


if __name__ == "__main__":
    unittest.main()
