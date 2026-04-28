"""Search across help articles and troubleshooting flows.

Substring matching with simple relevance ranking.  No fuzzy matching,
no stemming — the library is small enough (~60 articles) that exact
substring search returns useful results immediately as the user types.

Ranking favors title hits over body hits over keyword hits.
"""

from typing import Optional

from fam.help.content import (
    ARTICLES,
    TROUBLESHOOTING_FLOWS,
    Article,
    TroubleshootingFlow,
)


def _normalize(text: str) -> str:
    """Lowercase and collapse whitespace for case-insensitive search."""
    return ' '.join(text.lower().split())


def _score_article(article: Article, query: str) -> int:
    """Return a relevance score, or 0 if no match.

    Higher score = better match.  Title and id matches outrank body /
    keyword matches because users typically search for what something
    is called, not what's in it.
    """
    q = _normalize(query)
    if not q:
        return 0

    score = 0
    title = _normalize(article.title)
    if q in title:
        # Exact title match is the strongest signal
        score += 100
        # Title-starts-with bonus — likely what the user typed
        if title.startswith(q):
            score += 50

    if q in article.id.lower():
        score += 80

    for kw in article.keywords:
        if q in kw.lower():
            score += 40
            break  # one keyword hit is enough

    if q in _normalize(article.body):
        score += 20

    if q in _normalize(article.category_id):
        score += 10

    return score


def search_articles(query: str, limit: int = 50) -> list[Article]:
    """Return articles matching ``query``, ranked by relevance.

    Empty / whitespace-only query returns an empty list — callers
    should display the full library themselves in that case.
    """
    q = _normalize(query)
    if not q:
        return []

    scored = [
        (_score_article(a, q), a)
        for a in ARTICLES
    ]
    matches = [(s, a) for s, a in scored if s > 0]
    matches.sort(key=lambda pair: (-pair[0], pair[1].title.lower()))
    return [a for _, a in matches[:limit]]


def _score_troubleshooting(flow: TroubleshootingFlow, query: str) -> int:
    """Same ranking model as articles, applied to troubleshooting flows."""
    q = _normalize(query)
    if not q:
        return 0

    score = 0
    if q in _normalize(flow.title):
        score += 100
        if _normalize(flow.title).startswith(q):
            score += 50
    if q in _normalize(flow.symptom):
        score += 60
    for kw in flow.keywords:
        if q in kw.lower():
            score += 40
            break
    if q in _normalize(' '.join(flow.steps)):
        score += 20
    return score


def search_troubleshooting(query: str,
                            limit: int = 50) -> list[TroubleshootingFlow]:
    """Return troubleshooting flows matching ``query``, ranked by relevance."""
    q = _normalize(query)
    if not q:
        return []

    scored = [
        (_score_troubleshooting(t, q), t)
        for t in TROUBLESHOOTING_FLOWS
    ]
    matches = [(s, t) for s, t in scored if s > 0]
    matches.sort(key=lambda pair: (-pair[0], pair[1].title.lower()))
    return [t for _, t in matches[:limit]]


def search_combined(query: str, limit_per_kind: int = 25
                     ) -> tuple[list[Article], list[TroubleshootingFlow]]:
    """Search both articles and troubleshooting flows in one call."""
    return (
        search_articles(query, limit=limit_per_kind),
        search_troubleshooting(query, limit=limit_per_kind),
    )
