# Localization guide

This directory contains the translation workflow and helper scripts.

The default workflow is interactive, so you'll be prompted for missing translations.

## Windows prerequisites

The helper scripts expect GNU gettext tools from MSYS2.

1. Install [MSYS2](https://www.msys2.org/).
2. Open the **MSYS2 MSYS** terminal.
3. Update packages with `pacman -Syu` and reopen the terminal if MSYS2 asks you to.
4. Install gettext with `pacman -S gettext`.

By default, the scripts expect `xgettext`, `msgmerge`, `msginit`, and `msgfmt` under `C:\msys64\usr\bin\`. If MSYS2 lives elsewhere, update the path variables in:

- `update_translation_files_interactive.bat`
- `update_translation_files_noninteractive.bat`
- `update_mo_files.bat`

## Translation workflow

Interactive update:

```powershell
# Generate/update POT/POs and run the interactive review (the batch calls the review script for you)
& .\update_translation_files_interactive.bat
```

Non-interactive / batch mode (for CI or automation):

```powershell
& .\update_translation_files_noninteractive.bat
```

This workflow will:

- run `xgettext` to extract strings from `src/gui/ui_strings.py` into `base.pot`
- create/update `base.po` in each locale subfolder
- run `po_update_and_review.py`
- compile `.po` files to `.mo`

Notes:

- The extraction currently scans only `src/gui/ui_strings.py` (this is intentional).
- You can pass an explicit PO filename with `--po-file` if you use a different name than `base.po`:

```powershell
uv run python .\po_update_and_review.py --po-file yourfile.po
```
