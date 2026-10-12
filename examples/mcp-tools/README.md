# MCP tool examples (NOT deployed)

Reference implementations for writing separate MCP tool servers compatible with ContextForge. These examples are **not automatically registered, loaded or executed** by the production gateway. Never give the PostgreSQL example privileged database credentials.

* `basic_server.py` — input validation and deterministic tool output.
* `rest_server.py` — read-only REST API client with timeouts and upstream errors.
* `postgres_server.py` — read-only PostgreSQL query with parameter binding and least-privilege database access.
* `upstream_completion_server.py` — copied from IBM ContextForge's `mcp-servers/python/completion_test_server/server.py` for direct upstream reference.

The first three are **locally authored educational examples**, not copies from IBM. They use the official Python MCP SDK `FastMCP` interface. Install the dependencies listed in `requirements.txt` inside an isolated environment. They have not undergone deployment or integration testing.

For local STDIO tests run `python examples/mcp-tools/basic_server.py` through an MCP-aware client, not interactively in a terminal. REST requires `COURSES_API_BASE_URL`, and PostgreSQL requires `READONLY_DATABASE_URL` with an actual read-only role and suitable tables. These examples contain no OAuth server: authentication of client connections must be handled by ContextForge or another gateway.

**Security:** Do not expose the example servers directly to the public internet. Test independently, then register only selected servers/tools through the ContextForge admin interface and apply permissions there and at each downstream API/database.
