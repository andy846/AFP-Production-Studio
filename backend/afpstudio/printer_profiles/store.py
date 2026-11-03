"""Loading and saving printer profiles."""

from __future__ import annotations

import json
from pathlib import Path

from ..resources import package_data_dir
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
        target = self.directory / f"{name}.json"
        target.write_text(
            json.dumps(profile.as_dict(), indent=2) + "\n", encoding="utf-8"
        )
        return profile

    def _resolve(self, name: str) -> Path:
        safe = Path(name).name
        if not safe or safe != name:
            raise ProfileError("Profile name must be a plain file name.")
        path = self.directory / f"{safe}.json"
        if not path.exists():
            raise ProfileError(f"No printer profile named {name!r}.")
        return path
