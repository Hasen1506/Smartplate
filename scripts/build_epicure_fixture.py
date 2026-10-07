"""Rebuild the small offline Epicure fixture the tests use (tests/fixtures/epicure).

    python scripts/fetch_epicure.py && python scripts/build_epicure_fixture.py

Keeps the real vectors for SmartPlate's pantry ingredients, the ingredients dish names
imply, and a few extra tokens the tests query; keeps the modes behind the cuisine poles
(poles rounded to 6 decimals) and every factor mode's label; keeps factor_poles.npy as is.
The result has the real file formats, so the tests exercise the same loader as production.
Epicure-Core © 2026 Jakub Radzikowski and Josef Chen (KAIKAKU.AI), CC BY 4.0.
"""
import hashlib
import json
import os
import shutil
import struct
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from smartplate import config                           # noqa: E402
from smartplate.domain import epicure, flavour, ingredients   # noqa: E402

OUT = os.path.join(ROOT, "tests", "fixtures", "epicure")
EXTRA = ["peanut_butter", "almond_paste", "soy_sauce", "cheddar_cheese", "egg_noodle", "fish_sauce", "shrimp_paste",
         "tahini", "pasta", "bread", "hummus", "coconut_water", "plant_based_milk", "eggplant", "buckwheat"]


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else config.EPICURE_DIR
    model = epicure.load(src, dict(epicure.PINNED))
    keep = sorted({*ingredients.PANTRY, *(t for ts in flavour.DISH_WORDS.values() for t in ts), *EXTRA} & set(model.vocab))
    with open(os.path.join(src, "embeddings.safetensors"), "rb") as f:
        raw = epicure.read_safetensors(f.read())
    E = np.stack([raw[model.vocab[t]] for t in keep]).astype("<f4")
    os.makedirs(OUT, exist_ok=True)
    header = json.dumps({"embeddings": {"dtype": "F32", "shape": list(E.shape), "data_offsets": [0, E.nbytes]}},
                        separators=(",", ":")).encode()
    with open(os.path.join(OUT, "embeddings.safetensors"), "wb") as f:
        f.write(struct.pack("<Q", len(header)) + header + E.tobytes())
    with open(os.path.join(OUT, "vocab.json"), "w") as f:
        json.dump({t: i for i, t in enumerate(keep)}, f, indent=0)
    with open(os.path.join(OUT, "itos.json"), "w") as f:
        json.dump({str(i): t for i, t in enumerate(keep)}, f, indent=0)
    with open(os.path.join(src, "cuisine_pole_provenance.json")) as f:
        prov = json.load(f)
    cuisine_ids = {m["mode_id"] for v in prov.values() for m in v}
    with open(os.path.join(src, "modes.json")) as f:
        modes = json.load(f)
    small, kept = [], set(keep)
    for m in modes:
        if m["mode_id"] in cuisine_ids or m["kind"] == "factor":
            entry = {k: m[k] for k in ("mode_id", "kind", "property", "label")}
            entry["members"] = [x for x in m["members"] if x in kept]
            entry["n_members"] = len(entry["members"])
            if m["mode_id"] in cuisine_ids:
                entry["pole"] = [round(x, 6) for x in m["pole"]]
            small.append(entry)
    with open(os.path.join(OUT, "modes.json"), "w") as f:
        json.dump(small, f, separators=(",", ":"))
    shutil.copy(os.path.join(src, "factor_poles.npy"), OUT)
    shutil.copy(os.path.join(src, "cuisine_pole_provenance.json"), OUT)
    with open(os.path.join(OUT, "SHA256SUMS"), "w") as f:
        for name in epicure.FILES:
            f.write(f"{hashlib.sha256(open(os.path.join(OUT, name), 'rb').read()).hexdigest()}  {name}\n")
    print(f"fixture: {len(keep)} ingredients, {len(small)} modes → {OUT}")


if __name__ == "__main__":
    main()
