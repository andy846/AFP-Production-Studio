# Compare two AFP documents

1. Open the old and new AFP in the same Studio window. If you need resolved
   paper or tray information, apply the appropriate Printer Profile when you
   open each document.
2. Select **Compare**, assign the old and new sources, and start the job.
   Use the filter to focus on text, position, size, media, unpaired pages or
   unverified information. Select a row to view both pages.
3. Review any provisional pairs. To confirm or correct an alignment, enter
   the old and new page numbers under **Old anchor** and **New anchor** and
   choose **Pair pages**. Remove an anchor with its corresponding button.
4. For a pair with matching known physical size, choose **Show pixel difference**.
   Magenta marks changed pixels in the 144 dpi Studio preview. Disable
   **Sync view** to navigate or zoom either pane independently.
5. Export CSV or Excel using a full output path, or leave the field empty to
   use the desktop save dialog. Text excerpts are excluded unless selected.
   The reports distinguish an unrendered pixel comparison from a completed one.

Unique exact page bytes are preferred for anchors; unique decoded text can
also establish an anchor. Repeated or crossing candidates do not become
confirmed automatic pairs. Equal gaps may be proposed for review; unequal gaps
remain unpaired. Manual anchors must preserve order. Text comparison uses
decoded runs and is not OCR. The preview is not RIP or printer verification;
requested media and profile estimates are separate from actual media.

Each comparison covers up to 20,000 pages per source. Text decoding also uses
the bounded replay budget; missing or incomplete decoding is marked in the
result. Pixel rendering is performed only for a selected pair. Closing or
changing a source invalidates the comparison. Changing anchors invalidates
the previous visual results.

Local Windows measurements on simple synthetic documents with one text run
per page, one changed page, and no pixel rendering:

| Pages per source | Open both | Compare | Peak process working set |
| ---: | ---: | ---: | ---: |
| 1,000 | 0.06 s | 0.58 s | 56.3 MiB |
| 10,000 | 0.48 s | 5.50 s | 125.6 MiB |
| 20,000 | 0.85 s | 11.38 s | 200.6 MiB |

The measurements used a single process in increasing workload order; the peak
is the process high-water mark, not incremental memory. Complex fonts, images
and network sources can take longer.

Validation used synthetic inputs: 334 Studio tests passed, including an Edge
comparison workflow, and readAFP reported 224 passed, 11 skipped and 3 expected
failures. The packaged Windows executable was also started in WebView2 to
compare two files, display the overlay and export CSV; its local API exported
the Excel report and verified that the source hash did not change.
