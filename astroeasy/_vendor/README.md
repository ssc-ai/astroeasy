# Vendored dependencies

## tetra3 (cedar-solve fork)

- **Source:** https://github.com/smroid/cedar-solve (fork of https://github.com/esa/tetra3)
- **Version:** 0.5.1, commit `d8ff1d857a363c88917fd8e126ab90e24b1cfbcc`
- **License:** Apache-2.0 (see `tetra3/LICENSE.txt`)
- **Why vendored:** cedar-solve on PyPI pins `numpy<2` / `Pillow<9`, which conflicts
  with astroeasy's environment; the code itself runs fine on numpy 2.x (validated on
  97 real DAO frames — `benchmarks/RESULTS.md`). Vendoring the four core modules makes
  `pip install astroeasy[cascade]` self-contained. Revisit if upstream relaxes the pins.
- **Subset:** only `tetra3.py`, `fov_util.py`, `breadth_first_combinations.py`,
  `__init__.py` — not the cedar-detect gRPC client, CLI, docs, or bundled databases.
- **Local modifications:** the two intra-package imports in `tetra3.py`
  (`from tetra3.X import …`) changed to relative (`from .X import …`) so the package
  imports as `astroeasy._vendor.tetra3`. No functional changes.

### Redistribution compliance (audited 2026-08-04)

Shipping this vendored code inside an MIT-licensed package on PyPI is fine.
Apache-2.0 is permissive and imposes no copyleft on astroeasy; it only requires
that the terms below be honored for the vendored portion. All of them are:

| Apache-2.0 §4 requirement | Status |
| --- | --- |
| (a) Give recipients a copy of the License | `tetra3/LICENSE.txt` (full text) ships inside the wheel |
| (b) State that files were changed | The "Local modifications" note above; `tetra3.py` is otherwise unmodified |
| (c) Retain copyright/attribution notices from the source | Intact — see below |
| (d) Include upstream `NOTICE` contents | N/A: upstream ships no `NOTICE` file |

Attribution notices retained verbatim in `tetra3.py`'s module docstring cover all
three upstream layers: Copyright 2023 Steven Rosenthal (cedar-solve, Apache-2.0),
Copyright 2019 European Space Agency (tetra3, Apache-2.0), and Copyright (c) 2016
brownj4 (original Tetra, MIT — its notice-retention condition is likewise met).
`breadth_first_combinations.py` carries its own header pointing at the LICENSE
file, which sits beside it. `fov_util.py` and `__init__.py` have no headers
upstream either; Apache-2.0 does not require per-file headers, only that existing
notices survive.

Compliance is structural rather than incidental: hatchling includes every file
under the package directory, so `LICENSE.txt` ships with the wheel automatically
and there is no include list that could drift out from under it. Verified present
in a built wheel at the time of this audit.

**Re-audit only if** the vendor tree is re-synced from upstream, files are added
or removed here, or `pyproject.toml` grows explicit packaging include/exclude
rules. Routine releases do not need to re-check this.
