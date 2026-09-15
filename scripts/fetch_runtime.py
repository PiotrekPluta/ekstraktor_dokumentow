"""Fetch and checksum-verify the pinned model + inference server binary.

Runs during `setup.sh`, the only point at which network is available
(docs/ZADANIE.md: network during `run` and tests is cut). Downloads, each
sha256-verified against `config/default.toml`:

1. The pinned GGUF from Hugging Face at a pinned **commit**, not a branch.
2. A prebuilt `llama-server` binary from a pinned llama.cpp release tag,
   selected for the current platform (macOS arm64 / Linux x64 — the two
   this project targets, per `pyproject.toml`'s `[tool.uv] environments`).

Writes what was actually fetched to `vendor/versions.lock`.

Offline token counting via a standalone `tokenizer.json` was the other
option `PROJECT_NOTES.md` §8 Stage 4 names, but the base (non-GGUF) Bielik
repo that ships one is gated on Hugging Face — authentication this tool
has no way to obtain non-interactively. `LlamaServerClient` uses the
running server's `/tokenize` endpoint instead, the plan's other explicitly
-named option, so this script does not fetch a tokenizer file.
"""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
import sys
import tarfile
import tomllib
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config" / "default.toml"
VENDOR_DIR = REPO_ROOT / "vendor"
CHUNK_SIZE = 1024 * 1024
DOWNLOAD_TIMEOUT_S = 120.0


class FetchError(Exception):
    pass


def main() -> None:
    config = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    llama_cfg = config["backend"]["llama_server"]

    VENDOR_DIR.mkdir(parents=True, exist_ok=True)

    with httpx.Client(follow_redirects=True, timeout=DOWNLOAD_TIMEOUT_S) as client:
        model_path = fetch_model(llama_cfg, client)
        server_dir = fetch_server_binary(llama_cfg["server_binary"], client)
    write_versions_lock(model_path, server_dir, llama_cfg)

    print("fetch_runtime: done.")
    print(f"  model:  {model_path}")
    print(f"  server: {server_dir / 'llama-server'}")


def fetch_model(llama_cfg: dict, client: httpx.Client) -> Path:
    repo = llama_cfg["model_repo"]
    revision = llama_cfg["model_revision"]
    filename = llama_cfg["model_file"]
    expected_sha256 = llama_cfg["model_sha256"]

    dest = VENDOR_DIR / filename
    if _already_verified(dest, expected_sha256):
        print(f"fetch_runtime: {dest.name} already present and verified, skipping")
        return dest

    url = f"https://huggingface.co/{repo}/resolve/{revision}/{filename}"
    print(f"fetch_runtime: downloading {url}")
    _download_and_verify(client, url, dest, expected_sha256)
    return dest


def fetch_server_binary(binary_cfg: dict, client: httpx.Client) -> Path:
    asset, expected_sha256 = _select_platform_asset(binary_cfg)
    tag = binary_cfg["release_tag"]
    url = f"https://github.com/ggml-org/llama.cpp/releases/download/{tag}/{asset}"

    extract_dir = VENDOR_DIR / "llama-server"
    server_exe = extract_dir / "llama-server"
    if server_exe.exists():
        print(f"fetch_runtime: {server_exe} already present, skipping")
        return extract_dir

    archive_path = VENDOR_DIR / asset
    print(f"fetch_runtime: downloading {url}")
    _download_and_verify(client, url, archive_path, expected_sha256)

    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    with tarfile.open(archive_path) as tf:
        tf.extractall(extract_dir, filter="data")
    archive_path.unlink()

    # The archive's single top-level dir is named after the release tag
    # (llama-b10985/...) — flatten it so vendor/llama-server/llama-server
    # is a stable path across releases.
    inner_dirs = [p for p in extract_dir.iterdir() if p.is_dir()]
    if len(inner_dirs) == 1:
        for item in inner_dirs[0].iterdir():
            item.rename(extract_dir / item.name)
        inner_dirs[0].rmdir()

    if not server_exe.exists():
        raise FetchError(f"extracted archive but {server_exe} is missing")
    server_exe.chmod(server_exe.stat().st_mode | 0o111)
    return extract_dir


def _select_platform_asset(binary_cfg: dict) -> tuple[str, str]:
    system = platform.system()
    machine = platform.machine().lower()

    if system == "Darwin" and machine in ("arm64", "aarch64"):
        return binary_cfg["macos_arm64_asset"], binary_cfg["macos_arm64_sha256"]
    if system == "Linux" and machine in ("x86_64", "amd64"):
        return binary_cfg["ubuntu_x64_asset"], binary_cfg["ubuntu_x64_sha256"]
    raise FetchError(
        f"no prebuilt llama-server binary pinned for {system}/{machine} — "
        "only macOS arm64 and Linux x64 are supported (docs/PROJECT_NOTES.md §6)"
    )


def _already_verified(path: Path, expected_sha256: str) -> bool:
    return path.exists() and _sha256_of(path) == expected_sha256


def _sha256_of(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(CHUNK_SIZE):
            hasher.update(chunk)
    return hasher.hexdigest()


def _download_and_verify(
    client: httpx.Client, url: str, dest: Path, expected_sha256: str
) -> None:
    tmp_path = dest.with_name(dest.name + ".part")
    hasher = hashlib.sha256()
    try:
        with client.stream("GET", url) as response, tmp_path.open("wb") as fh:
            response.raise_for_status()
            for chunk in response.iter_bytes(CHUNK_SIZE):
                hasher.update(chunk)
                fh.write(chunk)
    except httpx.HTTPError as exc:
        tmp_path.unlink(missing_ok=True)
        raise FetchError(f"failed to download {url}: {exc}") from exc

    actual_sha256 = hasher.hexdigest()
    if actual_sha256 != expected_sha256:
        tmp_path.unlink(missing_ok=True)
        raise FetchError(
            f"checksum mismatch for {url}: expected {expected_sha256}, got {actual_sha256}"
        )
    tmp_path.rename(dest)


def write_versions_lock(model_path: Path, server_dir: Path, llama_cfg: dict) -> None:
    lock_path = VENDOR_DIR / "versions.lock"
    lock_path.write_text(
        json.dumps(
            {
                "model_repo": llama_cfg["model_repo"],
                "model_revision": llama_cfg["model_revision"],
                "model_file": model_path.name,
                "model_sha256": llama_cfg["model_sha256"],
                "model_path": str(model_path),
                "llama_server_release": llama_cfg["server_binary"]["release_tag"],
                "llama_server_path": str(server_dir / "llama-server"),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    try:
        main()
    except FetchError as exc:
        print(f"fetch_runtime: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
