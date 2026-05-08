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
    """Forbid the bug class: a function-local import whose name is
    ALSO bound at module level.  Python's scoping rule promotes any
    name bound anywhere in a function body (including via local
    import) to a function-local for the WHOLE body, shadowing the
    module-level binding.  That combination is at minimum dead code
    (the module-level import already provides the name) and at worst
    a guaranteed ``UnboundLocalError`` when ANY code path references
    the name without first executing the local import.

    History — three near-misses in three releases:

      * **v2.0.1**  ``_adjust_transaction`` added
        ``from fam.models.transaction import get_transaction_by_id``
        BELOW the function's first reference to that name.  The
        first-reference check below caught this fix-forward.

      * **v2.0.7-intermediate**  ``_adjust_transaction`` added
        ``from fam.models.audit import log_action`` INSIDE a
        conditional branch (denom-method safety gate).  The first
        reference was the call right after the import (also inside
        the conditional, so first_ref > import_line and the v2.0.1
        check passed).  When the gate didn't fire (Cash-only
        adjustment), Python still treated ``log_action`` as a local
        because of the conditional binding, so later references at
        the save path raised ``UnboundLocalError: cannot access
        local variable 'log_action'`` and the user saw "Adjustment
        failed: ..." with no audit trail of what they tried to do.

      * **Generalized rule** (this test):  forbid the import
        outright.  The module-level binding is always sufficient;
        function-local re-imports of the same name buy nothing and
        risk this exact scoping footgun via any future conditional
        edit.
    """

    def test_no_function_local_imports_shadow_earlier_references(self):
        """v2.0.1 historical pin: caught when the local import was
        BELOW the first reference.  Kept for clarity — the broader
        check below subsumes it but the message here is more
        targeted for the specific 'first_ref < import_line' shape."""
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

    def test_no_function_local_imports_shadow_module_level_at_all(self):
        """v2.0.7 generalized pin: forbid ANY function-local import
        whose name is also bound at module level — regardless of
        whether the local import is above or below the first
        reference, regardless of whether it's in a conditional
        branch.

        Why this is the right rule:

          * If the module-level import provides the name, the local
            import is dead code at best.
          * If the local import is in a conditional branch, the name
            is function-local for the WHOLE body but bound only when
            that branch executes.  Any reference outside the branch
            UnboundLocalErrors when the branch is skipped.  The
            v2.0.7 ``log_action`` regression hit exactly this — the
            denom-method gate's import inside ``if denom_methods:``
            shadowed the module-level ``log_action`` for every
            non-denom adjustment.
          * If the local import is unconditional, it's still dead
            code shadowing the module-level binding — wasted work
            and a future conditional edit re-arms the bug.

        The fix is always the same: delete the local import.  If a
        contributor genuinely needs a circular-import-avoidance
        local import, the cleanest path is to import under a
        DIFFERENT name (``from x import y as _y_local``) so the
        rule below ignores it because ``_y_local`` isn't bound at
        module scope.
        """
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
                    bugs.append(
                        f"  {path}\n"
                        f"    fn '{node.name}' (def L{node.lineno})\n"
                        f"    L{import_line}: function-local import of "
                        f"'{name}' shadows module-level binding "
                        f"(first reference: "
                        f"{'L' + str(first_ref) if first_ref else 'none'})"
                    )
        assert not bugs, (
            "Function-local imports of names that are ALSO bound at "
            "module level are forbidden — see this test's docstring "
            "for the rationale and the fix.  In short: delete the "
            "local import (the module-level one already provides the "
            "name), or import under a different alias if you genuinely "
            "need a function-scoped binding.\n\n"
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
