# Host Nginx: proxy all MCP and OAuth paths

Keep your existing TLS-enabled `server { ... }` for
`mcp.cursurituv.ro`. Inside that same HTTPS server block, proxy **all** paths
to the Docker host port:

```nginx
location / {
    proxy_pass http://127.0.0.1:4444;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header X-Forwarded-Host $host;
    proxy_set_header Connection "";
    proxy_buffering off;
    proxy_read_timeout 300s;
    client_max_body_size 16k;
}
```

Do not put Cloudflare Access or interactive CAPTCHA before the OAuth discovery
and token endpoints; machine-to-machine clients cannot navigate challenges.

Verify the Nginx configuration with `nginx -t` before reloading.

**Public endpoints:** `/mcp`, `/.well-known/...`, `/register`,
`/authorize`, `/token`, `/login`. All must share the same public HTTPS origin.
No database or internal Docker port should be exposed to the public internet.

For production, add Nginx `limit_req` controls at `/login` in addition to
the database-based IP/account lockout.
