# compression-challenge-search

Search and evaluation pipeline used to produce the [compression lab](https://github.com/climet-eu/compression-lab-notebooks) challenge submissions (private helper scripts, not part of the lab itself).

| script | purpose |
|---|---|
| `cache_era5.py` | cache the ERA5 challenge variables (default timestep) as `data/era5/*.npz` |
| `fetch_scoreboard.py` | previous best ratio per variable from the public Google Sheet |
| `reqs.py` | fast float64 mirror of `compression_requirement_checks` (used inside the search only; winners are verified with the official checker) |
| `search.py <pressure\|single> <nproc> [vars]` | per-variable search over codec families (SPERR pwe/q/bpp, `interp_ctx`, `eb_quantize` + `context_mixing.*`, `pw_ratio`, `abs_or_rel`, `mask.meta`, `replace.threshold`, `zero`, lossless `grid_int`, LZMA/Zstd post-compression) with bisection of the error parameter, refinement and official verification |
| `finalize.py` | official re-verification, throughput measurement, Excel submission files with a ready-to-paste "Codec" column |
| `alltime.py`, `official_all.py`, `add_alltime_rows.py`, `update_alltime_notes.py` | all-timestep evaluation (each timestep compressed independently with one configuration) |
| `status.py` | wins/ties/losses against the scoreboard |

All codecs come from published packages: `SF-N/numcodecs-{clip,chunked,grid-int,eb-quantize,context-mixing,interp-ctx,lon-gradient,abs-or-rel}`, `numcodecs-mask` (with juntyr/numcodecs-mask#4), `numcodecs-replace` (juntyr/numcodecs-replace#5), `numcodecs-zero` (juntyr/numcodecs-zero#1), `numcodecs-pw-ratio`, `numcodecs-combinators` and the numcodecs-wasm codecs.

```bash
uv pip install -e .            # pipeline dependencies (into the compression-lab-notebooks venv)
python cache_era5.py && python fetch_scoreboard.py
python search.py single 6 && python search.py pressure 2
python finalize.py 4           # -> submissions/*.xlsx
```
