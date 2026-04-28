"""Help screen — structured help library + troubleshooting + system status.

Three tabs:
  1. Browse — categories on the left, article view on the right, search at top
  2. Troubleshooting — symptom-based decision-tree style entries
  3. System Status — live diagnostic snapshot with Copy Diagnostic Info button

All content comes from :mod:`fam.help.content`.  No AI generation.
"""

import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QSplitter, QTabWidget, QTextBrowser, QVBoxLayout,
    QWidget, QFrame, QMessageBox,
)

from fam.help import (
    ARTICLES, CATEGORIES, TROUBLESHOOTING_FLOWS,
    get_articles_by_category, get_article,
    search_articles, search_troubleshooting,
)
from fam.help.system_status import collect_status, format_status_for_clipboard
from fam.ui.styles import (
    BACKGROUND, LIGHT_GRAY, PRIMARY_GREEN, SUBTITLE_GRAY, TEXT_COLOR,
    ACCENT_GREEN,
)

logger = logging.getLogger('fam.ui.help_screen')


# ── Markdown-to-HTML helpers ────────────────────────────────────

def _markdown_to_html(md: str) -> str:
    """Render the article-body Markdown subset to HTML for QTextBrowser.

    Supports the subset used by content.py:
      - ## Headings
      - **bold**
      - *italic*
      - `inline code`
      - - bullet lists (consecutive lines starting with '- ')
      - | table | rows |  with --- separator
      - blank lines = paragraph breaks
      - ``` code blocks ```

    Intentionally simple — we control the input so we don't need a
    full Markdown parser.  If the input pattern set grows, swap in
    the ``markdown`` package via PyInstaller.
    """
    import re

    lines = md.split('\n')
    out = []
    in_table = False
    in_code = False
    in_list = False

    def _inline(s: str) -> str:
        # Inline code first so its contents aren't re-processed
        s = re.sub(r'`([^`]+)`',
                   r'<code style="background:#eee;padding:1px 4px;'
                   r'border-radius:3px;font-family:Consolas,monospace;">\1</code>',
                   s)
        s = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', s)
        s = re.sub(r'\*([^*]+)\*', r'<i>\1</i>', s)
        return s

    def _close_list():
        nonlocal in_list
        if in_list:
            out.append('</ul>')
            in_list = False

    def _close_table():
        nonlocal in_table
        if in_table:
            out.append('</table>')
            in_table = False

    for raw in lines:
        line = raw.rstrip()

        # Code-block fences
        if line.startswith('```'):
            _close_list()
            _close_table()
            if in_code:
                out.append('</pre>')
                in_code = False
            else:
                out.append(
                    '<pre style="background:#f4f4f4;padding:8px;'
                    'border-left:3px solid #ccc;font-family:Consolas,monospace;'
                    'font-size:11px;">')
                in_code = True
            continue
        if in_code:
            out.append(line.replace('<', '&lt;').replace('>', '&gt;'))
            continue

        # Headings
        if line.startswith('## '):
            _close_list()
            _close_table()
            out.append(
                f'<h3 style="color:{PRIMARY_GREEN};margin-top:18px;'
                f'margin-bottom:6px;font-size:14px;">{_inline(line[3:])}</h3>')
            continue

        # Tables (very simple: header | sep | rows)
        if line.startswith('|') and line.endswith('|'):
            cells = [c.strip() for c in line.strip('|').split('|')]
            # Separator row
            if all(c.replace('-', '').replace(':', '') == '' for c in cells):
                continue  # skip separator
            _close_list()
            if not in_table:
                out.append(
                    '<table style="border-collapse:collapse;margin:6px 0;">')
                in_table = True
                # First row of a new table is the header
                row_html = ''.join(
                    f'<th style="border:1px solid #ddd;padding:4px 8px;'
                    f'background:#eee;text-align:left;">{_inline(c)}</th>'
                    for c in cells)
            else:
                row_html = ''.join(
                    f'<td style="border:1px solid #ddd;padding:4px 8px;">'
                    f'{_inline(c)}</td>'
                    for c in cells)
            out.append(f'<tr>{row_html}</tr>')
            continue

        # Bullet list
        if line.startswith('- '):
            _close_table()
            if not in_list:
                out.append('<ul style="margin-top:4px;margin-bottom:4px;">')
                in_list = True
            out.append(f'<li>{_inline(line[2:])}</li>')
            continue

        # Blank line = paragraph break
        if not line:
            _close_list()
            _close_table()
            out.append('')
            continue

        # Default: paragraph
        _close_list()
        _close_table()
        out.append(f'<p style="margin:4px 0;">{_inline(line)}</p>')

    _close_list()
    _close_table()
    if in_code:
        out.append('</pre>')

    return f'<div style="color:{TEXT_COLOR};font-family:Inter,Arial,sans-serif;font-size:12px;line-height:1.5;">' \
           + '\n'.join(out) + '</div>'


# ── Help screen ────────────────────────────────────────────────

class HelpScreen(QWidget):
    """Sidebar-nav screen presenting the in-app help library."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_article_id = None
        self._build_ui()
        self._load_categories()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        title = QLabel("Help")
        title.setObjectName("screen_title")
        layout.addWidget(title)

        subtitle = QLabel(
            "Search the library, follow a troubleshooting guide, or check "
            "the system status.")
        subtitle.setStyleSheet(
            f"color:{SUBTITLE_GRAY};font-size:12px;background:transparent;")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        # Tab container — Walkthrough is the first / default tab so a new
        # volunteer sees the animated workflow training when they first
        # open the Help screen.
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_walkthrough_tab(), "Walkthrough")
        self.tabs.addTab(self._build_browse_tab(), "Browse")
        self.tabs.addTab(self._build_troubleshooting_tab(), "Troubleshooting")
        self.tabs.addTab(self._build_status_tab(), "System Status")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self.tabs)

    def _build_walkthrough_tab(self):
        """The 'Your Day at the Market' animated training walkthrough.

        Lazy-imported to keep help_screen lightweight when the user
        never visits the Walkthrough tab.
        """
        from fam.ui.help_walkthrough import WorkflowWalkthroughWidget
        self.walkthrough = WorkflowWalkthroughWidget()
        # Skip Tour button → switch to Browse (the categorized articles)
        self.walkthrough.skip_requested.connect(
            lambda: self.tabs.setCurrentIndex(1))
        return self.walkthrough

    # ── Browse tab ──────────────────────────────────────────────

    def _build_browse_tab(self) -> QWidget:
        wrapper = QWidget()
        v = QVBoxLayout(wrapper)
        v.setContentsMargins(0, 8, 0, 0)

        # Search bar
        search_row = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(
            "Search help — e.g. 'FMNP', 'returning customer', 'sync'…")
        self.search_input.setStyleSheet(
            "padding:6px 8px;border:1px solid #ccc;border-radius:4px;"
            "background:white;font-size:12px;")
        self.search_input.textChanged.connect(self._on_search_changed)
        search_row.addWidget(self.search_input, 1)
        v.addLayout(search_row)

        # Splitter: list on left, article body on right
        splitter = QSplitter(Qt.Horizontal)

        self.article_list = QListWidget()
        self.article_list.setStyleSheet(
            f"QListWidget {{ background:white;border:1px solid {LIGHT_GRAY};"
            f"border-radius:4px;padding:4px;font-size:12px; }}"
            f"QListWidget::item {{ padding:6px 8px; }}"
            f"QListWidget::item:selected {{"
            f"  background:{ACCENT_GREEN};color:white; }}")
        self.article_list.itemSelectionChanged.connect(self._on_article_selected)
        splitter.addWidget(self.article_list)

        self.article_view = QTextBrowser()
        self.article_view.setOpenLinks(False)
        self.article_view.setStyleSheet(
            f"QTextBrowser {{ background:white;"
            f"border:1px solid {LIGHT_GRAY};border-radius:4px;padding:14px; }}")
        self.article_view.anchorClicked.connect(self._on_article_link)
        splitter.addWidget(self.article_view)

        splitter.setSizes([300, 700])
        v.addWidget(splitter, 1)

        return wrapper

    def _load_categories(self):
        """Populate the article list with all articles, grouped by category."""
        self.article_list.clear()
        for cat in sorted(CATEGORIES, key=lambda c: c.sort_order):
            # Category header (non-selectable)
            header = QListWidgetItem(f"  {cat.name.upper()}")
            header.setFlags(Qt.NoItemFlags)
            f = QFont()
            f.setBold(True)
            f.setPointSize(10)
            header.setFont(f)
            header.setForeground(QGuiApplication.palette().mid())
            self.article_list.addItem(header)

            # Articles in this category
            for art in get_articles_by_category(cat.id):
                item = QListWidgetItem(f"     {art.title}")
                item.setData(Qt.UserRole, art.id)
                self.article_list.addItem(item)

        # Auto-select the first article so the right pane is never empty
        for i in range(self.article_list.count()):
            it = self.article_list.item(i)
            if it.data(Qt.UserRole):
                self.article_list.setCurrentRow(i)
                break

    def _on_search_changed(self, text: str):
        text = text.strip()
        if not text:
            self._load_categories()
            return

        self.article_list.clear()
        results = search_articles(text)
        if not results:
            placeholder = QListWidgetItem("(no matches)")
            placeholder.setFlags(Qt.NoItemFlags)
            self.article_list.addItem(placeholder)
            self.article_view.setHtml(
                f'<div style="color:{SUBTITLE_GRAY};padding:20px;">'
                f'No articles matched <b>"{text}"</b>.<br><br>'
                f'Try the <b>Troubleshooting</b> tab for symptom-based '
                f'guides, or clear the search to browse all articles.</div>')
            return

        for art in results:
            item = QListWidgetItem(f"  {art.title}")
            item.setData(Qt.UserRole, art.id)
            self.article_list.addItem(item)
        self.article_list.setCurrentRow(0)

    def _on_article_selected(self):
        items = self.article_list.selectedItems()
        if not items:
            return
        article_id = items[0].data(Qt.UserRole)
        if not article_id:
            return
        self._show_article(article_id)

    def _show_article(self, article_id: str):
        article = get_article(article_id)
        if article is None:
            self.article_view.setHtml(
                f'<div style="color:{SUBTITLE_GRAY};padding:20px;">'
                f'Article not found.</div>')
            return
        self._current_article_id = article_id

        body_html = _markdown_to_html(article.body)

        # Related-articles footer
        related_html = ''
        if article.related_articles:
            links = []
            for rel_id in article.related_articles:
                rel = get_article(rel_id)
                if rel is not None:
                    links.append(
                        f'<a href="article:{rel_id}" '
                        f'style="color:{ACCENT_GREEN};text-decoration:none;">'
                        f'{rel.title}</a>')
            if links:
                related_html = (
                    f'<hr style="margin-top:20px;margin-bottom:8px;">'
                    f'<div style="color:{SUBTITLE_GRAY};font-size:11px;'
                    f'font-family:Inter,Arial,sans-serif;">'
                    f'<b>Related:</b> ' + '  ·  '.join(links) + '</div>')

        title_html = (
            f'<div style="font-family:Inter,Arial,sans-serif;">'
            f'<h2 style="color:{PRIMARY_GREEN};margin-top:0;'
            f'margin-bottom:14px;font-size:18px;">{article.title}</h2></div>')

        self.article_view.setHtml(title_html + body_html + related_html)

    def _on_article_link(self, url):
        """Handle anchor clicks — for cross-article navigation only."""
        scheme = url.scheme()
        if scheme == 'article':
            target = url.path()
            if not target:
                # Some Qt versions parse 'article:id' with id in path part
                target = url.toString().split(':', 1)[-1]
            self._navigate_to_article(target)

    def _navigate_to_article(self, article_id: str):
        """Jump the list selection to the article with this id."""
        # Clear search so the article list is the full library
        if self.search_input.text():
            self.search_input.blockSignals(True)
            self.search_input.clear()
            self.search_input.blockSignals(False)
            self._load_categories()

        for i in range(self.article_list.count()):
            it = self.article_list.item(i)
            if it.data(Qt.UserRole) == article_id:
                self.article_list.setCurrentRow(i)
                self.article_list.scrollToItem(it)
                return

    # ── Troubleshooting tab ────────────────────────────────────

    def _build_troubleshooting_tab(self) -> QWidget:
        wrapper = QWidget()
        v = QVBoxLayout(wrapper)
        v.setContentsMargins(0, 8, 0, 0)

        ts_search = QLineEdit()
        ts_search.setPlaceholderText(
            "Search by symptom — e.g. 'sync red', 'photo missing', 'app slow'…")
        ts_search.setStyleSheet(
            "padding:6px 8px;border:1px solid #ccc;border-radius:4px;"
            "background:white;font-size:12px;")
        ts_search.textChanged.connect(self._on_ts_search_changed)
        v.addWidget(ts_search)
        self._ts_search = ts_search

        splitter = QSplitter(Qt.Horizontal)

        self.ts_list = QListWidget()
        self.ts_list.setStyleSheet(
            f"QListWidget {{ background:white;border:1px solid {LIGHT_GRAY};"
            f"border-radius:4px;padding:4px;font-size:12px; }}"
            f"QListWidget::item {{ padding:6px 8px; }}"
            f"QListWidget::item:selected {{"
            f"  background:{ACCENT_GREEN};color:white; }}")
        self.ts_list.itemSelectionChanged.connect(self._on_ts_selected)
        splitter.addWidget(self.ts_list)

        self.ts_view = QTextBrowser()
        self.ts_view.setStyleSheet(
            f"QTextBrowser {{ background:white;"
            f"border:1px solid {LIGHT_GRAY};border-radius:4px;padding:14px; }}")
        splitter.addWidget(self.ts_view)
        splitter.setSizes([320, 680])
        v.addWidget(splitter, 1)

        self._load_troubleshooting()
        return wrapper

    def _load_troubleshooting(self, query: str = ''):
        self.ts_list.clear()
        flows = (search_troubleshooting(query) if query
                 else list(TROUBLESHOOTING_FLOWS))
        if not flows:
            placeholder = QListWidgetItem("(no matches)")
            placeholder.setFlags(Qt.NoItemFlags)
            self.ts_list.addItem(placeholder)
            self.ts_view.setHtml(
                f'<div style="color:{SUBTITLE_GRAY};padding:20px;">'
                f'No troubleshooting guides matched. Try the '
                f'<b>Browse</b> tab.</div>')
            return

        for flow in flows:
            item = QListWidgetItem(f"  {flow.title}")
            item.setData(Qt.UserRole, flow.id)
            self.ts_list.addItem(item)

        self.ts_list.setCurrentRow(0)

    def _on_ts_search_changed(self, text: str):
        self._load_troubleshooting(text.strip())

    def _on_ts_selected(self):
        items = self.ts_list.selectedItems()
        if not items:
            return
        flow_id = items[0].data(Qt.UserRole)
        if not flow_id:
            return
        from fam.help.content import get_troubleshooting_flow
        flow = get_troubleshooting_flow(flow_id)
        if flow is None:
            return

        steps_html = '\n'.join(
            f'<p style="margin:6px 0;">{step}</p>'
            for step in flow.steps
        )
        title_html = (
            f'<h2 style="color:{PRIMARY_GREEN};margin-top:0;'
            f'margin-bottom:6px;font-size:18px;font-family:Inter,Arial,sans-serif;">'
            f'{flow.title}</h2>')
        symptom_html = (
            f'<p style="color:{SUBTITLE_GRAY};font-size:12px;font-style:italic;'
            f'margin-bottom:16px;font-family:Inter,Arial,sans-serif;">'
            f'<b>Symptom:</b> {flow.symptom}</p>')

        related_html = ''
        if flow.related_articles:
            links = []
            for rel_id in flow.related_articles:
                rel = get_article(rel_id)
                if rel is not None:
                    links.append(rel.title)
            if links:
                related_html = (
                    f'<hr style="margin-top:20px;margin-bottom:8px;">'
                    f'<div style="color:{SUBTITLE_GRAY};font-size:11px;'
                    f'font-family:Inter,Arial,sans-serif;">'
                    f'<b>See also (Browse tab):</b> '
                    + '  ·  '.join(f'<i>{t}</i>' for t in links) + '</div>')

        self.ts_view.setHtml(
            f'<div style="color:{TEXT_COLOR};font-family:Inter,Arial,sans-serif;font-size:12px;line-height:1.6;">'
            + title_html + symptom_html + steps_html + related_html
            + '</div>')

    # ── System Status tab ──────────────────────────────────────

    def _build_status_tab(self) -> QWidget:
        wrapper = QWidget()
        v = QVBoxLayout(wrapper)
        v.setContentsMargins(0, 8, 0, 0)

        intro = QLabel(
            "Diagnostic snapshot of this laptop. Click <b>Copy Diagnostic Info</b> "
            "to copy the report to your clipboard so you can paste it into a "
            "message to your coordinator.")
        intro.setWordWrap(True)
        intro.setStyleSheet(
            f"color:{SUBTITLE_GRAY};font-size:12px;background:transparent;")
        v.addWidget(intro)

        self.status_view = QTextBrowser()
        self.status_view.setStyleSheet(
            f"QTextBrowser {{ background:white;"
            f"border:1px solid {LIGHT_GRAY};border-radius:4px;padding:14px;"
            f"font-family:Consolas,monospace;font-size:12px; }}")
        v.addWidget(self.status_view, 1)

        button_row = QHBoxLayout()
        refresh_btn = QPushButton("⟳ Refresh")
        refresh_btn.setCursor(Qt.PointingHandCursor)
        refresh_btn.clicked.connect(self._refresh_status)

        copy_btn = QPushButton("📋 Copy Diagnostic Info")
        copy_btn.setCursor(Qt.PointingHandCursor)
        copy_btn.setStyleSheet(
            f"QPushButton {{ padding:8px 16px;background:{PRIMARY_GREEN};"
            f"color:white;border:none;border-radius:4px;font-weight:bold; }}"
            f"QPushButton:hover {{ background:{ACCENT_GREEN}; }}")
        copy_btn.clicked.connect(self._copy_status)

        button_row.addWidget(refresh_btn)
        button_row.addStretch()
        button_row.addWidget(copy_btn)
        v.addLayout(button_row)

        return wrapper

    def _refresh_status(self):
        try:
            status = collect_status()
            text = format_status_for_clipboard(status)
            # Render as <pre> for monospace alignment
            self.status_view.setHtml(
                f'<pre style="margin:0;color:{TEXT_COLOR};">'
                f'{text.replace("<", "&lt;").replace(">", "&gt;")}</pre>')
            self._status_cached_text = text
        except Exception:
            logger.exception("Could not refresh system status")
            self.status_view.setHtml(
                f'<div style="color:#d32f2f;padding:20px;">'
                f'Could not collect status — see fam_manager.log for details.'
                f'</div>')

    def _copy_status(self):
        text = getattr(self, '_status_cached_text', None)
        if text is None:
            self._refresh_status()
            text = getattr(self, '_status_cached_text', '')
        try:
            QGuiApplication.clipboard().setText(text)
            QMessageBox.information(
                self, "Copied",
                "Diagnostic info copied to clipboard.\n"
                "Paste into your coordinator's email or chat.")
        except Exception:
            logger.exception("Could not copy diagnostic info to clipboard")
            QMessageBox.warning(
                self, "Copy failed",
                "Could not copy to clipboard. Select and copy manually "
                "from the panel above.")

    def _on_tab_changed(self, idx: int):
        """Lazy-init the System Status tab and the Walkthrough tab on
        first activation."""
        tab_name = self.tabs.tabText(idx)
        if tab_name == "System Status":
            self._refresh_status()
        elif tab_name == "Walkthrough":
            # Auto-play the walkthrough the first time it's viewed
            if hasattr(self, 'walkthrough'):
                self.walkthrough.start_if_first_view()

    def showEvent(self, event):
        """When the Help screen first becomes visible, auto-play the
        walkthrough since it's the default tab."""
        super().showEvent(event)
        if hasattr(self, 'walkthrough') and self.tabs.currentIndex() == 0:
            self.walkthrough.start_if_first_view()

    def refresh(self):
        """Standard sidebar-nav refresh hook — re-read content + status."""
        self._load_categories()
        if self.tabs.currentWidget() and \
                self.tabs.tabText(self.tabs.currentIndex()) == "System Status":
            self._refresh_status()
