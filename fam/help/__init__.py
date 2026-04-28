"""Structured help library for FAM Market Manager.

This package is the **single source of truth** for in-app help content.
Volunteers reach this content through the Help screen (sidebar nav).
There is no AI / LLM involvement — every answer is curated text.

Structure:
    content.py      — categories, articles, troubleshooting flows
    search.py       — keyword/substring search across the library
    system_status.py — live diagnostic snapshot for the System Status tab

Discipline (see PROJECT_INSTRUCTIONS.md §8a):
    Any change to the user-facing surface (new screen, new button,
    new error condition, new sync state, changed workflow, changed
    label) MUST update the corresponding article in ``content.py`` in
    the same commit.  ``tests/test_help_content.py`` enforces structural
    correctness; the human discipline part is on the engineer.
"""

from fam.help.content import (
    ARTICLES,
    CATEGORIES,
    TROUBLESHOOTING_FLOWS,
    Article,
    Category,
    TroubleshootingFlow,
    get_article,
    get_articles_by_category,
    get_category,
)
from fam.help.search import search_articles, search_troubleshooting

__all__ = [
    'ARTICLES',
    'CATEGORIES',
    'TROUBLESHOOTING_FLOWS',
    'Article',
    'Category',
    'TroubleshootingFlow',
    'get_article',
    'get_articles_by_category',
    'get_category',
    'search_articles',
    'search_troubleshooting',
]
