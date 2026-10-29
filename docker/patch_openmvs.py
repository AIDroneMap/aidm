import sys
from pathlib import Path


config = Path(sys.argv[1])
before = b"#if __x86_64__ || __ppc64__\n"
after = b"#if __x86_64__ || __ppc64__ || __aarch64__\n"
content = config.read_bytes()

if content.count(before) == 1 and after not in content:
    config.write_bytes(content.replace(before, after))
elif before in content or content.count(after) != 1:
    raise SystemExit("OpenMVS width detection changed; check Config.h before building")
