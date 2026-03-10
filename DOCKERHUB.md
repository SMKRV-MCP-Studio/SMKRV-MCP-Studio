# SMKRV MCP Studio

**Turn Your Database Into an AI Data Source.**

Connect your analytical database, write SQL, and let Claude, GPT, or Cursor query your data through [MCP](https://modelcontextprotocol.io/). One Docker image — 60 seconds to deploy.

**Platforms:** `linux/amd64`, `linux/arm64`

## System Requirements

|  | Minimum | Recommended |
|--|---------|-------------|
| **CPU** | 2 cores | 4+ cores |
| **RAM** | 2 GB | 4 GB |
| **Disk** | 4 GB | 10 GB |
| **Arch** | `linux/amd64` or `linux/arm64` | — |

> **Minimum** runs the app with 1–2 connections under light load. **Recommended** handles multiple connections, concurrent queries, and the ML prompt injection guard (~180 MB ONNX model). For 10+ connections with heavy concurrency: 4–8 CPU, 8 GB RAM, 20 GB disk.

## Quick Start

```bash
# Generate secrets
export STUDIO_ENCRYPTION_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
export STUDIO_JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
export STUDIO_AGENT_SERVICE_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")

# Run
docker run -d \
  --name mcp-studio \
  -p 3000:3000 \
  -p 80:80 \
  -p 443:443 \
  -e STUDIO_ENCRYPTION_KEY=$STUDIO_ENCRYPTION_KEY \
  -e STUDIO_JWT_SECRET=$STUDIO_JWT_SECRET \
  -e STUDIO_AGENT_SERVICE_TOKEN=$STUDIO_AGENT_SERVICE_TOKEN \
  -v mcp-studio-data:/app/data \
  -v mcp-studio-generated:/shared/generated \
  -v mcp-studio-certs:/etc/letsencrypt \
  smkrv/smkrv-mcp-studio:latest
```

Open [http://localhost:3000](http://localhost:3000)

> Ports 80/443 are optional — only needed for SSL/HTTPS via Let's Encrypt. Without SSL, port 3000 is sufficient.

## How It Works

1. **Connect** — add your database (PostgreSQL, ClickHouse, BigQuery, Snowflake, etc.)
2. **Write SQL** — define parameterized queries as MCP tools
3. **Test** — execute against your real database with live preview
4. **Deploy** — AI assistants query your data through MCP protocol

## Supported Databases

PostgreSQL, ClickHouse, MySQL/MariaDB, SQLite, Microsoft SQL Server, Cassandra/ScyllaDB, Greenplum, Supabase, Snowflake, Google BigQuery

## What's Inside

Single image, 5 services (supervisord):

| Service | Port | Role |
|---------|------|------|
| nginx | 3000, 80, 443 (exposed) | Reverse proxy, SPA, SSL |
| backend | 8000 | FastAPI REST API |
| mcp | 8080 | Generated FastMCP v3 server |
| agent-mcp | 8090 | AI agent interface (44 tools) |
| redis | 6379 | Queue, metrics |

## Environment Variables

**Required:**

| Variable | Description |
|----------|-------------|
| `STUDIO_ENCRYPTION_KEY` | Fernet key for credential encryption |
| `STUDIO_JWT_SECRET` | JWT signing secret |
| `STUDIO_AGENT_SERVICE_TOKEN` | Shared token for agent-mcp ↔ backend communication |

**Optional:**

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_PASSWORD` | `studio-redis-secret` | Redis auth password |
| `STUDIO_DATABASE_URL` | `sqlite+aiosqlite:///data/studio.db` | Backend database |
| `STUDIO_ADMIN_USERNAME` | `admin` | Admin login |
| `STUDIO_ADMIN_PASSWORD` | *(auto-generated)* | Admin password |
| `STUDIO_SSL_STAGING` | `false` | Use Let's Encrypt staging environment |
| `STUDIO_EXTERNAL_HTTPS_PORT` | `443` | External HTTPS port for redirects |

## Volumes

| Volume | Path | Content |
|--------|------|---------|
| `mcp-studio-data` | `/app/data` | SQLite database, config |
| `mcp-studio-generated` | `/shared/generated` | Generated MCP server files |
| `mcp-studio-certs` | `/etc/letsencrypt` | SSL certificates (for persistence) |

## Docker Compose

```yaml
services:
  studio:
    image: smkrv/smkrv-mcp-studio:latest
    ports:
      - "3000:3000"
      - "80:80"
      - "443:443"
    environment:
      STUDIO_ENCRYPTION_KEY: "${STUDIO_ENCRYPTION_KEY}"
      STUDIO_JWT_SECRET: "${STUDIO_JWT_SECRET}"
      STUDIO_AGENT_SERVICE_TOKEN: "${STUDIO_AGENT_SERVICE_TOKEN}"
    volumes:
      - studio-data:/app/data
      - studio-generated:/shared/generated
      - studio-certs:/etc/letsencrypt
    restart: unless-stopped

volumes:
  studio-data:
  studio-generated:
  studio-certs:
```

## Tags

- `latest` — latest stable release
- `x.y.z` — specific version (e.g. `0.9.9`)
- `x.y` — latest patch of a minor version

## Links

- **Website:** [smcps.net](https://smcps.net/)
- **Documentation:** [docs.smcps.net](https://docs.smcps.net/)
- **Source & Issues:** [GitHub](https://github.com/SMKRV-MCP-Studio/SMKRV-MCP-Studio)
- **Report a Bug:** [GitHub Issues](https://github.com/SMKRV-MCP-Studio/SMKRV-MCP-Studio/issues)

## License

Proprietary Software License Agreement v1.0. Copyright (c) 2025-2026 Sergey Makarov.

- Non-commercial use — free (personal, education, research, evaluation)
- Commercial use — requires separate license ([ms@smcps.net](mailto:ms@smcps.net))

By pulling or running this image you accept the [license terms](https://github.com/SMKRV-MCP-Studio/SMKRV-MCP-Studio/blob/main/LICENSE).
