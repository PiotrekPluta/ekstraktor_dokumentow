"""Fetch and checksum-verify the pinned model + inference server binary.

Stage 0 placeholder. Model/backend are not chosen yet — see
docs/PROJECT_NOTES.md §4. This becomes a real download step in Stage 5:

1. Fetch the GGUF from Hugging Face at a pinned repo commit; verify sha256.
2. Fetch a prebuilt `llama-server` binary from a pinned llama.cpp release.
3. Write actual checksums to vendor/versions.lock.

Until then this is a deliberate no-op so `setup.sh` stays runnable end to end.
"""

from __future__ import annotations


def main() -> None:
    print(
        "fetch_runtime: nothing to fetch yet — model/backend not pinned. "
        "See docs/PROJECT_NOTES.md §4."
    )


if __name__ == "__main__":
    main()
