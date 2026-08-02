#!/usr/bin/env python3
"""Generate an owner-only Fernet key file for EggBot deployment or staging."""

import argparse
import os
from pathlib import Path

from cryptography.fernet import Fernet


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as key_file:
        key_file.write(Fernet.generate_key() + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
