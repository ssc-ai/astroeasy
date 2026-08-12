"""Configuration for astrometry.net plate solving."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from astroeasy.constants import AstrometryIndexSeries


@dataclass
class AstrometryConfig:
    """Configuration for astrometry.net plate solving.

    Attributes:
        indices_path: Path to the astrometry.net index files.
        indices_series: Which index series to use. Defaults to 5200_LITE.
        cpulimit_seconds: Maximum CPU time for solve-field. Defaults to 30.
        min_width_degrees: Minimum field width in degrees. Defaults to 0.1.
        max_width_degrees: Maximum field width in degrees. Defaults to 10.0.
        tweak_order: SIP polynomial order for distortion correction. 0 disables tweaking.
        max_sources: Maximum number of sources to use for solving. Defaults to 100.
        min_sources_for_attempt: Minimum sources required to attempt solving. Defaults to 4.
        docker_image: Docker image name for containerized execution. None = local.
        output_dir: Directory for output files. None = use temp directory.
        release_index_page_cache: After each solve, advise the OS to drop the index files'
            page cache (POSIX_FADV_DONTNEED) so repeated solves do not accumulate the whole
            index in resident memory. Defaults to True. Set False to opt out (e.g. when the
            same sky region is solved repeatedly and re-faulting the tiles costs more than the
            memory). No effect on platforms without posix_fadvise (macOS, Windows).
        search_radius: Radius in degrees around the boresight hint that solve-field is allowed
            to search (`--radius`), used only when the metadata carries a boresight. Defaults
            to 10.0, the value previously hardcoded. Narrowing it to the pointing uncertainty
            keeps the solver from spending its cpulimit on sky the frame cannot contain, which
            matters most for frames that will not solve at all and would otherwise burn the
            whole budget before failing.
        odds_to_solve: Odds ratio at which solve-field declares a field solved
            (`--odds-to-solve`). None (the default) omits the flag and keeps solve-field's own
            default. Raising it rejects marginal solutions that a downstream refinement would
            otherwise have to detect and discard.
    """

    # Required
    indices_path: Path

    # Optional with defaults
    indices_series: AstrometryIndexSeries = AstrometryIndexSeries.SERIES_5200_LITE
    cpulimit_seconds: int = 30
    min_width_degrees: float = 0.1
    max_width_degrees: float = 10.0
    tweak_order: int = 2
    max_sources: int = 100
    min_sources_for_attempt: int = 4
    docker_image: str | None = None
    output_dir: Path | None = None
    release_index_page_cache: bool = True
    search_radius: float = 10.0
    odds_to_solve: float | None = None

    def __post_init__(self):
        """Convert string paths to Path objects."""
        if isinstance(self.indices_path, str):
            self.indices_path = Path(self.indices_path)
        if isinstance(self.output_dir, str):
            self.output_dir = Path(self.output_dir)
        if isinstance(self.indices_series, str):
            self.indices_series = AstrometryIndexSeries(self.indices_series)

    @classmethod
    def from_yaml(cls, path: Path | str) -> "AstrometryConfig":
        """Load configuration from a YAML file.

        Args:
            path: Path to the YAML configuration file.

        Returns:
            AstrometryConfig instance.

        Example YAML format:
            indices_path: /data/indices/5200-LITE
            indices_series: 5200_LITE
            cpulimit_seconds: 30
            docker_image: astrometry-cli
        """
        path = Path(path)
        with open(path) as f:
            data = yaml.safe_load(f)

        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AstrometryConfig":
        """Create configuration from a dictionary.

        Args:
            data: Dictionary with configuration values.

        Returns:
            AstrometryConfig instance.
        """
        # Handle indices_path as required
        if "indices_path" not in data:
            raise ValueError("indices_path is required in configuration")

        return cls(
            indices_path=Path(data["indices_path"]),
            indices_series=data.get("indices_series", AstrometryIndexSeries.SERIES_5200_LITE),
            cpulimit_seconds=data.get("cpulimit_seconds", 30),
            min_width_degrees=data.get("min_width_degrees", 0.1),
            max_width_degrees=data.get("max_width_degrees", 10.0),
            tweak_order=data.get("tweak_order", 2),
            max_sources=data.get("max_sources", 100),
            min_sources_for_attempt=data.get("min_sources_for_attempt", 4),
            docker_image=data.get("docker_image"),
            output_dir=Path(data["output_dir"]) if data.get("output_dir") else None,
            release_index_page_cache=data.get("release_index_page_cache", True),
            search_radius=data.get("search_radius", 10.0),
            odds_to_solve=data.get("odds_to_solve"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert configuration to a dictionary.

        Returns:
            Dictionary representation of the configuration.
        """
        return {
            "indices_path": str(self.indices_path),
            "indices_series": str(self.indices_series),
            "cpulimit_seconds": self.cpulimit_seconds,
            "min_width_degrees": self.min_width_degrees,
            "max_width_degrees": self.max_width_degrees,
            "tweak_order": self.tweak_order,
            "max_sources": self.max_sources,
            "min_sources_for_attempt": self.min_sources_for_attempt,
            "docker_image": self.docker_image,
            "output_dir": str(self.output_dir) if self.output_dir else None,
            "release_index_page_cache": self.release_index_page_cache,
            "search_radius": self.search_radius,
            "odds_to_solve": self.odds_to_solve,
        }

    def to_yaml(self, path: Path | str) -> None:
        """Save configuration to a YAML file.

        Args:
            path: Path to write the YAML configuration file.
        """
        path = Path(path)
        with open(path, "w") as f:
            yaml.dump(self.to_dict(), f, default_flow_style=False)
