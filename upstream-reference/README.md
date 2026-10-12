# Selected ContextForge upstream reference files

This directory contains **unmodified copies** of selected documentation and one sample configuration file from [IBM ContextForge](https://github.com/IBM/mcp-context-forge), retrieved from upstream `main` on 2026-10-12.

They cover OAuth, RBAC, security, Compose, container deployment, TLS, PostgreSQL upgrades, backups and tool catalog management.

They are **not** a standalone copy of the ContextForge application source. This repository runs the official upstream Docker image. Therefore no vendored Ansible, Cargo/Rust tooling, benchmarks, or unrelated upstream build assets are needed.

**Important:** The upstream docs refer to optional services, commands and settings that may not match our minimal Docker deployment. Our root `compose.yaml`, `.env.example` and host Nginx setup are authoritative for this repository.

License: Apache 2.0; see root `LICENSE`.
