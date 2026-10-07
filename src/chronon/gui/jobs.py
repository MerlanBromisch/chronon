"""Work in a child process: ``chronon <command> --json`` (contract in docs/app.md).

Cancel = kill the process: all its memory is returned, a crash in the core does not take the
window down, and the GUI thread never waits for the GIL."""

from __future__ import annotations

import json
import sys

from PySide6.QtCore import QObject, QProcess, Signal

CONTRACT = 2  # the --json version this GUI reads


def command(args: list[str]) -> tuple[str, list[str]]:
    """Program and arguments that run ``chronon <args>``, frozen or from source."""
    if getattr(sys, "frozen", False):
        return sys.executable, ["--worker", *args]
    return sys.executable, ["-m", "chronon.gui", "--worker", *args]


class Job(QObject):
    """One run of a chronon command. Emits ``plan``, ``progress``, then exactly one of
    ``result`` / ``failed`` (an ``error`` event, a crash, a cancel)."""

    plan = Signal(list)
    progress = Signal(dict)
    result = Signal(dict)
    failed = Signal(dict)  # the error event, or {"code": None, "message": ...}

    def __init__(self, args: list[str], log: str | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self.args = [*args, "--json"] + (["--log", log] if log else [])
        self.proc = QProcess(self)
        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.finished.connect(self._finished)
        self.stderr = ""
        self.proc.readyReadStandardError.connect(self._read_stderr)
        self.done = False
        self.cancelled = False

    def start(self) -> None:
        program, arguments = command(self.args)
        self.proc.start(program, arguments)

    def cancel(self) -> None:
        self.cancelled = True
        if self.proc.state() != QProcess.ProcessState.NotRunning:
            self.proc.kill()

    def wait(self, ms: int = 30_000) -> bool:
        return self.proc.waitForFinished(ms)

    def _read_stderr(self) -> None:
        self.stderr += bytes(self.proc.readAllStandardError()).decode(errors="replace")

    def _read(self) -> None:
        while self.proc.canReadLine():
            line = bytes(self.proc.readLine()).decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue  # not ours (a library printing to stdout)
            self._event(event)

    def _event(self, e: dict) -> None:
        if e.get("v") != CONTRACT:
            self._fail(None, f"chronon speaks --json version {e.get('v')}, the app {CONTRACT}")
            return
        kind = e.get("event")
        if kind == "plan":
            self.plan.emit(e["steps"])
        elif kind == "progress":
            self.progress.emit(e)
        elif kind == "result" and not self.done:
            self.done = True
            self.result.emit(e)
        elif kind == "error":
            self._fail(e.get("code"), e.get("message", ""), e)

    def _fail(self, code: str | None, message: str, event: dict | None = None) -> None:
        if not self.done:
            self.done = True
            self.failed.emit(event or {"code": code, "message": message})

    def _finished(self, exit_code: int, _status) -> None:
        self._read()
        if self.cancelled:
            self._fail("cancelled", "cancelled")
        elif not self.done:
            last = self.stderr.strip().splitlines()[-1:] or [f"exit code {exit_code}"]
            self._fail(None, last[0])
