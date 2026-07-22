#!/usr/bin/env python3
"""Upload test data files to GitHub release.

This script uploads test data files to a GitHub release and updates the manifest.
Requires gh CLI to be installed and authenticated.

Usage:
    python scripts/upload_test_data.py
    # or to create a new version:
    python scripts/upload_test_data.py --tag test-data-v2
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

# Paths
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
TEST_DATA_DIR = PROJECT_ROOT / "tests" / "data"
MANIFEST_PATH = TEST_DATA_DIR / "manifest.json"

# Files to upload (add new files here)
DATA_FILES = [
    "example_data.fits",
    "x_y_counts_1024_1024.txt",
]


def compute_sha256(filepath: Path) -> str:
    """Compute SHA256 hash of a file."""
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def load_manifest() -> dict:
    """Load the manifest file."""
    if MANIFEST_PATH.exists():
        with open(MANIFEST_PATH) as f:
            return json.load(f)
    return {"release_tag": "test-data-v1", "repo": "ssc-ai/astroeasy", "files": []}


def save_manifest(manifest: dict) -> None:
    """Save the manifest file."""
    with open(MANIFEST_PATH, "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")


def check_gh_cli() -> bool:
    """Check if gh CLI is available and authenticated."""
    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


def release_exists(repo: str, tag: str) -> bool:
    """Check if a release exists."""
    result = subprocess.run(
        ["gh", "release", "view", tag, "--repo", repo],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def create_release(repo: str, tag: str) -> bool:
    """Create a new release."""
    result = subprocess.run(
        [
            "gh",
            "release",
            "create",
            tag,
            "--repo",
            repo,
            "--title",
            f"Test Data ({tag})",
            "--notes",
            "Test data files for astroeasy. These files are downloaded automatically when running tests.",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"Error creating release: {result.stderr}")
        return False
    return True


def upload_file(repo: str, tag: str, filepath: Path) -> bool:
    """Upload a file to a release."""
    result = subprocess.run(
        [
            "gh",
            "release",
            "upload",
            tag,
            "--repo",
            repo,
            str(filepath),
            "--clobber",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"Error uploading {filepath.name}: {result.stderr}")
        return False
    return True


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Upload test data to GitHub release")
    parser.add_argument(
        "--tag",
        default=None,
        help="Release tag to use (default: from manifest or test-data-v1)",
    )
    parser.add_argument(
        "--repo",
        default=None,
        help="GitHub repo (default: from manifest or ssc-ai/astroeasy)",
    )
    args = parser.parse_args()

    # Check gh CLI
    if not check_gh_cli():
        print("Error: gh CLI not available or not authenticated")
        print("Install: https://cli.github.com/")
        print("Auth: gh auth login")
        return 1

    # Load manifest
    manifest = load_manifest()
    repo = args.repo or manifest.get("repo", "ssc-ai/astroeasy")
    tag = args.tag or manifest.get("release_tag", "test-data-v1")

    print(f"Uploading test data to {repo} release {tag}...")

    # Check all files exist
    missing = []
    for filename in DATA_FILES:
        filepath = TEST_DATA_DIR / filename
        if not filepath.exists():
            missing.append(filename)

    if missing:
        print(f"Error: Missing files: {', '.join(missing)}")
        return 1

    # Create release if needed
    if not release_exists(repo, tag):
        print(f"Creating release {tag}...")
        if not create_release(repo, tag):
            return 1

    # Upload files and compute checksums
    file_infos = []
    all_ok = True

    for filename in DATA_FILES:
        filepath = TEST_DATA_DIR / filename
        sha256 = compute_sha256(filepath)

        print(f"  Uploading {filename}...")
        if upload_file(repo, tag, filepath):
            print(f"    OK (sha256: {sha256[:16]}...)")
            file_infos.append({"name": filename, "sha256": sha256})
        else:
            all_ok = False

    if not all_ok:
        print("Some uploads failed.")
        return 1

    # Update manifest
    manifest["release_tag"] = tag
    manifest["repo"] = repo
    manifest["files"] = file_infos
    save_manifest(manifest)

    print(f"Manifest updated: {MANIFEST_PATH}")
    print("Done! Don't forget to commit the updated manifest.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
