"""Map a workflow cell (workflow_cells_official_<lk>.jsonl) to the numcodecs
configuration that produced it, and optionally re-run it.

    from workflow_config import config_for, cell_to_config
    cfg = config_for("pressure", "z", "W1")        # best official cell of that workflow
    cfg = cell_to_config(cell_dict, "single", "tp") # any cell record

    cfg = config_from_table("pressure", "z", "W1")  # from tables/<lk>_workflow_table.xlsx (made by make_table.py)
    cfg = config_from_params("single", "avg_esrwe", "W3", "ratio=1+0.01979686908366251 (ebq core)")  # from a parameter string

Command line:
    python workflow_config.py pressure z W1          # print the config
    python workflow_config.py pressure z W1 --run    # ... and encode/decode + official check

The configuration is built exactly as workflow_cells_official.py built it:
    clip (if the recommendation has data limits)
      -> mask.meta planes (NaN if nanmask, 0.0 if zeromask; context_mixing.bitmap)
      -> core codec of the cell's family at the cell's parameter p
      -> zstd.rs level 22
Note: cells with nanmask=True whose core is stacked behind a clip (W2 on
NaN-masked fields with data limits) need the mask-forwarding combinators fork;
in the standard environment place the clip outside mask.meta instead (see
fold_improvements.py)."""

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from reqs import analyse
from search import (_ebq_residuals, _ebq_symbols, _interp_abs, _pwr, _sperr_bpp,
                    _sperr_pwe, _sperr_q, get_requirements, wrap)

FAMS = {
    "ebq-residuals": _ebq_residuals, "ctx-abs": _ebq_residuals,
    "ebq-symbols": _ebq_symbols, "nanctx-abs": _ebq_symbols,
    "interp-abs": _interp_abs,
    "ctx-rel": _pwr(_ebq_residuals), "pwratio-ebq-residuals": _pwr(_ebq_residuals),
    "pwratio-ctx": _pwr(_ebq_residuals), "pwratio-interp": _pwr(_interp_abs),
    "sperr-pwe": _sperr_pwe, "sperr-q": _sperr_q, "sperr-bpp": _sperr_bpp,
    "pwratio-sperr": _pwr(_sperr_pwe),
}


def cell_to_config(cell, lk, var):
    """numcodecs config dict for one jsonl cell record."""
    if cell.get("infeasible") or cell.get("failed"):
        raise ValueError(f"cell has no configuration: {cell}")
    info = analyse(get_requirements(lk, var))
    make = FAMS[cell["family"]]
    core = wrap(make(cell["p"]), info,
                mask_nan=bool(cell.get("nanmask", False)),
                mask_zero=bool(cell.get("zeromask", False)))
    return {"id": "combinators.stack", "codecs": [core, {"id": "zstd.rs", "level": 22}]}


def cells(lk):
    path = HERE / f"workflow_cells_official_{lk}.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def best_cell(lk, var, workflow):
    cands = [c for c in cells(lk) if c["var"] == var and c["workflow"] == workflow and c.get("cr")]
    if not cands:
        raise KeyError(f"no official cell for {lk} {var} {workflow}")
    return max(cands, key=lambda c: c["cr"])


def config_for(lk, var, workflow):
    return cell_to_config(best_cell(lk, var, workflow), lk, var)


def config_from_params(lk, var, workflow, params):
    """Build the config from a parameter string as shown in the xlsx parameter
    columns, e.g.
        W1: 'eb=9.576032806985737 (residuals), zero-mask'
        W2: 'eb=0.022533248198703566, zero-mask'
        W3: 'ratio=1+0.01979686908366251 (interp core)'
        W4: 'pwe=1.0580121460444473e-08, zero-mask'   (or q=..., bpp=...)
    The NaN plane is added iff the field contains NaNs (the rule used for all
    cells); clip comes from the recommendation."""
    import re
    import numpy as np
    m = re.match(r"\s*(eb|ratio|pwe|q|bpp)=(?:1\+)?([-+0-9.eE]+)\s*(?:\(([^)]*)\))?(.*)$", params)
    if not m:
        raise ValueError(f"cannot parse parameter string: {params!r}")
    kind, value, flavour, rest = m.group(1), float(m.group(2)), (m.group(3) or "").strip(), m.group(4)
    zeromask = "zero-mask" in rest
    if workflow == "W1":
        family = "ebq-symbols" if flavour.startswith("symbols") else "ebq-residuals"
    elif workflow == "W2":
        family = "interp-abs"
    elif workflow == "W3":
        family = "pwratio-interp" if flavour.startswith("interp") else "pwratio-ebq-residuals"
    elif workflow == "W4":
        family = {"pwe": "sperr-pwe", "q": "sperr-q", "bpp": "sperr-bpp"}[kind]
        if flavour.startswith("log"):
            family = "pwratio-sperr"
    else:
        raise ValueError(workflow)
    x = np.load(HERE / "data/era5" / f"{lk}__{var}.npz")["arr"]
    nanmask = bool(np.isnan(x).any()) and not family.startswith("pwratio") and family != "ctx-rel"
    cell = {"family": family, "p": value, "zeromask": zeromask, "nanmask": nanmask}
    return cell_to_config(cell, lk, var)


def config_from_table(lk, var, workflow, path=None):
    """Read the '<workflow> config (JSON)' cell of the xlsx table -> config dict.
    workflow = 'W1'..'W4' or 'submission'."""
    from openpyxl import load_workbook
    path = path or HERE / "tables" / f"{lk}_workflow_table.xlsx"
    ws = load_workbook(path).active
    hdr = [c.value for c in ws[1]]
    col = hdr.index("Submission config (JSON)" if workflow == "submission" else f"{workflow} config (JSON)")
    for r in ws.iter_rows(min_row=2, values_only=True):
        if r[0] == var:
            if not r[col] or str(r[col]).startswith("error"):
                raise ValueError(f"no configuration for {var} {workflow}: {r[col]}")
            return json.loads(r[col])
    raise KeyError(f"{var} not in {path.name}")


def run(lk, var, cfg):
    import numpy as np
    from numcodecs.registry import get_codec
    from compression_requirement_checks import check_safety_requirements
    x = np.load(HERE / "data/era5" / f"{lk}__{var}.npz")["arr"]
    codec = get_codec(cfg)
    e = codec.encode(x)
    d = np.asarray(codec.decode(np.frombuffer(bytes(e), np.uint8))).reshape(x.shape)
    ok = bool(check_safety_requirements(original=x, reconstructed=d, requirements=get_requirements(lk, var)))
    return x.nbytes / len(e), ok


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(__doc__); sys.exit(1)
    lk, var, wf = sys.argv[1:4]
    cell = best_cell(lk, var, wf)
    cfg = cell_to_config(cell, lk, var)
    print(f"# {lk} {var} {wf}: family={cell['family']} p={cell['p']!r} cr={cell['cr']:.4f}")
    print(json.dumps(cfg, indent=1))
    if "--run" in sys.argv:
        cr, ok = run(lk, var, cfg)
        print(f"# re-run: cr={cr:.4f} official_ok={ok}")
