"""IF-KEYPROVIDER (local): KEK creation, DEK wrapping, and the failure modes."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from veyra_common.settings import Settings
from veyra_evidence.keys import (
    KEY_BYTES,
    LOCAL_KEY_ID,
    KeyError_,
    LocalKeyProvider,
    get_key_provider,
)


@pytest.fixture
def cfg(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path)


@pytest.fixture
def keys(cfg: Settings) -> LocalKeyProvider:
    return LocalKeyProvider(cfg=cfg)


def test_kek_is_created_once_with_restrictive_permissions(keys: LocalKeyProvider) -> None:
    path = keys.ensure_kek()
    assert path.stat().st_size == KEY_BYTES
    mode = path.stat().st_mode
    assert not mode & (stat.S_IRGRP | stat.S_IROTH | stat.S_IWUSR), "the KEK must be 0400"
    # A second call must not replace the key: every existing segment depends on it.
    before = path.read_bytes()
    assert keys.ensure_kek().read_bytes() == before


def test_no_temp_file_is_left_behind(keys: LocalKeyProvider) -> None:
    path = keys.ensure_kek()
    assert list(path.parent.glob("*.tmp")) == []


def test_each_data_key_is_fresh(keys: LocalKeyProvider) -> None:
    dek1, key_id, wrapped1 = keys.new_data_key()
    dek2, _, wrapped2 = keys.new_data_key()
    assert len(dek1) == KEY_BYTES
    assert dek1 != dek2, "a segment must not reuse another segment's key"
    assert wrapped1 != wrapped2
    assert key_id == LOCAL_KEY_ID


def test_wrap_unwrap_round_trip(keys: LocalKeyProvider) -> None:
    dek, key_id, wrapped = keys.new_data_key()
    assert keys.unwrap(key_id, wrapped) == dek


def test_a_tampered_wrapped_key_is_rejected(keys: LocalKeyProvider) -> None:
    _, key_id, wrapped = keys.new_data_key()
    broken = bytearray(wrapped)
    broken[-1] ^= 0x01
    with pytest.raises(KeyError_, match="integrity"):
        keys.unwrap(key_id, bytes(broken))


def test_a_truncated_wrapped_key_is_rejected(keys: LocalKeyProvider) -> None:
    _, key_id, wrapped = keys.new_data_key()
    with pytest.raises(KeyError_, match="truncated"):
        keys.unwrap(key_id, wrapped[:8])


def test_an_unknown_key_id_is_rejected(keys: LocalKeyProvider) -> None:
    _, _, wrapped = keys.new_data_key()
    with pytest.raises(KeyError_, match="unknown key id"):
        keys.unwrap("veyra-somebody-elses-key", wrapped)


def test_another_kek_cannot_unwrap(cfg: Settings, tmp_path: Path) -> None:
    mine = LocalKeyProvider(cfg=cfg)
    _, key_id, wrapped = mine.new_data_key()
    theirs = LocalKeyProvider(keys_dir=tmp_path / "elsewhere", cfg=cfg)
    with pytest.raises(KeyError_, match="integrity"):
        theirs.unwrap(key_id, wrapped)


def test_a_corrupt_kek_file_is_reported(keys: LocalKeyProvider) -> None:
    keys.ensure_kek()
    keys.kek_path.chmod(0o600)
    keys.kek_path.write_bytes(b"too short")
    with pytest.raises(KeyError_, match="byte key"):
        keys.new_data_key()


def test_get_key_provider_honours_the_setting(cfg: Settings) -> None:
    assert isinstance(get_key_provider(cfg), LocalKeyProvider)
    openbao = Settings(_env_file=None, data_dir=cfg.data_dir, key_provider="openbao")
    with pytest.raises(NotImplementedError, match="B3"):
        get_key_provider(openbao)


def test_keys_dir_follows_the_data_dir(cfg: Settings, tmp_path: Path) -> None:
    assert LocalKeyProvider(cfg=cfg).kek_path == tmp_path / "keys" / "kek.bin"
