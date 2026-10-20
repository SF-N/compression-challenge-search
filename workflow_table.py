"""Fill the workflow comparison table cells for one leveltype.

Usage: python workflow_table.py [pressure|single]

For each variable evaluate the four candidate workflows

  W1  quantize + context-mixing      (eb_quantize -> context_mixing)
  W2  quantize + interpolation       (interp_ctx)
  W3  log-relative quantizer         (pw_ratio around the W1/W2 core)
  W4  SPERR wavelet                  (sperr.rs; log-domain pwratio-sperr when
                                      the bound is pointwise-relative)

reusing the ratios already stored in results/<lk>__*.json where the family was
searched, and running only the missing cells. Lossless/categorical variables
are skipped. Cells whose required quantization step would need > MAX_BITS
bits per value are marked infeasible without running.

Resumable: every finished cell is appended to workflow_table_cells[_<lk>].jsonl.
"""

import glob
import json
import math
import re
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from reqs import analyse
from search import (
    _ebq_residuals,
    _interp_abs,
    _pwr,
    _sperr_bpp,
    _sperr_pwe,
    _sperr_q,
    evaluate,
    get_requirements,
    search_family,
    wrap,
)

LK = sys.argv[1] if len(sys.argv) > 1 else "pressure"
CACHE = HERE / ("workflow_table_cells.jsonl" if LK == "pressure" else f"workflow_table_cells_{LK}.jsonl")
MAX_BITS = 34
MAX_EVALS = 12

LOSSLESS = {"lsm", "slt", "tvl", "tvh", "ptype", "cdww", "msqs", "rhoao", "tauoc"}

# normalized family name -> workflow
FAMILY_WORKFLOW = {
    "ebq-residuals": "W1", "ebq-symbols": "W1", "ctx-abs": "W1", "nanctx-abs": "W1",
    "grid-symbols": "W1", "grid-residuals": "W1", "grid-nanctx": "W1", "grid-ctx": "W1",
    "interp-abs": "W2",
    "ctx-rel": "W3", "pwratio-ebq-residuals": "W3", "pwratio-interp": "W3", "pwratio-ctx": "W3",
    "sperr-pwe": "W4", "sperr-q": "W4", "sperr-bpp": "W4",
    "sperr-pwe-2d": "W4", "sperr-q-2d": "W4", "sperr-bpp-2d": "W4",
    "pwratio-sperr": "W4", "pwratio-sperr-2d": "W4",
}


def norm_family(fam):
    if not fam:
        return None
    return re.sub(r"\+thr[\d.]+", "", fam).replace("+zeromask", "")


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def load_cells():
    cells = {}
    if CACHE.exists():
        for line in CACHE.read_text().splitlines():
            c = json.loads(line)
            cells[(c["var"], c["workflow"], c["family"])] = c
    return cells


def save_cell(cell):
    with open(CACHE, "a") as f:
        f.write(json.dumps(cell) + "\n")


def stored_workflows(v):
    path = HERE / "results" / f"{LK}__{v}.json"
    r = json.load(open(path))
    have = set()
    for c in r.get("candidates", []) + [{"meta": r.get("meta", {})}]:
        wf = FAMILY_WORKFLOW.get(norm_family(c.get("meta", {}).get("family")))
        if wf:
            have.add(wf)
    return have


class _Lim:
    minimum = None
    maximum = None


INFO_NS = _Lim()


def run_cell(name, make, p0, wk, x, reqs, bisect):
    logf = lambda m: log("    " + m)
    if bisect:
        best = search_family(name, lambda p: wrap(make(p), INFO_NS, **wk), p0, x, reqs, logf, max_evals=MAX_EVALS)
        if best is None:
            return None
        size, p = best
        return {"cr": x.nbytes / size, "p": p}
    ok, size, _ = evaluate(wrap(make(p0), INFO_NS, **wk), x, reqs)
    if not ok:
        ok, size, _ = evaluate(wrap(make(p0 * 0.98), INFO_NS, **wk), x, reqs)
        if not ok:
            return None
        return {"cr": x.nbytes / size, "p": p0 * 0.98}
    return {"cr": x.nbytes / size, "p": p0}


def main():
    cells = load_cells()
    vars_ = sorted(Path(p).stem.split("__", 1)[1] for p in glob.glob(str(HERE / "results" / f"{LK}__*.json"))
                   if "config" in json.load(open(p)))
    vars_ = [v for v in vars_ if v not in LOSSLESS]
    log(f"{LK}: {len(vars_)} variables")
    for v in vars_:
        reqs = get_requirements(LK, v)
        info = analyse(reqs)
        if info.lossless:
            continue
        INFO_NS.minimum, INFO_NS.maximum = info.minimum, info.maximum
        x = np.load(HERE / "data/era5" / f"{LK}__{v}.npz")["arr"]
        fin = x[np.isfinite(x)]
        has_nan = bool(np.isnan(x).any())
        zf = float((fin == 0).mean()) if fin.size else 0.0
        nz = np.abs(fin[fin != 0])
        minnz = float(nz.min()) if nz.size else 0.0
        rng = float(fin.max() - fin.min()) if fin.size else 1.0
        maxabs = float(np.abs(fin).max()) if fin.size else 1.0
        mean_abs_x = float(np.abs(fin).mean()) if fin.size else 1.0
        stored = stored_workflows(v)
        need = [w for w in ("W1", "W2", "W3", "W4") if w not in stored
                and not any(k[0] == v and k[1] == w for k in cells)]
        if not need:
            continue

        # largest uniform step satisfying all pointwise bounds (zeros masked)
        steps = [2 * b for b in info.max_abs] + [2 * b * rng for b in info.max_range_rel]
        rel_ok = True
        for rel in info.max_rel:
            if minnz == 0:
                rel_ok = False
            else:
                steps.append(2 * rel * minnz)
        step = min(steps) if steps and rel_ok else (min(steps) if steps and not info.max_rel else None)
        if info.max_rel and not rel_ok:
            step = None

        mean_bounds = (list(info.mean_abs) + [b * mean_abs_x for b in info.mean_rel]
                       + [b * rng for b in info.mean_range_rel])
        has_mean = bool(mean_bounds)
        pointwise = info.has_pointwise
        if pointwise and step is not None:
            p0_abs = step / 2
            bisect_abs = has_mean  # a mean budget may allow a coarser step
        elif has_mean:
            p0_abs = 4.0 * max(mean_bounds)
            bisect_abs = True
        else:
            p0_abs = None
            bisect_abs = False
        lin_infeasible = (p0_abs is None or p0_abs <= 0
                          or math.log2(max(rng / (2 * p0_abs), 1.0)) > MAX_BITS)

        need_zm = zf > 0.001 and bool(info.max_rel or info.mean_rel)
        mz = need_zm or zf > 0.2
        wk_lin = {"mask_nan": has_nan, "mask_zero": mz}
        wk_pwr = {"mask_nan": has_nan, "mask_zero": False}

        # pw_ratio start value
        relb = ([b / maxabs for b in info.max_abs] + [b * rng / maxabs for b in info.max_range_rel]
                + list(info.max_rel))
        if relb:
            p0_rel = min(relb)
            bisect_rel = has_mean
        elif info.mean_rel:
            p0_rel = 2.0 * max(info.mean_rel)
            bisect_rel = True
        else:
            p0_rel = 2.0 * max(mean_bounds) / mean_abs_x if mean_bounds else None
            bisect_rel = True
        pure_rel = bool(info.max_rel) and not info.max_abs and not info.max_range_rel

        log(f"== {v}: nan={has_nan} zeros={zf:.3f} step={step} p0_abs={p0_abs} p0_rel={p0_rel} need={need}")

        plans = []
        if "W1" in need:
            if lin_infeasible:
                save_cell({"var": v, "workflow": "W1", "family": "ebq", "infeasible": True,
                           "note": "pointwise-relative bound: absolute step infeasible"})
            else:
                plans.append(("W1", "ebq-residuals", _ebq_residuals, p0_abs, wk_lin, bisect_abs))
        if "W2" in need:
            if lin_infeasible:
                save_cell({"var": v, "workflow": "W2", "family": "interp", "infeasible": True,
                           "note": "same limit as W1"})
            else:
                plans.append(("W2", "interp-abs", _interp_abs, p0_abs, wk_lin, bisect_abs))
        if "W3" in need:
            if p0_rel is None or p0_rel <= 0:
                save_cell({"var": v, "workflow": "W3", "family": "pwratio", "infeasible": True,
                           "note": "no usable relative start value"})
            else:
                plans.append(("W3", "pwratio-ebq-residuals", _pwr(_ebq_residuals), p0_rel, wk_pwr, bisect_rel))
                plans.append(("W3", "pwratio-interp", _pwr(_interp_abs), p0_rel, wk_pwr, bisect_rel))
        if "W4" in need:
            if pure_rel or lin_infeasible:
                if p0_rel is None or p0_rel <= 0:
                    save_cell({"var": v, "workflow": "W4", "family": "sperr", "infeasible": True,
                               "note": "no usable tolerance"})
                else:
                    plans.append(("W4", "pwratio-sperr", _pwr(_sperr_pwe), p0_rel, wk_pwr, bisect_rel))
            else:
                plans.append(("W4", "sperr-pwe", _sperr_pwe, p0_abs, wk_lin, bisect_abs))
                if has_mean and not pointwise:
                    plans.append(("W4", "sperr-q", _sperr_q, p0_abs, wk_lin, True))
                    plans.append(("W4", "sperr-bpp", _sperr_bpp, 1.0, wk_lin, True))

        for wf, fam, make, p0, wk, bisect in plans:
            if (v, wf, fam) in cells:
                continue
            log(f"  run {v} {wf}/{fam} p0={p0:.4g} bisect={bisect} zm={wk.get('mask_zero')} nan={wk.get('mask_nan')}")
            t0 = time.time()
            try:
                r = run_cell(fam, make, p0, wk, x, reqs, bisect)
            except Exception as ex:
                r = None
                log(f"    ERROR {type(ex).__name__}: {str(ex)[:150]}")
            cell = {"var": v, "workflow": wf, "family": fam, "time_s": round(time.time() - t0, 1)}
            if r is None:
                cell["failed"] = True
            else:
                cell.update(cr=r["cr"], p=r["p"], zeromask=bool(wk.get("mask_zero")))
            save_cell(cell)
            cells[(v, wf, fam)] = cell
            log(f"    -> {cell.get('cr', 'FAILED')} ({cell['time_s']}s)")
    log("ALL CELLS DONE")


if __name__ == "__main__":
    main()
