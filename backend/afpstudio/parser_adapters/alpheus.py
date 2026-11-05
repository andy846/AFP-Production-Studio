"""Runs the Java Alpheus parser as a separate process.

The adapter is deliberately kept at arm's length:

* it runs as its own process, so a parser bug cannot take the server
  down and a crash is visible rather than silent;
* it is started with an argument array and ``shell=False``, so a path
  with spaces or quotes cannot turn into a command;
* it has a heap limit and a wall clock timeout;
* its output is read with a cap, so a runaway parser cannot exhaust the
  server's memory.

If the adapter is not built, the caller gets a clean
``parser_unavailable`` error instead of an exception, and Studio keeps
working with its own parser.
"""

from __future__ import annotations

import json
import hashlib
import os
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any

from .contract import AdapterError, AdapterResult, validate_result
from .jsonl import read_jsonl

#: Error code used when the Java side is simply not there.
UNAVAILABLE = "parser_unavailable"


def _read_capped(stream, limit: int, destination=None) -> tuple[bytes, bool]:
    """Read a stream, stopping once ``limit`` bytes have arrived."""
    chunks: list[bytes] = []
    total = 0
    overflow = False
    while True:
        chunk = stream.read(65536)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            overflow = True
            chunk = chunk[: max(0, limit - (total - len(chunk)))]
            if destination is not None:
                destination.write(chunk)
            else:
                chunks.append(chunk)
            break
        if destination is not None:
            destination.write(chunk)
        else:
            chunks.append(chunk)
    return b"".join(chunks), overflow


class AlpheusAdapter:
    """Invokes the bundled Java adapter."""

    def __init__(
        self,
        jar: Path | None,
        java_executable: str = "java",
        timeout_seconds: int = 180,
        max_heap: str = "1024m",
        max_output_bytes: int = 256 * 1024 * 1024,
    ) -> None:
        self.jar = Path(jar) if jar else None
        self.java_executable = java_executable
        self.timeout_seconds = timeout_seconds
        self.max_heap = max_heap
        self.max_output_bytes = max_output_bytes

    def describe(self) -> dict[str, Any]:
        """Explain whether the adapter can be used."""
        if self.jar is None:
            return {
                "available": False,
                "reason": (
                    "No Alpheus adapter jar is configured. Run "
                    "scripts/setup_deps.ps1 to build it, or set "
                    "AFP_STUDIO_ALPHEUS_JAR."
                ),
            }
        if not self.jar.is_file():
            return {
                "available": False,
                "reason": f"The adapter jar {self.jar} does not exist.",
            }
        return {
            "available": True,
            "jar": str(self.jar),
            "java": self.java_executable,
        }

    def available(self) -> bool:
        """Is the adapter ready to run?"""
        return bool(self.describe()["available"])

    def command(self, path: Path) -> list[str]:
        """Build the argument array. Never a shell string."""
        return [
            self.java_executable,
            f"-Xmx{self.max_heap}",
            "-Djava.awt.headless=true",
            "-jar",
            str(self.jar),
            "--input",
            str(path),
            "--format",
            "jsonl",
        ]

    def parse(
        self,
        path: Path,
        timeout: int | None = None,
        cancel: threading.Event | None = None,
    ) -> AdapterResult:
        """Parse ``path`` with the Java parser and return the answer."""
        description = self.describe()
        if not description["available"]:
            raise AdapterError(description["reason"], UNAVAILABLE)

        path = Path(path)
        try:
            with path.open("rb") as source:
                expected_digest = hashlib.file_digest(source, "sha256").hexdigest()
        except OSError:
            expected_digest = None  # Startup error is still reported by the subprocess.
        deadline = timeout if timeout is not None else self.timeout_seconds
        command = self.command(path)

        creation_flags = 0
        if os.name == "nt":
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        try:
            process = subprocess.Popen(  # noqa: S603 - argument array, no shell
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                cwd=str(path.parent),
                creationflags=creation_flags,
            )
        except FileNotFoundError as exc:
            raise AdapterError(
                f"The Java executable {self.java_executable!r} was not found. "
                "Install a JDK 21 or set AFP_STUDIO_JAVA.",
                UNAVAILABLE,
            ) from exc
        except OSError as exc:
            raise AdapterError(
                f"The Alpheus adapter could not be started: {exc}",
                UNAVAILABLE,
            ) from exc

        captured: dict[str, Any] = {}
        overflow = threading.Event()

        def read_stdout() -> None:
            try:
                with tempfile.TemporaryFile() as spool:
                    _, captured["stdout_overflow"] = _read_capped(process.stdout, self.max_output_bytes, spool)
                    if captured["stdout_overflow"]:
                        overflow.set()
                        return
                    spool.seek(0)
                    first = spool.readline(16 * 1024 * 1024 + 1)
                    if len(first) > 16 * 1024 * 1024:
                        raise AdapterError("Adapter metadata line exceeds the limit.")
                    try:
                        header = json.loads(first)
                    except (ValueError, UnicodeError) as exc:
                        raise AdapterError("Invalid adapter JSON.") from exc
                    spool.seek(0)
                    if isinstance(header, dict) and header.get("type") == "header":
                        captured["result"] = read_jsonl(spool, expected_digest=expected_digest)
                    else:
                        # Contract 1 is retained for existing development adapters.
                        legacy = spool.read(16 * 1024 * 1024 + 1)
                        if len(legacy) > 16 * 1024 * 1024:
                            raise AdapterError("Legacy adapter result exceeds the limit.")
                        captured["result"] = validate_result(json.loads(legacy))
            except (AdapterError, ValueError, UnicodeError, OSError) as exc:
                captured["error"] = exc if isinstance(exc, AdapterError) else AdapterError("Invalid adapter result.")

        def read_stderr() -> None:
            captured["stderr"], captured["stderr_overflow"] = _read_capped(
                process.stderr, 8 * 1024 * 1024
            )
            if captured["stderr_overflow"]:
                overflow.set()

        threads = [
            threading.Thread(target=read_stdout, daemon=True),
            threading.Thread(target=read_stderr, daemon=True),
        ]
        for thread in threads:
            thread.start()

        cancelled = False
        timed_out = False
        waited = 0.0
        step = 0.2
        while process.poll() is None:
            if cancel is not None and cancel.is_set():
                cancelled = True
                self._stop(process)
                break
            if overflow.is_set():
                # A parser that floods its output is stopped immediately
                # instead of being left to fill the pipe until the timeout.
                self._stop(process)
                break
            if waited >= deadline:
                timed_out = True
                self._stop(process)
                break
            threading.Event().wait(step)
            waited += step

        for thread in threads:
            thread.join(timeout=5)

        if cancelled:
            raise AdapterError(
                "The Alpheus adapter was cancelled.", "cancelled"
            )
        if timed_out:
            raise AdapterError(
                f"The Alpheus adapter did not finish within {deadline} seconds.",
                "timeout",
            )

        stderr = captured.get("stderr", b"").decode("utf-8", errors="replace")
        if captured.get("stdout_overflow"):
            raise AdapterError(
                "The Alpheus adapter produced more output than Studio allows.",
                "output_too_large",
            )
        if process.returncode != 0:
            raise AdapterError(
                f"The Alpheus adapter exited with code {process.returncode}."
                + (f" {stderr.strip()[:500]}" if stderr.strip() else ""),
                "adapter_failed",
            )

        if "error" in captured:
            raise captured["error"]
        if "result" not in captured:
            raise AdapterError("Adapter output reader did not complete.")
        result = captured["result"]
        if result.input_sha256:
            with path.open("rb") as source:
                if hashlib.file_digest(source, "sha256").hexdigest() != result.input_sha256:
                    raise AdapterError("Source changed during independent parsing.", "source_changed")
        return result

    @staticmethod
    def _stop(process: subprocess.Popen) -> None:
        """Stop the adapter, escalating to a kill if it ignores the polite ask."""
        try:
            process.terminate()
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
                process.wait(timeout=5)
            except Exception:  # noqa: BLE001 - nothing else to do
                pass
        except OSError:
            pass
