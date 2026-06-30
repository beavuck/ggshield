"""The import-time guard rejects pre-3.9 interpreters with a clear message."""

from __future__ import annotations

import pytest

import ggshield


# Real pre-3.9 interpreters ggshield can actually start under: the message only
# prints if this module *parses*, which excludes Python 2.x (the annotations in
# ggshield/__init__.py are 3.x-only syntax). 3.6-3.8 parse it fine.
@pytest.mark.parametrize("version", [(3, 6, 0), (3, 7, 5), (3, 8, 18)])
def test_rejects_unsupported_python(version, capsys):
    with pytest.raises(SystemExit) as exc_info:
        ggshield._ensure_supported_python(version)

    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "Python 3.9 or newer" in err
    # reports the actual running version, and points at a fix for both installers
    assert "Python %d.%d" % (version[0], version[1]) in err
    assert "uv tool install --python 3.12 ggshield" in err
    assert "pipx install --python 3.12 ggshield" in err


@pytest.mark.parametrize(
    "version", [(3, 9, 0), (3, 11, 9), (3, 12, 1), (3, 13, 0), (4, 0)]
)
def test_accepts_supported_python(version):
    # Must not raise on 3.9+ (this is what runs on every supported install).
    ggshield._ensure_supported_python(version)
