<p align="center">
  <img src="docs-site/images/logo.svg" alt="SMKRV MCP Studio" width="80" />
</p>

<h1 align="center">SMKRV MCP Studio</h1>

<p align="center">
  <strong>Turn Your Database Into an AI Data Source.</strong>
</p>

<p align="center">
  <a href="https://smcps.net/"><img src="https://img.shields.io/badge/Website-smcps.net-F97316" alt="Website" /></a>
  <a href="https://docs.smcps.net/"><img src="https://img.shields.io/badge/Docs-docs.smcps.net-8B5CF6" alt="Documentation" /></a>
  <a href="https://hub.docker.com/r/smkrv/smkrv-mcp-studio"><img src="https://img.shields.io/docker/v/smkrv/smkrv-mcp-studio?sort=semver&label=Docker%20Hub&color=2496ED" alt="Docker Hub" /></a>
  <a href="https://hub.docker.com/r/smkrv/smkrv-mcp-studio"><img src="https://img.shields.io/docker/image-size/smkrv/smkrv-mcp-studio?sort=semver&label=Image%20Size&color=2496ED" alt="Docker Image Size" /></a>
  <a href="https://hub.docker.com/r/smkrv/smkrv-mcp-studio"><img src="https://img.shields.io/docker/pulls/smkrv/smkrv-mcp-studio?label=Pulls&color=2496ED" alt="Docker Pulls" /></a>
  <img src="https://img.shields.io/badge/Databases-10+-F97316" alt="Databases" />
  <img src="https://img.shields.io/badge/MCP-Protocol-8B5CF6" alt="MCP Protocol" />
  <img src="https://img.shields.io/badge/Arch-amd64%20%7C%20arm64-2496ED" alt="Multi-arch" />
  <a href="https://github.com/SMKRV-MCP-Studio/SMKRV-MCP-Studio/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-Proprietary-gray" alt="License" /></a>
</p>

---

Connect your analytical database, write SQL, and let Claude, GPT, or Cursor query your data through [MCP](https://modelcontextprotocol.io/).

No boilerplate. No backend code. One Docker image — 60 seconds to deploy.

## System Requirements

|  | Minimum | Recommended |
|--|---------|-------------|
| **CPU** | 2 cores | 4+ cores |
| **RAM** | 2 GB | 4 GB |
| **Disk** | 4 GB | 10 GB |
| **Arch** | x86_64 (amd64) or ARM64 | — |

**Minimum** runs the app with 1–2 database connections under light load. **Recommended** provides headroom for multiple connections, concurrent queries, and the ML-based prompt injection guard (~180 MB ONNX model).

For high-load scenarios (10+ connections, heavy concurrent queries): 4–8 CPU cores, 8 GB RAM, 20 GB disk.

## Quick Start

```bash
# 1. Pull the image
docker pull smkrv/smkrv-mcp-studio:latest

# 2. Generate secrets
export STUDIO_ENCRYPTION_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
export STUDIO_JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
export STUDIO_AGENT_SERVICE_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
export REDIS_PASSWORD=$(openssl rand -base64 32)

# 3. Run
docker run -d \
  --name mcp-studio \
  -p 3000:3000 \
  -p 80:80 \
  -p 443:443 \
  -e STUDIO_ENCRYPTION_KEY=$STUDIO_ENCRYPTION_KEY \
  -e STUDIO_JWT_SECRET=$STUDIO_JWT_SECRET \
  -e STUDIO_AGENT_SERVICE_TOKEN=$STUDIO_AGENT_SERVICE_TOKEN \
  -e REDIS_PASSWORD=$REDIS_PASSWORD \
  -v mcp-studio-data:/app/data \
  -v mcp-studio-generated:/shared/generated \
  -v mcp-studio-certs:/etc/letsencrypt \
  -v mcp-studio-redis:/data/redis \
  smkrv/smkrv-mcp-studio:latest

# 4. Open http://localhost:3000
```

> Ports 80/443 are optional — only needed for SSL/HTTPS via Let's Encrypt. Without SSL, port 3000 is sufficient.

## How It Works

```
┌──────────────┐    ┌──────────────────┐    ┌────────────────┐    ┌──────────────┐
│  1. Connect  │───▶│  2. Write SQL    │───▶│  3. Test Live  │───▶│ 4. AI Queries│
│  Database    │    │  Define Tools    │    │  Preview Data  │    │  Your Data   │
└──────────────┘    └──────────────────┘    └────────────────┘    └──────────────┘
 PostgreSQL          SELECT revenue         Execute against       Claude, GPT,
 ClickHouse          FROM analytics         real database         Cursor — via MCP
 BigQuery            WHERE date > :start    with parameters
 + 7 more
```

**Connect** your analytical database — PostgreSQL, ClickHouse, BigQuery, Snowflake, or 6 more. Credentials encrypted at rest.

**Write SQL** — define tools with parameterized queries. Each tool becomes an MCP endpoint.

**Test Live** — execute read-only queries against your real database with typed parameters.

**AI Queries Your Data** — deploy and any MCP-compatible AI assistant pulls live data from your database.

## Supported Databases

| Database | Driver | Status |
|----------|--------|--------|
| PostgreSQL | asyncpg | Stable |
| ClickHouse | clickhouse-connect | Stable |
| MySQL / MariaDB | aiomysql | Stable |
| SQLite | aiosqlite | Stable |
| Microsoft SQL Server | pymssql | Stable |
| Cassandra / ScyllaDB | cassandra-driver | Stable |
| Greenplum | asyncpg | Stable |
| Supabase (PostgreSQL) | asyncpg | Stable |
| Snowflake | snowflake-connector | Stable |
| Google BigQuery | google-cloud-bigquery | Stable |

## Architecture

Single Docker image, 5 services managed by supervisord:

```
┌─────────────────────────────────────────────────────┐
│              Ports 3000 / 80 / 443 (nginx)            │
│  ┌──────────┐  ┌──────────┐  ┌───────┐  ┌────────┐ │
│  │ Frontend │  │ Backend  │  │  MCP  │  │ Agent  │ │
│  │   SPA    │  │ FastAPI  │  │FastMCP│  │  MCP   │ │
│  │          │  │  :8000   │  │ :8080 │  │ :8090  │ │
│  └──────────┘  └────┬─────┘  └───┬───┘  └───┬────┘ │
│                     │            │           │      │
│                     └────────┬───┘           │      │
│                           ┌──┴──┐            │      │
│                           │Redis│────────────┘      │
│                           │:6379│                    │
│                           └─────┘                    │
└─────────────────────────────────────────────────────┘
```

| Service | Port | Role |
|---------|------|------|
| nginx | 3000, 80, 443 (exposed) | Reverse proxy, SPA, SSL |
| backend | 8000 | FastAPI REST API, codegen, auth |
| mcp | 8080 | Generated FastMCP v3 server |
| agent-mcp | 8090 | AI agent MCP interface (45 tools) |
| redis | 6379 | Queue, semaphores, metrics |

## Configuration

### Required Environment Variables

| Variable | Description |
|----------|-------------|
| `STUDIO_ENCRYPTION_KEY` | Fernet key for credential encryption |
| `STUDIO_JWT_SECRET` | JWT signing secret |
| `STUDIO_AGENT_SERVICE_TOKEN` | Shared token for agent-mcp ↔ backend communication |

### Optional Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_PASSWORD` | — (required) | Redis auth password. Generate: `openssl rand -base64 32` |
| `STUDIO_DATABASE_URL` | `sqlite+aiosqlite:///data/studio.db` | Backend database URL |
| `STUDIO_ADMIN_USERNAME` | `admin` | Admin login |
| `STUDIO_ADMIN_PASSWORD` | *(auto-generated)* | Admin password |
| `STUDIO_SSL_STAGING` | `false` | Use Let's Encrypt staging environment for testing |
| `STUDIO_EXTERNAL_HTTPS_PORT` | `443` | External HTTPS port for HTTP→HTTPS redirects |

### Volumes

| Volume | Container Path | Content |
|--------|---------------|---------|
| `mcp-studio-data` | `/app/data` | SQLite database, config |
| `mcp-studio-generated` | `/shared/generated` | Generated MCP server files |
| `mcp-studio-certs` | `/etc/letsencrypt` | SSL certificates (required for SSL persistence) |
| `mcp-studio-redis` | `/data/redis` | Redis persistence (metrics, tokens, rate limits) |

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
      STUDIO_ADMIN_PASSWORD: "your-secure-password"
    volumes:
      - studio-data:/app/data
      - studio-generated:/shared/generated
      - studio-certs:/etc/letsencrypt
      - studio-redis:/data/redis
    restart: unless-stopped

volumes:
  studio-data:
  studio-generated:
  studio-certs:
  studio-redis:
```

## Links

| | |
|---|---|
| Website | [smcps.net](https://smcps.net/) |
| Documentation | [docs.smcps.net](https://docs.smcps.net/) |
| Docker Hub | [smkrv/smkrv-mcp-studio](https://hub.docker.com/r/smkrv/smkrv-mcp-studio) |

## License

**Proprietary Software License Agreement v1.0**

Copyright (c) 2025-2026 Sergey Makarov. All rights reserved.

- **Non-commercial use** — free (personal, education, research, evaluation)
- **Commercial use** — requires separate license ([ms@smcps.net](mailto:ms@smcps.net))
- **No reverse engineering** of obfuscated components

Full license: [LICENSE](LICENSE)

---

<details>
<summary><strong>Open-Source Attribution</strong></summary>

SMKRV MCP Studio incorporates the following open-source components, each governed by its own license:

### Backend (Python)

| Package | License | Purpose |
|---------|---------|---------|
| [FastAPI](https://fastapi.tiangolo.com) | MIT | Web framework |
| [Uvicorn](https://www.uvicorn.org) | BSD-3 | ASGI server |
| [SQLAlchemy](https://sqlalchemy.org) | MIT | ORM / database toolkit |
| [aiosqlite](https://github.com/omnilib/aiosqlite) | MIT | Async SQLite driver |
| [Alembic](https://alembic.sqlalchemy.org) | MIT | Database migrations |
| [Pydantic](https://docs.pydantic.dev) | MIT | Data validation |
| [pydantic-settings](https://docs.pydantic.dev) | MIT | Configuration management |
| [Jinja2](https://jinja.palletsprojects.com) | BSD-3 | Template engine (codegen) |
| [asyncpg](https://github.com/MagicStack/asyncpg) | Apache-2.0 | PostgreSQL async driver |
| [aiomysql](https://github.com/aio-libs/aiomysql) | MIT | MySQL async driver |
| [cassandra-driver](https://github.com/datastax/python-driver) | Apache-2.0 | Cassandra driver |
| [snowflake-connector-python](https://docs.snowflake.com) | Apache-2.0 | Snowflake driver |
| [google-cloud-bigquery](https://cloud.google.com/bigquery) | Apache-2.0 | BigQuery client |
| [clickhouse-connect](https://clickhouse.com) | Apache-2.0 | ClickHouse driver |
| [pymssql](https://github.com/pymssql/pymssql) | LGPL-2.1 | MSSQL driver |
| [cryptography](https://cryptography.io) | Apache-2.0 / BSD-3 | Fernet encryption |
| [bcrypt](https://github.com/pyca/bcrypt) | Apache-2.0 | Password hashing |
| [PyJWT](https://pyjwt.readthedocs.io) | MIT | JWT tokens |
| [pyotp](https://github.com/pyauth/pyotp) | MIT | TOTP 2FA |
| [qrcode](https://github.com/lincolnloop/python-qrcode) | BSD-3 | QR code generation |
| [httpx](https://www.python-httpx.org) | BSD-3 | HTTP client |
| [websockets](https://websockets.readthedocs.io) | BSD-3 | WebSocket support |
| [redis-py](https://github.com/redis/redis-py) | MIT | Redis client |
| [ONNX Runtime](https://onnxruntime.ai) | MIT | ML inference (prompt guard) |
| [Tokenizers](https://github.com/huggingface/tokenizers) | Apache-2.0 | Text tokenization |
| [FastMCP](https://gofastmcp.com) | Apache-2.0 | MCP server framework |
| [maxminddb](https://github.com/maxmind/MaxMind-DB-Reader-python) | Apache-2.0 | GeoIP lookups |

### Frontend (JavaScript/TypeScript)

| Package | License | Purpose |
|---------|---------|---------|
| [React](https://react.dev) | MIT | UI framework |
| [React DOM](https://react.dev) | MIT | DOM rendering |
| [React Router](https://reactrouter.com) | MIT | Client-side routing |
| [React Flow (@xyflow/react)](https://reactflow.dev) | MIT | Flow/graph editor |
| [TanStack React Query](https://tanstack.com/query) | MIT | Server state management |
| [Recharts](https://recharts.org) | MIT | Charts and data viz |
| [Monaco Editor](https://microsoft.github.io/monaco-editor/) | MIT | Code editor |
| [Zustand](https://zustand-demo.pmnd.rs) | MIT | State management |
| [Zod](https://zod.dev) | MIT | Schema validation |
| [Lucide React](https://lucide.dev) | ISC | Icons |
| [Radix UI](https://radix-ui.com) | MIT | Accessible UI primitives |
| [Tailwind CSS](https://tailwindcss.com) | MIT | Utility-first CSS |
| [Vite](https://vite.dev) | MIT | Build tool |
| [TypeScript](https://typescriptlang.org) | Apache-2.0 | Type system |
| [sql-formatter](https://github.com/sql-formatter-org/sql-formatter) | MIT | SQL formatting |
| [class-variance-authority](https://cva.style) | Apache-2.0 | Component variants |
| [clsx](https://github.com/lukeed/clsx) | MIT | Class name utility |
| [tailwind-merge](https://github.com/dcastil/tailwind-merge) | MIT | Tailwind class merging |

### Infrastructure

| Component | License | Purpose |
|-----------|---------|---------|
| [Python](https://python.org) | PSF-2.0 | Runtime |
| [Node.js](https://nodejs.org) | MIT | Build toolchain |
| [nginx](https://nginx.org) | BSD-2 | Reverse proxy |
| [Redis](https://redis.io) | BSD-3 | In-memory store |
| [Docker](https://docker.com) | Apache-2.0 | Containerization |
| [supervisord](http://supervisord.org) | BSD-like | Process manager |
| [Geist Font](https://vercel.com/font) | OFL-1.1 | UI typography |
| [shadcn/ui](https://ui.shadcn.com) | MIT | UI component collection |

Nothing in the SMKRV MCP Studio license restricts your rights under these open-source licenses.

</details>
