#!/usr/bin/env python3
"""Install symlink entrypoints without replacing unrelated user files."""
import argparse
import os
from pathlib import Path
import sys


def install(root, bin_dir):
    sources = sorted((root / "bin").iterdir())
    for source in sources:
        if not source.is_file():
            continue
        if not os.access(source, os.X_OK):
            raise RuntimeError(f"Entrypoint is not executable: {source}")
        target = bin_dir / source.name
        if target.exists() or target.is_symlink():
            if not target.is_symlink() or target.resolve() != source.resolve():
                raise RuntimeError(f"Refusing to replace unrelated entrypoint: {target}")
    bin_dir.mkdir(parents=True, exist_ok=True)
    for source in sources:
        if not source.is_file():
            continue
        target = bin_dir / source.name
        if not target.is_symlink():
            target.symlink_to(source.resolve())
        print(f"{target} -> {source.resolve()}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bin-dir", type=Path, default=Path.home() / ".local/bin")
    args = parser.parse_args()
    install(Path(__file__).resolve().parent, args.bin_dir.expanduser())


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
