# compression-challenge-search

Search, verification and consolidation pipeline behind the ERA5 submissions to the
[compression lab](https://github.com/climet-eu/compression-lab-notebooks) challenge: per-variable
error-bounded compression under the `compression-recommendations` 0.1.0b1 requirements, verified
with the official `compression-requirement-checks`, and the reduction of the 242 bespoke winners to
four standardised workflows (W1 quantize + context model, W2 quantize + interpolation,
W3 log-domain relative quantizer, W4 SPERR).

This repository is self-contained: it does **not** need a checkout of the compression lab. All
data is downloaded from the ESiWACE object store (or replaced by your own arrays), and all results
are regenerated — none are committed.

## 1. Set up (from an empty directory)

```bash
git clone https://github.com/SF-N/compression-challenge-search.git
cd compression-challenge-search
python3 -m venv .venv && source .venv/bin/activate          # Python 3.11+ (tested with 3.14)
pip install --upgrade pip
pip install -r requirements.txt
pip install --no-deps compression-requirement-checks==0.1.0a2
```

The second `pip install` is needed only because the checker's packaging metadata (released before
compression-recommendations 0.1.0b1 existed) pins recommendations 0.1.0a2; its code is
version-agnostic, and every result here was produced with recommendations **0.1.0b1** + checks 0.1.0a2.

Do not install the `mask-aware-codecs` branch head of numcodecs-mask (commit d8080bc, structural
subclass hook) or the `mask-forwarding` branch of numcodecs-combinators: they change the encoded
stream of every `mask.meta -> combinators.stack` configuration, and 47 of the submitted single-level
configurations then no longer reproduce. `requirements.txt` pins the verified commits.

## 2. Data

```bash
python cache_era5.py pressure single        # ERA5 challenge timestep 2026-07-15T12:00 -> data/era5/<leveltype>__<var>.npz
```

`era5.py` reads the organizers' kerchunk reference catalogs
(`https://object-store.os-api.cci1.ecmwf.int/esiwacebucket/ERA5_07_2026/era5_{pressure_50_500_850_1000_202607_3h,single_202607_6h}.grib.refs.json`)
and stores each variable as one float64 array: `(721, 1440)` for single-level, `(4, 721, 1440)` for
the pressure levels 50/500/850/1000 hPa (~430 MB in total, lossless `savez_compressed`).
The three small-challenge datasets (NaN, PwRel, Gradient) are downloaded by `finalize.py` on first use.

**Your own data:** save any float array of shape `[rows, cols]` or `[levels, rows, cols]` as
`data/era5/<leveltype>__<var>.npz` with key `arr`; the requirements are looked up by `<leveltype>`
(`pressure` or `single`) and the GRIB short name `<var>` in compression-recommendations.

## 3. Run

```bash
python fetch_scoreboard.py                      # previous best per variable -> scoreboard_best.json
python search.py single 6 && python search.py pressure 2      # per-variable search -> results/<lk>__<var>.json
python finalize.py 4                            # official verification + sheets -> results_verified/, submissions/*.xlsx
python official_all.py && python add_alltime_rows.py && python update_alltime_notes.py   # all-timestep rows
python workflow_cells_official.py pressure single   # the four workflows per variable, official basis -> workflow_cells_official_<lk>.jsonl
python make_table.py pressure && python make_table.py single    # ratio/parameter/config tables -> tables/<lk>_workflow_table.xlsx
python audit_submissions.py                     # every submitted configuration re-measured on the current data
```

`search.py` takes hours per leveltype (context-mixing coders run at a few MB/s; the first use of
each codec compiles its numba kernels, ~30 s). All steps are resumable: finished variables, cells
and timesteps are cached and skipped on rerun.

## 4. Use a configuration without searching

```bash
python workflow_config.py pressure z W1 --run   # cell of a workflow -> numcodecs config, ratio, official check
```

```python
from workflow_config import config_for, config_from_table, config_from_params, run
cfg = config_for("pressure", "z", "W1")                         # best official cell (workflow_cells_official_*.jsonl)
cfg = config_from_table("pressure", "z", "W1")                  # from tables/<lk>_workflow_table.xlsx ('submission' also works)
cfg = config_from_params("single", "bld", "W4", "bpp=7.101801803694664")   # from a parameter string
cr, ok = run("pressure", "z", cfg)
```

The configuration of every submitted variable is in `results/<lk>__<var>.json` (`config`) after
`search.py`/`finalize.py`, or in the "Configuration" column of `submissions/*.xlsx`; any of them
can be instantiated with `numcodecs.registry.get_codec(config)` and used in Zarr directly.

## Scripts

| script | purpose |
|---|---|
| `era5.py`, `cache_era5.py` | data access and the local cache |
| `fetch_scoreboard.py` | previous best ratio per variable from the public scoreboard |
| `reqs.py` | fast float64 mirror of `compression_requirement_checks` (inside the search only; every final result is verified with the official checker) |
| `search.py <pressure\|single> <nproc> [vars]` | per-variable search over codec families with bisection of the error parameter, refinement and official verification |
| `finalize.py [nproc] [small,pressure,single]` | official re-verification, throughput, Excel submission sheets with a ready-to-paste "Codec" column |
| `alltime.py`, `official_all.py`, `add_alltime_rows.py`, `update_alltime_notes.py` | all-timestep evaluation (each timestep compressed independently with one configuration) |
| `workflow_cells_official.py [pressure] [single]` | the four standardised workflows measured for every variable on the official data |
| `workflow_table.py` | fast (pre-official) variant used to seed the cells |
| `make_table.py <pressure\|single> [--out DIR]` | per-variable table: submitted ratio/config, ratio and parameters of W1–W4, full configurations as JSON |
| `workflow_config.py` | cell / table row / parameter string -> numcodecs configuration, and re-run |
| `fold_improvements.py` | fold officially verified workflow improvements into `results/` (fork-free configurations) |
| `audit_submissions.py` | re-measure every submitted configuration with the official checker |
| `validate_submissions.py` | validate the public scoreboard sheet and render Markdown leaderboards |
| `status.py`, `probe_small.py`, `test_pack_signs.py` | helpers |

## Codecs

All codecs are published numcodecs packages: `SF-N/numcodecs-{eb-quantize,context-mixing,interp-ctx,clip,replace,zero,grid-int,chunked,lon-gradient,abs-or-rel}`
(numba-compiled Python), `numcodecs-mask` and `numcodecs-pw-ratio` (SF-N forks, pinned commits),
`numcodecs-combinators`, and juntyr's `numcodecs-wasm-sperr` / `numcodecs-wasm-zstd`.
