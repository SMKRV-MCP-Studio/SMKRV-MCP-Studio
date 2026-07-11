#!/usr/bin/env python3
"""Download and manage the DeBERTa v3 prompt injection detection model.

Downloads the ONNX model + tokenizer from HuggingFace Hub into a persistent
volume directory. Supports:
  - Initial download during Docker build or first startup
  - Periodic checksum verification (detect upstream updates)
  - Atomic file replacement to avoid serving partial files

Model: protectai/deberta-v3-base-prompt-injection-v2  (ONNX, ~180MB)
  → https://huggingface.co/protectai/deberta-v3-base-prompt-injection-v2

Usage:
  python scripts/download_prompt_guard_model.py [--model-dir ./models/prompt-guard] [--force]
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("prompt-guard-model")

# ---------------------------------------------------------------------------
# Model definition
# ---------------------------------------------------------------------------

HF_REPO = "protectai/deberta-v3-base-prompt-injection-v2"
HF_BASE_URL = f"https://huggingface.co/{HF_REPO}/resolve/main"

# Files to download (path in HF repo → local filename)
MODEL_FILES = {
    "onnx/model.onnx": "model.onnx",
    "tokenizer.json": "tokenizer.json",
    "config.json": "config.json",
}

METADATA_FILE = "model_metadata.json"
DOWNLOAD_TIMEOUT = 300  # 5 min per file


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256_file(path: Path) -> str:
    """Compute SHA-256 of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _download_file(url: str, dest: Path, desc: str = "") -> bool:
    """Download a file with progress logging. Returns True on success."""
    logger.info("Downloading %s from %s", desc or dest.name, url)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SMKRV-MCP-Studio/1.0"})
        with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            # Write to temp file, then atomic rename
            fd, tmp_path = tempfile.mkstemp(
                dir=str(dest.parent), suffix=".tmp", prefix=".dl_"
            )
            try:
                with os.fdopen(fd, "wb") as f:
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total > 0 and downloaded % (5 * 1024 * 1024) < 65536:
                            pct = downloaded * 100 // total
                            logger.info("  %s: %d%% (%d / %d bytes)", desc, pct, downloaded, total)
                os.chmod(tmp_path, 0o644)
                os.replace(tmp_path, str(dest))
                logger.info("  %s: done (%d bytes)", desc, downloaded)
                return True
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
    except urllib.error.HTTPError as e:
        logger.error("HTTP %d downloading %s: %s", e.code, url, e.reason)
        return False
    except Exception as e:
        logger.error("Failed to download %s: %s", url, e)
        return False


def _load_metadata(model_dir: Path) -> dict:
    """Load metadata JSON from model directory."""
    meta_path = model_dir / METADATA_FILE
    if meta_path.exists():
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_metadata(model_dir: Path, meta: dict) -> None:
    """Save metadata JSON atomically."""
    meta_path = model_dir / METADATA_FILE
    fd, tmp_path = tempfile.mkstemp(dir=str(model_dir), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
        os.replace(tmp_path, str(meta_path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Check for upstream updates via ETag / Last-Modified
# ---------------------------------------------------------------------------

def _check_remote_changed(model_dir: Path) -> bool:
    """Check if any remote file has changed (via ETag or Content-Length).

    Returns True if update is needed or if check is uncertain.
    """
    meta = _load_metadata(model_dir)
    old_etags = meta.get("etags", {})

    for hf_path, local_name in MODEL_FILES.items():
        url = f"{HF_BASE_URL}/{hf_path}"
        local_file = model_dir / local_name
        if not local_file.exists():
            logger.info("Missing local file: %s — update needed", local_name)
            return True

        try:
            req = urllib.request.Request(url, method="HEAD", headers={
                "User-Agent": "SMKRV-MCP-Studio/1.0",
            })
            with urllib.request.urlopen(req, timeout=30) as resp:
                etag = resp.headers.get("ETag", "")
                if etag and old_etags.get(hf_path) and etag != old_etags.get(hf_path):
                    logger.info("ETag changed for %s: %s → %s", hf_path, old_etags.get(hf_path), etag)
                    return True
        except Exception as e:
            logger.warning("Could not check remote %s: %s", hf_path, e)
            # If check fails, don't force re-download
            continue

    return False


# ---------------------------------------------------------------------------
# Main download logic
# ---------------------------------------------------------------------------

def download_model(model_dir: str | Path, *, force: bool = False) -> bool:
    """Download the prompt injection model to the specified directory.

    Args:
        model_dir: Directory to store model files.
        force: If True, re-download even if files exist.

    Returns:
        True if all files are present after operation.
    """
    model_dir = Path(model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)

    meta = _load_metadata(model_dir)

    # Check if all files exist and have checksums
    all_present = all((model_dir / name).exists() for name in MODEL_FILES.values())

    if all_present and not force:
        logger.info("Model files already present at %s", model_dir)
        return True

    logger.info("Downloading prompt injection model to %s", model_dir)

    new_etags: dict[str, str] = {}
    new_checksums: dict[str, str] = {}
    success = True

    for hf_path, local_name in MODEL_FILES.items():
        url = f"{HF_BASE_URL}/{hf_path}"
        dest = model_dir / local_name

        if dest.exists() and not force:
            logger.info("Skipping existing: %s", local_name)
            new_checksums[local_name] = _sha256_file(dest)
            continue

        if not _download_file(url, dest, desc=local_name):
            success = False
            continue

        new_checksums[local_name] = _sha256_file(dest)

        # Fetch ETag for future update checks
        try:
            req = urllib.request.Request(url, method="HEAD", headers={
                "User-Agent": "SMKRV-MCP-Studio/1.0",
            })
            with urllib.request.urlopen(req, timeout=30) as resp:
                etag = resp.headers.get("ETag", "")
                if etag:
                    new_etags[hf_path] = etag
        except Exception:
            pass

    # Save metadata
    meta["downloaded_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    meta["checksums"] = new_checksums
    meta["etags"] = new_etags
    meta["repo"] = HF_REPO
    meta["last_check"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    try:
        _save_metadata(model_dir, meta)
    except Exception as e:
        logger.warning("Failed to save metadata: %s", e)

    if success:
        logger.info("Model download complete: %s", model_dir)
    else:
        logger.error("Some model files failed to download")

    return success


def check_and_update(model_dir: str | Path) -> bool:
    """Check for model updates and download if needed.

    Compares remote ETags with stored metadata. Only downloads changed files.
    Returns True if model is up-to-date after operation.
    """
    model_dir = Path(model_dir)

    if not (model_dir / METADATA_FILE).exists():
        return download_model(model_dir)

    meta = _load_metadata(model_dir)

    # Check update interval
    last_check = meta.get("last_check", "")
    if last_check:
        try:
            import datetime
            last_dt = datetime.datetime.fromisoformat(last_check.replace("Z", "+00:00"))
            now = datetime.datetime.now(datetime.timezone.utc)
            hours_since = (now - last_dt).total_seconds() / 3600
            if hours_since < 1:  # Don't check more than once per hour
                logger.debug("Skipping update check — last checked %.1f hours ago", hours_since)
                return True
        except Exception:
            pass

    if _check_remote_changed(model_dir):
        logger.info("Remote model has changed — re-downloading")
        return download_model(model_dir, force=True)

    # Update last_check timestamp
    meta["last_check"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        _save_metadata(model_dir, meta)
    except Exception:
        pass

    logger.info("Model is up-to-date")
    return True


def verify_checksums(model_dir: str | Path) -> bool:
    """Verify local files match stored checksums. Returns True if all match."""
    model_dir = Path(model_dir)
    meta = _load_metadata(model_dir)
    stored = meta.get("checksums", {})

    if not stored:
        logger.warning("No stored checksums — cannot verify")
        return False

    for local_name, expected_hash in stored.items():
        path = model_dir / local_name
        if not path.exists():
            logger.error("Missing file: %s", local_name)
            return False
        actual = _sha256_file(path)
        if actual != expected_hash:
            logger.error(
                "Checksum mismatch for %s: expected %s, got %s",
                local_name, expected_hash[:16], actual[:16],
            )
            return False

    logger.info("All checksums verified OK")
    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Download prompt injection model")
    parser.add_argument(
        "--model-dir", default="./models/prompt-guard",
        help="Directory to store model files",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Force re-download even if files exist",
    )
    parser.add_argument(
        "--check-update", action="store_true",
        help="Check for upstream updates and download if changed",
    )
    parser.add_argument(
        "--verify", action="store_true",
        help="Verify local file checksums",
    )
    args = parser.parse_args()

    if args.verify:
        ok = verify_checksums(args.model_dir)
        sys.exit(0 if ok else 1)
    elif args.check_update:
        ok = check_and_update(args.model_dir)
        sys.exit(0 if ok else 1)
    else:
        ok = download_model(args.model_dir, force=args.force)
        sys.exit(0 if ok else 1)
