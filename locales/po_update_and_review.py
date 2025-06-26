import polib
import os
import re

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

def review_and_update_de(en_po_path, de_po_path):
    en_po = polib.pofile(en_po_path)
    de_po = polib.pofile(de_po_path)
    de_dict = {entry.msgid: entry for entry in de_po}
    changed = False

    for en_entry in en_po:
        de_entry = de_dict.get(en_entry.msgid)
        if not de_entry:
            # New string, add to German .po
            print("\n[NEW] English:\n" + en_entry.msgid)
            if en_entry.msgid_plural:
                print("Plural English:\n" + en_entry.msgid_plural)
                msgstr_plural = {}
                for idx in range(2):
                    translation = input(f"Enter German translation for plural form [{idx}]:\n> ")
                    msgstr_plural[idx] = translation
                new_entry = polib.POEntry(msgid=en_entry.msgid, msgid_plural=en_entry.msgid_plural, msgstr_plural=msgstr_plural)
            else:
                translation = input("Enter German translation:\n> ")
                new_entry = polib.POEntry(msgid=en_entry.msgid, msgstr=translation)
            de_po.append(new_entry)
            changed = True
        elif 'fuzzy' in de_entry.flags or (not de_entry.msgstr and not de_entry.msgstr_plural) or de_entry.msgid != en_entry.msgid:
            print("\n[UPDATE] English:\n" + en_entry.msgid)
            if en_entry.msgid_plural:
                print("Plural English:\n" + en_entry.msgid_plural)
                # Ensure msgstr_plural exists and has at least 2 forms
                if not de_entry.msgstr_plural or len(de_entry.msgstr_plural) < 2:
                    de_entry.msgstr_plural = {0: '', 1: ''}
                for idx in range(2):
                    current = de_entry.msgstr_plural.get(idx, "[empty]")
                    translation = input(f"Enter German translation for plural form [{idx}] (or press Enter to keep current: {current}):\n> ")
                    if translation.strip():
                        de_entry.msgstr_plural[idx] = translation
                        changed = True
            else:
                print("Current German:\n" + (de_entry.msgstr or "[empty]"))
                translation = input("Enter new German translation (or press Enter to keep current):\n> ")
                if translation.strip():
                    de_entry.msgstr = translation
                    changed = True
            if 'fuzzy' in de_entry.flags:
                de_entry.flags.remove('fuzzy')
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
    bat_path = os.path.join(os.path.dirname(__file__), "update_po_files.bat")
    en_po, de_po = get_po_paths_from_bat(bat_path)
    en_po_path = os.path.join(os.path.dirname(__file__), "en", "LC_MESSAGES", os.path.basename(en_po))
    de_po_path = os.path.join(os.path.dirname(__file__), "de", "LC_MESSAGES", os.path.basename(de_po))

    print("Checking for German translation updates...")
    review_and_update_de(en_po_path, de_po_path)
    print("\nAutofilling English .po...")
    autofill_en_po(en_po_path)