"""Probe the three small challenges for improvements over the stored configs.

Uses the exact per-challenge checks from validate_submissions.load_small and
reports every configuration that passes with a smaller encoded size.
"""

import itertools
import json
import sys
import time
from pathlib import Path

import numpy as np
from numcodecs.registry import get_codec

HERE = Path(__file__).parent
CW = HERE.parent / "compression-lab-notebooks" / "challenge_work"
sys.path.insert(0, str(CW))

from validate_submissions import load_small  # noqa: E402

CURRENT = {
    "NaN": {"id": "mask.meta", "mask": float("nan"), "codec": {"id": "eb_quantize", "codec": {"id": "context_mixing.symbols", "mixer_rate": 0.004, "model_floor": 0.00390625}, "eb": 1.0, "offset": None}, "bitmap_codec": {"id": "context_mixing.bitmap", "mixer_rate": 0.005, "model_floor": 0.001953125}},
    "PwRel": {"id": "pw_ratio", "eb_ratio": 1.01, "eb_abs_marker": "$eb_abs", "log_codec": {"id": "eb_quantize", "eb": "$eb_abs", "codec": {"id": "context_mixing.residuals", "mixer_rate": 0.003}}, "sign_codec": {"id": "zstd.rs", "level": 19}},
    "Gradient": {"id": "lon_gradient", "eb": 1e-06, "codec": {"id": "eb_quantize", "eb": "$eb_abs", "codec": {"id": "context_mixing.residuals"}}, "eb_abs_marker": "$eb_abs", "spacing": 0.25, "stencil": 5, "anchor_step": None, "shrink": 0.02, "closure_flips": True},
}


def measure(cfg, da, check):
    codec = get_codec(cfg)
    x = da.values
    e = codec.encode(x)
    d = np.asarray(codec.decode(np.frombuffer(bytes(e), np.uint8))).reshape(x.shape)
    da_dec = da.copy(data=d.astype(x.dtype) if d.dtype != x.dtype else d)
    return len(bytes(e)), float(check(da, da_dec))


def variants(challenge):
    if challenge == "NaN":
        for mr, mf in itertools.product([0.003, 0.004, 0.005, 0.006], [1 / 512, 1 / 256, 1 / 128]):
            yield f"symbols mr={mr} mf={mf:.4g}", {"id": "mask.meta", "mask": float("nan"), "codec": {"id": "eb_quantize", "codec": {"id": "context_mixing.symbols", "mixer_rate": mr, "model_floor": mf}, "eb": 1.0}, "bitmap_codec": {"id": "context_mixing.bitmap", "mixer_rate": 0.005, "model_floor": 0.001953125}}
        for mr in [0.003, 0.004]:
            yield f"residuals mr={mr}", {"id": "mask.meta", "mask": float("nan"), "codec": {"id": "eb_quantize", "codec": {"id": "context_mixing.residuals", "mixer_rate": mr}, "eb": 1.0}, "bitmap_codec": {"id": "context_mixing.bitmap", "mixer_rate": 0.005, "model_floor": 0.001953125}}
        for br in [0.003, 0.008]:
            yield f"bitmap mr={br}", {"id": "mask.meta", "mask": float("nan"), "codec": {"id": "eb_quantize", "codec": {"id": "context_mixing.symbols", "mixer_rate": 0.004, "model_floor": 0.00390625}, "eb": 1.0}, "bitmap_codec": {"id": "context_mixing.bitmap", "mixer_rate": br, "model_floor": 0.001953125}}
    elif challenge == "PwRel":
        def pwr(mr, sign):
            return {"id": "pw_ratio", "eb_ratio": 1.01, "eb_abs_marker": "$eb_abs", "log_codec": {"id": "eb_quantize", "eb": "$eb_abs", "codec": {"id": "context_mixing.residuals", "mixer_rate": mr}}, "sign_codec": sign}
        for mr in [0.002, 0.003, 0.004, 0.005]:
            yield f"mr={mr}", pwr(mr, {"id": "zstd.rs", "level": 19})
        # route the zero plane through mask.meta + context-mixing bitmap:
        # zeros are masked (bitmap-coded), filled with a tiny constant so that
        # pw_ratio's own packed zero plane becomes trivial
        for mr, fill in itertools.product([0.003, 0.004], [1e-12, 1e-9]):
            yield f"zeromask mr={mr} fill={fill}", {"id": "mask.meta", "mask": 0.0, "bitmap_codec": {"id": "context_mixing.bitmap"}, "codec": {"id": "combinators.stack", "codecs": [{"id": "replace.threshold", "threshold": fill, "replacement": fill}, pwr(mr, {"id": "zstd.rs", "level": 19})]}}
    else:
        for stencil, shrink in itertools.product([3, 5, 7], [0.01, 0.02, 0.04]):
            yield f"stencil={stencil} shrink={shrink}", {"id": "lon_gradient", "eb": 1e-06, "codec": {"id": "eb_quantize", "eb": "$eb_abs", "codec": {"id": "context_mixing.residuals"}}, "eb_abs_marker": "$eb_abs", "spacing": 0.25, "stencil": stencil, "anchor_step": None, "shrink": shrink, "closure_flips": True}
        for mr in [0.002, 0.003, 0.005]:
            yield f"mr={mr}", {"id": "lon_gradient", "eb": 1e-06, "codec": {"id": "eb_quantize", "eb": "$eb_abs", "codec": {"id": "context_mixing.residuals", "mixer_rate": mr}}, "eb_abs_marker": "$eb_abs", "spacing": 0.25, "stencil": 5, "anchor_step": None, "shrink": 0.02, "closure_flips": True}


for challenge in ["NaN", "PwRel", "Gradient"]:
    da, check = load_small(challenge)
    base_size, base_viol = measure(CURRENT[challenge], da, check)
    assert base_viol == 0.0, (challenge, base_viol)
    print(f"== {challenge}: current size {base_size} cr {da.values.nbytes / base_size:.2f}", flush=True)
    best = (base_size, "current", CURRENT[challenge])
    for name, cfg in variants(challenge):
        t0 = time.time()
        try:
            size, viol = measure(cfg, da, check)
        except Exception as ex:
            print(f"   {name}: ERROR {type(ex).__name__}: {str(ex)[:100]}", flush=True)
            continue
        mark = " <-- BETTER" if viol == 0 and size < best[0] else ""
        print(f"   {name}: size {size} viol {viol:.2g} ({time.time() - t0:.0f}s){mark}", flush=True)
        if viol == 0 and size < best[0]:
            best = (size, name, cfg)
    if best[1] != "current":
        # try lossless post-compression on the winner
        for lcfg, lname in [({"id": "lzma", "preset": 9}, "lzma9"), ({"id": "zstd.rs", "level": 22}, "zstd22")]:
            pcfg = {"id": "combinators.stack", "codecs": [best[2], lcfg]}
            try:
                size, viol = measure(pcfg, da, check)
            except Exception:
                continue
            if viol == 0 and size < best[0]:
                best = (size, best[1] + "+" + lname, pcfg)
        print(f"   BEST {challenge}: {best[1]} size {base_size} -> {best[0]} (cr {da.values.nbytes / best[0]:.2f})", flush=True)
        json.dump({"challenge": challenge, "size": best[0], "cr": da.values.nbytes / best[0], "config": best[2], "name": best[1]},
                  open(HERE / f"probe_{challenge}.json", "w"), indent=1)
    else:
        print(f"   {challenge}: current config remains the best", flush=True)
print("PROBE DONE", flush=True)
