"""Write <lk>_workflow_table.xlsx (default: tables/ next to this script): per variable the submitted best ratio/config
(our submissions lead the scoreboard) next to the ratio and parameters of each
of the four workflows (W1 quantize+context, W2 quantize+interpolation,
W3 log-relative, W4 SPERR), best of the four in bold. Prints a Markdown
version for pressure.

Usage: python make_table.py [pressure|single] [--out DIR]

Sources: submissions/ERA5-{Pressure,Single}.xlsx (official verified ratios),
results/<lk>__*.json (stored search candidates) and
workflow_table_cells[_single].jsonl (cells computed by workflow_table.py).
All workflow cells are measured on the same cached challenge arrays as the
verified submissions and confirmed with the official
compression_requirement_checks.check_safety_requirements; the standardized
pipeline (clip -> masks -> core -> zstd-22) is used for every cell."""

import glob
import json
import re
import sys
from pathlib import Path

import openpyxl
from workflow_config import cell_to_config
from openpyxl.styles import Alignment, Font

HERE = Path(__file__).parent
LK = sys.argv[1] if len(sys.argv) > 1 else "pressure"
SUB = HERE / "submissions" / ("ERA5-Pressure.xlsx" if LK == "pressure" else "ERA5-Single.xlsx")
CELLS = HERE / f"workflow_cells_official_{LK}.jsonl"
OUT_DIR = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else HERE / "tables"
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT = OUT_DIR / f"{LK}_workflow_table.xlsx"

LOSSLESS = {"lsm", "slt", "tvl", "tvh", "ptype", "cdww", "msqs", "rhoao", "tauoc"}

WF_OF_FAMILY = {
    "ebq-residuals": ("W1", "residuals"), "ebq-symbols": ("W1", "symbols"),
    "ctx-abs": ("W1", "residuals"), "nanctx-abs": ("W1", "symbols"),
    "grid-residuals": ("W1", "residuals"), "grid-symbols": ("W1", "symbols"),
    "grid-nanctx": ("W1", "symbols"), "grid-ctx": ("W1", "residuals"),
    "interp-abs": ("W2", None),
    "ctx-rel": ("W3", "ebq core"), "pwratio-ebq-residuals": ("W3", "ebq core"),
    "pwratio-interp": ("W3", "interp core"), "pwratio-ctx": ("W3", "ebq core"),
    "sperr-pwe": ("W4", "pwe"), "sperr-q": ("W4", "q"), "sperr-bpp": ("W4", "bpp"),
    "sperr-pwe-2d": ("W4", "pwe"), "sperr-q-2d": ("W4", "q"), "sperr-bpp-2d": ("W4", "bpp"),
    "pwratio-sperr": ("W4", "log+pwe"), "pwratio-sperr-2d": ("W4", "log+pwe"),
}


def norm_family(fam):
    if not fam:
        return None
    return re.sub(r"\+thr[\d.]+", "", fam).replace("+zeromask", "")


def fmt(x, sig=None):
    # full precision: the exact value needed to reproduce the cell
    return "" if x is None else repr(float(x))


def param_str(wf, flavour, p, zeromask):
    if p is None:
        return ""
    zm = ", zero-mask" if zeromask else ""
    if wf == "W1":
        return f"eb={fmt(p)} ({flavour or 'residuals'}){zm}"
    if wf == "W2":
        return f"eb={fmt(p)}{zm}"
    if wf == "W3":
        return f"ratio=1+{fmt(p)} ({flavour or 'ebq core'})"
    if wf == "W4":
        return f"{flavour or 'pwe'}={fmt(p)}{zm}"
    return fmt(p)


def read_submissions():
    wb = openpyxl.load_workbook(SUB)
    ws = wb.active
    hdr = [c.value for c in ws[1]]
    iv = hdr.index("Variable")
    ic = hdr.index("Compression")
    ish = hdr.index("Config Short")
    idt = hdr.index("Default/all timestep(s)")
    out = {}
    for r in ws.iter_rows(min_row=2, values_only=True):
        if r[iv] is None or str(r[idt]) != "default":
            continue
        out[str(r[iv])] = (float(r[ic]), str(r[ish]))
    return out


def main():
    cells = {}
    if CELLS.exists():
        for line in CELLS.read_text().splitlines():
            c = json.loads(line)
            k = (c["var"], c["workflow"])
            if c.get("infeasible") or c.get("failed"):
                cells.setdefault(k, {"cr": None, "note": c.get("note", "failed")})
                continue
            cur = cells.get(k)
            if cur is None or (cur.get("cr") or 0) < c["cr"]:
                fam = norm_family(c["family"])
                hit = WF_OF_FAMILY.get(fam, (None, None))
                cells[k] = {"cr": c["cr"], "p": c.get("p"), "flavour": hit[1],
                            "zeromask": c.get("zeromask", False), "raw": c}

    subs = read_submissions()
    rows = []
    for path in sorted(glob.glob(str(HERE / "results" / f"{LK}__*.json"))):
        r = json.load(open(path))
        if "config" not in r:
            continue
        v = Path(path).stem.split("__", 1)[1]
        if v in LOSSLESS:
            continue
        # the official cells are the sole source for the workflow columns
        per_wf = {wf: cells[(v, wf)] for wf in ("W1", "W2", "W3", "W4") if (v, wf) in cells}
        req = str(r["requirements"])
        req = (req.replace("MaxPointwiseAbsoluteErrorBound", "PwAbs")
                  .replace("MaxPointwiseRelativeErrorBound", "PwRel")
                  .replace("MaxPointwiseRangeRelativeErrorBound", "PwRangeRel")
                  .replace("MeanAbsoluteErrorBound", "MeanAbs")
                  .replace("MeanRelativeErrorBound", "MeanRel")
                  .replace("MeanRangeRelativeErrorBound", "MeanRangeRel")
                  .replace("DataLimits", "Limits"))
        sub = subs.get(v)
        rows.append(dict(var=v, req=req, sub_cr=sub[0] if sub else None,
                         sub_cfg=sub[1] if sub else "", sub_json=r["config"], wf=per_wf))

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"{LK}-workflows"
    header = (["Variable", "Requirement (0.1.0b1)", "Best ratio (our submission)",
               "W1 Quantize+Context ratio", "W2 Quantize+Interp ratio",
               "W3 Log-relative ratio", "W4 SPERR ratio",
               "Best config (our submission)",
               "W1 parameters", "W2 parameters", "W3 parameters", "W4 parameters",
               "Submission config (JSON)",
               "W1 config (JSON)", "W2 config (JSON)", "W3 config (JSON)", "W4 config (JSON)"])
    ws.append(header)
    for c in ws[1]:
        c.font = Font(bold=True)
        c.alignment = Alignment(wrap_text=True, vertical="top")
    md = ["| Var | Requirement | Submission | W1 quant+ctx | W2 quant+interp | W3 log-rel | W4 SPERR |",
          "|---|---|---|---|---|---|---|"]
    for row in rows:
        crs = [row["wf"].get(w, {}).get("cr") for w in ("W1", "W2", "W3", "W4")]
        best_j = max(range(4), key=lambda j: crs[j] or 0)
        params = [param_str(w, row["wf"].get(w, {}).get("flavour"),
                            row["wf"].get(w, {}).get("p"), row["wf"].get(w, {}).get("zeromask"))
                  or row["wf"].get(w, {}).get("note", "not applicable")
                  for w in ("W1", "W2", "W3", "W4")]
        xl = [row["var"], row["req"],
              round(row["sub_cr"], 2) if row["sub_cr"] else "-"]
        xl += [round(c, 2) if c else "-" for c in crs]
        xl += [row["sub_cfg"]]
        xl += params
        xl += [json.dumps(row["sub_json"])]
        for w in ("W1", "W2", "W3", "W4"):
            raw = row["wf"].get(w, {}).get("raw")
            try:
                xl.append(json.dumps(cell_to_config(raw, LK, row["var"])) if raw else "")
            except Exception as ex:
                xl.append(f"error: {ex}")
        ws.append(xl)
        i = ws.max_row
        ws.cell(row=i, column=4 + best_j).font = Font(bold=True)
        mdvals = [f"**{c:.1f}**" if j == best_j and c else (f"{c:.1f}" if c else "—")
                  for j, c in enumerate(crs)]
        md.append(f"| {row['var']} | {row['req']} | "
                  + (f"{row['sub_cr']:.1f}" if row["sub_cr"] else "—")
                  + " | " + " | ".join(mdvals) + " |")
    widths = [9, 38, 13, 13, 13, 13, 13, 42, 26, 22, 26, 24, 60, 60, 60, 60, 60]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w
    for r_ in ws.iter_rows(min_row=2):
        for c in r_:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    wb.save(OUT)
    print("saved", OUT, f"({len(rows)} variables)")
    if LK == "pressure":
        print("\n".join(md))


if __name__ == "__main__":
    main()
