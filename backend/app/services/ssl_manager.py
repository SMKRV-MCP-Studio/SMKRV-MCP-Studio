"""SSL Manager — certbot orchestration and certificate status checking."""

import asyncio
import json
import logging
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from app.config import settings
from app.services.crypto import decrypt

logger = logging.getLogger(__name__)


class SSLManager:
    """Manages Let's Encrypt certificates via certbot CLI."""

    def __init__(
        self,
        letsencrypt_dir: str | None = None,
        webroot: str | None = None,
        staging: bool | None = None,
    ):
        self.letsencrypt_dir = Path(letsencrypt_dir or settings.letsencrypt_dir)
        self.webroot = Path(webroot or settings.certbot_webroot)
        self.staging = staging if staging is not None else settings.ssl_staging

    # ------------------------------------------------------------------
    # Certificate issuance
    # ------------------------------------------------------------------

    async def issue_certificate(
        self,
        domains: list[str],
        email: str,
        challenge_type: str = "http-01",
        dns_provider: str | None = None,
        dns_credentials_encrypted: str | None = None,
        force_renew: bool = False,
    ) -> dict:
        """Run certbot to obtain or renew a certificate.

        Returns dict with keys: success, message, domains, cert_status.
        """
        if not domains:
            return {"success": False, "message": "No domains specified", "domains": []}

        # Ensure certbot working directories exist and are writable
        # (appuser cannot write to default /var/log/letsencrypt)
        work_dir = Path("/tmp/certbot-work")
        logs_dir = Path("/tmp/certbot-logs")
        work_dir.mkdir(parents=True, exist_ok=True)
        logs_dir.mkdir(parents=True, exist_ok=True)
        self.letsencrypt_dir.mkdir(parents=True, exist_ok=True)

        # Build base certbot command
        cmd = [
            "certbot", "certonly",
            "--non-interactive",
            "--agree-tos",
            "--email", email,
            "--config-dir", str(self.letsencrypt_dir),
            "--work-dir", str(work_dir),
            "--logs-dir", str(logs_dir),
        ]

        # Domain flags
        for domain in domains:
            cmd.extend(["-d", domain])

        if self.staging:
            cmd.append("--staging")

        if force_renew:
            cmd.append("--force-renewal")

        # Temp credential files to clean up
        temp_files: list[str] = []
        env = dict(os.environ)

        try:
            if challenge_type == "dns-01" and dns_provider:
                cmd = self._build_dns_challenge(
                    cmd, dns_provider, dns_credentials_encrypted, temp_files, env,
                )
            else:
                # HTTP-01 challenge (default)
                self.webroot.mkdir(parents=True, exist_ok=True)
                cmd.extend(["--webroot", "--webroot-path", str(self.webroot)])

            logger.info("Running certbot: %s", " ".join(cmd))

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, stderr = await proc.communicate()

            stdout_text = stdout.decode() if stdout else ""
            stderr_text = stderr.decode() if stderr else ""

            if proc.returncode == 0:
                logger.info("certbot succeeded for %s", domains)
                # Certbot stores SAN certs under the first domain's directory.
                # Nginx config references each domain's own path, so create
                # symlinks for additional SAN domains → primary domain dir.
                self._ensure_domain_symlinks(domains)
                status = self.get_cert_status(domains[0])
                return {
                    "success": True,
                    "message": f"Certificate issued for {', '.join(domains)}",
                    "domains": domains,
                    "cert_status": status.get("status", "issued"),
                }
            else:
                error_msg = stderr_text or stdout_text or f"certbot exit code {proc.returncode}"
                logger.error("certbot failed: %s", error_msg)
                return {
                    "success": False,
                    "message": f"certbot error: {error_msg.strip()[:500]}",
                    "domains": domains,
                }

        finally:
            # Clean up temp credential files
            for tf in temp_files:
                try:
                    os.unlink(tf)
                except OSError:
                    pass

    def _build_dns_challenge(
        self,
        cmd: list[str],
        dns_provider: str,
        dns_credentials_encrypted: str | None,
        temp_files: list[str],
        env: dict,
    ) -> list[str]:
        """Add DNS-01 challenge flags to the certbot command."""
        if dns_provider == "cloudflare":
            if not dns_credentials_encrypted:
                raise ValueError("Cloudflare API credentials are required for DNS-01")
            api_token = decrypt(dns_credentials_encrypted)
            # Write temp cloudflare.ini
            fd, path = tempfile.mkstemp(suffix=".ini", prefix="cloudflare_")
            with os.fdopen(fd, "w") as f:
                f.write(f"dns_cloudflare_api_token = {api_token}\n")
            os.chmod(path, 0o600)
            temp_files.append(path)
            cmd.extend([
                "--dns-cloudflare",
                "--dns-cloudflare-credentials", path,
                "--dns-cloudflare-propagation-seconds", "30",
            ])

        elif dns_provider == "route53":
            if dns_credentials_encrypted:
                creds = json.loads(decrypt(dns_credentials_encrypted))
                env["AWS_ACCESS_KEY_ID"] = creds.get("aws_access_key_id", "")
                env["AWS_SECRET_ACCESS_KEY"] = creds.get("aws_secret_access_key", "")
                if creds.get("aws_region"):
                    env["AWS_DEFAULT_REGION"] = creds["aws_region"]
            cmd.append("--dns-route53")

        else:
            raise ValueError(f"Unsupported DNS provider: {dns_provider}")

        return cmd

    # ------------------------------------------------------------------
    # SAN domain symlinks
    # ------------------------------------------------------------------

    def _ensure_domain_symlinks(self, domains: list[str]) -> None:
        """Create symlinks so each SAN domain resolves to the cert directory.

        Certbot stores a multi-domain (SAN) certificate under the first
        domain's directory (e.g. ``/live/adm.example.com/``).  Nginx config
        references ``/live/<domain>/fullchain.pem`` for *each* domain, so
        additional SAN domains need a symlink to the primary directory.
        """
        if len(domains) < 2:
            return

        live_dir = self.letsencrypt_dir / "live"
        primary = live_dir / domains[0]

        if not primary.is_dir():
            logger.warning("Primary cert dir %s missing — skipping symlinks", primary)
            return

        for extra in domains[1:]:
            link = live_dir / extra
            if link.exists() or link.is_symlink():
                # Already exists (previous run or manual fix) — skip
                logger.debug("Cert path %s already exists, skipping", link)
                continue
            try:
                link.symlink_to(primary)
                logger.info("Created cert symlink %s → %s", link, primary)
            except OSError as exc:
                logger.error("Failed to create cert symlink %s: %s", link, exc)

    # ------------------------------------------------------------------
    # Certificate status
    # ------------------------------------------------------------------

    def get_cert_status(self, domain: str) -> dict:
        """Read cert from disk and return status info.

        Returns dict with: status, domains, issuer, valid_from, valid_until, days_remaining.
        """
        cert_path = self.letsencrypt_dir / "live" / domain / "fullchain.pem"

        if not cert_path.exists():
            return {"status": "none", "domains": [], "issuer": None}

        try:
            from cryptography import x509

            cert_data = cert_path.read_bytes()
            cert = x509.load_pem_x509_certificate(cert_data)

            # Extract SAN domains
            try:
                san_ext = cert.extensions.get_extension_for_class(
                    x509.SubjectAlternativeName
                )
                domains = san_ext.value.get_values_for_type(x509.DNSName)
            except x509.ExtensionNotFound:
                domains = []

            # Issuer
            issuer_parts = []
            for attr in cert.issuer:
                issuer_parts.append(f"{attr.oid._name}={attr.value}")
            issuer_str = ", ".join(issuer_parts)

            now = datetime.now(UTC)
            valid_from = cert.not_valid_before_utc
            valid_until = cert.not_valid_after_utc
            days_remaining = (valid_until - now).days

            if days_remaining < 0:
                status = "expired"
            elif days_remaining < 30:
                status = "expiring_soon"
            else:
                status = "issued"

            return {
                "status": status,
                "domains": domains,
                "issuer": issuer_str,
                "valid_from": valid_from.isoformat(),
                "valid_until": valid_until.isoformat(),
                "days_remaining": days_remaining,
            }
        except Exception as e:
            logger.error("Failed to read certificate at %s: %s", cert_path, e)
            return {
                "status": "error",
                "domains": [],
                "issuer": None,
                "message": str(e),
            }
