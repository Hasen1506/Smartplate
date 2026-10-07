"""Epicure ingredient embeddings, read with numpy only.

Epicure-Core (Radzikowski & Chen, 2026, arXiv:2605.22391, CC BY 4.0) is a 300-d
skip-gram embedding over 1,790 canonical ingredients. SmartPlate uses it for four
things: allergen- and diet-safe ingredient swaps, "more like this" dish similarity,
a cuisine tilt (slerp toward a cuisine pole) and out-of-stock grocery swaps.

The files are downloaded at build/setup time by `scripts/fetch_epicure.py`, pinned to
one Hugging Face revision and checked against the SHA-256 sums below. Nothing here
imports torch, safetensors or gensim: the safetensors header is parsed by hand.

If the files are missing or fail their checksums, `get()` returns None and every
feature built on it is hidden. Safety never depends on the embedding: allergen and
diet filtering is done by `domain/ingredients.py` from explicit labels.
"""
from __future__ import annotations

import hashlib
import json
import os
import struct
import threading
from dataclasses import dataclass, field

import numpy as np

REPO = "Kaikaku/epicure-core"
REVISION = "d31ebb5af8e92bbaf5cb67381d5006d4ea8368b7"
SOURCE_URL = f"https://huggingface.co/{REPO}/tree/{REVISION}"
LICENSE = "CC BY 4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
PAPER_URL = "https://arxiv.org/abs/2605.22391"

# SHA-256 of every file at REVISION. embeddings.safetensors and factor_poles.npy match
# the LFS oids Hugging Face publishes; the JSON files were hashed from the same revision.
PINNED = {
    "embeddings.safetensors": "58c965532709e415cc00098ea24ad153ca5e02f2ffc88d6b7287c36308e34120",
    "vocab.json": "5a10278cd71eeb66051d23ef8621b917a60ecd493d4580f60d324623191f8005",
    "itos.json": "0fd77eb89612b303ab8414d937daf3f7c1f24f90bbdb388d4c1c864219d769ad",
    "modes.json": "55e7238f7021907602bffa1aa2ece70237377b10185385189241d08b941ee8bf",
    "factor_poles.npy": "841cfe0b76cf80da944032902104db79ef40e6e30bc03126f1ed92b143e8eff6",
    # which modes make up each cuisine pole (the model card's reconstruction)
    "cuisine_pole_provenance.json": "54041a38298fb13ddc127cc3cf30c489aa3a7fb38442947034e2ab5e8c72a016",
}
FILES = tuple(PINNED)
D_MODEL = 300
_MAX_HEADER = 1 << 20          # a 1-tensor safetensors header is a few hundred bytes


class EpicureError(ValueError):
    """The files are missing, malformed or do not match their checksums."""


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def read_safetensors(data: bytes, name: str = "embeddings") -> np.ndarray:
    """One tensor from a safetensors byte string: an 8-byte little-endian header length,
    a JSON header naming dtype/shape/data_offsets, then the raw little-endian buffer."""
    if len(data) < 8:
        raise EpicureError("safetensors file is too short")
    (n,) = struct.unpack("<Q", data[:8])
    if n == 0 or n > _MAX_HEADER or 8 + n > len(data):
        raise EpicureError("safetensors header length is out of range")
    try:
        header = json.loads(data[8:8 + n])
    except ValueError as exc:
        raise EpicureError("safetensors header is not JSON") from exc
    info = header.get(name) if isinstance(header, dict) else None
    if not isinstance(info, dict):
        raise EpicureError(f"safetensors file has no tensor {name!r}")
    if info.get("dtype") != "F32":
        raise EpicureError(f"tensor {name!r} must be F32, got {info.get('dtype')!r}")
    shape, offsets = info.get("shape"), info.get("data_offsets")
    if (not isinstance(shape, list) or len(shape) != 2 or not all(isinstance(s, int) and s > 0 for s in shape)
            or not isinstance(offsets, list) or len(offsets) != 2):
        raise EpicureError(f"tensor {name!r} has a bad shape or offsets")
    begin, end = offsets
    body = data[8 + n:]
    if not (isinstance(begin, int) and isinstance(end, int) and 0 <= begin <= end <= len(body)):
        raise EpicureError(f"tensor {name!r} offsets fall outside the file")
    if end - begin != shape[0] * shape[1] * 4:
        raise EpicureError(f"tensor {name!r} byte length does not match its shape")
    return np.frombuffer(body[begin:end], dtype="<f4").reshape(shape).astype(np.float32)


def _unit(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.maximum(n, 1e-9)


@dataclass
class Model:
    E: np.ndarray                          # (V, 300), rows L2-normalised
    vocab: dict[str, int]
    itos: list[str]
    factor_ids: list[str]                  # row order of factor_poles
    factor_poles: np.ndarray               # (F, 300) unit
    factor_labels: dict[str, str]
    cuisine_poles: dict[str, np.ndarray] = field(default_factory=dict)   # "East_Asian" → unit (300,)

    def has(self, token: str) -> bool:
        return token in self.vocab

    def vec(self, token: str) -> np.ndarray:
        return self.E[self.vocab[token]]

    def mean(self, tokens) -> np.ndarray | None:
        """Unit mean of the known tokens' vectors (None when none are known)."""
        rows = [self.vocab[t] for t in tokens if t in self.vocab]
        if not rows:
            return None
        return _unit(self.E[rows].mean(axis=0))

    def cos(self, a: np.ndarray, b: np.ndarray) -> float:
        return float(_unit(a) @ _unit(b))

    def rank(self, query: np.ndarray, candidates) -> list[tuple[str, float]]:
        """Candidates (tokens) sorted by cosine to `query`, best first; unknown tokens dropped."""
        known = [t for t in candidates if t in self.vocab]
        if not known:
            return []
        sims = self.E[[self.vocab[t] for t in known]] @ _unit(query)
        order = np.argsort(-sims, kind="stable")
        return [(known[i], float(sims[i])) for i in order]

    @staticmethod
    def slerp(v: np.ndarray, direction: np.ndarray, theta_deg: float) -> np.ndarray:
        """Rotate unit `v` toward `direction` by theta on the sphere (the model card's
        operator): q = cos θ · v + sin θ · d⊥, with d⊥ the part of d orthogonal to v."""
        v, d = _unit(v), _unit(direction)
        perp = d - (d @ v) * v
        n = np.linalg.norm(perp)
        if n < 1e-9:
            return v
        theta = np.deg2rad(float(theta_deg))
        return _unit(np.cos(theta) * v + np.sin(theta) * (perp / n))

    def closest_factor(self, v: np.ndarray) -> tuple[str, str, float]:
        """The emergent factor mode nearest to `v`: (mode_id, label, cosine)."""
        sims = self.factor_poles @ _unit(v)
        i = int(np.argmax(sims))
        mid = self.factor_ids[i]
        return mid, self.factor_labels.get(mid, mid), float(sims[i])


def _load_checksums(spec: str | None) -> dict[str, str] | None:
    """'pinned' (default) → PINNED; 'off' → no check; a path → `sha256  name` lines."""
    if spec in (None, "", "pinned"):
        return dict(PINNED)
    if spec == "off":
        return None
    out = {}
    with open(spec) as f:
        for line in f:
            if line.strip():
                digest, name = line.split()
                out[name.lstrip("*")] = digest
    return out


def load(directory: str, checksums: dict[str, str] | None = None) -> Model:
    """Read and validate a full set of Epicure files from `directory`."""
    paths = {f: os.path.join(directory, f) for f in FILES}
    missing = [f for f, p in paths.items() if not os.path.isfile(p)]
    if missing:
        raise EpicureError(f"missing Epicure file(s): {', '.join(missing)}")
    if checksums is not None:
        for f, p in paths.items():
            want = checksums.get(f)
            if not want or sha256(p) != want:
                raise EpicureError(f"{f} does not match its pinned SHA-256")
    with open(paths["embeddings.safetensors"], "rb") as fh:
        E = read_safetensors(fh.read())
    if E.shape[1] != D_MODEL:
        raise EpicureError(f"embeddings must be {D_MODEL}-d")
    with open(paths["vocab.json"]) as fh:
        vocab = json.load(fh)
    with open(paths["itos.json"]) as fh:
        itos_raw = json.load(fh)
    itos = [itos_raw[str(i)] for i in range(len(itos_raw))]
    if len(vocab) != E.shape[0] or len(itos) != E.shape[0] or any(vocab.get(t) != i for i, t in enumerate(itos)):
        raise EpicureError("vocab.json, itos.json and the embedding rows disagree")
    with open(paths["modes.json"]) as fh:
        modes = json.load(fh)
    by_id = {m["mode_id"]: m for m in modes}
    factor_ids = sorted(m["mode_id"] for m in modes if m.get("kind") == "factor")
    poles = np.load(paths["factor_poles.npy"], allow_pickle=False).astype(np.float32)
    if poles.shape != (len(factor_ids), D_MODEL):
        raise EpicureError("factor_poles.npy does not match the factor modes in modes.json")
    with open(paths["cuisine_pole_provenance.json"]) as fh:
        provenance = json.load(fh)
    cuisine = {}
    for key, members in provenance.items():
        vecs = [np.asarray(by_id[m["mode_id"]]["pole"], dtype=np.float32)
                for m in members if m["mode_id"] in by_id and by_id[m["mode_id"]].get("pole")]
        if vecs:                    # the model card: unit mean of the cuisine-labelled mode poles
            cuisine[key.split(":", 1)[-1]] = _unit(_unit(np.stack(vecs)).mean(axis=0))
    return Model(E=_unit(E), vocab=vocab, itos=itos, factor_ids=factor_ids, factor_poles=_unit(poles),
                 factor_labels={m: by_id[m].get("label", m) for m in factor_ids}, cuisine_poles=cuisine)


_lock = threading.Lock()
_cache: dict[str, object] = {}


def get() -> Model | None:
    """The process-wide model, or None when the files are not installed or invalid."""
    from .. import config
    key = f"{config.EPICURE_DIR}|{config.EPICURE_CHECKSUMS}"
    with _lock:
        if key not in _cache:
            try:
                _cache[key] = load(config.EPICURE_DIR, _load_checksums(config.EPICURE_CHECKSUMS))
            except (OSError, EpicureError, KeyError, ValueError) as exc:
                _cache[key] = None
                _cache[key + "|error"] = str(exc)
        return _cache[key]


def status() -> dict:
    model = get()
    from .. import config
    return {"available": model is not None, "revision": REVISION,
            "error": None if model else _cache.get(f"{config.EPICURE_DIR}|{config.EPICURE_CHECKSUMS}|error")}


def reset_cache() -> None:
    with _lock:
        _cache.clear()
