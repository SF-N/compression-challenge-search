# Installing a fresh environment

Tested with Python 3.14.8 (3.11+ should work). Everything is pip-installable; the SF-N
packages come straight from GitHub, no local checkouts needed.

```bash
python3 -m venv compression-env
source compression-env/bin/activate
pip install --upgrade pip
pip install -r requirements-fresh.txt          # core: codecs + requirements (~2 min)
pip install --no-deps compression-requirement-checks==0.1.0a2  # official checker (its metadata still pins
                                                              # recommendations 0.1.0a2; it works with 0.1.0b1)
pip install -r requirements-fresh-full.txt     # optional: data download, figures, deck
```

Then, from the repository root:

```bash
python workflow_config.py pressure z W1 --run   # -> cr=75.9407 official_ok=True
```

Notes
* The data cache `data/era5/*.npz` is not in git (433 MB). Rebuild it with
  `python cache_era5.py pressure single` (needs the "full" requirements and
  network access to the ESiWACE object store).
* Do **not** install the newer `mask-aware-codecs` branch head of numcodecs-mask (commit d8080bc,
  structural subclass hook) or the `mask-forwarding` branch of numcodecs-combinators when
  verifying submissions: they change the encoded stream of every `mask.meta -> combinators.stack`
  configuration (47 submitted single-level configs no longer reproduce). They are only needed for
  the 13 W2 cells on NaN-masked fields with data limits in `single_workflow_table.xlsx`.
* First use of each context-mixing codec compiles its numba kernels (~30 s, cached afterwards).
