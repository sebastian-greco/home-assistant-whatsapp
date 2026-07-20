"""Validate that project, manifest, and optional tag versions agree."""

from __future__ import annotations

import argparse
import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]
MANIFEST = ROOT / "custom_components" / "waha_whatsapp" / "manifest.json"
APP_CONFIG = ROOT / "waha" / "config.yaml"
APP_DOCKERFILE = ROOT / "waha" / "Dockerfile"


def _match_version(path: Path, pattern: str) -> str:
    """Read one version value from a small metadata file."""
    match = re.search(pattern, path.read_text(), flags=re.MULTILINE)
    if match is None:
        raise SystemExit(f"Version not found in {path.relative_to(ROOT)}")
    return match.group(1)


def main() -> None:
    """Check the version sources and exit with an error on a mismatch."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "tag",
        nargs="?",
        help="Optional release tag such as v1.0.0 or waha-v0.2.0",
    )
    args = parser.parse_args()

    project_version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"][
        "version"
    ]
    manifest_version = json.loads(MANIFEST.read_text())["version"]
    app_version = _match_version(
        APP_CONFIG,
        r'^version:\s*["\']?([^"\'\s]+)',
    )
    docker_app_version = _match_version(
        APP_DOCKERFILE,
        r"^ARG BUILD_VERSION=([^\s]+)",
    )

    if project_version != manifest_version:
        raise SystemExit(
            "Version mismatch: "
            f"pyproject={project_version}, manifest={manifest_version}"
        )

    if app_version != docker_app_version:
        raise SystemExit(
            "WAHA app version mismatch: "
            f"config={app_version}, Dockerfile={docker_app_version}"
        )

    if args.tag is not None:
        if args.tag.startswith("waha-v"):
            tag_version = args.tag.removeprefix("waha-v")
            expected_version = app_version
            release_name = "WAHA app"
        elif args.tag.startswith("v"):
            tag_version = args.tag.removeprefix("v")
            expected_version = manifest_version
            release_name = "WAHA WhatsApp integration"
        else:
            raise SystemExit(f"Unsupported release tag: {args.tag}")

        if tag_version != expected_version:
            raise SystemExit(
                f"Tag mismatch for {release_name}: "
                f"tag={tag_version}, expected={expected_version}"
            )

    print(
        "Versions are consistent: "
        f"WAHA integration={manifest_version}, WAHA app={app_version}"
    )


if __name__ == "__main__":
    main()
