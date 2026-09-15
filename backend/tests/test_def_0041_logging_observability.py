"""LIVE-CORR-4 -- the bounded logging-observability prerequisite named in
DEF-0041's own "FINAL LIVE ISOLATION" note: this codebase never raised the
root logger above Python's own default (`WARNING`) anywhere, so every
existing `_logger.info(...)` diagnostic/security line -- including
evidence.py's own `troubleshooting_command_grounding` instrumentation --
was silently dropped in every real run to date.

`Settings.log_level` (backend/config/settings.py) mirrors `session_
backend`'s own "reject an invalid configured value, default otherwise"
discipline; `_configure_application_logging` (backend/api/app.py) is the
ONE centralized `logging.basicConfig` call for this codebase, wired as the
first line of `_lifespan`, and a safe no-op whenever the root logger
already has a handler (never duplicates handlers, never clobbers a host's
own logging configuration, never pollutes pytest's own log capture).
"""
from __future__ import annotations

import logging

import pytest

from backend.config.settings import ConfigurationError, Settings


def test_log_level_defaults_to_info() -> None:
    settings = Settings(env={})
    assert settings.log_level == "INFO"


def test_log_level_can_be_set_explicitly() -> None:
    settings = Settings(env={"SLOPANOC_LOG_LEVEL": "DEBUG"})
    assert settings.log_level == "DEBUG"


def test_log_level_is_case_insensitive() -> None:
    settings = Settings(env={"SLOPANOC_LOG_LEVEL": "warning"})
    assert settings.log_level == "WARNING"


def test_unsupported_log_level_raises_configuration_error() -> None:
    settings = Settings(env={"SLOPANOC_LOG_LEVEL": "verbose"})
    with pytest.raises(ConfigurationError):
        _ = settings.log_level


def test_configure_application_logging_sets_root_level_when_unconfigured() -> None:
    """A no-handler root logger (the exact state DEF-0041 confirmed this
    codebase leaves it in) gets a real handler at the configured level."""
    from backend.api.app import _configure_application_logging

    root_logger = logging.getLogger()
    saved_handlers, saved_level = list(root_logger.handlers), root_logger.level
    root_logger.handlers = []
    try:
        _configure_application_logging("INFO")
        assert root_logger.handlers
        assert root_logger.getEffectiveLevel() <= logging.INFO
    finally:
        root_logger.handlers = saved_handlers
        root_logger.level = saved_level


def test_configure_application_logging_is_a_no_op_when_a_handler_already_exists() -> None:
    """Never duplicates handlers -- e.g. a test runner's own log-capture
    handler, or a second `create_app()`/`_lifespan` entry in one process."""
    from backend.api.app import _configure_application_logging

    root_logger = logging.getLogger()
    sentinel_handler = logging.NullHandler()
    saved_handlers = list(root_logger.handlers)
    root_logger.handlers = [sentinel_handler]
    try:
        _configure_application_logging("INFO")
        assert root_logger.handlers == [sentinel_handler]
    finally:
        root_logger.handlers = saved_handlers
