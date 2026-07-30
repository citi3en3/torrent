"""ResumeStore round-trips.

If these break, a restart loses download progress -- so they guard the single
most valuable property the engine has.
"""

from __future__ import annotations

from pathlib import Path

import libtorrent as lt
import pytest

from torrentapp.engine.resume import ResumeStore
from torrentapp.engine.torrent import hash_string


@pytest.fixture
def store(tmp_path: Path) -> ResumeStore:
    return ResumeStore(
        tmp_path / "resume", tmp_path / "torrents", tmp_path / "session.state"
    )


def make_torrent_info(root: Path) -> "lt.torrent_info":
    """Build a genuine (tiny) torrent so we exercise the real serialisers."""
    payload = root / "payload"
    payload.mkdir(parents=True, exist_ok=True)
    (payload / "file.bin").write_bytes(b"x" * 32768)

    storage = lt.file_storage()
    lt.add_files(storage, str(payload))
    creator = lt.create_torrent(storage, piece_size=16384)
    lt.set_piece_hashes(creator, str(root))
    return lt.torrent_info(creator.generate())


def make_params(root: Path, info: "lt.torrent_info") -> "lt.add_torrent_params":
    """Params carrying full metadata -- write_resume_data_buf embeds the info dict."""
    params = lt.add_torrent_params()
    params.ti = info
    params.save_path = str(root / "downloads")
    return params


def make_metadata_free_params(root: Path, info_hash: str) -> "lt.add_torrent_params":
    """Params as they exist for a magnet whose metadata has not arrived yet.

    This is what the engine actually persists: ``save_resume_data()`` is called
    without the ``save_info_dict`` flag, so resume files stay small and metadata
    lives in the sibling .torrent instead.
    """
    params = lt.parse_magnet_uri(f"magnet:?xt=urn:btih:{info_hash}")
    params.save_path = str(root / "downloads")
    return params


def test_write_torrent_persists_metadata(store: ResumeStore, tmp_path: Path) -> None:
    info = make_torrent_info(tmp_path)
    info_hash = hash_string(info)
    assert info_hash

    store.write_torrent(info_hash, info)
    target = store.torrent_file(info_hash)
    assert target.exists() and target.stat().st_size > 0

    # Re-reading it must yield the same torrent.
    assert hash_string(lt.torrent_info(str(target))) == info_hash


def test_resume_round_trip_reattaches_metadata_from_disk(
    store: ResumeStore, tmp_path: Path
) -> None:
    """The real path: a metadata-free resume file plus its sibling .torrent.

    Asserting against metadata-free params matters -- params carrying ``ti``
    would embed the info dict and pass this test without the reattachment in
    ``load_all`` doing anything at all.
    """
    info = make_torrent_info(tmp_path)
    info_hash = hash_string(info)

    params = make_metadata_free_params(tmp_path, info_hash)
    assert params.ti is None, "fixture must not carry metadata"

    store.write_resume(info_hash, lt.write_resume_data_buf(params))
    store.write_torrent(info_hash, info)

    restored = store.load_all()
    assert len(restored) == 1
    assert restored[0].save_path == str(tmp_path / "downloads")
    assert restored[0].ti is not None, "metadata should be reattached from the .torrent"
    assert hash_string(restored[0].ti) == info_hash


def test_resume_file_without_metadata_is_far_smaller(
    store: ResumeStore, tmp_path: Path
) -> None:
    """Why the engine omits the info dict: resume writes happen every 60s."""
    info = make_torrent_info(tmp_path)
    info_hash = hash_string(info)

    lean = lt.write_resume_data_buf(make_metadata_free_params(tmp_path, info_hash))
    fat = lt.write_resume_data_buf(make_params(tmp_path, info))
    assert len(lean) < len(fat)


def test_load_all_survives_a_corrupt_resume_file(
    store: ResumeStore, tmp_path: Path
) -> None:
    """One bad file must not stop the other torrents coming back."""
    info = make_torrent_info(tmp_path)
    info_hash = hash_string(info)
    store.write_resume(info_hash, lt.write_resume_data_buf(make_params(tmp_path, info)))
    store.write_torrent(info_hash, info)

    (store.resume_dir / "0000000000000000000000000000000000000000.fastresume").write_bytes(
        b"this is not bencoded data"
    )

    restored = store.load_all()
    assert len(restored) == 1


def test_missing_metadata_still_restores_params(store: ResumeStore, tmp_path: Path) -> None:
    """Without a .torrent the entry still returns; libtorrent refetches metadata."""
    info = make_torrent_info(tmp_path)
    info_hash = hash_string(info)
    params = make_metadata_free_params(tmp_path, info_hash)
    store.write_resume(info_hash, lt.write_resume_data_buf(params))

    restored = store.load_all()
    assert len(restored) == 1
    assert restored[0].ti is None
    assert hash_string(restored[0]) == info_hash


def test_forget_removes_both_files(store: ResumeStore, tmp_path: Path) -> None:
    info = make_torrent_info(tmp_path)
    info_hash = hash_string(info)
    store.write_resume(info_hash, lt.write_resume_data_buf(make_params(tmp_path, info)))
    store.write_torrent(info_hash, info)
    assert store.known_hashes() == {info_hash}

    store.forget(info_hash)
    assert not store.resume_file(info_hash).exists()
    assert not store.torrent_file(info_hash).exists()
    assert store.known_hashes() == set()


def test_forget_is_idempotent(store: ResumeStore) -> None:
    store.forget("deadbeef" * 5)  # never existed -- must not raise
    store.forget("")


def test_session_state_round_trip(store: ResumeStore) -> None:
    assert store.read_session_state() is None
    store.write_session_state(b"d4:testi1ee")
    assert store.read_session_state() == b"d4:testi1ee"


def test_atomic_write_leaves_no_temp_files(store: ResumeStore, tmp_path: Path) -> None:
    info = make_torrent_info(tmp_path)
    info_hash = hash_string(info)
    store.write_resume(info_hash, lt.write_resume_data_buf(make_params(tmp_path, info)))
    assert list(store.resume_dir.glob("*.tmp")) == []


def test_write_torrent_does_not_overwrite_existing(
    store: ResumeStore, tmp_path: Path
) -> None:
    """Metadata is immutable once known; rewriting it every alert is wasted IO."""
    info = make_torrent_info(tmp_path)
    info_hash = hash_string(info)
    store.write_torrent(info_hash, info)
    first = store.torrent_file(info_hash).stat().st_mtime_ns

    store.write_torrent(info_hash, info)
    assert store.torrent_file(info_hash).stat().st_mtime_ns == first
