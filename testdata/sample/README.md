# Sample AFP files

`sample/demo-medium-transition.afp` is a synthetic four page job. It is
built by `backend/afpstudio/testing/synthetic.py`, so it can be
regenerated at any time:

    python -c "from afpstudio.testing import synthetic; from pathlib import Path; \
        Path('testdata/sample/demo-medium-transition.afp').write_bytes(synthetic.demo_job(4))"

It contains:

* a named page group `LETTER`,
* a form map `FORM01` with two medium maps, `MAP01` (plain A4, tray 1)
  and `MAP02` (letterhead, tray 2),
* visible presentation text on every page, and
* a medium map change on page three.

Opening it with the bundled `sample_profile` printer profile produces a
`fail` on pages 1, 3 and 4, because that profile's rules say the
letterhead may only be used on page 1 and every other page must be
plain A4. That is the intended demonstration: the preflight catches a
media policy the document breaks.

No customer AFP is in this directory. The larger corpus under
`backend/vendor/readafp/testdata/` comes from the publicly licensed
upstream test suites and is only used by the automated tests.
