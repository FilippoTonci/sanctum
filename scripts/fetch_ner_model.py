#!/usr/bin/env python3
"""Fetch the pinned NER model (install / build time only).

Usage:
    python scripts/fetch_ner_model.py                 # into ~/.cache/sanctum/models/...
    python scripts/fetch_ner_model.py --dest DIR      # into DIR
    python scripts/fetch_ner_model.py --check [--dest DIR]   # verify only, no network

Downloads the files listed in ``sanctum.analyzer.ner_model.FILES`` from
Hugging Face at the pinned revision and checks each SHA-256 before moving it
into place. Files already present with the right hash are skipped. The
model's LICENSE and NOTICE (``licenses/<model>/``) are copied alongside, so
every install, including the desktop sidecar bundle, carries its attribution. Sanctum
itself never downloads the model at runtime (see CLAUDE.md, airgap invariant);
this script is the one sanctioned way to get it onto a machine.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sanctum.analyzer.ner_model import (  # noqa: E402
    DIR_NAME,
    FILES,
    REPO_ID,
    REVISION,
    checksum_mismatches,
    default_model_dir,
    sha256_of,
)

URL = "https://huggingface.co/{repo}/resolve/{rev}/{name}"
LICENSES = ROOT / "licenses" / DIR_NAME


def fetch(dest: Path) -> None:
    for name, expected in FILES.items():
        target = dest / name
        if target.is_file() and sha256_of(target) == expected:
            print(f"ok       {name}")
            continue
        url = URL.format(repo=REPO_ID, rev=REVISION, name=name)
        print(f"fetching {name} ...", flush=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as tmp:
            tmp_path = Path(tmp.name)
            try:
                with urllib.request.urlopen(url, timeout=60) as resp:
                    shutil.copyfileobj(resp, tmp, length=1 << 20)
            except BaseException:
                tmp_path.unlink(missing_ok=True)
                raise
        actual = sha256_of(tmp_path)
        if actual != expected:
            tmp_path.unlink(missing_ok=True)
            raise SystemExit(f"checksum mismatch for {name}: expected {expected}, got {actual}")
        tmp_path.replace(target)
        print(f"ok       {name}")
    for src in sorted(LICENSES.iterdir()):
        shutil.copyfile(src, dest / src.name)
        print(f"copied   {src.name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dest", type=Path, default=None, help="target directory")
    parser.add_argument("--check", action="store_true", help="verify checksums, fetch nothing")
    args = parser.parse_args()
    dest = (args.dest or default_model_dir()).expanduser().resolve()

    if not args.check:
        print(f"{REPO_ID} @ {REVISION[:12]} -> {dest}")
        fetch(dest)
    bad = checksum_mismatches(dest)
    if bad:
        print(f"missing or modified in {dest}: {', '.join(bad)}", file=sys.stderr)
        return 1
    print(f"verified {len(FILES)} files in {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
