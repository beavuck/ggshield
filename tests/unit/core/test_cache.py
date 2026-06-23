import json
import os
from pathlib import Path

import pytest
import yaml
from pyfakefs.fake_filesystem import FakeFilesystem, set_uid

from ggshield.core.cache import Cache
from ggshield.core.config import Config
from ggshield.core.types import IgnoredMatch
from ggshield.utils.git_shell import is_gitignored
from ggshield.utils.os import cd
from tests.repository import Repository


@pytest.fixture(autouse=True)
def _isolate_cache_path():
    """Opt out of conftest's CACHE_PATH redirection.

    These tests exercise Cache's path/gitignore behaviour and manage the cache
    location themselves (via isolated_fs or cd into tmp_path), so the default
    relative ``./.cache_ggshield`` must be kept.
    """
    yield


@pytest.mark.usefixtures("isolated_fs")
class TestCache:
    def test_defaults(self):
        cache = Cache()
        assert cache.last_found_secrets == []

    def test_load_cache_and_purge(self):
        with open(".cache_ggshield", "w") as file:
            json.dump({"last_found_secrets": [{"name": "", "match": "XXX"}]}, file)
        cache = Cache()
        assert cache.last_found_secrets == [IgnoredMatch(name="", match="XXX")]

        cache.purge()
        assert cache.last_found_secrets == []

    def test_load_invalid_cache(self, capsys):
        with open(".cache_ggshield", "w") as file:
            json.dump({"invalid_option": True}, file)

        Cache()
        captured = capsys.readouterr()
        assert "Unrecognized key in cache" in captured.err

    def test_save_cache(self):
        with open(".cache_ggshield", "w") as file:
            json.dump({}, file)
        cache = Cache()
        cache.update_cache(last_found_secrets=[{"match": "XXX"}])
        cache.save()
        with open(".cache_ggshield") as file:
            file_content = json.load(file)
            assert file_content == {
                "last_found_secrets": [{"match": "XXX", "name": ""}]
            }

    def test_read_only_fs(self, fs: FakeFilesystem):
        """
        GIVEN a read-only cache file
        WHEN save is called
        THEN it shouldn't raise an exception
        """

        # pyfakefs skips permission checks for the root user and maps a Windows
        # admin (CI runners) to uid 0; force a non-root uid so the read-only
        # mode is enforced. Set it before the file is created so it owns it.
        set_uid(1)

        cache = Cache()
        cache.update_cache(last_found_secrets=[{"match": "XXX"}])

        # Make cache file read-only and verify it really is read-only.
        # `touch(mode=...)` is not honored on Windows; `chmod` with
        # `force_unix_mode` is required for the read-only bit to take effect.
        cache.cache_path.touch()
        fs.chmod(str(cache.cache_path), 0o400, force_unix_mode=True)
        with pytest.raises(PermissionError):
            cache.cache_path.open("w")

        cache.save()

    @pytest.mark.parametrize("with_entry", [True, False])
    def test_save_cache_first_time(self, isolated_fs, with_entry):
        """
        GIVEN no existing cache
        WHEN save is called but there are (new entries/no entries in memory)
        THEN it should (create/not create) the file
        """
        cache = Cache()
        if with_entry:
            cache.update_cache(last_found_secrets=[{"match": "XXX"}])
        cache.save()

        assert os.path.isfile(".cache_ggshield") is with_entry

    def test_max_commits_for_hook_setting(self):
        """
        GIVEN a yaml config with `max-commits-for-hook=75`
        WHEN the config gets parsed
        THEN the default value of max_commits_for_hook (50) should be replaced with 75
        """
        with open(".gitguardian.yml", "w") as file:
            file.write(yaml.dump({"max-commits-for-hook": 75}))

        config = Config()
        assert config.user_config.max_commits_for_hook == 75


def test_auto_ignore_cache_file(tmp_path):
    """
    GIVEN a cache file in a git repository
    WHEN it is not ignored by git
    THEN the cache file is automatically added to the gitignore file
    """
    Repository.create(tmp_path)

    with open(tmp_path / ".cache_ggshield", "w") as file:
        json.dump({"last_found_secrets": [{"name": "", "match": "XXX"}]}, file)

    with cd(str(tmp_path)):
        assert not is_gitignored(Path(".cache_ggshield"))

        Cache()
        assert is_gitignored(Path(".cache_ggshield"))


def test_no_duplicate_gitignore_entry(tmp_path):
    """
    GIVEN a cache file in a git repository where .cache_ggshield is already in .gitignore
    WHEN Cache() is instantiated multiple times
    THEN the .gitignore entry should not be duplicated
    """
    Repository.create(tmp_path)

    with open(tmp_path / ".cache_ggshield", "w") as file:
        json.dump({"last_found_secrets": [{"name": "", "match": "XXX"}]}, file)

    with cd(str(tmp_path)):
        # First instantiation adds the entry
        Cache()
        assert is_gitignored(Path(".cache_ggshield"))

        gitignore_content_after_first = (tmp_path / ".gitignore").read_text()
        count_first = gitignore_content_after_first.count(".cache_ggshield")

        # Second instantiation should not add a duplicate
        Cache()
        gitignore_content_after_second = (tmp_path / ".gitignore").read_text()
        count_second = gitignore_content_after_second.count(".cache_ggshield")

        assert count_second == count_first, (
            f"Expected {count_first} entries but found {count_second}. "
            f".gitignore content:\n{gitignore_content_after_second}"
        )


def test_no_duplicate_gitignore_entry_tracked_file(tmp_path):
    """
    GIVEN a cache file that is tracked by git (git check-ignore returns not-ignored)
          AND .cache_ggshield is already listed in .gitignore
    WHEN Cache() is instantiated
    THEN the .gitignore entry should not be duplicated
    """
    repo = Repository.create(tmp_path)

    # Create and track .cache_ggshield
    with open(tmp_path / ".cache_ggshield", "w") as file:
        json.dump({"last_found_secrets": [{"name": "", "match": "XXX"}]}, file)
    repo.add(".cache_ggshield")
    repo.create_commit("add cache file")

    # Add .cache_ggshield to .gitignore (but file is already tracked so git check-ignore
    # will still say it's not ignored)
    with open(tmp_path / ".gitignore", "a") as f:
        f.write("\n# Added by ggshield\n.cache_ggshield\n")

    with cd(str(tmp_path)):
        # git check-ignore returns False for tracked files even if in .gitignore
        assert not is_gitignored(Path(".cache_ggshield"))

        gitignore_before = (tmp_path / ".gitignore").read_text()
        count_before = gitignore_before.count(".cache_ggshield")

        Cache()

        gitignore_after = (tmp_path / ".gitignore").read_text()
        count_after = gitignore_after.count(".cache_ggshield")

        assert count_after == count_before, (
            f"Expected {count_before} entries but found {count_after}. "
            f"Duplicate entry added despite .cache_ggshield already being in .gitignore.\n"
            f".gitignore content:\n{gitignore_after}"
        )
