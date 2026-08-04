# Contributing to astroeasy

## Development Setup

```bash
# Clone the repo
git clone https://github.com/ssc-ai/astroeasy.git
cd astroeasy

# Install with dev dependencies and all extras
uv sync
make install-dev

# Fetch test data (required before running tests)
make fetch-test-data

# Run tests
make test

# Run tests with coverage
make coverage
```

## Test Data Management

Test data files (FITS images, source lists) are stored in GitHub releases rather than the git repository to keep the repo size small.

### For Contributors

Test data is fetched automatically when you run `make test` or `make coverage`. You can also fetch it manually:

```bash
make fetch-test-data
```

The fetch script:
- Checks if files exist with correct checksums (skips download if OK)
- Downloads from the `test-data-v1` GitHub release
- Works with `gh` CLI or falls back to direct HTTP download

### For Maintainers

To update test data files:

1. Add or modify files in `tests/data/`
2. Upload to GitHub release and update the manifest:
   ```bash
   make upload-test-data
   ```
   Or with a new version tag:
   ```bash
   python scripts/upload_test_data.py --tag test-data-v2
   ```
3. Commit the updated manifest:
   ```bash
   git add tests/data/manifest.json
   git commit -m "Update test data manifest"
   ```

To add new test data files, edit `scripts/upload_test_data.py` and add the filename to the `DATA_FILES` list.

### Files

- `tests/data/manifest.json` - Tracked in git; contains checksums and release tag
- `tests/data/*.fits`, `tests/data/*.txt` - Gitignored; downloaded from release

## Astrometry.net Index Files

Some tests solve real fields and need astrometry.net index files (the 5200-LITE
series). These are far too large for the repo and are **not** downloaded by
`make fetch-test-data` — you supply them yourself. Tests that need them skip
cleanly when they are absent, so a fresh clone still runs green.

The default location is `/stars/data/share/5000/5200-LITE`. Point elsewhere with:

```bash
export ASTROEASY_TEST_INDICES=/path/to/your/5200-LITE
make test
```

A directory counts as usable when it contains at least one `*.fits` file.

You do **not** mount anything into the Docker container by hand. When
`docker_image` is set, astroeasy bind-mounts the host index directory to
`/usr/local/astrometry/data` inside the container and writes a matching
`astrometry.cfg` — see `astroeasy/dotnet/docker.py`. The host path is the only
knob.

Index-dependent tests come in two flavours:

- **Docker** (`requires_docker_install`) - needs the `astrometry-cli` image;
  build it with `make build-docker`.
- **Local** (`requires_local_install`) - needs `solve-field` on your `PATH`.
  Skipping these is fine if you work through Docker.

### Other opt-in tests

The tetra3 cascade tests build a pattern database (~10s) and are off by default:

```bash
ASTROEASY_TETRA3_TESTS=1 make test
```

## Code Quality

```bash
# Lint
make lint

# Format
make format
```

## Testing astrometry.net Installation

```bash
# Test Docker setup
make build-docker
make test-install-docker

# Test local installation
make test-install-local
```

## Project Structure

```
astroeasy/
├── astroeasy/           # Main package
│   ├── cli.py           # CLI entry point
│   ├── config.py        # AstrometryConfig
│   ├── constants.py     # Index series definitions
│   ├── indices.py       # Index download/verification
│   ├── models.py        # Detection, WCSResult, etc.
│   ├── runner.py        # Public solve_field() API
│   └── dotnet/          # astrometry.net integration
│       ├── docker.py    # Docker backend
│       ├── local.py     # Local backend
│       └── runner.py    # Orchestration
├── tests/
│   ├── data/            # Test data (gitignored, fetched from release)
│   └── test_*.py        # Test modules
└── scripts/
    ├── fetch_test_data.py   # Download test data
    └── upload_test_data.py  # Upload test data (maintainers)
```
