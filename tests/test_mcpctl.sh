#!/usr/bin/env bash
# Test the Bash management harness without Docker, production databases or credentials.
set -Eeuo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
bash -n "$HERE/mcpctl"
bash "$HERE/mcpctl" help | grep -q 'MCP Control'

TMP="$(mktemp -d)"
trap 'rm -rf -- "$TMP"' EXIT
mkdir -p "$TMP/bin" "$TMP/rag"
cp "$HERE/mcpctl" "$TMP/mcpctl"
printf 'POSTGRES_PASSWORD=CI_SECRET\nPUBLIC_BASE_URL=https://example.com\nLOGIN_COOKIE_KEY=LOCAL_TEST\n' > "$TMP/.env"
printf 'RAG_DB_PASSWORD=CI_RAG_PASSWORD\nRAG_EMBEDDINGS_ENABLED=0\n' > "$TMP/rag/.env"
: > "$TMP/docker.log"

cat > "$TMP/bin/docker" <<'MOCK'
#!/usr/bin/env bash
set -eu
if [[ "${1:-} ${2:-}" == "compose version" ]]; then exit 0; fi
if [[ "${1:-} ${2:-}" == "network inspect" ]]; then exit 1; fi
if [[ " $* " == *" ps -aq mcp "* ]]; then exit 0; fi
printf '%s\n' "$*" >> "$MCPCTL_TEST_DOCKER_LOG"
MOCK
chmod +x "$TMP/bin/docker"
export PATH="$TMP/bin:$PATH"
export MCPCTL_TEST_DOCKER_LOG="$TMP/docker.log"
ctl() { bash "$TMP/mcpctl" "$@"; }

ctl tools list | grep 'ping.*enabled' >/dev/null
ctl tools list | grep 'inspect_site.*enabled' >/dev/null
ctl tools list | grep 'corpus_stats.*unavailable' >/dev/null
ctl tools list | grep 'list_docs.*unavailable' >/dev/null
ctl tools list | grep 'document_status.*unavailable' >/dev/null
ctl tools list | grep 'corpus_health.*unavailable' >/dev/null
ctl tools disable whoami
grep -qx 'MCP_DISABLED_TOOLS=whoami' "$TMP/.env"
ctl tools list | grep 'whoami.*disabled' >/dev/null
ctl tools disable whoami
[[ "$(grep -c '^MCP_DISABLED_TOOLS=' "$TMP/.env")" == 1 ]]
ctl tools disable search_docs
grep -qx 'MCP_DISABLED_TOOLS=whoami,search_docs' "$TMP/.env"
ctl tools enable whoami
grep -qx 'MCP_DISABLED_TOOLS=search_docs' "$TMP/.env"
if ctl tools disable unknown >/dev/null 2>&1; then
  echo "Expected unknown tool rejection" >&2
  exit 1
fi
ctl user enable admin@example.com
grep -q 'app.manage enable-user admin@example.com' "$TMP/docker.log"
ctl user disable admin@example.com
grep -q 'app.manage disable-user admin@example.com' "$TMP/docker.log"
ctl rag start
grep -q 'up -d --build database indexer' "$TMP/docker.log"
ctl rag logs
grep -q 'logs -f --tail=100 indexer' "$TMP/docker.log"
ctl rag enable
grep -q 'up -d --build indexer api' "$TMP/docker.log"
[[ -f "$TMP/.mcp-rag-enabled" ]]
test -n "$(grep '^RAG_API_TOKEN=' "$TMP/rag/.env")"
ctl tools list | grep 'get_text.*enabled' >/dev/null
ctl tools list | grep 'corpus_stats.*enabled' >/dev/null
ctl tools list | grep 'corpus_health.*enabled' >/dev/null
ctl rag disable
[[ ! -f "$TMP/.mcp-rag-enabled" ]]
ctl rag stop
grep -q 'stop indexer database' "$TMP/docker.log"
ctl tools list | grep 'get_text.*unavailable' >/dev/null
ctl tools list | grep 'corpus_stats.*unavailable' >/dev/null
ctl tools list | grep 'list_docs.*unavailable' >/dev/null
ctl tools list | grep 'document_status.*unavailable' >/dev/null
ctl tools list | grep 'corpus_health.*unavailable' >/dev/null
grep -qx 'POSTGRES_PASSWORD=CI_SECRET' "$TMP/.env"
grep -qx 'RAG_DB_PASSWORD=CI_RAG_PASSWORD' "$TMP/rag/.env"
ctl rag stats autorizare --second-term suspendare --mode all
grep -q 'run --rm cli stats autorizare --second-term suspendare --mode all' "$TMP/docker.log"
ctl rag scan --lexical-only
grep -q 'run --rm cli scan --lexical-only' "$TMP/docker.log"

echo 'PASS: Bash control harness (users, global tools, optional RAG lifecycle, secrets)'
