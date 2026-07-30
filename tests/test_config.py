"""Config persistence and self-repair."""

from __future__ import annotations

import json
from pathlib import Path

from torrentapp.config import DEFAULT_DOWNLOAD_ROOT, Config


def test_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    original = Config()
    original.listen_port = 12345
    original.download_rate_limit = 500_000
    original.download_root = r"D:\Stuff"
    original.save(path)

    loaded = Config.load(path)
    assert loaded.listen_port == 12345
    assert loaded.download_rate_limit == 500_000
    assert loaded.download_root == r"D:\Stuff"


def test_missing_file_yields_defaults(tmp_path: Path) -> None:
    assert Config.load(tmp_path / "absent.json").listen_port == Config().listen_port


def test_corrupt_file_yields_defaults(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{{{ not json", encoding="utf-8")
    assert Config.load(path).listen_port == Config().listen_port


def test_unknown_keys_are_ignored(tmp_path: Path) -> None:
    """A settings file from a newer build must not brick an older one."""
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"listen_port": 4242, "some_future_option": True}), encoding="utf-8"
    )
    config = Config.load(path)
    assert config.listen_port == 4242
    assert not hasattr(config, "some_future_option")


def test_normalise_repairs_bad_port(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"listen_port": 999_999}), encoding="utf-8")
    assert Config.load(path).listen_port == Config().listen_port


def test_normalise_repairs_blank_download_root(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"download_root": "   "}), encoding="utf-8")
    assert Config.load(path).download_root == DEFAULT_DOWNLOAD_ROOT


def test_normalise_clamps_negative_limits(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"download_rate_limit": -5}), encoding="utf-8")
    assert Config.load(path).download_rate_limit == 0


def test_normalise_rejects_unknown_encryption_mode(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"encryption": "banana"}), encoding="utf-8")
    assert Config.load(path).encryption == "enabled"


def test_save_leaves_no_temp_file(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    Config().save(path)
    assert list(tmp_path.glob("*.tmp")) == []


def test_default_save_path_uses_download_root_when_nothing_remembered() -> None:
    config = Config()
    assert config.default_save_path() == config.download_root


def test_default_save_path_prefers_the_last_used_folder() -> None:
    config = Config()
    config.remember(r"D:\Somewhere Else")
    assert config.default_save_path() == r"D:\Somewhere Else"


def test_remember_can_be_switched_off() -> None:
    config = Config()
    config.remember_last_save_path = False
    config.remember(r"D:\Ignored")
    assert config.default_save_path() == config.download_root


def test_remember_ignores_blank_paths() -> None:
    config = Config()
    config.remember("   ")
    assert config.default_save_path() == config.download_root
