# The Java ↔ Python JSON contract

The Alpheus adapter is a separate process. It reads one AFP file and
writes one JSON document to standard output. This page is the
authoritative description of that document.

The Python side lives in `backend/afpstudio/parser_adapters/`:

* `contract.py` — the schema, the validation and the comparison rules.
* `alpheus.py` — the subprocess runner.

The Java side lives in `alpheus-adapter/src/main/java/com/studio/afp/`.

## Versioning

The contract version is a three part number, currently **1.0.0**.

| Change | Version bump |
|---|---|
| A new optional field | Minor |
| A new field that older consumers may ignore | Minor |
| An existing field changes meaning, is removed, or becomes required | Major |
| A bug fix that does not change the shape | Patch |

A consumer **must refuse** a document whose major version it does not
know, and **must ignore** fields it does not know. The Python side
implements both rules in `validate_result`.

## Invocation

    java -Xmx1024m -jar alpheus-adapter.jar --input <file.afp> --format json

The arguments are passed as an array; no shell is involved. The process
has a wall clock timeout, a heap limit and a cap on the amount of output
it may produce.

## The document

```json
{
  "contract_version": "1.0.0",
  "adapter": {
    "name": "alpheus-adapter",
    "version": "1.0.0",
    "alpheus_commit": "b2261a78c2254c32ab136c66199a9f86471d79f1"
  },
  "document": {
    "filename": "job.afp",
    "size_bytes": 368661,
    "page_count": 1,
    "documents": 1,
    "structured_field_count": 64
  },
  "complete": true,
  "pages": [
    { "page_number": 1, "offset": 368277, "page_group": null, "field_count": 0 }
  ],
  "medium_maps": [
    {
      "name": "MM000000",
      "offset": 118,
      "form_map": "",
      "width_mm": null,
      "height_mm": null,
      "media_type": null,
      "media_destination": null,
      "duplex": null
    }
  ],
  "invokes": [
    { "name": "MM000000", "offset": 368260, "page_number": null, "in_page": false }
  ],
  "errors": []
}
```

### Field notes

| Field | Meaning |
|---|---|
| `document.page_count` | Pages the Java parser counted from the Begin Page fields |
| `document.documents` | Documents counted from the Begin Document fields |
| `document.structured_field_count` | Fields the parser built, including the ones it could not decode |
| `complete` | `false` when at least one structured field failed to decode. **A result with `complete: false` can never produce a preflight pass.** |
| `pages[].offset` | The byte offset of the Begin Page field. Pages are numbered from 1. |
| `pages[].page_group` | The named page group, when the parser knows it |
| `medium_maps[].offset` | The byte offset of the Begin Medium Map field |
| `medium_maps[].width_mm` / `height_mm` | The medium extent in millimetres, or `null` when the medium does not state a size or uses a logical resolution ratio |
| `invokes[].offset` | The byte offset of the Invoke Medium Map field |
| `invokes[].page_number` | The page the invoke belongs to, or `null` when it sits between pages |
| `invokes[].in_page` | `true` when the invoke is inside a page bracket |
| `errors[]` | Decode failures, each with a code, a message and the byte offset |

`null` always means "the source did not say". It never means zero, and
it never means "not applicable".

## How Studio uses it

The adapter is optional. When it is available and the operator asks for
the cross-check, Studio compares the two parsers:

* the page counts must agree;
* the sets of medium map names must agree.
* reported page offsets and IMM names at their original offsets are
  compared when the local index and media analysis are available.

The 1.x comparison is **partial**, even when no discrepancies are found.
It does not independently compare all structured fields, resources,
triplets, duplex controls, or record framing. The current adapter's
`in_page` flag is derived from its most recent page number, so it is not
used to assert page association for an IMM between page brackets.

Any difference is reported as a `PARSER_DISAGREEMENT` finding with a
fail status. Studio does not pick a winner — it says the two engines
disagree, because at that point it genuinely does not know which one is
right.

An adapter that cannot start, times out or crashes is reported as a
`PARSER_FAILURE` finding, also with a fail status, and the document is
marked incomplete.

## Security notes

* The subprocess is started with an argument array and `shell=False`.
  A path containing spaces or quotes cannot become a command.
* The output is read with a byte cap, so a runaway parser cannot
  exhaust the server's memory.
* The adapter is given a heap limit and a wall clock timeout.
* The adapter never touches the network.
