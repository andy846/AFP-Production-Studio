# Candidate compatibility and licensing lock

Queried from GitHub on 2026-11-06 using repository commit and licence APIs.
Candidate repositories are research references; they are not newly vendored
or bundled merely because they appear in this table.

| Project | Exact inspected/reference revision | Licence | Role / adoption gate |
|---|---|---|---|
| [readAFP](https://github.com/bone1966/readAFP) | `22938322e4b8bee340d63a8e0a707532c914de53` | MIT | Existing renderer; preserve notices and differential regressions |
| [Alpheus](https://github.com/chatelao/alpheusafpparser) | `b2261a78c2254c32ab136c66199a9f86471d79f1` | GPL-3.0-or-later | Optional independent parser; no iText PDF handler |
| [afplib](https://github.com/yan74/afplib) | `770eec9e745199cee5c69fddfaae595defa9e59f` | Apache-2.0 | Writer/reference concepts; EMF/runtime dependencies need audit before bundling |
| [afpbox](https://github.com/michaelknigge/afpbox) | `3da296c1cf349497d7340f8e88a5d4e9d9100812` | MPL-2.0 repository metadata | Record framing reference; individual file headers differ and must be reviewed before copying |
| [Python AFP](https://github.com/mdneale/afp) | `8967c10503fd8ab28bb5df3b85cbd61beeabd0b9` | Apache-2.0 | Text/reference only; no code copied |
| [go-afp-line](https://github.com/pratik-saptarshi/go-afp-line) | `e6a0d13e9159f23f23a62081d18af2d37ee202b7` | Apache-2.0 | Round-trip/fuzz concepts, limited architecture subset; not a renderer replacement |
| xafp / AFPDF | Exact candidate identity not established | Unverified / proposed AGPL | No reuse, binaries or copied source |

RDW reader and constrained writer in Studio are original implementations.
Using concepts does not imply compatibility with every upstream input
variant. Only measured/tested subsets are listed in the implementation record.

The product continues under GPL-3.0-or-later. Process isolation does not by
itself establish a proprietary distribution boundary. See
[LICENSE_COMPLIANCE.md](LICENSE_COMPLIANCE.md) for the existing product
obligations. This engineering record is not a legal opinion.
