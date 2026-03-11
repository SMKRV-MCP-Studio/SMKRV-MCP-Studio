"""Client IP extraction and GeoIP country lookup.

Provides a centralized utility for extracting the real client IP from
proxied requests (Cloudflare → nginx → backend) and resolving the
client's country via CF-IPCountry header or GeoLite2 MMDB fallback.

Supports two GeoIP database sources:
- **Generic (P3TERX)**: Community-maintained mirror, no account needed.
- **MaxMind API**: Official source, requires a free license key from maxmind.com.
"""

import io
import logging
import os
import shutil
import tarfile
import tempfile
from datetime import UTC, datetime
from urllib.request import Request as URLRequest
from urllib.request import urlopen

from starlette.requests import Request

logger = logging.getLogger(__name__)

# --- GeoLite2 MMDB ---

_GEOIP_DB_PATH = os.getenv("STUDIO_GEOIP_DB_PATH", "/app/data/GeoLite2-Country.mmdb")
_MAXMIND_LICENSE_KEY = os.getenv("STUDIO_MAXMIND_LICENSE_KEY", "")
_GEOIP_SOURCE = os.getenv("STUDIO_GEOIP_SOURCE", "generic")  # "generic" or "maxmind"

_P3TERX_URL = (
    "https://raw.githubusercontent.com/P3TERX/GeoLite.mmdb/download/GeoLite2-Country.mmdb"
)
_MAXMIND_URL_TEMPLATE = (
    "https://download.maxmind.com/app/geoip_download"
    "?edition_id=GeoLite2-Country&license_key={key}&suffix=tar.gz"
)

_mmdb_reader = None
_mmdb_build_date: datetime | None = None
_mmdb_loaded = False


def _load_mmdb() -> None:
    """Load GeoLite2 MMDB database (once). Safe to call multiple times."""
    global _mmdb_reader, _mmdb_build_date, _mmdb_loaded
    if _mmdb_loaded:
        return
    _mmdb_loaded = True

    if not os.path.exists(_GEOIP_DB_PATH):
        logger.info("GeoIP: MMDB not found at %s — country lookup disabled", _GEOIP_DB_PATH)
        return

    try:
        import maxminddb

        _mmdb_reader = maxminddb.open_database(_GEOIP_DB_PATH)
        build_epoch = _mmdb_reader.metadata().build_epoch
        _mmdb_build_date = datetime.fromtimestamp(build_epoch, tz=UTC)
        logger.info(
            "GeoIP: loaded %s (built %s, %d records)",
            _GEOIP_DB_PATH,
            _mmdb_build_date.strftime("%Y-%m-%d"),
            _mmdb_reader.metadata().node_count,
        )
    except ImportError:
        logger.warning("GeoIP: maxminddb not installed — country lookup disabled")
    except Exception:
        logger.exception("GeoIP: failed to load MMDB from %s", _GEOIP_DB_PATH)


def reload_mmdb() -> None:
    """Force-reload the MMDB database (e.g. after an update)."""
    global _mmdb_reader, _mmdb_build_date, _mmdb_loaded
    if _mmdb_reader is not None:
        try:
            _mmdb_reader.close()
        except Exception:
            pass
    _mmdb_reader = None
    _mmdb_build_date = None
    _mmdb_loaded = False
    _load_mmdb()


def _lookup_country(ip: str) -> str | None:
    """Lookup country ISO code from IP using MMDB. Returns None on failure."""
    _load_mmdb()
    if _mmdb_reader is None:
        return None
    try:
        result = _mmdb_reader.get(ip)
        if result and "country" in result:
            return result["country"].get("iso_code")
    except Exception:
        pass
    return None


# --- IP extraction ---


def get_client_ip(request: Request) -> str:
    """Extract the real client IP from the request.

    Priority:
    1. CF-Connecting-IP (Cloudflare — single reliable IP)
    2. X-Forwarded-For first entry (nginx proxy chain)
    3. X-Real-IP (nginx $remote_addr passthrough)
    4. request.client.host (direct connection fallback)
    """
    cf_ip = request.headers.get("cf-connecting-ip")
    if cf_ip:
        return cf_ip.strip()
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else ""


def get_client_country(request: Request) -> str | None:
    """Get client country as 2-letter ISO 3166-1 alpha-2 code.

    Priority:
    1. CF-IPCountry header (Cloudflare — authoritative)
    2. GeoLite2 MMDB lookup (fallback)
    """
    cf_country = request.headers.get("cf-ipcountry")
    if cf_country and cf_country not in ("XX", "T1"):
        return cf_country.upper()[:2]
    ip = get_client_ip(request)
    return _lookup_country(ip)


def get_geoip_status() -> dict:
    """Return GeoIP database status for admin display."""
    _load_mmdb()
    stale_days = 90
    is_stale = False
    if _mmdb_build_date:
        age = (datetime.now(UTC) - _mmdb_build_date).days
        is_stale = age > stale_days

    return {
        "loaded": _mmdb_reader is not None,
        "path": _GEOIP_DB_PATH,
        "source": _GEOIP_SOURCE,
        "build_date": _mmdb_build_date.isoformat() if _mmdb_build_date else None,
        "is_stale": is_stale,
        "record_count": _mmdb_reader.metadata().node_count if _mmdb_reader else None,
        "maxmind_key_set": bool(_MAXMIND_LICENSE_KEY),
        "updatable": bool(_MAXMIND_LICENSE_KEY) or _GEOIP_SOURCE == "generic",
    }


def update_mmdb(source: str | None = None) -> dict:
    """Download a fresh GeoLite2-Country MMDB and reload.

    Args:
        source: Override source ("generic" or "maxmind"). Defaults to env setting.

    Returns:
        Status dict with success/error info.
    """
    src = source or _GEOIP_SOURCE

    if src == "maxmind" and not _MAXMIND_LICENSE_KEY:
        return {
            "success": False,
            "error": "MaxMind license key not configured (STUDIO_MAXMIND_LICENSE_KEY).",
        }

    try:
        if src == "maxmind":
            url = _MAXMIND_URL_TEMPLATE.format(key=_MAXMIND_LICENSE_KEY)
            logger.info("GeoIP update: downloading from MaxMind API...")
            req = URLRequest(url, headers={"User-Agent": "SMKRV-MCP-Studio/1.0"})
            data = urlopen(req, timeout=60).read()  # noqa: S310
            # MaxMind returns tar.gz with nested dir
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
                mmdb_member = next(
                    (m for m in tf.getmembers() if m.name.endswith(".mmdb")), None
                )
                if not mmdb_member:
                    return {"success": False, "error": "No .mmdb file found in MaxMind archive."}
                # Extract to temp, then move atomically
                with tempfile.TemporaryDirectory() as tmpdir:
                    tf.extract(mmdb_member, tmpdir)
                    extracted = os.path.join(tmpdir, mmdb_member.name)
                    os.makedirs(os.path.dirname(_GEOIP_DB_PATH), exist_ok=True)
                    shutil.move(extracted, _GEOIP_DB_PATH)
        else:
            logger.info("GeoIP update: downloading from P3TERX mirror...")
            req = URLRequest(_P3TERX_URL, headers={"User-Agent": "SMKRV-MCP-Studio/1.0"})
            data = urlopen(req, timeout=60).read()  # noqa: S310
            os.makedirs(os.path.dirname(_GEOIP_DB_PATH), exist_ok=True)
            # Write to temp file then move for atomicity
            fd, tmp_path = tempfile.mkstemp(suffix=".mmdb")
            try:
                os.write(fd, data)
                os.close(fd)
                shutil.move(tmp_path, _GEOIP_DB_PATH)
            except Exception:
                os.close(fd) if not os.path.exists(tmp_path) else os.unlink(tmp_path)
                raise

        # Reload the database
        reload_mmdb()

        return {
            "success": True,
            "source": src,
            "build_date": _mmdb_build_date.isoformat() if _mmdb_build_date else None,
            "record_count": _mmdb_reader.metadata().node_count if _mmdb_reader else None,
        }
    except Exception as exc:
        logger.exception("GeoIP update failed")
        return {"success": False, "error": str(exc)}
