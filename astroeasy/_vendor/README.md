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
