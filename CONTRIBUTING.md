# Contributing to astroeasy

## Development Setup

```bash
# Clone the repo
git clone https://github.com/ssc-ai/astroeasy.git
cd astroeasy

# Install with dev dependencies
uv sync
uv pip install -e ".[dev]"

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
