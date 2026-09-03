"""Tests proving API logs are useful without leaking secrets."""

import asyncio
import logging

from ept.api.app import configure_logging
from ept.api.dependencies import require_edito_bearer_auth
from ept.infrastructure.services.edito_auth import EditoBearerAuth
from ept.infrastructure.utils.logging import token_diagnostic

from .helpers import create_api_shell_test_app, get_many, request
from tests.conftest import unit


@unit
def test_request_logging_redacts_sensitive_query_values(caplog):
    """Query-string secrets are redacted before request logs are emitted."""
    caplog.set_level(logging.INFO, logger="ept.api.app")
    app = create_api_shell_test_app()

    asyncio.run(get_many("/v1?token=secret&view=summary", app=app))

    assert "query=token=%2A%2A%2AREDACTED%2A%2A%2A&view=summary" in caplog.text
    assert "secret" not in caplog.text


@unit
def test_successful_api_index_request_is_not_logged(caplog):
    """Successful GET / health-style traffic stays out of ordinary logs."""
    caplog.set_level(logging.INFO, logger="ept.api.app")
    app = create_api_shell_test_app()

    asyncio.run(get_many("/", "/v1", app=app))

    assert "API request completed method=GET path=/" not in caplog.text
    assert "API request completed method=GET path=/v1" not in caplog.text
    assert "API index requested" not in caplog.text


@unit
def test_successful_healthz_request_is_not_logged(caplog):
    """Successful GET /healthz probe traffic stays out of ordinary logs."""
    caplog.set_level(logging.INFO, logger="ept.api.app")
    app = create_api_shell_test_app()

    asyncio.run(get_many("/healthz", app=app))

    assert "API request completed method=GET path=/healthz" not in caplog.text


@unit
def test_token_diagnostic_redacts_by_default(monkeypatch):
    """Token diagnostics expose presence and length without token values by default."""
    monkeypatch.delenv("EPT_LOG_TOKEN_VALUES", raising=False)

    assert token_diagnostic("access-secret") == {"present": True, "length": 13}


@unit
def test_token_diagnostic_can_log_full_values_when_enabled(monkeypatch):
    """Local troubleshooting can opt in to full token values."""
    monkeypatch.setenv("EPT_LOG_TOKEN_VALUES", "true")

    assert token_diagnostic("access-secret") == {
        "present": True,
        "length": 13,
        "value": "access-secret",
    }


@unit
def test_validation_logging_redacts_sensitive_body_values(caplog):
    """Validation logs redact sensitive body fields and ordinary user values."""
    caplog.set_level(logging.WARNING, logger="ept.api.app")
    app = create_api_shell_test_app()

    async def authenticated_principal() -> EditoBearerAuth:
        """Return a stable principal for the validation logging request."""
        return EditoBearerAuth(
            subject="test-user",
            username="alice",
            claims={"sub": "test-user"},
            access_token="access-secret",
        )

    app.dependency_overrides[require_edito_bearer_auth] = authenticated_principal
    try:
        response = asyncio.run(
            request(
                "POST",
                "/v1/protected",
                app=app,
                json={"refresh_token": "secret", "username": "alice", "path": "/catalogs/demo"},
            )
        )
        assert response.status_code == 422
        assert "***REDACTED***" in caplog.text
        assert "secret" not in caplog.text
        assert "alice" not in caplog.text
    finally:
        app.dependency_overrides.pop(require_edito_bearer_auth, None)


@unit
def test_configure_logging_writes_to_console_and_rotating_file(
    tmp_path, monkeypatch, capsys
):
    """Process logging writes queued records to the console and rotating file."""
    log_path = tmp_path / "api.log"
    root_logger = logging.getLogger()
    previous_handlers = root_logger.handlers[:]
    previous_level = root_logger.level
    monkeypatch.setenv("API_LOG", str(log_path))
    try:
        listener = configure_logging()
        logging.getLogger("ept.test").warning("queued file logging works")
        listener.stop()
    finally:
        root_logger.handlers.clear()
        root_logger.handlers.extend(previous_handlers)
        root_logger.setLevel(previous_level)

    assert "queued file logging works" in capsys.readouterr().err
    assert "queued file logging works" in log_path.read_text(encoding="utf-8")
