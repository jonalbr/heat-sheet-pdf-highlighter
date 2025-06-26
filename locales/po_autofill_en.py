import polib
import sys

def autofill_en_po(po_path):
    po = polib.pofile(po_path)
    for entry in po:
        if entry.msgid and entry.msgid != entry.msgstr:
            entry.msgstr = entry.msgid
        if 'fuzzy' in entry.flags:
            entry.flags.remove('fuzzy')
    po.save()

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python po_autofill_en.py path/to/base.po")
        sys.exit(1)
    autofill_en_po(sys.argv[1])