"""Metadata checks for the experimental WAHA HAOS app."""

from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
APP_DIR = ROOT / "waha"


def _config() -> dict:
    return yaml.safe_load((APP_DIR / "config.yaml").read_text())


def test_app_defaults_are_private() -> None:
    config = _config()

    assert config["ingress"] is True
    assert config.get("panel_admin", True) is True
    assert config["ports"]["3000/tcp"] is None
    assert config["options"]["api_key"] is None
    assert config["options"]["download_media"] is False


def test_app_image_and_upstream_are_version_pinned() -> None:
    config = _config()
    dockerfile = (APP_DIR / "Dockerfile").read_text()

    assert config["version"] == "0.2.0"
    assert config["image"] == "ghcr.io/sebastian-greco/ha-waha"
    assert "devlikeapro/waha:gows-2026.7.1@sha256:" in dockerfile
    assert "ARG BUILD_VERSION=0.2.0" in dockerfile


def test_app_discovers_the_companion_integration() -> None:
    """The app advertises its private internal API to Home Assistant."""
    config = _config()

    assert config["discovery"] == ["waha_whatsapp"]
    assert (APP_DIR / "discovery.mjs").is_file()
