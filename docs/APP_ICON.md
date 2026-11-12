# Application icon

The application uses `frontend/static/studio-icon.ico` for the native window,
Windows taskbar and browser tab. PyInstaller embeds the same icon into the
EXE; file associations already use the EXE's first icon resource.
The native process sets `Andy846.AFPProductionStudio` as its Windows
AppUserModelID before creating a window, including source-based launches.

`frontend/static/studio-icon.png` is the original RGBA artwork, generated
with the built-in imagegen tool. The final prompt was:

> Use case: logo-brand. Asset type: Windows desktop application icon for AFP
> Production Studio, an AFP document viewer and print production inspection
> tool. Create ONE centered, simple professional flat app icon, square 1:1
> composition, transparent background outside a rounded navy square tile.
> The tile contains a bold white document with a folded upper-right corner
> and two broad navy text lines, with a distinct teal small stack of print
> sheets at the lower right. Strong geometric silhouette, thick clean shapes,
> generous spacing, crisp edges, readable at 24 and 32 pixels. Color palette
> deep navy, white, teal. Front-on flat design, no perspective, no photorealism,
> no lettering, no words, no watermark, no decorative background, no tiny
> details. Render a single finished icon filling most of the canvas.

To regenerate only the Windows format from the saved original:

```powershell
python scripts/build_icon.py
```

This local conversion preserves the PNG and creates ICO frames at 16, 20,
24, 32, 40, 48, 64, 128 and 256 pixels. Build the EXE using the regular
`scripts/build_exe.py` command. Both the ICO and original PNG are included
in the existing static-asset bundle, so no external file or network request
is needed at runtime.

Windows can cache an older pinned shortcut's icon. If a previously pinned
copy still shows the old icon, unpin it, launch the new EXE and pin its
running window again. No registry default or pin is changed automatically.

Verification: 43 desktop, security and file-association tests passed. On the
frozen Windows EXE, the shell-extracted 32px icon and the live window's
32px/64px icons matched the ICO's pixels exactly. The synthetic AFP startup,
warning navigation and clean shutdown smoke test also passed.
