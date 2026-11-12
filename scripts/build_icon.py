"""Package the original RGBA artwork as a multi-resolution Windows icon.

Run with the project's Python environment; Pillow is a runtime dependency.
The original PNG is retained unchanged. No image-generation service is called.
"""
from pathlib import Path

from PIL import Image


def main():
    assets = Path(__file__).resolve().parents[1] / "frontend" / "static"
    with Image.open(assets / "studio-icon.png") as source:
        if source.width != source.height or source.mode != "RGBA":
            raise ValueError("Icon artwork must be a square RGBA PNG.")
        source.save(assets / "studio-icon.ico", format="ICO",
                    sizes=[(size, size) for size in (16, 20, 24, 32, 40, 48, 64, 128, 256)])
    print(assets / "studio-icon.ico")


if __name__ == "__main__":
    main()
