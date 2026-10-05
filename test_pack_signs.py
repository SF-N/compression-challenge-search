"""Test pack_signs=False + context_mixing.bitmap signs for pressure d and vo."""

import copy
import json
import sys
from pathlib import Path

import numpy as np
from numcodecs.registry import get_codec

HERE = Path(__file__).parent
CW = HERE.parent / "compression-lab-notebooks" / "challenge_work"


def patch_pw_ratio(cfg):
    if isinstance(cfg, dict):
        if cfg.get("id") == "pw_ratio":
            cfg = dict(cfg, sign_codec={"id": "context_mixing.bitmap"}, pack_signs=False)
        return {k: patch_pw_ratio(v) for k, v in cfg.items()}
    if isinstance(cfg, list):
        return [patch_pw_ratio(v) for v in cfg]
    return cfg


for v in ["d", "vo"]:
    x = np.load(CW / "data" / "era5" / f"pressure__{v}.npz")["arr"]
    old = json.load(open(CW / "results_verified" / f"pressure__{v}.json"))["result"]
    cfg = patch_pw_ratio(copy.deepcopy(json.load(open(CW / "results" / f"pressure__{v}.json"))["config"]))
    codec = get_codec(cfg)
    e = codec.encode(x)
    d = np.asarray(codec.decode(np.frombuffer(bytes(e), np.uint8))).reshape(x.shape)
    size = len(bytes(e))
    print(f"{v}: verified size {old['nbytes'] // round(old['cr'] * 0 + 1) if False else round(x.nbytes / old['cr'])} (cr {old['cr']:.2f}) -> pack_signs=False size {size} (cr {x.nbytes / size:.2f})", flush=True)
    if x.nbytes / size > old["cr"]:
        from compression_recommendations import Recommendations
        from compression_requirement_checks import check_safety_requirements

        reqs = Recommendations.provide.search(markers={"grib-short-name": v, "level-kind": "pressure"})
        ok = bool(check_safety_requirements(original=x, reconstructed=d, requirements=reqs))
        print(f"   official={ok}", flush=True)
        if ok:
            json.dump({"variable": v, "size": size, "cr": x.nbytes / size, "config": cfg},
                      open(HERE / f"packsigns_pressure__{v}.json", "w"), indent=1)
print("PACK_SIGNS TEST DONE", flush=True)
