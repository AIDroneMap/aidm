import os
import re
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")

DOCKERFILES = ["Dockerfile", "gpu.Dockerfile"]

# FROM [--platform=...] <image> [AS <stage>]
FROM_LINE = re.compile(r"^\s*FROM\s+(?:--\S+\s+)*(\S+)", re.IGNORECASE | re.MULTILINE)
PINNED = re.compile(r"^[^@\s]+:[^@\s]+@sha256:[0-9a-f]{64}$")


def base_images(name):
    with open(os.path.join(ROOT, name)) as f:
        text = f.read()
    stages = set(re.findall(r"^\s*FROM\s+.*\s+AS\s+(\S+)\s*$", text, re.IGNORECASE | re.MULTILINE))
    # A FROM that names an earlier stage is not a base image
    return [image for image in FROM_LINE.findall(text) if image not in stages]


class TestBaseImages(unittest.TestCase):
    def test_every_base_image_is_pinned_by_digest(self):
        for name in DOCKERFILES:
            images = base_images(name)
            self.assertTrue(images, "%s has no FROM line" % name)
            for image in images:
                with self.subTest(dockerfile=name, image=image):
                    self.assertRegex(
                        image,
                        PINNED,
                        "%s builds from %s, which is not pinned. Write it as "
                        "<image>:<tag>@sha256:<index digest>." % (name, image),
                    )

    def test_stages_share_one_base(self):
        # The builder and the final stage of the CPU image start from the
        # same base, so a pin moves both or neither.
        images = base_images("Dockerfile")
        self.assertEqual(len(set(images)), 1, images)


if __name__ == "__main__":
    unittest.main()
