"""Measure the four standardized workflows per variable on the OFFICIAL basis:
the same cached arrays (data/era5/<lk>__<v>.npz) and the same official checker
(compression_requirement_checks.check_safety_requirements) that produced the
verified submission ratios.

Standardized workflow shape (identical for every cell):
    clip -> [NaN/zero mask bitmaps] -> core(p) -> zstd(level 22)

Starting parameters come from the stored search candidates and the earlier
fast-run cells; missing cells are bisected with the fast checker first. Every
final cell is confirmed with the official checker; on failure the error knob
is tightened until it passes.

Usage:  python workflow_cells_official.py [pressure] [single]
Cells are appended to workflow_cells_official_<lk>.jsonl (resumable).
"""

import glob
import json
import math
import re
import sys
import time
from pathlib import Path

import numpy as np
from numcodecs.registry import get_codec

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from reqs import analyse, fast_check
from search import (
    _ebq_residuals,
    _ebq_symbols,
    _interp_abs,
    _pwr,
    _sperr_bpp,
    _sperr_pwe,
    _sperr_q,
    get_requirements,
    search_family,
    wrap,
)

MAX_BITS = 34
MAX_EVALS = 12
LOSSLESS = {"lsm", "slt", "tvl", "tvh", "ptype", "cdww", "msqs", "rhoao", "tauoc"}

# family -> (make, workflow); families with incompatible p semantics (grid-*)
# or outside the four workflows (absrel-*) are intentionally absent
FAMS = {
    "ebq-residuals": (_ebq_residuals, "W1"), "ctx-abs": (_ebq_residuals, "W1"),
    "ebq-symbols": (_ebq_symbols, "W1"), "nanctx-abs": (_ebq_symbols, "W1"),
    "interp-abs": (_interp_abs, "W2"),
    "ctx-rel": (_pwr(_ebq_residuals), "W3"), "pwratio-ebq-residuals": (_pwr(_ebq_residuals), "W3"),
    "pwratio-ctx": (_pwr(_ebq_residuals), "W3"), "pwratio-interp": (_pwr(_interp_abs), "W3"),
    "sperr-pwe": (_sperr_pwe, "W4"), "sperr-q": (_sperr_q, "W4"), "sperr-bpp": (_sperr_bpp, "W4"),
    "pwratio-sperr": (_pwr(_sperr_pwe), "W4"),
}


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


class _Lim:
    minimum = None
    maximum = None


INFO_NS = _Lim()


def norm_family(fam):
    if not fam:
        return None
    f = re.sub(r"\+thr[\d.]+", "", fam).replace("+zeromask", "").replace("-2d", "")
    return f


def build_cfg(make, p, wk):
    cfg = wrap(make(p), INFO_NS, **wk)
    return {"id": "combinators.stack", "codecs": [cfg, {"id": "zstd.rs", "level": 22}]}


def roundtrip(cfg, x):
    codec = get_codec(cfg)
    e = codec.encode(x)
    d = np.asarray(codec.decode(np.frombuffer(bytes(e), np.uint8)))
    if d.shape != x.shape:
        d = d.reshape(x.shape)
    return len(e), d


def official_measure(make, p, wk, x, reqs, check_official, max_tighten=6):
    """Encode/decode with the standardized shape, confirm officially; tighten
    p if needed. Returns (size, p) or None."""
    for _ in range(max_tighten):
        cfg = build_cfg(make, p, wk)
        try:
            size, d = roundtrip(cfg, x)
        except Exception as ex:
            log(f"    roundtrip ERROR {type(ex).__name__}: {str(ex)[:120]}")
            return None
        if bool(check_official(original=x, reconstructed=d, requirements=reqs)):
            return size, p
        log(f"    official check FAILED at p={p:.6g}, tightening")
        p *= 0.9
    return None


def main():
    from compression_requirement_checks import check_safety_requirements

    leveltypes = sys.argv[1:] or ["pressure", "single"]
    for lk in leveltypes:
        cache = HERE / f"workflow_cells_official_{lk}.jsonl"
        cells = {}
        if cache.exists():
            for line in cache.read_text().splitlines():
                c = json.loads(line)
                cells[(c["var"], c["workflow"])] = c

        def save(cell):
            with open(cache, "a") as f:
                f.write(json.dumps(cell) + "\n")
            cells[(cell["var"], cell["workflow"])] = cell

        # earlier fast-run cells as parameter sources
        fast_cells = {}
        fc_path = HERE / ("workflow_table_cells.jsonl" if lk == "pressure" else f"workflow_table_cells_{lk}.jsonl")
        if fc_path.exists():
            for line in fc_path.read_text().splitlines():
                c = json.loads(line)
                if c.get("cr"):
                    fast_cells.setdefault((c["var"], c["workflow"]), []).append(c)

        vars_ = sorted(Path(p).stem.split("__", 1)[1]
                       for p in glob.glob(str(HERE / "results" / f"{lk}__*.json"))
                       if "config" in json.load(open(p)))
        vars_ = [v for v in vars_ if v not in LOSSLESS]
        log(f"=== {lk}: {len(vars_)} variables ===")

        for v in vars_:
            todo = [w for w in ("W1", "W2", "W3", "W4") if (v, w) not in cells]
            if not todo:
                continue
            reqs = get_requirements(lk, v)
            info = analyse(reqs)
            if info.lossless:
                continue
            INFO_NS.minimum, INFO_NS.maximum = info.minimum, info.maximum
            x = np.load(HERE / "data/era5" / f"{lk}__{v}.npz")["arr"]
            fin = x[np.isfinite(x)]
            has_nan = bool(np.isnan(x).any())
            zf = float((fin == 0).mean()) if fin.size else 0.0
            nzv = np.abs(fin[fin != 0])
            minnz = float(nzv.min()) if nzv.size else 0.0
            rng = float(fin.max() - fin.min()) if fin.size else 1.0
            maxabs = float(np.abs(fin).max()) if fin.size else 1.0
            mean_abs_x = float(np.abs(fin).mean()) if fin.size else 1.0

            # starting points: best stored candidate per workflow (no thresholds)
            r = json.load(open(HERE / "results" / f"{lk}__{v}.json"))
            starts = {}
            cands = list(r.get("candidates", []))
            cands.append({"cr": r["cr"], "meta": r.get("meta", {}), "short": r.get("config_short", "")})
            for c in cands:
                short = c.get("short", "")
                if "Threshold" in short or "replace.threshold" in short:
                    continue
                fam = norm_family(c.get("meta", {}).get("family"))
                if fam not in FAMS:
                    continue
                make, wf = FAMS[fam]
                zm = "+zeromask" in short or "Mask(zero)" in short
                cur = starts.get(wf)
                if cur is None or c["cr"] > cur["cr"]:
                    starts[wf] = {"cr": c["cr"], "p": c["meta"].get("p"), "fam": fam, "zm": zm}
            for wf in ("W1", "W2", "W3", "W4"):
                for c in fast_cells.get((v, wf), []):
                    fam = norm_family(c["family"])
                    if fam not in FAMS:
                        continue
                    cur = starts.get(wf)
                    if cur is None or c["cr"] > cur["cr"]:
                        starts[wf] = {"cr": c["cr"], "p": c["p"], "fam": fam, "zm": c.get("zeromask", False)}

            # fresh-plan fallbacks (same logic as workflow_table.py)
            steps = [2 * b for b in info.max_abs] + [2 * b * rng for b in info.max_range_rel]
            for rel in info.max_rel:
                steps.append(2 * rel * minnz if minnz > 0 else float("nan"))
            step = min(steps) if steps and not any(map(math.isnan, steps)) else None
            mean_bounds = (list(info.mean_abs) + [b * mean_abs_x for b in info.mean_rel]
                           + [b * rng for b in info.mean_range_rel])
            has_mean = bool(mean_bounds)
            if info.has_pointwise and step is not None:
                p0_abs, bisect_abs = step / 2, has_mean
            elif has_mean:
                p0_abs, bisect_abs = 4.0 * max(mean_bounds), True
            else:
                p0_abs, bisect_abs = None, False
            lin_infeasible = (p0_abs is None or p0_abs <= 0
                              or math.log2(max(rng / (2 * p0_abs), 1.0)) > MAX_BITS)
            relb = ([b / maxabs for b in info.max_abs] + [b * rng / maxabs for b in info.max_range_rel]
                    + list(info.max_rel))
            if relb:
                p0_rel, bisect_rel = min(relb), has_mean
            elif info.mean_rel:
                p0_rel, bisect_rel = 2.0 * max(info.mean_rel), True
            elif mean_bounds:
                p0_rel, bisect_rel = 2.0 * max(mean_bounds) / mean_abs_x, True
            else:
                p0_rel, bisect_rel = None, False
            pure_rel = bool(info.max_rel) and not info.max_abs and not info.max_range_rel
            need_zm = zf > 0 and bool(info.max_rel or info.mean_rel)
            mz_rule = need_zm or zf > 0.2
            fresh = {
                "W1": None if lin_infeasible else ("ebq-residuals", p0_abs, mz_rule, bisect_abs),
                "W2": None if lin_infeasible else ("interp-abs", p0_abs, mz_rule, bisect_abs),
                "W3": None if p0_rel is None else ("pwratio-interp", p0_rel, False, bisect_rel),
                "W4": (("pwratio-sperr", p0_rel, False, bisect_rel) if (pure_rel or lin_infeasible)
                       else ("sperr-q" if has_mean and not info.has_pointwise else "sperr-pwe",
                             p0_abs, mz_rule, True if has_mean else False)) if (p0_rel or p0_abs) else None,
            }

            start_desc = ", ".join(k + ":" + s["fam"] for k, s in sorted(starts.items()))
            log(f"== {lk} {v}: nan={has_nan} zeros={zf:.3f} todo={todo} starts=[{start_desc}]")
            for wf in todo:
                t0 = time.time()
                src = starts.get(wf)
                if src is None and fresh[wf] is None:
                    save({"var": v, "workflow": wf, "infeasible": True,
                          "note": "no feasible configuration (pointwise-relative bound)"})
                    log(f"  {wf}: infeasible")
                    continue
                if src is not None:
                    fam, p, zm = src["fam"], src["p"], src["zm"]
                    make, _ = FAMS[fam]
                    is_pwr = fam.startswith("pwratio") or fam == "ctx-rel"
                    wk = {"mask_nan": has_nan, "mask_zero": bool(zm) and not is_pwr}
                else:
                    fam, p, zm, bisect = fresh[wf]
                    make, _ = FAMS[fam]
                    wk = {"mask_nan": has_nan, "mask_zero": zm}
                    if bisect:
                        logf = lambda m: log("    " + m)
                        best = search_family(fam, lambda q: wrap(make(q), INFO_NS, **wk), p, x, reqs,
                                             logf, max_evals=MAX_EVALS)
                        if best is None and wf == "W4" and fam != "sperr-pwe" and p0_abs:
                            # fallback: pointwise-tolerance mode with exact zeros
                            fam, make = "sperr-pwe", _sperr_pwe
                            wk = {"mask_nan": has_nan, "mask_zero": True}
                            best = search_family(fam, lambda q: wrap(make(q), INFO_NS, **wk), p0_abs,
                                                 x, reqs, logf, max_evals=MAX_EVALS)
                        if best is None:
                            save({"var": v, "workflow": wf, "family": fam, "failed": True})
                            log(f"  {wf}/{fam}: bisection found nothing")
                            continue
                        _, p = best
                log(f"  {wf}/{fam} p={p:.6g} zm={wk['mask_zero']}")
                res = official_measure(make, p, wk, x, reqs, check_safety_requirements)
                if res is None:
                    save({"var": v, "workflow": wf, "family": fam, "failed": True})
                    log(f"  {wf}/{fam}: FAILED officially")
                    continue
                size, p_ok = res
                save({"var": v, "workflow": wf, "family": fam, "p": p_ok,
                      "zeromask": bool(wk["mask_zero"]), "nanmask": has_nan,
                      "cr": x.nbytes / size, "size": size, "official_ok": True,
                      "time_s": round(time.time() - t0, 1)})
                log(f"  {wf}/{fam}: cr={x.nbytes / size:.2f} official-ok ({time.time() - t0:.0f}s)")
    log("ALL OFFICIAL CELLS DONE")


if __name__ == "__main__":
    main()
