from readafp.ptoca import Page, TextRun, _estimate_font_sizes


def test_unknown_font_uses_repeated_column_spacing_not_sparse_global_gaps():
    page = Page(units_per_inch=1440)
    page.texts = [TextRun(x=850, y=y, text="SYNTHETIC", font_id=1)
                  for y in (2076, 2262, 2448, 2634, 2820, 3006)]
    page.texts += [TextRun(x=3373, y=4677 + 366 * n, text="DATA", font_id=1)
                   for n in range(20)]
    _estimate_font_sizes(page, {})
    assert max(run.font_size for run in page.texts) <= 170
