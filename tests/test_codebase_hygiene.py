"""Codebase-wide hygiene regressions
(v2.0.1 follow-up, 2026-05-05).

Two regressions converged in a single user-reported v2.0.1 incident:

  1. **Shadow-import UnboundLocalError.**  A function-local
     ``from fam.models.transaction import get_transaction_by_id``
     was added inside ``_adjust_transaction``, but the same name was
     already imported at module level.  Python's scoping rule
     promotes any name bound in a function body to a function-local
     for the WHOLE body, so the earlier reference at the top of
     the function raised ``UnboundLocalError`` before the local
     import had even executed.  The user clicked "Adjust" → instant
     crash.

  2. **CRITICAL log entries silently filtered out of the Error Log
     report.**  ``fam.app._global_exception_handler`` logs
     unhandled exceptions at CRITICAL level, but
     ``parse_log_file``'s default ``levels = {'ERROR', 'WARNING'}``
     excluded CRITICAL.  Result: app crashes that DID make it into
     ``fam_manager.log`` were silently dropped from the Reports →
     Error Log tab and the Cloud Sync "Error Log" sheet — exactly
     the entries a coordinator most needs to see.

These two pins prevent both regressions from re-emerging.
"""

import ast
import os

import pytest


# ════════════════════════════════════════════════════════════════════
# 1. Shadow-import UnboundLocalError detector
# ════════════════════════════════════════════════════════════════════


def _collect_module_level_names(tree: ast.Module) -> set:
    """All names bound at module scope by import statements."""
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add((alias.asname or alias.name).split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def _collect_function_local_imports(func: ast.FunctionDef) -> dict:
    """Returns ``{name: first_import_lineno}`` for names imported
    anywhere inside this function's body."""
    out: dict[str, int] = {}
    for node in ast.walk(func):
        if isinstance(node, ast.Import):
            for alias in node.names:
                n = (alias.asname or alias.name).split('.')[0]
                out.setdefault(n, node.lineno)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                n = alias.asname or alias.name
                out.setdefault(n, node.lineno)
    return out


def _first_reference_lineno(func: ast.FunctionDef, name: str):
    """Lineno of first ``ast.Name(id=name)`` reference in the
    function body, ignoring import statements themselves."""
    for node in ast.walk(func):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.Name) and node.id == name:
            return node.lineno
    return None


def _walk_fam_modules():
    """Yield ``(absolute_path, ast_module)`` for every .py file in
    the ``fam/`` package."""
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    fam_root = os.path.join(repo_root, 'fam')
    for dirpath, _dirs, files in os.walk(fam_root):
        for fn in files:
            if not fn.endswith('.py'):
                continue
            path = os.path.join(dirpath, fn)
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    src = f.read()
                yield path, ast.parse(src)
            except (OSError, SyntaxError):
                continue


class TestNoUnboundLocalShadows:
    """Forbid the exact bug class: a function-local import whose
    name is ALSO bound at module level AND is referenced earlier in
    the function body.  That combination is a guaranteed
    UnboundLocalError when the function runs."""

    def test_no_function_local_imports_shadow_earlier_references(self):
        bugs = []
        for path, tree in _walk_fam_modules():
            module_names = _collect_module_level_names(tree)
            for node in ast.walk(tree):
                if not isinstance(
                        node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                local_imps = _collect_function_local_imports(node)
                for name, import_line in local_imps.items():
                    if name not in module_names:
                        continue
                    first_ref = _first_reference_lineno(node, name)
                    if first_ref is not None and first_ref < import_line:
                        bugs.append(
                            f"  {path}\n"
                            f"    fn '{node.name}' (def L{node.lineno})\n"
                            f"    L{first_ref}: first reference to '{name}' "
                            f"would UnboundLocalError\n"
                            f"    L{import_line}: local import shadows "
                            f"module-level binding"
                        )
        assert not bugs, (
            "Function-local import shadows a module-level name AND a "
            "reference exists earlier in the function — guaranteed "
            "UnboundLocalError when the function runs.  Either remove "
            "the local import (the module-level one already provides "
            "the name) or move it above every reference.\n\n"
            + "\n\n".join(bugs)
        )


# ════════════════════════════════════════════════════════════════════
# 2. CRITICAL log entries reach the Error Log report
# ════════════════════════════════════════════════════════════════════


class TestErrorLogIncludesCritical:
    """``fam.app._global_exception_handler`` logs unhandled exceptions
    at CRITICAL level.  ``parse_log_file``'s default level filter
    must include CRITICAL or app crashes vanish from the report."""

    def test_default_level_set_includes_critical(self):
        from fam.utils.log_reader import parse_log_file
        # Black-box: write a synthetic log with one CRITICAL entry,
        # parse with defaults, assert it surfaces.
        import tempfile
        critical_line = (
            "2026-05-05 12:06:34 [CRITICAL] [v2.0.1] fam.app: "
            "Unhandled exception:\n")
        traceback_line = (
            "Traceback (most recent call last):\n"
            "  File \"x.py\", line 1, in <module>\n"
            "    foo\n"
            "UnboundLocalError: cannot access local variable 'x'\n"
        )
        with tempfile.NamedTemporaryFile(
                mode='w', suffix='.log', delete=False, encoding='utf-8') as f:
            f.write(critical_line)
            f.write(traceback_line)
            log_path = f.name

        try:
            entries = parse_log_file(log_path)
        finally:
            os.unlink(log_path)

        assert len(entries) == 1, (
            f"CRITICAL entry must be parsed and surfaced (got "
            f"{len(entries)} entries)")
        e = entries[0]
        assert e['level'] == 'CRITICAL'
        assert 'Unhandled exception' in e['message']
        assert 'UnboundLocalError' in e['traceback']

    def test_critical_entries_not_filtered_at_default(self):
        """Pin: the default ``levels`` argument of ``parse_log_file``
        includes CRITICAL.  The pre-v2.0.1 default of
        ``{'ERROR', 'WARNING'}`` silently dropped app-crash entries
        from the Error Log report — exactly the entries a
        coordinator most needs to see."""
        import inspect
        from fam.utils import log_reader
        src = inspect.getsource(log_reader.parse_log_file)
        # Source-pin: the default must mention CRITICAL.
        assert "'CRITICAL'" in src, (
            "parse_log_file default level set must include "
            "'CRITICAL' so unhandled-exception entries (logged at "
            "CRITICAL by fam.app._global_exception_handler) surface "
            "in the Error Log report")

    def test_warning_and_error_still_included_at_default(self):
        """Belt-and-suspenders: don't accidentally narrow the
        default by removing ERROR or WARNING."""
        from fam.utils.log_reader import parse_log_file
        import tempfile
        lines = [
            "2026-05-05 12:00:00 [WARNING] [v2.0.1] fam.x: warn msg\n",
            "2026-05-05 12:00:01 [ERROR] [v2.0.1] fam.y: err msg\n",
            "2026-05-05 12:00:02 [INFO] [v2.0.1] fam.z: info msg\n",
            "2026-05-05 12:00:03 [CRITICAL] [v2.0.1] fam.w: crit msg\n",
        ]
        with tempfile.NamedTemporaryFile(
                mode='w', suffix='.log', delete=False, encoding='utf-8') as f:
            f.writelines(lines)
            log_path = f.name
        try:
            entries = parse_log_file(log_path)
        finally:
            os.unlink(log_path)

        levels = {e['level'] for e in entries}
        assert 'WARNING' in levels
        assert 'ERROR' in levels
        assert 'CRITICAL' in levels
        # INFO must still be filtered out (the default isn't "everything").
        assert 'INFO' not in levels
