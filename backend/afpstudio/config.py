"""Runtime configuration.

Every default is local-only: the HTTP server binds to the loopback
interface, no external service is contacted, and document content is
never transmitted off the machine.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw).expanduser() if raw else default


def _default_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME")
    if base:
        return Path(base) / "AFP-Production-Studio"
    return Path.home() / ".afp-production-studio"


class Config:
    """Settings bag resolved once at app creation."""

    def __init__(self) -> None:
        # "desktop" opens a native window; "web" serves the application
        # for a browser. The desktop shell is the same application on a
        # loopback port, so the two differ only in presentation.
        self.mode: str = (
            os.environ.get("AFP_STUDIO_MODE", "web").strip().lower() or "web"
        )
        self.host: str = os.environ.get("AFP_STUDIO_HOST", "127.0.0.1")
        self.port: int = _env_int("AFP_STUDIO_PORT", 8787)
        self.debug: bool = os.environ.get("AFP_STUDIO_DEBUG", "") == "1"

        self.data_dir: Path = _env_path("AFP_STUDIO_DATA_DIR", _default_data_dir())
        self.profile_dir: Path = self.data_dir / "printer_profiles"

        self.max_upload_bytes: int = _env_int(
            "AFP_STUDIO_MAX_UPLOAD_BYTES", 512 * 1024 * 1024
        )
        self.max_local_file_bytes: int = _env_int(
            "AFP_STUDIO_MAX_LOCAL_FILE_BYTES", 8 * 1024 * 1024 * 1024
        )

        self.page_cache_bytes: int = _env_int(
            "AFP_STUDIO_PAGE_CACHE_BYTES", 64 * 1024 * 1024
        )
        self.max_concurrent_jobs: int = _env_int("AFP_STUDIO_MAX_JOBS", 2)
        self.job_timeout_seconds: int = _env_int("AFP_STUDIO_JOB_TIMEOUT", 900)
        self.pdf_timeout_seconds: int = _env_int("AFP_STUDIO_PDF_TIMEOUT_SECONDS", 14400)

        self.adapter_jar: Path | None = None
        jar_override = os.environ.get("AFP_STUDIO_ALPHEUS_JAR")
        if jar_override:
            self.adapter_jar = Path(jar_override).expanduser()

        self.java_executable: str = os.environ.get("AFP_STUDIO_JAVA", "java")
        self.adapter_timeout_seconds: int = _env_int("AFP_STUDIO_ADAPTER_TIMEOUT", 180)
        self.adapter_max_heap: str = os.environ.get("AFP_STUDIO_ADAPTER_HEAP", "1024m")
        self.adapter_max_output_bytes: int = _env_int(
            "AFP_STUDIO_ADAPTER_MAX_OUTPUT", 512 * 1024 * 1024
        )

        # The Pyodide in-browser mode fetches a WebAssembly engine from a
        # CDN and wheels from PyPI. That breaks the "nothing leaves this
        # machine" guarantee, so it stays off unless explicitly enabled.
        self.allow_external_resources: bool = (
            os.environ.get("AFP_STUDIO_ALLOW_EXTERNAL", "") == "1"
        )

        self.temp_root: Path = (
            Path(os.environ.get("AFP_STUDIO_TEMP_DIR", tempfile.gettempdir()))
            / "afp-production-studio"
        )
        self.stale_temp_seconds: int = _env_int(
            "AFP_STUDIO_STALE_TEMP_SECONDS", 24 * 3600
        )

    @property
    def is_loopback(self) -> bool:
        return self.host in ("127.0.0.1", "::1", "localhost")

    def allowed_roots(self) -> list[Path]:
        """Directories a local AFP file may be read from."""
        roots = [
            Path.home() / "Documents",
            Path.home() / "Downloads",
            Path.home() / "Desktop",
            Path.cwd(),
        ]
        override = os.environ.get("AFP_STUDIO_ALLOWED_ROOTS")
        if override:
            roots = [
                Path(p).expanduser() for p in override.split(os.pathsep) if p
            ]
        seen: list[Path] = []
        for root in roots:
            try:
                resolved = root.resolve()
            except OSError:
                continue
            if resolved.is_dir() and resolved not in seen:
                seen.append(resolved)
        return seen

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.temp_root.mkdir(parents=True, exist_ok=True)


CONFIG = Config()
