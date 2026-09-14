# ChatGPT GitHub Actions Devbox

Two independent, manually started GitHub-hosted development environments connected to ChatGPT through **two different OpenAI Secure MCP Tunnels**:

- **Ubuntu Devbox** — `.github/workflows/chatgpt-devbox-ubuntu.yml`
- **macOS Devbox** — `.github/workflows/chatgpt-devbox-macos.yml`

Each job uses `timeout-minutes: 360`, so it can remain available for up to six hours. The Ubuntu and macOS workflows have separate tunnel IDs, separate tunnel API keys, and separate concurrency groups, so **both can run at the same time**.

## Required repository Actions secrets

Create these five secrets:

- `DEVBOX_GIT_PAT` — preferably a fine-grained PAT restricted to only the repositories and permissions the devboxes need.
- `OPENAI_TUNNEL_ID_UBUNTU` — tunnel ID dedicated to the Ubuntu devbox.
- `OPENAI_TUNNEL_API_KEY_UBUNTU` — runtime API key for the Ubuntu tunnel.
- `OPENAI_TUNNEL_ID_MACOS` — tunnel ID dedicated to the macOS devbox.
- `OPENAI_TUNNEL_API_KEY_MACOS` — runtime API key for the macOS tunnel.

The tunnel credentials are available only to `tunnel-client`. The stdio MCP subprocess is launched through `env -u CONTROL_PLANE_API_KEY -u OPENAI_TUNNEL_ID`, so shell/file tools do not inherit the OpenAI tunnel API key or tunnel ID.

## Keep the two ChatGPT plugins visually distinct

When adding the two tunnel-backed custom plugins/connectors in ChatGPT Developer Mode, give them unambiguous names such as:

- `GitHub Devbox Ubuntu`
- `GitHub Devbox macOS`

The MCP server itself also advertises a different server name for each platform. Every tool response includes an identity marker (`[UBUNTU DEVBOX]` or `[MACOS DEVBOX]`) and `server_info()` returns `devbox_platform` plus `devbox_name`. This gives the model several independent signals so it does not confuse the two machines.

## Independent execution

The workflows use different concurrency groups:

- Ubuntu: `chatgpt-devbox-ubuntu`
- macOS: `chatgpt-devbox-macos`

Starting another Ubuntu run cancels only the previous Ubuntu run. Starting another macOS run cancels only the previous macOS run. Starting one platform never cancels the other platform.

Therefore this is valid:

```text
Ubuntu Devbox  ── tunnel A ── ChatGPT plugin: GitHub Devbox Ubuntu
     running

macOS Devbox   ── tunnel B ── ChatGPT plugin: GitHub Devbox macOS
     running
```

Both can remain online simultaneously for up to six hours each.

## Runner identities

- Ubuntu workflow: `ubuntu-latest` (x64 standard GitHub-hosted runner)
- macOS workflow: `macos-latest` (currently Apple Silicon / arm64 on GitHub-hosted standard runners)

Always call `server_info()` when platform identity matters; it reports `RUNNER_OS`, `RUNNER_ARCH`, `platform.machine()`, repository, ref, SHA, and workspace.

## Tools exposed to ChatGPT

- `server_info`
- `list_directory`
- `read_file`
- `write_file`
- `exec_command`
- `start_command`
- `poll_command`
- `stop_command`

The file helpers are constrained to `GITHUB_WORKSPACE`. Shell commands are intentionally powerful and execute as the GitHub Actions runner user.

## Start

Open the repository's **Actions** tab and independently start either or both:

- **ChatGPT Devbox - Ubuntu**
- **ChatGPT Devbox - macOS**

Optionally provide a branch/tag/SHA in the workflow input.

## Security note

An arbitrary-shell MCP devbox is effectively remote code execution on the Actions VM. Use a fine-grained PAT, keep repository secrets minimal, and do not run untrusted pull-request code in this workflow. The runner is ephemeral, but credentials available to Git can still be accessed from the runner while the job is alive.
