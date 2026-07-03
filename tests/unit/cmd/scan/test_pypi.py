import io
import tarfile
from pathlib import Path
from unittest.mock import MagicMock, patch
from zipfile import ZipFile

import pytest
from packaging.requirements import InvalidRequirement

from ggshield.cmd.secret.scan.pypi import get_files_from_package, save_package_to_tmp
from ggshield.core.errors import UnexpectedError


def _mock_best_match(filename: str, url: str) -> MagicMock:
    best_match = MagicMock()
    best_match.link.filename = filename
    best_match.link.normalized = url
    return best_match


def _set_stream_response(finder: MagicMock, chunks: list[bytes]) -> None:
    """Wire finder.session.get_stream(...) to yield a response streaming `chunks`."""
    response = MagicMock()
    response.iter_bytes.return_value = list(chunks)
    finder.session.get_stream.return_value.__enter__.return_value = response


@patch("ggshield.cmd.secret.scan.pypi.PackageFinder")
class TestSavePackageToTmp:
    package_name: str = "what-ever-non-existing"

    def test_downloads_archive_into_temp_dir(
        self, finder_cls: MagicMock, tmp_path: Path
    ) -> None:
        finder = finder_cls.return_value
        finder.find_best_match.return_value.best = _mock_best_match(
            "pkg-1.0.tar.gz", "https://index.test/pkg-1.0.tar.gz"
        )
        _set_stream_response(finder, [b"chunk1", b"chunk2"])

        save_package_to_tmp(temp_dir=tmp_path, package_name=self.package_name)

        assert (tmp_path / "pkg-1.0.tar.gz").read_bytes() == b"chunk1chunk2"

    def test_raises_when_package_not_found(
        self, finder_cls: MagicMock, tmp_path: Path
    ) -> None:
        finder_cls.return_value.find_best_match.return_value.best = None
        with pytest.raises(UnexpectedError):
            save_package_to_tmp(temp_dir=tmp_path, package_name=self.package_name)

    def test_raises_on_invalid_requirement(
        self, finder_cls: MagicMock, tmp_path: Path
    ) -> None:
        finder_cls.return_value.find_best_match.side_effect = InvalidRequirement("bad")
        with pytest.raises(UnexpectedError):
            save_package_to_tmp(temp_dir=tmp_path, package_name=self.package_name)

    def test_raises_when_lookup_fails(
        self, finder_cls: MagicMock, tmp_path: Path
    ) -> None:
        finder_cls.return_value.find_best_match.side_effect = RuntimeError("boom")
        with pytest.raises(UnexpectedError):
            save_package_to_tmp(temp_dir=tmp_path, package_name=self.package_name)

    def test_raises_when_download_fails(
        self, finder_cls: MagicMock, tmp_path: Path
    ) -> None:
        finder = finder_cls.return_value
        finder.find_best_match.return_value.best = _mock_best_match(
            "pkg-1.0.tar.gz", "https://index.test/pkg-1.0.tar.gz"
        )
        finder.session.get_stream.side_effect = RuntimeError("boom")
        with pytest.raises(UnexpectedError):
            save_package_to_tmp(temp_dir=tmp_path, package_name=self.package_name)


class TestGetFilesFromPackage:
    package_name: str = "what-ever-non-existing"

    def _make_archive(self, directory: Path, extension: str) -> Path:
        """Create a package archive containing a single `hello.py` file."""
        archive_path = directory / f"{self.package_name}.{extension}"
        payload = b"print('hello')\n"
        if extension == "tar.gz":
            with tarfile.open(archive_path, "w:gz") as tar:
                info = tarfile.TarInfo(name="hello.py")
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))
        elif extension == "whl":
            with ZipFile(archive_path, "w") as wheel:
                wheel.writestr("hello.py", payload)
        return archive_path

    @pytest.mark.parametrize("extension", ["whl", "tar.gz"])
    def test_returns_scannables_for_archive_contents(
        self, extension: str, tmp_path: Path
    ) -> None:
        """
        GIVEN a directory containing only a package archive
        WHEN get_files_from_package is called
        THEN it returns scannables for the files inside the archive, and excludes the
        archive itself
        """
        archive_path = self._make_archive(tmp_path, extension)

        files, _binary_paths = get_files_from_package(
            archive_dir=tmp_path,
            package_name=self.package_name,
            exclusion_regexes=set(),
        )

        scanned_names = {scannable.path.name for scannable in files}
        assert "hello.py" in scanned_names
        assert archive_path.name not in scanned_names
