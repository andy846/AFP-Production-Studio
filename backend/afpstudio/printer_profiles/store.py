"""Loading and saving printer profiles."""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..resources import package_data_dir
from ..security.paths import atomic_write_bytes
from .schema import PrinterProfile, ProfileError, validate_profile

SAMPLE_PROFILE = "sample_profile.json"


class ProfileStore:
    """Reads and writes printer profiles in the user's profile folder."""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    def ensure_sample(self) -> Path:
        """Install the bundled sample profile the first time it is needed."""
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / SAMPLE_PROFILE
        if not target.exists():
            source = package_data_dir("afpstudio.printer_profiles") / "profiles" / SAMPLE_PROFILE
            target.write_bytes(source.read_bytes())
        return target

    def paths(self) -> list[Path]:
        if not self.directory.is_dir():
            return []
        return sorted(self.directory.glob("*.json"))

    def load(self, name: str) -> PrinterProfile:
        path = self._resolve(name)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ProfileError(f"{path.name} is not valid JSON: {exc}") from exc
        return validate_profile(data)

    def load_all(self) -> dict[str, PrinterProfile]:
        profiles: dict[str, PrinterProfile] = {}
        for path in self.paths():
            try:
                profile = self.load(path.stem)
            except (ProfileError, OSError):
                # A broken profile must not take the whole list down.
                continue
            profiles[path.stem] = profile
        return profiles

    def save(self, name: str, data: dict) -> PrinterProfile:
        profile = validate_profile(data)
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self._path(name)
        atomic_write_bytes(target, (json.dumps(profile.as_dict(), indent=2) + "\n").encode("utf-8"))
        return profile

    def _path(self, name: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,127}", name):
            raise ProfileError("Profile name must be a plain file name.")
        root = self.directory.resolve()
        path = (root / f"{name}.json").resolve()
        if path.parent != root:
            raise ProfileError("Profile path is outside the profile directory.")
        return path

    def _resolve(self, name: str) -> Path:
        path = self._path(name)
        if not path.exists():
            raise ProfileError(f"No printer profile named {name!r}.")
        return path
