# Independent parser stream 2.0.0

Transport: UTF-8 JSON objects, one per LF-terminated line. CLI:
`java -jar alpheus-adapter.jar --input <path> --format jsonl`.
Standard 5A AFP only; offsets refer to original bytes including the 5A byte.

1. Header: `type=header`, `contract_version=2.0.0`, `input_size` nonnegative
   integer, `input_sha256` lowercase 64-character SHA-256 of original input.
2. Fields: `type=field`, `seq` consecutive from zero, `offset`, `length`
   including 5A, `sf_id` 24-bit integer, `page` one-based integer inside a
   BPG/EPG bracket or null otherwise. EPG belongs to the closing page.
3. Final summary: `type=summary`, `field_count`, `fields_sha256` SHA-256 of
   the exact UTF-8 field lines including LF, `result` validated legacy
   contract-1 metadata with strict boolean `complete`.

No records may follow the summary. Limits: 2,000,000 fields, 16 MiB per
line, configured total stdout cap. Stdout is spooled to a temporary file
that closes and is removed after reading. Compact integer arrays retain
field observations. Reader rejects negative/floating-point integers,
overlaps, count/digest mismatches and missing header/summary. Complete
results must cover every input byte contiguously, have at least one field
and no parser errors. Source digest is checked against Studio's source.

Comparison checks every field's offset, length, identifier and bracketed
page association. Page starts, IMM names, medium map offsets, supported
physical dimensions and explicit MMC duplex values have separate diagnostics.
At most 1000 differences are retained, then an explicit comparison-limit
failure is added. Errors and incompleteness fail preflight. Equal supported
observations do not prove full semantic agreement: resource scope, triplet
contents and unsupported media controls remain partial coverage.

`--format json` remains available for legacy metadata consumers. Python
accepts old contract-1 development adapters as explicitly partial results.
An older jar that rejects `jsonl` reports a parser failure and must be rebuilt;
Studio does not silently substitute its own parser as independent evidence.
