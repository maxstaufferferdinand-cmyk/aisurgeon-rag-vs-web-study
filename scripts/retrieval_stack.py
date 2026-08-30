#!/usr/bin/env python3
"""Safe lifecycle wrapper for the pinned local PostgreSQL/pgvector stack."""

from __future__ import annotations

import argparse
import os
import secrets
import shutil
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env.retrieval"
COMPOSE_PATH = ROOT / "docker-compose.yml"


def ensure_env() -> None:
    if ENV_PATH.exists():
        mode = stat.S_IMODE(ENV_PATH.stat().st_mode)
        if mode & 0o077:
            ENV_PATH.chmod(0o600)
        return
    password = secrets.token_urlsafe(36)
    content = (
        "AISURGEON_DB_NAME=aisurgeon_retrieval\n"
        "AISURGEON_DB_USER=aisurgeon\n"
        f"AISURGEON_DB_PASSWORD={password}\n"
        "AISURGEON_DB_PORT=55432\n"
    )
    descriptor = os.open(ENV_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(content)


def compose(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    ensure_env()
    docker = docker_executable()
    command = [
        docker,
        "compose",
        "--env-file",
        ENV_PATH.name,
        "-f",
        COMPOSE_PATH.name,
        *arguments,
    ]
    return subprocess.run(command, cwd=ROOT, text=True, check=check)


def docker_executable() -> str:
    """Return a working Docker CLI without assuming one installation layout."""

    candidates: list[Path] = []
    for name in ("docker", "docker.exe"):
        resolved = shutil.which(name)
        if resolved:
            candidates.append(Path(resolved))
    candidates.extend(
        Path("/mnt/c/Users").glob(
            "*/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe"
        )
    )
    candidates.append(Path("/mnt/c/Program Files/Docker/Docker/resources/bin/docker.exe"))
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            probe = subprocess.run(
                [str(candidate), "version", "--format", "{{.Client.Version}}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if probe.returncode == 0:
            return str(candidate)
    raise RuntimeError("no working Docker CLI found")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("start", "stop", "status", "logs", "reset"))
    parser.add_argument(
        "--yes-really-reset",
        action="store_true",
        help="required for reset; removes only the named local retrieval volume",
    )
    args = parser.parse_args()
    if args.action == "start":
        compose("up", "-d", "--wait")
    elif args.action == "stop":
        compose("down")
    elif args.action == "status":
        compose("ps")
    elif args.action == "logs":
        compose("logs", "--tail", "200", "postgres")
    elif args.action == "reset":
        if not args.yes_really_reset:
            parser.error("reset requires --yes-really-reset")
        compose("down", "--volumes", "--remove-orphans")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"retrieval stack command failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
