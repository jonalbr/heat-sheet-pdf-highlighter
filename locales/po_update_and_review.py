"""
po_update_and_review.py

This script is intended to be run directly from the command line:
    python po_update_and_review.py

Workflow:
- The script reads the batch file 'update_translation_files.bat' to determine the .po file names for English and German locales.
- It loads the English and German .po files from the 'en/LC_MESSAGES' and 'de/LC_MESSAGES' directories, respectively.
- For each entry in the English .po file:
    - If the entry is missing in the German .po, the user is prompted to provide a German translation (including plural forms if needed), and the entry is added.
    - If the German entry is marked as 'fuzzy', empty, or out of sync, the user is prompted to review and update the translation.
    - The script removes the 'fuzzy' flag from updated entries.
- After reviewing and updating the German .po file, the script autofills the English .po file by setting each msgstr to its msgid and removing any 'fuzzy' flags.
- Changes are saved to the respective .po files if any updates are made.

This workflow ensures that new or updated English strings from the modular src/ structure are reviewed for German translation and that the English .po file is always up to date for use as a template or fallback.

Note: Translatable strings are centralized in main_window.py using self._() and self.n_() patterns, with all dialogs using the self.app.strings dictionary for consistency and performance.
"""

import polib
import os
import re
import sys

def get_po_paths_from_bat(bat_path):
    with open(bat_path, encoding="utf-8") as f:
        content = f.read()
    # Find the PO_FILE variable
    match = re.search(r'set PO_FILE=([^\r\n]+)', content)
    if not match:
        raise ValueError("PO_FILE variable not found in the batch file.")
    po_file = match.group(1).strip()
    # English and German .po paths
    en_po = os.path.join("en", "LC_MESSAGES", po_file)
    de_po = os.path.join("de", "LC_MESSAGES", po_file)
    return en_po, de_po

def prompt_for_plural_translation(msgid_plural, current=None):
    print("Plural English:\n" + msgid_plural)
    sys.stdout.flush()
    msgstr_plural = {}
    for idx in range(2):
        cur = current[idx] if current and idx in current else "[empty]"
        prompt = f"Enter German translation for plural form [{idx}]"
        if current:
            prompt += f" (or press Enter to keep current: {cur})"
        prompt += ":\n> "
        print(prompt, end='', flush=True)
        try:
            translation = input().strip()
        except (EOFError, KeyboardInterrupt):
            print("\nInput cancelled, keeping current translation.")
            translation = cur if current else ""
        msgstr_plural[idx] = translation if translation else (cur if current else "")
    return msgstr_plural

def prompt_for_translation(current=None):
    if current:
        print("Current German:\n" + (current or "[empty]"))
        sys.stdout.flush()
        print("Enter new German translation (or press Enter to keep current):\n> ", end='', flush=True)
        try:
            translation = input().strip()
        except (EOFError, KeyboardInterrupt):
            print("\nInput cancelled, keeping current translation.")
            return current
        return translation if translation else current
    else:
        print("Enter German translation:\n> ", end='', flush=True)
        try:
            return input().strip()
        except (EOFError, KeyboardInterrupt):
            print("\nInput cancelled, using empty translation.")
            return ""

def handle_new_entry(en_entry):
    print("\n[NEW] English:\n" + en_entry.msgid)
    if en_entry.msgid_plural:
        msgstr_plural = prompt_for_plural_translation(en_entry.msgid_plural)
        return polib.POEntry(msgid=en_entry.msgid, msgid_plural=en_entry.msgid_plural, msgstr_plural=msgstr_plural)
    else:
        translation = prompt_for_translation()
        return polib.POEntry(msgid=en_entry.msgid, msgstr=translation)

def handle_update_entry(en_entry, de_entry):
    print("\n[UPDATE] English:\n" + en_entry.msgid)
    changed = False
    if en_entry.msgid_plural:
        if not de_entry.msgstr_plural or len(de_entry.msgstr_plural) < 2:
            de_entry.msgstr_plural = {0: '', 1: ''}
        updated_plural = prompt_for_plural_translation(en_entry.msgid_plural, de_entry.msgstr_plural)
        if updated_plural != de_entry.msgstr_plural:
            de_entry.msgstr_plural = updated_plural
            changed = True
    else:
        updated = prompt_for_translation(de_entry.msgstr)
        if updated != de_entry.msgstr:
            de_entry.msgstr = updated
            changed = True
    if 'fuzzy' in de_entry.flags:
        de_entry.flags.remove('fuzzy')
        changed = True
    return changed

def review_and_update_de(en_po_path, de_po_path):
    en_po = polib.pofile(en_po_path)
    de_po = polib.pofile(de_po_path)
    de_dict = {entry.msgid: entry for entry in de_po}
    changed = False

    for en_entry in en_po:
        de_entry = de_dict.get(en_entry.msgid)
        if not de_entry:
            new_entry = handle_new_entry(en_entry)
            de_po.append(new_entry)
            changed = True
        elif 'fuzzy' in de_entry.flags or (not de_entry.msgstr and not de_entry.msgstr_plural) or de_entry.msgid != en_entry.msgid:
            if handle_update_entry(en_entry, de_entry):
                changed = True
    if changed:
        de_po.save()
        print("German .po updated.")
    else:
        print("No changes needed for German.")

def autofill_en_po(en_po_path):
    po = polib.pofile(en_po_path)
    changed = False
    for entry in po:
        if entry.msgid and entry.msgid != entry.msgstr:
            entry.msgstr = entry.msgid
            changed = True
        if 'fuzzy' in entry.flags:
            entry.flags.remove('fuzzy')
            changed = True
    if changed:
        po.save()
        print("English .po autofilled.")
    else:
        print("No changes needed for English.")

if __name__ == "__main__":
    bat_path = os.path.join(os.path.dirname(__file__), "update_translation_files.bat")
    en_po, de_po = get_po_paths_from_bat(bat_path)
    en_po_path = os.path.join(os.path.dirname(__file__), "en", "LC_MESSAGES", os.path.basename(en_po))
    de_po_path = os.path.join(os.path.dirname(__file__), "de", "LC_MESSAGES", os.path.basename(de_po))

    print("Checking for German translation updates...")
    review_and_update_de(en_po_path, de_po_path)
    print("\nAutofilling English .po...")
    autofill_en_po(en_po_path)