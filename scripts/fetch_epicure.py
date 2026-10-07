"""Download the Epicure-Core files SmartPlate uses, pinned and checksum-verified.

    python scripts/fetch_epicure.py [target_dir]

Run at build/setup time (render.yaml does). Files land in data/epicure/ (or
SMARTPLATE_EPICURE_DIR / the argument). Each file is fetched from one pinned Hugging Face
revision, hashed while streaming, and only moved into place when its SHA-256 matches
smartplate.domain.epicure.PINNED. A file already present with the right hash is kept.
Exits non-zero on any failure, so a broken download fails the build instead of shipping
an app whose swaps silently vanish.

Epicure-Core © 2026 Jakub Radzikowski and Josef Chen (KAIKAKU.AI), CC BY 4.0.
"""
import hashlib
import os
import sys
import tempfile
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from smartplate import config                          # noqa: E402
from smartplate.domain import epicure                 # noqa: E402

BASE = f"https://huggingface.co/{epicure.REPO}/resolve/{epicure.REVISION}/"
TIMEOUT_S = 60
MAX_BYTES = 16 * 1024 * 1024        # the largest file is ~2.1 MB


def fetch(name: str, target: str, opener=urllib.request.urlopen) -> str:
    """Download one file into `target`; returns 'kept' or 'downloaded'. Raises on mismatch."""
    want = epicure.PINNED[name]
    dest = os.path.join(target, name)
    if os.path.isfile(dest) and epicure.sha256(dest) == want:
        return "kept"
    h, size = hashlib.sha256(), 0
    fd, tmp = tempfile.mkstemp(dir=target, prefix=f".{name}.")
    try:
        with os.fdopen(fd, "wb") as out, opener(BASE + name, timeout=TIMEOUT_S) as resp:
            while chunk := resp.read(1 << 16):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError(f"{name}: larger than expected")
                h.update(chunk)
                out.write(chunk)
        if h.hexdigest() != want:
            raise ValueError(f"{name}: SHA-256 {h.hexdigest()} does not match the pinned {want}")
        os.replace(tmp, dest)
        return "downloaded"
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main(argv: list[str]) -> int:
    target = argv[1] if len(argv) > 1 else config.EPICURE_DIR
    os.makedirs(target, exist_ok=True)
    try:
        for name in epicure.FILES:
            print(f"epicure {name}: {fetch(name, target)}")
        epicure.load(target, dict(epicure.PINNED))      # parse once: fail the build, not the first request
    except Exception as exc:                            # noqa: BLE001 — any failure must stop the build
        print(f"epicure: FAILED — {exc}", file=sys.stderr)
        return 1
    print(f"epicure: ready in {target} (revision {epicure.REVISION})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
