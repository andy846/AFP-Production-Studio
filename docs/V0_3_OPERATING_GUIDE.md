# Studio 0.3 development build: Windows UAT

Launch `AFP-Production-Studio.exe` on Windows 11 with WebView2. No separate
server, browser tab, Python installation, Java or Go is required. The shell
starts a loopback service and stops it when its window closes. If running from
source, use `scripts/run.ps1 -Desktop`; `--web` remains available. See
[Windows setup](SETUP_WINDOWS.md) for permitted paths and file association.

1. **Open AFP**: choose a local or network file with Browse. For the supported
   RDW variant select **RDW: one 5A field per record** under Advanced first.
   Java cross-check is available for standard AFP with a rebuilt optional
   adapter. It is intentionally unavailable for RDW.
2. **Document Index**: select raw TLE, documents, groups or positioned text.
   Choose EBCDIC 037/500 explicitly for preview text; it is a fallback, not a
   declaration that every code page is known. Coordinates are page logical
   units. Offsets identify original structured fields.
3. **Engineering**: use a different output path. Copy verifies byte identity;
   edit accepts e.g. `[{"offset":123,"medium_map":"MAP02"}]`. For raw TLE
   use `offset`, `triplet_offset` and `raw_hex`; preserve the first two encoding
   or format bytes and the full original triplet payload length. Review the
   hash/diff report and reopen the output before evaluating its media results.
4. **Extract/merge**: extract an entire indexed document or merge complete
   standard-AFP documents in order. Only the validated self-contained subset
   is allowed. The tool refuses inherited print-file resources, unsupported
   fields, malformed brackets and map-name collisions. It does not rename
   resources or split arbitrary page ranges.
5. **Preview**: export one PNG page or up to 20 TIFF/PDF pages at the supported
   72–200 dpi, subject to 32 million aggregate pixels and known page dimensions.
   PDF is raster, not searchable/vector output; the outputs have preview
   fidelity and may differ from the RIP. They are not AFP conversion proofs.
6. **Batch**: enter permitted paths (one per line). Uses the selected profile
   and explicit framing, with no external FormDefs or independent parser.
   The result distinguishes complete counts from partial counts; actual
   production remains unknown.
7. **Production Report**: expand the journal. Record external expectations
   and observations with distinct units and evidence references. Printing
   means net good sheets accepted. Record rejects and reprints separately;
   nonzero values need a later external reconciliation reference. The gate
   compares sheets to sheets and mailpieces to mailpieces and requires a
   complete passing media preflight. Sign-off is a manual record, not a
   digitally signed approval or a command to the printer. Later changes
   supersede sign-off. CSV/Excel include the evidence journal.

Add these optional properties to a Printer Profile JSON in Settings. These
values are synthetic examples and must be replaced with your own evidence:

```json
{
  "resource_inventory": [
    {"kind":"overlay","name":"OVL01","evidence_ref":"Synthetic RIP inventory",
     "verified_by":"UAT Operator","verified_at":"2026-11-06"}
  ],
  "mailpiece_rule": {
    "page_group_name":"LETTER","evidence_ref":"Synthetic customer rule",
    "verified_by":"UAT Operator","verified_at":"2026-11-06"
  }
}
```

Add them alongside `schema_version`, `printer_profile`, `media_maps`, and
`rules`; the sample alone is not a full profile. An inventory attestation
does not prove embedded scope or active RIP configuration. A candidate
mailpiece count is calculated only when the rule covers the indexed pages
without gaps or overlaps. Actual enclosing/dispatch counts remain unknown
unless separately recorded from external evidence.

The journal database is local `production-journal.sqlite` in the Studio
data directory. Treat it as sensitive production metadata. Back it up
according to your site's evidence retention policy. No multi-user identity
provider, external RIP feed or tamper-resistant log storage is configured.
