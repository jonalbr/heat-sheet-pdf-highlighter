from pathlib import Path


def test_update_batch_wait_loop_sleeps_between_process_checks():
    script = Path("update_app.bat").read_text(encoding="utf-8")

    assert 'tasklist /FI "PID eq %pid%"' in script
    assert "timeout /t 1 /nobreak >NUL 2>&1" in script
    assert 'start /wait "" "%installer_path%" /SILENT' in script
