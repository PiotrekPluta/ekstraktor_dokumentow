"""fetch_runtime.py's download/verify logic against httpx.MockTransport —
no real network, no dependency on the actual pinned model/binary having
been fetched. VENDOR_DIR is monkeypatched to a tmp_path per test so these
never touch the real vendor/ directory.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tarfile
from pathlib import Path

import httpx
import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import fetch_runtime


@pytest.fixture(autouse=True)
def _vendor_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    vendor = tmp_path / "vendor"
    monkeypatch.setattr(fetch_runtime, "VENDOR_DIR", vendor)
    return vendor


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_download_and_verify_writes_file_on_matching_checksum(tmp_path: Path) -> None:
    content = b"pretend model bytes"
    expected_sha256 = hashlib.sha256(content).hexdigest()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=content)

    dest = tmp_path / "model.gguf"
    fetch_runtime._download_and_verify(
        _mock_client(handler), "https://x/model", dest, expected_sha256
    )

    assert dest.read_bytes() == content
    assert not dest.with_name(dest.name + ".part").exists()


def test_download_and_verify_rejects_checksum_mismatch(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"wrong bytes")

    dest = tmp_path / "model.gguf"
    with pytest.raises(fetch_runtime.FetchError, match="checksum mismatch"):
        fetch_runtime._download_and_verify(
            _mock_client(handler), "https://x/model", dest, "0" * 64
        )

    assert not dest.exists()
    assert not dest.with_name(dest.name + ".part").exists()


def test_download_and_verify_raises_on_http_error(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    dest = tmp_path / "model.gguf"
    with pytest.raises(fetch_runtime.FetchError):
        fetch_runtime._download_and_verify(
            _mock_client(handler), "https://x/model", dest, "0" * 64
        )

    assert not dest.exists()


def test_download_and_verify_raises_on_connection_failure(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    dest = tmp_path / "model.gguf"
    with pytest.raises(fetch_runtime.FetchError):
        fetch_runtime._download_and_verify(
            _mock_client(handler), "https://x/model", dest, "0" * 64
        )

    assert not dest.exists()


def test_already_verified_true_for_matching_file(tmp_path: Path) -> None:
    content = b"some content"
    path = tmp_path / "f.bin"
    path.write_bytes(content)
    assert fetch_runtime._already_verified(path, hashlib.sha256(content).hexdigest())


def test_already_verified_false_for_mismatched_file(tmp_path: Path) -> None:
    path = tmp_path / "f.bin"
    path.write_bytes(b"some content")
    assert not fetch_runtime._already_verified(path, "0" * 64)


def test_already_verified_false_for_missing_file(tmp_path: Path) -> None:
    assert not fetch_runtime._already_verified(tmp_path / "missing.bin", "0" * 64)


def test_fetch_model_skips_download_when_already_verified(
    tmp_path: Path, _vendor_dir: Path
) -> None:
    content = b"already here"
    _vendor_dir.mkdir(parents=True)
    (_vendor_dir / "model.gguf").write_bytes(content)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not download when already verified")

    llama_cfg = {
        "model_repo": "org/repo",
        "model_revision": "abc123",
        "model_file": "model.gguf",
        "model_sha256": hashlib.sha256(content).hexdigest(),
    }
    result = fetch_runtime.fetch_model(llama_cfg, _mock_client(handler))
    assert result == _vendor_dir / "model.gguf"


@pytest.mark.parametrize(
    ("system", "machine", "expected_key"),
    [
        ("Darwin", "arm64", "macos_arm64"),
        ("Linux", "x86_64", "ubuntu_x64"),
    ],
)
def test_select_platform_asset(
    monkeypatch: pytest.MonkeyPatch, system: str, machine: str, expected_key: str
) -> None:
    monkeypatch.setattr(fetch_runtime.platform, "system", lambda: system)
    monkeypatch.setattr(fetch_runtime.platform, "machine", lambda: machine)

    binary_cfg = {
        "macos_arm64_asset": "macos.tar.gz",
        "macos_arm64_sha256": "a" * 64,
        "ubuntu_x64_asset": "ubuntu.tar.gz",
        "ubuntu_x64_sha256": "b" * 64,
    }
    asset, sha = fetch_runtime._select_platform_asset(binary_cfg)
    assert asset == binary_cfg[f"{expected_key}_asset"]
    assert sha == binary_cfg[f"{expected_key}_sha256"]


def test_select_platform_asset_rejects_unsupported_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fetch_runtime.platform, "system", lambda: "Windows")
    monkeypatch.setattr(fetch_runtime.platform, "machine", lambda: "AMD64")

    with pytest.raises(fetch_runtime.FetchError, match="Windows"):
        fetch_runtime._select_platform_asset({})


def test_fetch_server_binary_extracts_and_flattens_archive(
    tmp_path: Path, _vendor_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fetch_runtime.platform, "system", lambda: "Linux")
    monkeypatch.setattr(fetch_runtime.platform, "machine", lambda: "x86_64")
    _vendor_dir.mkdir(
        parents=True
    )  # main() does this before calling in; we bypass main()

    # Build a small real tar.gz mirroring the release layout: a single
    # top-level dir containing llama-server plus a "shared lib".
    archive_src = tmp_path / "build"
    (archive_src / "llama-bTEST").mkdir(parents=True)
    (archive_src / "llama-bTEST" / "llama-server").write_bytes(
        b"#!/bin/sh\necho fake\n"
    )
    (archive_src / "llama-bTEST" / "libggml-base.so").write_bytes(b"fake lib")
    archive_path = tmp_path / "llama-bTEST-bin-ubuntu-x64.tar.gz"
    with tarfile.open(archive_path, "w:gz") as tf:
        tf.add(archive_src / "llama-bTEST", arcname="llama-bTEST")
    archive_bytes = archive_path.read_bytes()
    expected_sha256 = hashlib.sha256(archive_bytes).hexdigest()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=archive_bytes)

    binary_cfg = {
        "release_tag": "bTEST",
        "ubuntu_x64_asset": "llama-bTEST-bin-ubuntu-x64.tar.gz",
        "ubuntu_x64_sha256": expected_sha256,
        "macos_arm64_asset": "unused.tar.gz",
        "macos_arm64_sha256": "0" * 64,
    }
    extract_dir = fetch_runtime.fetch_server_binary(binary_cfg, _mock_client(handler))

    server_exe = extract_dir / "llama-server"
    assert server_exe.exists()
    assert server_exe.read_bytes() == b"#!/bin/sh\necho fake\n"
    assert (extract_dir / "libggml-base.so").exists()
    # archive is cleaned up, not left in vendor/
    assert not (fetch_runtime.VENDOR_DIR / binary_cfg["ubuntu_x64_asset"]).exists()


def test_write_versions_lock_produces_valid_json(
    tmp_path: Path, _vendor_dir: Path
) -> None:
    _vendor_dir.mkdir(parents=True)
    llama_cfg = {
        "model_repo": "org/repo",
        "model_revision": "abc123",
        "model_sha256": "a" * 64,
        "server_binary": {"release_tag": "bTEST"},
    }
    model_path = _vendor_dir / "model.gguf"
    server_dir = _vendor_dir / "llama-server"

    fetch_runtime.write_versions_lock(model_path, server_dir, llama_cfg)

    lock_data = json.loads((_vendor_dir / "versions.lock").read_text(encoding="utf-8"))
    assert lock_data["model_repo"] == "org/repo"
    assert lock_data["model_revision"] == "abc123"
    assert lock_data["llama_server_release"] == "bTEST"
    assert lock_data["llama_server_path"] == str(server_dir / "llama-server")
