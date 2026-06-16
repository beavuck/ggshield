"""Tests for truststore setup in __main__.py."""

import builtins
import sys
from unittest import mock

import pytest


@pytest.mark.skipif(
    sys.version_info < (3, 10), reason="truststore requires Python 3.10+"
)
def test_setup_truststore_swallows_import_errors(monkeypatch) -> None:
    """A failure while *importing* truststore must not crash the CLI (see #1265).

    The reported bug crashes inside ``import truststore`` itself (truststore's
    ``_macos`` submodule fails to parse the macOS version), before
    ``inject_into_ssl`` is ever reached, so this is the case we must guard.
    """
    import ggshield.__main__ as main_module

    # Make sure the import statement actually triggers __import__ rather than
    # returning an already-cached module.
    monkeypatch.delitem(sys.modules, "truststore", raising=False)

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "truststore":
            raise ValueError("invalid literal for int() with base 10: ''")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    # Should not raise.
    main_module.setup_truststore()


@pytest.mark.skipif(
    sys.version_info < (3, 10), reason="truststore requires Python 3.10+"
)
def test_setup_truststore_swallows_injection_errors(monkeypatch) -> None:
    """A failure while injecting truststore must also not crash the CLI."""
    import ggshield.__main__ as main_module

    fake_truststore = mock.MagicMock()
    fake_truststore.inject_into_ssl.side_effect = RuntimeError("boom")
    monkeypatch.setitem(sys.modules, "truststore", fake_truststore)

    # Should not raise.
    main_module.setup_truststore()

    fake_truststore.inject_into_ssl.assert_called_once()
