"""Bounded local raster preview export; never a production AFP conversion."""
import base64
import io
import os
from pathlib import Path
import re
import tempfile

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
from PIL import Image
from PIL.PngImagePlugin import PngInfo
import resvg_py

from afpstudio.security.paths import safe_output_path, PathError, publish_temporary


class ConversionError(ValueError):
    pass


def validate_svg(svg):
    if len(svg.encode("utf-8")) > 16 * 1024 * 1024:
        raise ConversionError("SVG exceeds the 16 MiB preview conversion limit.")
    try:
        root = ElementTree.fromstring(svg, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except (ElementTree.ParseError, DefusedXmlException) as exc:
        raise ConversionError("Unsafe or malformed SVG.") from exc
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1]
        if tag in ("script", "foreignObject", "style"):
            raise ConversionError("Active SVG content or stylesheets cannot be converted.")
        for name, value in node.attrib.items():
            name = name.rsplit("}", 1)[-1]
            if name.startswith("on"):
                raise ConversionError("SVG event handlers are forbidden.")
            if "url(" in value.lower():
                stripped = re.sub(r"url\(\s*['\"]?#[A-Za-z0-9_.:-]+['\"]?\s*\)", "", value, flags=re.I)
                if "url(" in stripped.lower():
                    raise ConversionError("SVG external CSS resources are forbidden.")
            if name == "href" and not value.startswith("#"):
                match = re.fullmatch(r"data:image/(?:png|jpeg);base64,([A-Za-z0-9+/=\s]+)", value)
                if not match:
                    raise ConversionError("Only embedded PNG/JPEG image resources are supported.")
                try:
                    image_bytes = base64.b64decode(match.group(1), validate=True)
                    with Image.open(io.BytesIO(image_bytes)) as image:
                        if image.width * image.height > 16_000_000:
                            raise ConversionError("Embedded image exceeds the pixel limit.")
                        image.verify()
                except (ValueError, OSError, Image.DecompressionBombError) as exc:
                    raise ConversionError("Embedded image failed validation.") from exc
    return root


def convert_preview(document, target, fmt, pages, *, dpi=144, overwrite=False, check_cancelled=None, publisher=None):
    check = check_cancelled or (lambda: None)
    if fmt not in ("png", "tiff", "pdf") or type(dpi) is not int or not 72 <= dpi <= 200:
        raise ConversionError("Select PNG, TIFF or raster PDF at 72-200 dpi.")
    if (not isinstance(pages, list) or not 1 <= len(pages) <= 20 or
        any(type(p) is not int or not 0 <= p < document.index.page_count for p in pages) or
        len(set(pages)) != len(pages) or (fmt == "png" and len(pages) != 1)):
        raise ConversionError("Select 1-20 distinct bracketed pages; PNG supports one page.")
    document.check_sources()
    target = Path(target)
    try:
        for source in document.source_signatures or {document.path: None}:
            target = safe_output_path(target, [target.parent.resolve()], source=source)
    except PathError as exc:
        raise ConversionError(str(exc)) from exc
    if target.exists() and not overwrite:
        raise ConversionError("Output exists; explicit replacement confirmation is required.")
    images = []
    pixels = 0
    temporary = None
    try:
        for page in pages:
            check()
            svg = document.page_svg(page)
            validate_svg(svg)
            # Page logical units are converted from the parsed PGD resolution.
            descriptor = document.analysis.page_descriptors.get(page)
            if descriptor and descriptor.width_mm and descriptor.height_mm:
                width = round(descriptor.width_mm / 25.4 * dpi)
                height = round(descriptor.height_mm / 25.4 * dpi)
            else:
                raise ConversionError("Physical page dimensions are unknown; conversion cannot select a reliable output size.")
            if not 1 <= width <= 5000 or not 1 <= height <= 5000:
                raise ConversionError("Preview dimensions exceed the 5000-pixel side limit.")
            pixels += width * height
            if pixels > 32_000_000:
                raise ConversionError("Selected pages exceed the 32-million-pixel aggregate limit.")
            png = resvg_py.svg_to_bytes(svg_string=svg, width=width, height=height,
                                       background="#ffffff", log_information=False)
            with Image.open(io.BytesIO(png)) as decoded:
                images.append(decoded.convert("RGB"))
        check()
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".afp-preview-", suffix=".part", dir=target.parent)
        os.close(fd)
        temporary = Path(name)
        if fmt == "png":
            metadata = PngInfo()
            metadata.add_text("Fidelity", "Preview only - not production validated")
            images[0].save(temporary, format="PNG", dpi=(dpi, dpi), pnginfo=metadata)
        elif fmt == "pdf":
            images[0].save(temporary, format="PDF", save_all=True, append_images=images[1:],
                resolution=dpi, title="AFP raster preview - not production validated")
        else:
            images[0].save(temporary, format="TIFF", save_all=True, append_images=images[1:],
                           dpi=(dpi, dpi), compression="tiff_deflate")
        document.check_sources()
        check()
        size = temporary.stat().st_size
        def commit():
            publish_temporary(temporary, target, overwrite=overwrite)
        (publisher or (lambda destination, action: action()))(target, commit)
        return {"path": str(target), "format": fmt, "pages": [p + 1 for p in pages],
                "bytes": size, "dpi": dpi, "fidelity": "preview_not_production_validated"}
    except FileExistsError as exc:
        raise ConversionError("Output appeared during conversion; confirm replacement explicitly.") from exc
    finally:
        for image in images:
            image.close()
        if temporary:
            temporary.unlink(missing_ok=True)
