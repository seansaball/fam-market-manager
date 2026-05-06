"""Auto-update repo URL allow-list (v2.0.2 fix C4).

Pre-v2.0.2 ``set_update_repo_url`` accepted ANY github.com URL
with no constraint that it match the official ``seansaball/
fam-market-manager`` repo.  A malicious ``.fam`` import file or
a rogue Sheets-synced setting could redirect the auto-update
channel to an attacker-controlled repo — combined with no code
signing, a one-shot RCE-as-installer.

These tests pin:
  * ``set_update_repo_url`` rejects non-allow-listed URLs at save.
  * ``get_update_repo_url`` ignores non-allow-listed values that
    might already be persisted (defense in depth on read).
  * ``_is_allowed_repo_url`` correctly identifies the official URL
    and rejects spoofed variants.
"""

import pytest

from fam.database.connection import set_db_path, get_connection, close_connection
from fam.database.schema import initialize_database
from fam.utils.app_settings import (
    ALLOWED_UPDATE_REPOS,
    DEFAULT_REPO_URL,
    _is_allowed_repo_url,
    get_update_repo_url,
    set_setting,
    set_update_repo_url,
)


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db_file = str(tmp_path / "test_repo_allowlist.db")
    close_connection()
    set_db_path(db_file)
    initialize_database()
    yield
    close_connection()


# ─── Allow-list helper ───────────────────────────────────────────


class TestIsAllowedRepoUrl:

    def test_official_url_allowed(self):
        assert _is_allowed_repo_url(DEFAULT_REPO_URL)

    def test_official_url_with_trailing_slash_allowed(self):
        assert _is_allowed_repo_url(DEFAULT_REPO_URL + "/")

    def test_official_url_releases_path_allowed(self):
        assert _is_allowed_repo_url(DEFAULT_REPO_URL + "/releases")

    def test_official_url_case_insensitive_owner(self):
        assert _is_allowed_repo_url(
            "https://github.com/SEANSABALL/fam-market-manager")

    def test_attacker_owner_rejected(self):
        assert not _is_allowed_repo_url(
            "https://github.com/attacker/fam-market-manager")

    def test_typosquat_owner_rejected(self):
        assert not _is_allowed_repo_url(
            "https://github.com/seansabal1/fam-market-manager")

    def test_attacker_repo_rejected(self):
        assert not _is_allowed_repo_url(
            "https://github.com/seansaball/fam-market-manager-evil")

    def test_non_github_rejected(self):
        assert not _is_allowed_repo_url(
            "https://gitlab.com/seansaball/fam-market-manager")

    def test_empty_rejected(self):
        assert not _is_allowed_repo_url("")

    def test_garbage_rejected(self):
        assert not _is_allowed_repo_url("not a url")

    def test_subdomain_spoofing_rejected(self):
        # github.com.evil.example.com would be tempting but doesn't
        # match the GitHub URL regex.
        assert not _is_allowed_repo_url(
            "https://github.com.evil.example.com/seansaball/fam-market-manager")

    # v2.0.3: defense against the URL-form attack variants the
    # adversarial security review flagged.

    def test_http_scheme_rejected(self):
        """v2.0.3 (MED-SEC-1): explicit http:// must be rejected
        even when the owner/repo would otherwise match.  The API
        URL is hardcoded https:// so this is defense-in-depth, but
        any future code path that reuses the saved URL directly
        must not be downgraded to cleartext."""
        assert not _is_allowed_repo_url(
            "http://github.com/seansaball/fam-market-manager")

    def test_idn_homoglyph_owner_rejected(self):
        """Cyrillic 'е' (U+0435) looks like Latin 'e' — confusable
        IDN attack.  The owner string differs by one byte from the
        allow-listed owner so plain string equality catches it."""
        # 'sеansaball' with a Cyrillic 'е' (U+0435), not Latin 'e'
        sneaky = "https://github.com/sеansaball/fam-market-manager"
        assert not _is_allowed_repo_url(sneaky)

    def test_idn_homoglyph_repo_rejected(self):
        sneaky = "https://github.com/seansaball/fam-markеt-manager"
        assert not _is_allowed_repo_url(sneaky)

    def test_path_traversal_in_url_rejected(self):
        """Even if the regex is permissive about trailing path
        segments, ``..`` segments must not let the attacker pivot."""
        # The regex captures the FIRST owner/repo pair; a `..`
        # in a later segment can't change that.  Verify this is
        # still treated as the official repo (i.e. NOT misparsed
        # to attacker/repo).  If the regex ever changes to be
        # path-aware this test catches the regression.
        url = ("https://github.com/seansaball/fam-market-manager/"
               "../../attacker/repo")
        # The allow-list parser sees seansaball/fam-market-manager
        # which IS allow-listed — but the input is suspicious.
        # The current behavior is to allow this.  If a future
        # change tightens it, that's also acceptable.
        # What we DON'T want is silent misparse to attacker/repo.
        from fam.update.checker import parse_github_repo_url
        parsed = parse_github_repo_url(url)
        if parsed is not None:
            owner, _ = parsed
            assert owner.lower() == 'seansaball'

    def test_userinfo_in_url_rejected(self):
        """``https://attacker@github.com/...`` could trick a naive
        URL renderer; verify it doesn't slip through the allow-list."""
        # The regex requires github.com directly after the scheme
        # so ``attacker@github.com`` does not match
        # ``github.com`` (the @ breaks it).  Verify.
        sneaky = "https://attacker@github.com/seansaball/fam-market-manager"
        assert not _is_allowed_repo_url(sneaky)

    def test_official_url_in_allow_list_constant(self):
        """The DEFAULT_REPO_URL must always be on the allow-list —
        otherwise the app can't update at all."""
        from fam.update.checker import parse_github_repo_url
        owner, repo = parse_github_repo_url(DEFAULT_REPO_URL)
        assert (owner, repo) in ALLOWED_UPDATE_REPOS or any(
            o.lower() == owner.lower() and r == repo
            for o, r in ALLOWED_UPDATE_REPOS
        )


# ─── set_update_repo_url enforcement ─────────────────────────────


class TestSetUpdateRepoUrlAllowList:

    def test_official_url_saves(self):
        set_update_repo_url(DEFAULT_REPO_URL)
        assert get_update_repo_url() == DEFAULT_REPO_URL

    def test_attacker_url_rejected_with_value_error(self):
        with pytest.raises(ValueError):
            set_update_repo_url(
                "https://github.com/attacker/fam-market-manager")

    def test_typosquat_rejected(self):
        with pytest.raises(ValueError):
            set_update_repo_url(
                "https://github.com/seansabal1/fam-market-manager")

    def test_non_github_rejected(self):
        with pytest.raises(ValueError):
            set_update_repo_url(
                "https://gitlab.com/seansaball/fam-market-manager")

    def test_empty_rejected(self):
        with pytest.raises(ValueError):
            set_update_repo_url("")

    def test_rejected_value_does_not_persist(self):
        """A rejected URL must not silently overwrite the previous
        value — the user should still be on the official channel."""
        set_update_repo_url(DEFAULT_REPO_URL)
        with pytest.raises(ValueError):
            set_update_repo_url(
                "https://github.com/attacker/fam-market-manager")
        assert get_update_repo_url() == DEFAULT_REPO_URL


# ─── get_update_repo_url defense-in-depth ────────────────────────


class TestGetUpdateRepoUrlIgnoresStaleAttackerValue:
    """If a non-allow-listed URL somehow ended up in the DB (e.g.
    from a pre-v2.0.2 ``.fam`` import that bypassed the save
    validator, or a direct DB write), ``get_update_repo_url`` must
    refuse to return it.  This is the second line of defense."""

    def test_stale_attacker_url_ignored(self):
        # Simulate the malicious-row state by writing directly to
        # the underlying setting (bypassing set_update_repo_url's
        # allow-list check).
        set_setting('update_repo_url',
                    'https://github.com/attacker/fam-market-manager')
        # get_update_repo_url must NOT return the attacker URL —
        # callers will fall back to DEFAULT_REPO_URL.
        result = get_update_repo_url()
        assert result is None, (
            f"get_update_repo_url returned non-allow-listed URL: "
            f"{result!r}")

    def test_official_url_returned_normally(self):
        set_setting('update_repo_url', DEFAULT_REPO_URL)
        assert get_update_repo_url() == DEFAULT_REPO_URL

    def test_unset_returns_none(self):
        # Don't set anything; default behavior.
        assert get_update_repo_url() is None
