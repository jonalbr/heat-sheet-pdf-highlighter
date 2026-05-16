from pathlib import Path
import subprocess

import pytest

from locales import update_translations as translations


def _make_locale(root: Path, name: str, *, with_po: bool) -> Path:
    messages = root / name / "LC_MESSAGES"
    messages.mkdir(parents=True)
    if with_po:
        (messages / "base.po").write_text("msgid \"\"\nmsgstr \"\"\n", encoding="utf-8")
    return messages


def test_find_gettext_tool_prefers_explicit_path(tmp_path, monkeypatch):
    tool = tmp_path / "msgfmt.exe"
    tool.write_text("", encoding="utf-8")
    monkeypatch.setenv("MSGFMT_PATH", str(tool))

    assert translations.find_gettext_tool("msgfmt", env_var="MSGFMT_PATH") == tool


def test_run_delegates_to_subprocess(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        translations.subprocess,
        "run",
        lambda cmd, cwd=None, check=True: calls.append((cmd, cwd, check)) or "done",
    )

    assert translations.run(["msgfmt"], cwd=tmp_path) == "done"
    assert calls == [(["msgfmt"], tmp_path, True)]


def test_find_gettext_tool_rejects_missing_explicit_path(monkeypatch):
    monkeypatch.setenv("MSGFMT_PATH", "C:\\missing\\msgfmt.exe")

    with pytest.raises(translations.TranslationToolError):
        translations.find_gettext_tool("msgfmt", env_var="MSGFMT_PATH")


def test_find_gettext_tool_uses_path_hit(monkeypatch):
    monkeypatch.delenv("MSGFMT_PATH", raising=False)
    monkeypatch.setattr(translations.shutil, "which", lambda candidate: "C:\\Tools\\msgfmt.exe" if "msgfmt" in candidate else None)

    assert translations.find_gettext_tool("msgfmt", env_var="MSGFMT_PATH") == Path("C:\\Tools\\msgfmt.exe")


def test_find_gettext_tool_uses_default_bin(tmp_path, monkeypatch):
    monkeypatch.delenv("MSGFMT_PATH", raising=False)
    monkeypatch.setattr(translations.shutil, "which", lambda _candidate: None)
    monkeypatch.setattr(translations, "DEFAULT_GETTEXT_BIN", tmp_path)
    tool = tmp_path / translations._tool_candidates("msgfmt")[0]
    tool.write_text("", encoding="utf-8")

    assert translations.find_gettext_tool("msgfmt", env_var="MSGFMT_PATH") == tool


def test_find_gettext_tool_reports_missing_tool(tmp_path, monkeypatch):
    monkeypatch.delenv("MSGFMT_PATH", raising=False)
    monkeypatch.setattr(translations.shutil, "which", lambda _candidate: None)
    monkeypatch.setattr(translations, "DEFAULT_GETTEXT_BIN", tmp_path)

    with pytest.raises(translations.TranslationToolError):
        translations.find_gettext_tool("msgfmt", env_var="MSGFMT_PATH")


def test_tool_candidates_prefers_native_name_off_windows(monkeypatch):
    monkeypatch.setattr(translations.os, "name", "posix")

    assert translations._tool_candidates("msgfmt") == ("msgfmt", "msgfmt.exe")


def test_locale_directories_returns_only_locales_with_messages(tmp_path):
    _make_locale(tmp_path, "de", with_po=True)
    (tmp_path / "ignored").mkdir()
    _make_locale(tmp_path, "en", with_po=False)

    assert [path.name for path in translations.locale_directories(tmp_path)] == ["de", "en"]


def test_extract_strings_builds_expected_command(tmp_path, monkeypatch):
    calls = []
    source = tmp_path / "ui_strings.py"
    pot = tmp_path / "base.pot"
    monkeypatch.setattr(translations, "run", lambda cmd, cwd=None: calls.append((cmd, cwd)))

    translations.extract_strings(Path("xgettext"), pot_file=pot, source_files=(source,))

    assert calls == [
        (
            [
                "xgettext",
                "--from-code=UTF-8",
                "--language=Python",
                "--keyword=ngettext:1,2",
                "--keyword=n_:1,2",
                "--keyword=_:1",
                "--keyword=gettext:1",
                "--keyword=self._:1",
                "--keyword=self.n_:1,2",
                "-o",
                str(pot),
                str(source),
            ],
            translations.SCRIPT_DIR,
        )
    ]


def test_extract_strings_defaults_to_repository_relative_source_paths(monkeypatch):
    calls = []
    monkeypatch.setattr(translations, "run", lambda cmd, cwd=None: calls.append((cmd, cwd)))

    translations.extract_strings(Path("xgettext"))

    cmd, cwd = calls[0]
    assert cwd == translations.SCRIPT_DIR
    assert cmd[-1] == str(Path("..") / "src" / "gui" / "ui_strings.py")
    assert not Path(cmd[-1]).is_absolute()


def test_update_po_files_initializes_missing_files_and_merges_existing(tmp_path, monkeypatch):
    _make_locale(tmp_path, "de", with_po=True)
    _make_locale(tmp_path, "en", with_po=False)
    pot = tmp_path / "base.pot"
    calls = []
    monkeypatch.setattr(translations, "run", lambda cmd, cwd=None: calls.append(cmd))

    translations.update_po_files(
        msginit=Path("msginit"),
        msgmerge=Path("msgmerge"),
        pot_file=pot,
        locales_dir=tmp_path,
    )

    assert calls == [
        ["msgmerge", "-U", str(tmp_path / "de" / "LC_MESSAGES" / "base.po"), str(pot)],
        [
            "msginit",
            "--locale=en",
            "-i",
            str(pot),
            "-o",
            str(tmp_path / "en" / "LC_MESSAGES" / "base.po"),
            "--no-translator",
        ],
        ["msgmerge", "-U", str(tmp_path / "en" / "LC_MESSAGES" / "base.po"), str(pot)],
    ]


def test_review_translations_uses_current_python_and_optional_batch_flag(monkeypatch):
    calls = []
    monkeypatch.setattr(translations, "run", lambda cmd, cwd=None: calls.append(cmd))

    translations.review_translations(non_interactive=False)
    translations.review_translations(non_interactive=True, po_file="custom.po")

    assert calls == [
        [translations.sys.executable, str(translations.SCRIPT_DIR / "po_update_and_review.py"), "--po-file", "base.po"],
        [
            translations.sys.executable,
            str(translations.SCRIPT_DIR / "po_update_and_review.py"),
            "--po-file",
            "custom.po",
            "--non-interactive",
        ],
    ]


def test_compile_mo_files_skips_locales_without_requested_po(tmp_path, monkeypatch):
    _make_locale(tmp_path, "de", with_po=True)
    _make_locale(tmp_path, "en", with_po=False)
    calls = []
    monkeypatch.setattr(translations, "run", lambda cmd, cwd=None: calls.append(cmd))

    translations.compile_mo_files(msgfmt=Path("msgfmt"), locales_dir=tmp_path)

    assert calls == [
        [
            "msgfmt",
            "-o",
            str(tmp_path / "de" / "LC_MESSAGES" / "base.mo"),
            str(tmp_path / "de" / "LC_MESSAGES" / "base.po"),
        ]
    ]


def test_remove_po_backups_deletes_nested_backups(tmp_path):
    backup = tmp_path / "de" / "LC_MESSAGES" / "base.po~"
    backup.parent.mkdir(parents=True)
    backup.write_text("backup", encoding="utf-8")

    translations.remove_po_backups(tmp_path)

    assert not backup.exists()


def test_update_translations_runs_full_workflow(monkeypatch):
    calls = []
    monkeypatch.setattr(translations, "find_gettext_tool", lambda name, env_var: Path(name))
    monkeypatch.setattr(translations, "extract_strings", lambda xgettext: calls.append(("extract", xgettext)))
    monkeypatch.setattr(
        translations,
        "update_po_files",
        lambda **kwargs: calls.append(("merge", kwargs["msginit"], kwargs["msgmerge"], kwargs["po_file"])),
    )
    monkeypatch.setattr(
        translations,
        "review_translations",
        lambda **kwargs: calls.append(("review", kwargs["non_interactive"], kwargs["po_file"])),
    )
    monkeypatch.setattr(
        translations,
        "compile_mo_files",
        lambda **kwargs: calls.append(("compile", kwargs["msgfmt"], kwargs["po_file"])),
    )
    monkeypatch.setattr(translations, "remove_po_backups", lambda: calls.append(("cleanup",)))

    translations.update_translations(non_interactive=True, compile_only=False, po_file="custom.po")

    assert calls == [
        ("extract", Path("xgettext")),
        ("merge", Path("msginit"), Path("msgmerge"), "custom.po"),
        ("review", True, "custom.po"),
        ("compile", Path("msgfmt"), "custom.po"),
        ("cleanup",),
    ]


def test_update_translations_compile_only_skips_extract_and_review(monkeypatch):
    calls = []
    monkeypatch.setattr(translations, "find_gettext_tool", lambda name, env_var: calls.append(("tool", name)) or Path(name))
    monkeypatch.setattr(translations, "extract_strings", lambda xgettext: calls.append(("extract", xgettext)))
    monkeypatch.setattr(translations, "update_po_files", lambda **kwargs: calls.append(("merge", kwargs)))
    monkeypatch.setattr(translations, "review_translations", lambda **kwargs: calls.append(("review", kwargs)))
    monkeypatch.setattr(
        translations,
        "compile_mo_files",
        lambda **kwargs: calls.append(("compile", kwargs["msgfmt"], kwargs["po_file"])),
    )
    monkeypatch.setattr(translations, "remove_po_backups", lambda: calls.append(("cleanup",)))

    translations.update_translations(non_interactive=False, compile_only=True)

    assert calls == [("tool", "msgfmt"), ("compile", Path("msgfmt"), "base.po"), ("cleanup",)]


def test_main_returns_zero_on_success(monkeypatch):
    monkeypatch.setattr(translations, "update_translations", lambda **kwargs: None)

    assert translations.main(["--non-interactive"]) == 0


def test_main_reports_tool_error(monkeypatch, capsys):
    monkeypatch.setattr(
        translations,
        "update_translations",
        lambda **kwargs: (_ for _ in ()).throw(translations.TranslationToolError("missing tool")),
    )

    assert translations.main([]) == 1
    assert "missing tool" in capsys.readouterr().err


def test_main_reports_command_error(monkeypatch, capsys):
    monkeypatch.setattr(
        translations,
        "update_translations",
        lambda **kwargs: (_ for _ in ()).throw(subprocess.CalledProcessError(7, ["msgfmt"])),
    )

    assert translations.main([]) == 7
    assert "msgfmt" in capsys.readouterr().err
