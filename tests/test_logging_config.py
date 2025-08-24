import importlib
import logging
import sys
import types


def reload_module(module_name: str) -> types.ModuleType:
    # Remove from import caches to force fresh import
    importlib.invalidate_caches()
    if module_name in sys.modules:
        del sys.modules[module_name]
    return importlib.import_module(module_name)


from src.utils import logging as app_logging


def test_basic_logging_configured(tmp_path, monkeypatch):
    # Ensure no handlers exist on root before import
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)

    # Call the logging helper directly
    app_logging.configure_basic_logging()

    # After calling, root should have handlers
    assert logging.getLogger().handlers, "No logging handlers configured"


def test_log_file_env_creates_file_handler(tmp_path, monkeypatch):
    # Remove handlers
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)

    log_file = tmp_path / "app.log"
    monkeypatch.setenv("LOG_FILE", str(log_file))
    app_logging.configure_basic_logging(log_file=str(log_file))

    handlers = logging.getLogger().handlers
    assert any(isinstance(h, logging.FileHandler) for h in handlers), "FileHandler not set when LOG_FILE env provided"

    # Log something and verify file written
    logging.getLogger(__name__).info("test message")
    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    assert "test message" in content


def test_cli_overrides_env(tmp_path, monkeypatch):
    # Remove handlers
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)

    env_log_file = tmp_path / "env.log"
    cli_log_file = tmp_path / "cli.log"
    monkeypatch.setenv("LOG_FILE", str(env_log_file))

    # Simulate CLI overriding env by calling configure_basic_logging with cli value
    app_logging.configure_basic_logging(log_file=str(cli_log_file))

    handlers = logging.getLogger().handlers
    assert any(isinstance(h, logging.FileHandler) and getattr(h, "baseFilename", None) == str(cli_log_file) for h in handlers), "CLI log file not used when provided"
