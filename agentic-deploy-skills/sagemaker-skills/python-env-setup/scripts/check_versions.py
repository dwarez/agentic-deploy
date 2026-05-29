#!/usr/bin/env python
"""check_versions.py — Report installed versions of key dependencies.

Uses importlib.metadata.version() which works for every installed package,
unlike `module.__version__` which is inconsistently defined and sometimes
missing entirely (e.g. `sagemaker` does not expose `__version__`).

Usage:
    python check_versions.py
    python check_versions.py sagemaker boto3 transformers
"""

import sys
from importlib.metadata import PackageNotFoundError, version

DEFAULT_PACKAGES = ["sagemaker", "boto3", "botocore", "awscli"]


def report(package: str) -> None:
    try:
        v = version(package)
        print(f"{package}=={v}")
    except PackageNotFoundError:
        print(f"{package}: NOT INSTALLED")


def main() -> int:
    packages = sys.argv[1:] if len(sys.argv) > 1 else DEFAULT_PACKAGES
    for pkg in packages:
        report(pkg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
