# OCR Benchmarks

Benchmark date: 2026-05-20

## Test Input

- File: `C:\Users\albre\Downloads\3.Pfingstmeeting AMTV 2026 ME Teil1.pdf`
- Pages: 37
- Input size: 7.28 MB
- OCR language: `deu`
- OCR DPI: 300
- Output size: 20.81 MB for all tested worker counts
- OCR text check: 74 occurrences of `SGS Hamburg` for all tested worker counts
- Test machine: 20 logical CPUs

CPU percentages are measured against total machine CPU capacity. For example,
`50%` means about half of all logical CPU capacity was used. Peak RAM is the
observed working set of the OCR child process tree. Child process count includes
the OCR supervisor plus page workers.

## Results

| OCR workers | Time | Speedup vs 1 worker | Avg CPU | Peak CPU | Peak RAM | Child processes |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 235.33 s | 1.00x | 4.8% | 5.4% | 181.8 MB | 1 |
| 2 | 68.50 s | 3.44x | 9.7% | 10.7% | 374.5 MB | 3 |
| 4 | 40.07 s | 5.87x | 19.2% | 21.2% | 689.1 MB | 5 |
| 6 | 32.70 s | 7.20x | 28.0% | 31.9% | 1003.1 MB | 7 |
| 8 | 28.55 s | 8.24x | 36.5% | 42.3% | 1308.9 MB | 9 |
| 10 | 28.12 s | 8.37x | 45.1% | 52.5% | 1621.2 MB | 11 |
| 12 | 26.44 s | 8.90x | 54.7% | 63.2% | 1931.7 MB | 13 |
| 16 | 29.18 s | 8.06x | 70.5% | 83.9% | 2550.3 MB | 17 |

## Decision

Default OCR worker cap is `8`.

`12` workers was the fastest on this machine, but it was only about 2 seconds
faster than `8` workers while using roughly 600 MB more peak RAM. `16` workers
was slower and used much more memory. The `8` worker cap is therefore the better
default balance for fast OCR without making memory usage too aggressive.

## Notes

- PyMuPDF documents are not shared across processes. Each worker opens the input
  PDF independently and OCRs assigned pages.
- Workers write one searchable PDF per page to a temporary directory.
- The OCR supervisor merges page PDFs back in original page order.
- Progress is reported as completed pages, so UI text uses `{0}/{1} pages
  processed` rather than a current page number.
