# astroeasy

![Tests](tests.svg) ![Coverage](coverage.svg)

Astrometry.net made easy - a standalone Python package for plate-solving with containerized execution, clean API, and indices management.

## Installation

```sh
pip install astroeasy
```

## Quick Start

```python
from astroeasy import (
    AstrometryConfig,
    Detection,
    ImageMetadata,
    solve_field,
)

# Configure astrometry.net
config = AstrometryConfig(
    indices_path="/data/indices/5200-LITE",
    docker_image="astrometry-cli",  # Or None for local installation
)

# Your detected sources (x, y pixel coordinates with optional flux)
detections = [
    Detection(x=100, y=200, flux=1000),
    Detection(x=500, y=300, flux=800),
    # ... more detections
]

# Image metadata
metadata = ImageMetadata(width=4096, height=4096)

# Solve!
result = solve_field(detections, metadata, config)

if result.success:
    print(f"Solved! Center: {result.wcs.center_ra:.4f}, {result.wcs.center_dec:.4f}")
    print(f"Pixel scale: {result.wcs.pixel_scale:.3f} arcsec/pix")
    print(f"Matched {len(result.matched_stars)} catalog stars")
```

## Setup

### Option 1: Docker (Recommended)

Build the astrometry.net container:

```sh
cd astroeasy/dotnet
docker build -t astrometry-cli .
```

Verify installation:

```sh
astroeasy test-install --docker astrometry-cli
```

### Option 2: Local Installation

If you have astrometry.net installed locally:

```sh
astroeasy test-install --local
```

## Index Files

Astrometry.net requires index files for plate solving. We recommend the 5200_LITE series (~15 GB) for most use cases.

### Download Indices

```sh
astroeasy indices download --series 5200_LITE --output /data/indices/5200-LITE
```

### Verify Indices

```sh
astroeasy indices examine --series 5200_LITE --path /data/indices/5200-LITE
```

### FOV-Filtered Downloads

Download only the index files needed for your camera's field of view (saves significant disk space).

`--fov` is the field of view **on a side**, in degrees — so `--fov 2.0` means a
2°×2° field (4 square degrees) on a square detector. Filtered downloads land in
an automatic subdirectory of `--output` named `<series>_<fov>deg` (e.g.
`4200_2p0deg`) so they can't be mixed up with a full series download:

```sh
# 1-degree (1°x1°) FOV camera (~4.9 GB instead of ~35 GB for 5200_LITE_4100)
# -> downloads to /data/indices/5200_LITE_4100_1p0deg
astroeasy indices download --series 5200_LITE_4100 --output /data/indices --fov 1.0

# 2-degree (2°x2°) FOV camera (~1.3 GB instead of ~32 GB for 4200)
# -> downloads to /data/indices/4200_2p0deg
astroeasy indices download --series 4200 --output /data/indices --fov 2.0
```

Validate a filtered set by passing the same `--fov` to `examine`:

```sh
astroeasy indices examine --series 4200 --path /data/indices/4200_2p0deg --fov 2.0
```

### Supported Index Series

| Series | Size | Description |
|--------|------|-------------|
| `5200_LITE` | ~36 GB | Recommended - good balance of coverage and size |
| `5200` | ~80 GB | Full 5200 series with photometry |
| `4100` | ~0.4 GB | Smaller, for wider fields |
| `4200` | ~32 GB | Alternative to 4100 |
| `5200_LITE_4100` | ~35 GB | Combined 5200_LITE + 4100 |

## CLI Reference

### Plate Solving

```sh
# Solve with configuration file
astroeasy solve --config astrometry.yaml --image image.fits

# Solve with explicit parameters
astroeasy solve --xylist sources.csv --width 4096 --height 4096 \
    --indices-path /data/indices/5200-LITE \
    --docker-image astrometry-cli
```

### Index Management

```sh
# Download indices
astroeasy indices download --series 5200_LITE --output /data/indices

# Check index completeness
astroeasy indices examine --series 5200_LITE --path /data/indices

# List supported series
astroeasy indices --help
```

### Installation Verification

```sh
# Test Docker installation
astroeasy test-install --docker astrometry-cli

# Test local installation
astroeasy test-install --local
```

## Configuration

### YAML Configuration File

```yaml
# astrometry.yaml
indices_path: /data/indices/5200-LITE
indices_series: 5200_LITE
docker_image: astrometry-cli  # null for local execution
cpulimit_seconds: 30
min_width_degrees: 0.1
max_width_degrees: 10.0
tweak_order: 2
max_sources: 100
min_sources_for_attempt: 4
```

### Python Configuration

```python
from astroeasy import AstrometryConfig

config = AstrometryConfig(
    indices_path="/data/indices/5200-LITE",
    docker_image="astrometry-cli",
    cpulimit_seconds=60,
    min_width_degrees=0.5,
    max_width_degrees=5.0,
)

# Load from YAML
config = AstrometryConfig.from_yaml("astrometry.yaml")

# Save to YAML
config.to_yaml("astrometry.yaml")
```

## API Reference

### Models

```python
from astroeasy import Detection, ImageMetadata, WCSResult, SolveResult

# Detection - a source in pixel coordinates
detection = Detection(x=100.5, y=200.3, flux=1000.0)

# ImageMetadata - image dimensions and optional hints
metadata = ImageMetadata(
    width=4096,
    height=4096,
    boresight_ra=180.0,   # Optional RA hint (degrees)
    boresight_dec=45.0,   # Optional Dec hint (degrees)
)

# SolveResult - the result of plate solving
result = solve_field(detections, metadata, config)
result.success       # bool
result.status        # WCSStatus enum
result.wcs           # WCSResult or None
result.matched_stars # list[MatchedStar]

# WCSResult - WCS solution
result.wcs.center_ra   # RA at reference pixel (degrees)
result.wcs.center_dec  # Dec at reference pixel (degrees)
result.wcs.pixel_scale # Approximate pixel scale (arcsec/pix)
result.wcs.to_astropy_wcs()  # Convert to astropy WCS
```

### Functions

```python
from astroeasy import solve_field, test_install, examine_indices, download_indices

# Plate solve
result = solve_field(detections, metadata, config)

# Test installation
is_working = test_install(docker_image="astrometry-cli")
is_working = test_install()  # Local

# Index management
is_complete = examine_indices("/data/indices", series="5200_LITE")
download_indices("/data/indices", series="5200_LITE")
```

## Legacy Python Module Usage

The package can also be invoked as Python modules:

```sh
# Test local installation
python -m astroeasy.dotnet.local

# Test Docker installation
python -m astroeasy.dotnet.docker

# Index management
python -m astroeasy.indices examine --series 5200_LITE --index_path /data/indices
python -m astroeasy.indices download --series 5200_LITE --index_path /data/indices
```

## License

MIT
