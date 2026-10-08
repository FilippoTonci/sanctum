"""The bundled NER model: which one, where it lives, and whether it is there.

Sanctum ships one NER model, ``knowledgator/gliner-pii-base-v1.0`` (uint8 ONNX
export, Apache-2.0), pinned to a Hugging Face revision with a SHA-256 per file.
It is fetched at install / build time only, by ``scripts/fetch_ner_model.py``
(developers, CI) or the desktop sidecar build. At runtime it is only ever read
from disk: a missing model is a configuration error, never a download.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from sanctum.analyzer.gliner_onnx import CONFIG_FILE, ONNX_FILE, TOKENIZER_FILE
from sanctum.core.exceptions import ConfigurationError

REPO_ID = "knowledgator/gliner-pii-base-v1.0"
REVISION = "61726e0ad791dcab3e29339bbec3ad42ded65641"
DIR_NAME = "gliner-pii-base-v1.0"
LICENSE = "Apache-2.0"

# Every file we take from the repo, with its SHA-256 at REVISION. README.md is
# the model card, kept for provenance and licence attribution.
FILES: dict[str, str] = {
    ONNX_FILE: "0514c8fd86d0513ce5351a3267f132b57d5bcd8f99a90d43cde1228092881d19",
    TOKENIZER_FILE: "ee028763434d18611c1c36356ea1d050e90a9fa94ede57fac48b39f85f818ad1",
    CONFIG_FILE: "e33d3da38e0d369fa7574668d3798ca6c7d2b23cba7d628507112eeb426aaccb",
    "README.md": "f715f1ad8ff24c0f6f1b2745684a587dfe7390f8a1f0621deec57243b34a8b5a",
}
REQUIRED_FILES = (ONNX_FILE, TOKENIZER_FILE, CONFIG_FILE)


def default_model_dir() -> Path:
    """Where the model is looked for when ``nlp.ner_model_dir`` is not set.

    Frozen (the desktop sidecar): ``models/`` next to the executable, where the
    sidecar build puts it. Otherwise: ``~/.cache/sanctum/models/``, where
    ``scripts/fetch_ner_model.py`` puts it by default.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "models" / DIR_NAME
    return Path.home() / ".cache" / "sanctum" / "models" / DIR_NAME


def resolve_model_dir(configured: Path | None = None) -> Path:
    """The model directory to load, or a ``ConfigurationError`` saying how to get it."""
    model_dir = configured if configured is not None else default_model_dir()
    missing = [name for name in REQUIRED_FILES if not (model_dir / name).is_file()]
    if missing:
        raise ConfigurationError(
            f"NER model {REPO_ID} not found in {model_dir} (missing: {', '.join(missing)}). "
            "Sanctum never downloads models at runtime. Fetch it once with "
            f"`python scripts/fetch_ner_model.py --dest {model_dir}`, or point "
            "SANCTUM_NLP__NER_MODEL_DIR at a directory that has it."
        )
    return model_dir


def checksum_mismatches(model_dir: Path) -> list[str]:
    """Files under ``model_dir`` that are missing or whose SHA-256 differs from the pin."""
    bad: list[str] = []
    for name, expected in FILES.items():
        path = model_dir / name
        if not path.is_file() or sha256_of(path) != expected:
            bad.append(name)
    return bad


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()
