"""Fold the six officially verified workflow improvements into results/*.json
using fork-free configs (mask.meta wraps the core codec directly; clip stays
outside the mask so upstream numcodecs-mask can decode without the SF-N
combinators fork). Re-measures each config on the official data and runs the
official checker before touching anything."""

import json
import sys
from pathlib import Path

import numpy as np
from numcodecs.registry import get_codec

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from search import get_requirements
from reqs import analyse

IMPROVEMENTS = [
    # lk, var, family, p, zeromask
    ("pressure", "cc", "ebq-residuals", 1.953125e-05, True),
    ("pressure", "w", "ebq-residuals", 4.76837158203125e-08, False),
    ("single", "swh", "interp-abs", 0.022533248198703566, False),
    ("single", "shts", "interp-abs", 0.02204857012428944, False),
    ("single", "p1ww", "interp-abs", 0.02, False),
    ("single", "avg_esrwe", "pwratio-ebq-residuals", 0.01979686908366251, False),
]


def core_cfg(family, p):
    if family == "ebq-residuals":
        return {"id": "eb_quantize", "eb": float(p), "codec": {"id": "context_mixing.residuals"}}
    if family == "interp-abs":
        return {"id": "interp_ctx", "eb": float(p)}
    if family == "pwratio-ebq-residuals":
        return {"id": "pw_ratio", "eb_ratio": 1.0 + float(p), "eb_abs_marker": "$eb_abs",
                "log_codec": {"id": "eb_quantize", "eb": "$eb_abs", "codec": {"id": "context_mixing.residuals"}},
                "sign_codec": {"id": "zstd.rs", "level": 19}}
    raise KeyError(family)


def build(lk, v, family, p, zeromask, x, info):
    cfg = core_cfg(family, p)
    short = family
    has_nan = bool(np.isnan(x).any())
    if zeromask:
        cfg = {"id": "mask.meta", "mask": 0.0, "bitmap_codec": {"id": "context_mixing.bitmap"}, "codec": cfg}
        short = "Mask(zero) + " + short + "+zeromask"
    if has_nan and not family.startswith("pwratio"):
        cfg = {"id": "mask.meta", "mask": float("nan"), "bitmap_codec": {"id": "context_mixing.bitmap"}, "codec": cfg}
        short = "Mask(NaN) + " + short
    codecs = []
    if info.minimum is not None or info.maximum is not None:
        codecs.append({"id": "clip", "minimum": info.minimum, "maximum": info.maximum})
    codecs.append(cfg)
    codecs.append({"id": "zstd.rs", "level": 22})
    short += " + Zstd-22"
    return {"id": "combinators.stack", "codecs": codecs}, short


def main():
    from compression_requirement_checks import check_safety_requirements
    for lk, v, family, p, zeromask in IMPROVEMENTS:
        reqs = get_requirements(lk, v)
        info = analyse(reqs)
        x = np.load(HERE / "data/era5" / f"{lk}__{v}.npz")["arr"]
        cfg, short = build(lk, v, family, p, zeromask, x, info)
        codec = get_codec(cfg)
        e = codec.encode(x)
        d = np.asarray(codec.decode(np.frombuffer(bytes(e), np.uint8))).reshape(x.shape)
        ok = bool(check_safety_requirements(original=x, reconstructed=d, requirements=reqs))
        cr = x.nbytes / len(e)
        rpath = HERE / "results" / f"{lk}__{v}.json"
        r = json.load(open(rpath))
        old = r["cr"]
        vp = HERE / "results_verified" / f"{lk}__{v}.json"
        if vp.exists():
            old = json.load(open(vp))["result"]["cr"]
        print(f"{lk:8s} {v:11s} official_ok={ok} cr={cr:.2f} (old {old:.2f}) short={short}")
        if not ok or cr <= old:
            print("   -> SKIPPED (not ok or not better)")
            continue
        r.update(config=cfg, config_short=short, cr=cr, size=len(e),
                 meta={"family": family, "p": p})
        json.dump(r, open(rpath, "w"), indent=1)
    print("done")


if __name__ == "__main__":
    main()
