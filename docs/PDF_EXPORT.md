# PDF export and workflow workspace

## Operator workflow

1. Open an AFP using the native picker, a permitted local/network path or Recent.
   A failed or cancelled open keeps the document already in use.
2. Use **Viewer**, **Media Preflight**, **Export** and **Production Report** for
   routine work. **Tools** contains Compare, Structure, Document Index,
   Resources, Engineering, Batch and Settings. All existing inspectors remain.
3. Click **Export PDF** in Viewer or press **Ctrl+Shift+E**. Choose all pages
   (default), the current page, or ranges such as `1-5,8,12-15`. Ranges are
   deduplicated and exported in original order. DPI choices are 150, 200 and 300.
4. Choose a separate PDF destination. In desktop mode use **Save as…**; manual
   output paths, including network locations, must be inside the configured
   `AFP_STUDIO_ALLOWED_ROOTS`. Existing output requires confirmation.
5. Follow the persistent task status while using another tool or document.
   Cancellation takes effect after the current native render step returns.
   Closing the source cancels its unfinished export; switching documents does not.
6. If content is omitted, export stops without publishing a PDF. Inspect its
   page/field, supply missing resources where possible, or explicitly choose
   **Accept limitations and export**. Structural incompleteness, unknown sizes,
   invalid diagnostics and image/raster failures cannot be approved away.
7. On completion review the page count, DPI, size, elapsed time and warnings.
   Desktop actions open the verified generated PDF or its folder. They refuse
   outputs changed after conversion. They never accept an arbitrary file path.

Preview completeness and media preflight have separate status labels. A media
Pass does not prove a complete rendering. The PDF is a local raster preview,
not searchable/vector output, PDF/A, PDF/X or a production RIP validation.
No cover, watermark or extra diagnostic page is inserted. Original page sizes
and orientation are retained where reliable page descriptors are available.

## Workspace and keyboard

Panel widths and Light/Dark appearance remain local preferences. **Reset
layout** restores panel widths and expands the panels. At 800px or narrower,
Document tree and Page details open one at a time as overlay drawers; Escape
closes them. Tools supports keyboard navigation and returns focus when closed.
Left/Right still turn pages outside editable fields. The Shortcuts dialog lists
the available commands. All fonts, icons and application resources are local.

## API

`POST /api/documents/{id}/pdf-export` requires the existing CSRF header:

```json
{
  "path": "C:\\Jobs\\output.pdf",
  "selection": "range",
  "page_range": "1-5,8",
  "dpi": 150,
  "overwrite": false,
  "allow_incomplete": false
}
```

`selection` is `all` (default), `current` (requires one-based `current_page`),
or `range`. The endpoint validates input and returns the existing job handle.
Overwrite conflicts return HTTP 409 with `overwrite_required` before queuing.
The final `.pdf` path is checked against all open sources and checked again
under the source-registration lock immediately before publication.

Poll `/api/jobs/{id}` and cancel through its existing `/cancel` endpoint.
Failures add `error_code` and bounded `error_details`, including one-based
page and available source offsets. `incomplete_preview` permits an explicit
retry with `allow_incomplete:true`; fatal errors do not.

Successful results include `path`, `format`, one-based `pages`, `page_count`,
`bytes`, `dpi`, `elapsed_seconds`, `fidelity`, `status`, `warning_count`,
`warning_pages` and `warnings`. Status is `completed` or
`completed_with_warnings`. At most 200 diagnostic rows are returned; counts
and affected page numbers cover the full export. Existing page inspection
provides that page's diagnostics. Output signatures bind desktop actions to
the originally generated file.

`POST /api/jobs/{id}/output-action` accepts `{"action":"open"}` or
`{"action":"reveal"}`, requires CSRF and a completed, unchanged PDF job,
and uses Windows file association without shell command construction.

The Engineering PDF endpoint retains its zero-based `pages` array and
existing result keys, using the same streaming engine. The Engineering UI
routes PDF users to Export. PNG and TIFF keep their existing limits.

## Resource limits and verification

PDF output retains one raster at a time; RGB pixels are compressed losslessly
in bands into Image XObjects. Only page references, cross-reference offsets
and bounded diagnostics remain between pages. The 20-page/aggregate pixel
limit is removed for PDF. Each page is limited to 5,000 pixels per side and
20 million pixels; excessive geometry fails without reducing DPI silently.

Only one PDF runs per application at a time within the existing worker pool.
Other operations can use another configured worker. PDF jobs have a separate
four-hour default deadline (`AFP_STUDIO_PDF_TIMEOUT_SECONDS=14400`); ordinary
jobs retain their existing deadline. Network failures and disk errors fail the
job without replacing the prior output. Temporary files are cleaned on normal
failure/cancellation; after a machine crash, same-directory `.afp-pdf-*.part`
files may need removal once no export is running.

Tests independently read and render generated synthetic PDFs with pypdf and
PyMuPDF from `backend/requirements-test.txt`. These are test-only tools and
are explicitly excluded from the Windows executable. The specialized writer
adds no runtime PDF library dependency. Actual measurements are recorded in
[PERFORMANCE.md](PERFORMANCE.md); test results are recorded separately from
production fidelity claims.
