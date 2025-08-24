Regenerating translation files

This directory contains the translation workflow and helper scripts.

By default the workflow is interactive (you'll be prompted for missing translations).

Interactive update (default):

PowerShell
```
# Generate/update POT/POs and run the interactive review (the batch calls the review script for you)
& .\update_translation_files_interactive.bat
```

Non-interactive / batch mode (for CI or automation):

PowerShell
```
& .\update_translation_files_noninteractive.bat
```

This workflow will:
- run xgettext to extract strings from `src/gui/ui_strings.py` into `base.pot`
- create/update `base.po` in each locale subfolder
- run `po_update_and_review.py`
- compile `.po` files to `.mo`

Notes:
- The extraction currently scans only `src/gui/ui_strings.py` (this is intentional).
- You can pass an explicit PO filename with `--po-file` if you use a different name than `base.po`:

PowerShell
```
& .\.venv\Scripts\python.exe .\po_update_and_review.py --po-file yourfile.po
```