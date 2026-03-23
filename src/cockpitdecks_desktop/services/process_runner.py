from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class RunResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str


def run_command(command: list[str], cwd: str | Path | None = None) -> RunResult:
    proc = subprocess.run(
        command,
        cwd=str(cwd) if cwd is not None else None,
        text=True,
        capture_output=True,
        check=False,
    )
    return RunResult(
        command=command,
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )


def stream_shell_command(
    command: str,
    cwd: str | Path | None = None,
    on_output: Callable[[str], None] | None = None,
) -> int:
    """Run a shell command and stream merged stdout/stderr lines."""
    proc = subprocess.Popen(
        command,
        cwd=str(cwd) if cwd is not None else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        shell=True,
        executable="/bin/zsh",
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        if on_output is not None:
            on_output(line.rstrip("\n"))
    return proc.wait()
