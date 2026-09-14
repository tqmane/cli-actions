from __future__ import annotations

import json
import os
import platform
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from mcp.server import MCPServer

ROOT = Path(os.environ.get("DEVBOX_ROOT", os.getcwd())).resolve()
DEVBOX_PLATFORM = os.environ.get("DEVBOX_PLATFORM", "unknown").strip().lower() or "unknown"
DEVBOX_DISPLAY_NAME = os.environ.get("DEVBOX_DISPLAY_NAME", f"GitHub Actions Devbox - {DEVBOX_PLATFORM}")
DEVBOX_MARKER = f"[{DEVBOX_PLATFORM.upper()} DEVBOX]"
SESSION_DIR = ROOT / ".devbox" / "sessions"
SESSION_DIR.mkdir(parents=True, exist_ok=True)

mcp = MCPServer(DEVBOX_DISPLAY_NAME)
_sessions: dict[str, dict[str, Any]] = {}
_sessions_lock = threading.Lock()


def _resolve(path: str | None) -> Path:
    target = ROOT if not path else Path(path)
    if not target.is_absolute():
        target = ROOT / target
    target = target.resolve()
    try:
        target.relative_to(ROOT)
    except ValueError as exc:
        raise ValueError(f"Path must stay inside DEVBOX_ROOT ({ROOT}): {target}") from exc
    return target


def _shell() -> str:
    for candidate in ("/bin/bash", "/bin/zsh", "/bin/sh"):
        if Path(candidate).exists():
            return candidate
    return os.environ.get("SHELL", "/bin/sh")


def _tail(path: Path, max_chars: int) -> str:
    if not path.exists():
        return ""
    data = path.read_bytes()
    text = data[-max_chars:].decode("utf-8", errors="replace")
    if len(data) > max_chars:
        return "[output truncated to tail]\n" + text
    return text


@mcp.tool()
def server_info() -> str:
    """Return information about the ephemeral GitHub Actions development runner."""
    info = {
        "identity": DEVBOX_MARKER,
        "devbox_platform": DEVBOX_PLATFORM,
        "devbox_name": DEVBOX_DISPLAY_NAME,
        "root": str(ROOT),
        "os": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "github_repository": os.environ.get("GITHUB_REPOSITORY"),
        "github_ref": os.environ.get("GITHUB_REF"),
        "github_sha": os.environ.get("GITHUB_SHA"),
        "runner_os": os.environ.get("RUNNER_OS"),
        "runner_arch": os.environ.get("RUNNER_ARCH"),
        "workspace": os.environ.get("GITHUB_WORKSPACE"),
    }
    return json.dumps(info, indent=2, ensure_ascii=False)


@mcp.tool()
def list_directory(path: str = ".", max_entries: int = 500) -> str:
    """List files and directories under the development workspace."""
    target = _resolve(path)
    if not target.is_dir():
        raise ValueError(f"Not a directory: {target}")
    entries = []
    for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))[:max_entries]:
        rel = child.relative_to(ROOT)
        entries.append({
            "path": str(rel),
            "type": "dir" if child.is_dir() else "file",
            "size": None if child.is_dir() else child.stat().st_size,
        })
    return json.dumps({"identity": DEVBOX_MARKER, "devbox_platform": DEVBOX_PLATFORM, "entries": entries}, indent=2, ensure_ascii=False)


@mcp.tool()
def read_file(path: str, start_line: int = 1, end_line: int = 400) -> str:
    """Read a UTF-8 text file from the development workspace by line range."""
    if start_line < 1 or end_line < start_line:
        raise ValueError("Invalid line range")
    target = _resolve(path)
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    selected = lines[start_line - 1 : end_line]
    body = "\n".join(f"{i}: {line}" for i, line in enumerate(selected, start=start_line))
    return f"{DEVBOX_MARKER} {target.relative_to(ROOT)}\n{body}"


@mcp.tool()
def write_file(path: str, content: str, create_parents: bool = True) -> str:
    """Create or replace a UTF-8 text file inside the development workspace."""
    target = _resolve(path)
    if create_parents:
        target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"{DEVBOX_MARKER} wrote {target.relative_to(ROOT)} ({len(content.encode('utf-8'))} bytes)"


@mcp.tool()
def exec_command(
    cmd: str,
    workdir: str = ".",
    timeout_seconds: int = 120,
    max_output_chars: int = 60000,
) -> str:
    """Run a shell command synchronously in the development workspace."""
    if timeout_seconds < 1 or timeout_seconds > 1800:
        raise ValueError("timeout_seconds must be between 1 and 1800")
    cwd = _resolve(workdir)
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        shell=True,
        executable=_shell(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        timeout=timeout_seconds,
        env=os.environ.copy(),
    )
    output = proc.stdout or ""
    if len(output) > max_output_chars:
        output = "[output truncated to tail]\n" + output[-max_output_chars:]
    return json.dumps(
        {"identity": DEVBOX_MARKER, "devbox_platform": DEVBOX_PLATFORM, "exit_code": proc.returncode, "workdir": str(cwd.relative_to(ROOT)), "output": output},
        ensure_ascii=False,
    )


@mcp.tool()
def start_command(cmd: str, workdir: str = ".") -> str:
    """Start a long-running shell command and return a session id for later polling."""
    cwd = _resolve(workdir)
    session_id = uuid.uuid4().hex[:16]
    log_path = SESSION_DIR / f"{session_id}.log"
    log_handle = log_path.open("ab", buffering=0)
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        shell=True,
        executable=_shell(),
        stdin=subprocess.DEVNULL,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env=os.environ.copy(),
    )
    with _sessions_lock:
        _sessions[session_id] = {
            "process": proc,
            "log_path": log_path,
            "log_handle": log_handle,
            "cmd": cmd,
            "cwd": cwd,
            "started_at": time.time(),
        }
    return json.dumps({"identity": DEVBOX_MARKER, "devbox_platform": DEVBOX_PLATFORM, "session_id": session_id, "pid": proc.pid, "log": str(log_path.relative_to(ROOT))})


@mcp.tool()
def poll_command(session_id: str, max_output_chars: int = 60000) -> str:
    """Poll a command started with start_command and return status plus the tail of its output."""
    with _sessions_lock:
        session = _sessions.get(session_id)
    if session is None:
        log_path = SESSION_DIR / f"{session_id}.log"
        if log_path.exists():
            return json.dumps({"identity": DEVBOX_MARKER, "devbox_platform": DEVBOX_PLATFORM, "session_id": session_id, "status": "unknown_or_finished", "output": _tail(log_path, max_output_chars)}, ensure_ascii=False)
        raise ValueError(f"Unknown session: {session_id}")
    proc: subprocess.Popen[Any] = session["process"]
    code = proc.poll()
    status = "running" if code is None else "finished"
    if code is not None:
        session["log_handle"].close()
    return json.dumps(
        {
            "identity": DEVBOX_MARKER,
            "devbox_platform": DEVBOX_PLATFORM,
            "session_id": session_id,
            "status": status,
            "exit_code": code,
            "output": _tail(session["log_path"], max_output_chars),
        },
        ensure_ascii=False,
    )


@mcp.tool()
def stop_command(session_id: str, grace_seconds: int = 5) -> str:
    """Stop a command started with start_command."""
    with _sessions_lock:
        session = _sessions.get(session_id)
    if session is None:
        raise ValueError(f"Unknown session: {session_id}")
    proc: subprocess.Popen[Any] = session["process"]
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=max(1, grace_seconds))
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)
    session["log_handle"].close()
    return json.dumps({"identity": DEVBOX_MARKER, "devbox_platform": DEVBOX_PLATFORM, "session_id": session_id, "exit_code": proc.returncode, "output": _tail(session["log_path"], 60000)}, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run()
