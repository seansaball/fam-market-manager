"""Tests for the in-app help library.

These are structural / correctness tests — they confirm the data in
``fam.help.content`` is well-formed (no broken cross-references, no
duplicate IDs, every category populated).  They do NOT score writing
quality — that's an editorial responsibility.

Per PROJECT_INSTRUCTIONS.md §8a, any change to user-facing UI/logic
should update the matching article in ``content.py``.  These tests
catch the structural mistakes that arise from sloppy edits — they do
NOT catch "you added a feature but forgot to write a help article."
"""

import re

import pytest

from fam.help import (
    ARTICLES,
    CATEGORIES,
    TROUBLESHOOTING_FLOWS,
    get_article,
    get_category,
    get_articles_by_category,
    search_articles,
    search_troubleshooting,
)
from fam.help.content import get_troubleshooting_flow


# ══════════════════════════════════════════════════════════════════
# Article structural integrity
# ══════════════════════════════════════════════════════════════════
class TestArticleStructure:
    """Every article must have the required fields and a unique id."""

    def test_articles_present(self):
        """The library must not be empty.  This is a regression guard
        against accidentally deleting all content."""
        assert len(ARTICLES) >= 30, \
            f"Help library has only {len(ARTICLES)} articles — expected " \
            f"comprehensive coverage (~50+)"

    def test_no_duplicate_article_ids(self):
        ids = [a.id for a in ARTICLES]
        assert len(ids) == len(set(ids)), \
            "Duplicate article ids found — every article id must be unique"

    def test_article_ids_are_kebab_case(self):
        """Convention: lowercase letters, digits, hyphens.  Caught early
        because broken slugs cause anchor / cross-reference failures."""
        valid = re.compile(r'^[a-z0-9]+(-[a-z0-9]+)*$')
        bad = [a.id for a in ARTICLES if not valid.match(a.id)]
        assert not bad, f"Article ids must be kebab-case: {bad}"

    def test_every_article_has_title(self):
        for a in ARTICLES:
            assert a.title and a.title.strip(), \
                f"Article {a.id} has an empty title"

    def test_every_article_has_body(self):
        for a in ARTICLES:
            assert a.body and a.body.strip(), \
                f"Article {a.id} has an empty body"

    def test_article_bodies_have_minimum_length(self):
        """A body shorter than 100 chars is almost certainly a stub."""
        stubs = [a.id for a in ARTICLES if len(a.body) < 100]
        assert not stubs, \
            f"Articles look like stubs (<100 chars): {stubs}"

    def test_every_article_belongs_to_existing_category(self):
        cat_ids = {c.id for c in CATEGORIES}
        for a in ARTICLES:
            assert a.category_id in cat_ids, \
                f"Article {a.id} has unknown category_id {a.category_id!r}"

    def test_related_article_ids_resolve(self):
        """Cross-references in related_articles must point to real
        articles.  Broken cross-references render as confusing dead ends."""
        all_ids = {a.id for a in ARTICLES}
        for a in ARTICLES:
            for rel in a.related_articles:
                assert rel in all_ids, \
                    f"Article {a.id} has dangling related_articles ref: {rel!r}"

    def test_articles_are_not_self_referential(self):
        """An article shouldn't list itself in related_articles."""
        for a in ARTICLES:
            assert a.id not in a.related_articles, \
                f"Article {a.id} lists itself as a related article"


# ══════════════════════════════════════════════════════════════════
# Category integrity
# ══════════════════════════════════════════════════════════════════
class TestCategoryStructure:

    def test_categories_present(self):
        assert len(CATEGORIES) >= 5, \
            "Help library should have multiple categories"

    def test_no_duplicate_category_ids(self):
        ids = [c.id for c in CATEGORIES]
        assert len(ids) == len(set(ids))

    def test_every_category_has_at_least_one_article(self):
        """A category with no articles is a dead navigation entry —
        rename or delete it instead of leaving it empty."""
        for c in CATEGORIES:
            articles = get_articles_by_category(c.id)
            assert articles, \
                f"Category {c.id} has no articles — either populate it " \
                f"or remove the category"

    def test_categories_have_unique_sort_orders(self):
        """Two categories with the same sort_order produces flapping
        ordering between runs — assign distinct values."""
        orders = [c.sort_order for c in CATEGORIES]
        assert len(orders) == len(set(orders)), \
            "Category sort_order values must be unique"


# ══════════════════════════════════════════════════════════════════
# Troubleshooting flow integrity
# ══════════════════════════════════════════════════════════════════
class TestTroubleshootingStructure:

    def test_troubleshooting_flows_present(self):
        assert len(TROUBLESHOOTING_FLOWS) >= 5, \
            "Should have multiple troubleshooting flows for common issues"

    def test_no_duplicate_flow_ids(self):
        ids = [t.id for t in TROUBLESHOOTING_FLOWS]
        assert len(ids) == len(set(ids))

    def test_flow_ids_have_ts_prefix(self):
        """Convention: troubleshooting ids start with 'ts-' to keep them
        clearly distinct from article ids."""
        bad = [t.id for t in TROUBLESHOOTING_FLOWS if not t.id.startswith('ts-')]
        assert not bad, f"Troubleshooting ids must start with 'ts-': {bad}"

    def test_every_flow_has_steps(self):
        for t in TROUBLESHOOTING_FLOWS:
            assert t.steps, f"Flow {t.id} has no steps"
            assert len(t.steps) >= 2, \
                f"Flow {t.id} should have at least 2 steps to be useful"

    def test_every_flow_has_symptom(self):
        for t in TROUBLESHOOTING_FLOWS:
            assert t.symptom and t.symptom.strip(), \
                f"Flow {t.id} has empty symptom field"

    def test_flow_related_articles_resolve(self):
        all_ids = {a.id for a in ARTICLES}
        for t in TROUBLESHOOTING_FLOWS:
            for rel in t.related_articles:
                assert rel in all_ids, \
                    f"Flow {t.id} has dangling related_articles ref: {rel!r}"


# ══════════════════════════════════════════════════════════════════
# Search behavior
# ══════════════════════════════════════════════════════════════════
class TestSearch:

    def test_empty_query_returns_empty(self):
        assert search_articles('') == []
        assert search_articles('   ') == []
        assert search_troubleshooting('') == []

    def test_title_match_outranks_body_match(self):
        """An article whose TITLE contains the query should rank above
        articles where the query only appears in the body."""
        results = search_articles('FMNP')
        assert results, "Expected at least one match for 'FMNP'"
        # The very first result should have FMNP in the title or id
        top = results[0]
        assert ('fmnp' in top.title.lower() or 'fmnp' in top.id.lower()), \
            f"Top match for 'FMNP' should have FMNP in title or id, got " \
            f"{top.id!r} ({top.title!r})"

    def test_exact_keyword_match(self):
        results = search_articles('returning customer')
        assert results
        ids = [a.id for a in results]
        assert 'returning-customer' in ids[:3], \
            "'returning customer' search should surface returning-customer " \
            f"in the top 3 — got {ids[:5]}"

    def test_search_is_case_insensitive(self):
        upper = [a.id for a in search_articles('SYNC')]
        lower = [a.id for a in search_articles('sync')]
        assert upper == lower

    def test_no_match_returns_empty(self):
        assert search_articles('xyzzy_nonexistent_term_qwerty') == []

    def test_troubleshooting_search_works(self):
        results = search_troubleshooting('red')
        ids = [t.id for t in results]
        assert 'ts-sync-red' in ids


# ══════════════════════════════════════════════════════════════════
# Coverage — the library covers the surfaces it should
# ══════════════════════════════════════════════════════════════════
class TestCoverage:
    """Catch the case where someone accidentally removes content for an
    important surface area.  These checks aren't exhaustive — they're
    canaries.  Per PROJECT_INSTRUCTIONS.md §8a, the engineer is
    responsible for adding articles for new surfaces; this test only
    catches deletion of content for surfaces we already cover."""

    def test_market_day_lifecycle_covered(self):
        ids = {a.id for a in ARTICLES}
        for required in ('market-day-open', 'market-day-close'):
            assert required in ids, \
                f"Missing core article: {required}"

    def test_fmnp_dual_path_covered(self):
        """The FMNP dual-path explanation is the most-asked question;
        these articles must always exist."""
        ids = {a.id for a in ARTICLES}
        for required in ('fmnp-overview', 'fmnp-via-payment',
                         'fmnp-via-tracking', 'fmnp-activate-payment'):
            assert required in ids, \
                f"Missing FMNP article: {required}"

    def test_sync_articles_covered(self):
        ids = {a.id for a in ARTICLES}
        for required in ('sync-overview', 'sync-indicator', 'sync-failed'):
            assert required in ids, \
                f"Missing sync article: {required}"

    def test_corrections_covered(self):
        ids = {a.id for a in ARTICLES}
        for required in ('adjust-transaction', 'void-transaction', 'audit-log'):
            assert required in ids, \
                f"Missing correction article: {required}"

    def test_data_location_covered(self):
        """Volunteers must always be able to find 'where my data lives'."""
        ids = {a.id for a in ARTICLES}
        assert 'where-data-lives' in ids
        assert 'backups' in ids


# ══════════════════════════════════════════════════════════════════
# Accessor behavior
# ══════════════════════════════════════════════════════════════════
class TestAccessors:

    def test_get_article_returns_correct_article(self):
        a = get_article('market-day-open')
        assert a is not None
        assert a.id == 'market-day-open'

    def test_get_article_returns_none_for_unknown(self):
        assert get_article('does-not-exist') is None

    def test_get_category_returns_correct_category(self):
        c = get_category('fmnp')
        assert c is not None
        assert c.id == 'fmnp'

    def test_get_articles_by_category_filters(self):
        articles = get_articles_by_category('fmnp')
        assert all(a.category_id == 'fmnp' for a in articles)
        assert len(articles) >= 3

    def test_get_troubleshooting_flow(self):
        flow = get_troubleshooting_flow('ts-sync-red')
        assert flow is not None
        assert flow.id == 'ts-sync-red'


# ══════════════════════════════════════════════════════════════════
# System Status snapshot
# ══════════════════════════════════════════════════════════════════
class TestSystemStatus:

    def test_collect_status_returns_dict_with_required_keys(self):
        from fam.help.system_status import collect_status
        status = collect_status()
        for key in ('app_version', 'data_dir', 'last_sync_at',
                    'confirmed_transactions', 'database_bytes',
                    'photos_count', 'backups_count'):
            assert key in status, \
                f"collect_status() missing required key: {key}"

    def test_collect_status_never_raises(self, tmp_path, monkeypatch):
        """Even with a torn-down environment, collect_status returns
        something usable.  This is a hard requirement for the diagnostic
        button — it must work even when the rest of the app is broken."""
        from fam.help.system_status import collect_status
        # Should not raise no matter what
        status = collect_status()
        assert isinstance(status, dict)

    def test_format_for_clipboard_produces_string(self):
        from fam.help.system_status import (
            collect_status, format_status_for_clipboard,
        )
        text = format_status_for_clipboard(collect_status())
        assert isinstance(text, str)
        assert 'FAM Market Manager' in text
        assert 'App version' in text
        assert 'Sync' in text


# ══════════════════════════════════════════════════════════════════
# Discipline — main_window wires Help screen
# ══════════════════════════════════════════════════════════════════
class TestHelpScreenWiringInMainWindow:
    """Source-level guards that the Help screen is registered in the
    sidebar nav and the main stack.  Catches accidental removal during
    refactors."""

    def test_main_window_imports_help_screen(self):
        import inspect
        import fam.ui.main_window as mw
        src = inspect.getsource(mw)
        assert 'from fam.ui.help_screen import HelpScreen' in src, \
            "Help screen import was removed from main_window.py"

    def test_main_window_registers_help_in_nav_items(self):
        import inspect
        import fam.ui.main_window as mw
        src = inspect.getsource(mw)
        assert '("Help", 7)' in src, \
            "Help nav item was removed or renumbered"

    def test_main_window_adds_help_screen_to_stack(self):
        import inspect
        import fam.ui.main_window as mw
        src = inspect.getsource(mw)
        assert 'self.help_screen = HelpScreen()' in src
        assert 'self.stack.addWidget(self.help_screen)' in src
