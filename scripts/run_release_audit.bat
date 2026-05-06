@echo off
REM ============================================================
REM  FAM Market Manager — Release Audit Gate
REM
REM  Runs the three mandatory release gates (per
REM  docs/RELEASE_AUDIT_PROCEDURE.md):
REM    1. Pytest suite           (all tests pass)
REM    2. Production simulation  (300+ txn reconciliation)
REM    3. v1.9.9 stress sim      (mega-order + edge cases)
REM
REM  Halts on the first gate that fails.  Exit code 0 only when
REM  all three are clean.
REM
REM  Run from project root:  scripts\run_release_audit.bat
REM ============================================================

setlocal
pushd "%~dp0\.."

echo.
echo ============================================================
echo  FAM Market Manager - Release Audit Gate
echo ============================================================
echo  Running four mandatory release gates.  This takes about
echo  2 minutes total.  Halts on the first failure.
echo.

REM ------------------------------------------------------------
REM Gate 1 of 3 - Pytest suite
REM ------------------------------------------------------------
echo [1/3] Pytest suite ...
python -m pytest -q --tb=line
if errorlevel 1 goto :gate_fail_pytest
echo.

REM ------------------------------------------------------------
REM Gate 2 of 3 - Production simulation
REM ------------------------------------------------------------
echo [2/3] Production simulation ^(scripts\production_sim.py^) ...
python -m scripts.production_sim
if errorlevel 1 goto :gate_fail_prodsim
echo.

REM ------------------------------------------------------------
REM Gate 3 of 4 - v1.9.9 stress simulation
REM ------------------------------------------------------------
echo [3/4] v1.9.9 stress simulation ^(scripts\v1_9_9_stress_sim.py^) ...
python -m scripts.v1_9_9_stress_sim
if errorlevel 1 goto :gate_fail_stresssim
echo.

REM ------------------------------------------------------------
REM Gate 4 of 4 - Randomized fuzz smoke (5 seeds x 100 actions)
REM ------------------------------------------------------------
echo [4/4] Randomized fuzz smoke ^(scripts\fuzz_simulator.py^) ...
python -m scripts.fuzz_simulator
if errorlevel 1 goto :gate_fail_fuzzsmoke
echo.

REM ------------------------------------------------------------
REM All gates passed
REM ------------------------------------------------------------
echo ============================================================
echo  RELEASE AUDIT: PASS
echo  All four gates clean.  Safe to tag and build.
echo ============================================================
popd
endlocal
exit /b 0

:gate_fail_pytest
echo.
echo ============================================================
echo  RELEASE AUDIT: FAIL (gate 1 of 3 - pytest)
echo ============================================================
echo  At least one pytest test failed.  Re-run with:
echo      python -m pytest -v --tb=short
echo  to see which tests need attention.  Do not tag a release
echo  until every test passes.
echo ============================================================
popd
endlocal
exit /b 1

:gate_fail_prodsim
echo.
echo ============================================================
echo  RELEASE AUDIT: FAIL (gate 2 of 3 - production_sim)
echo ============================================================
echo  Production simulation reported [FAIL].  Re-run with:
echo      python -m scripts.production_sim
echo  and read the output carefully.  Reconciliation invariant
echo  failures are financial-integrity regressions.
echo ============================================================
popd
endlocal
exit /b 1

:gate_fail_stresssim
echo.
echo ============================================================
echo  RELEASE AUDIT: FAIL (gate 3 of 4 - v1_9_9_stress_sim)
echo ============================================================
echo  Stress simulation reported [FAIL].  Re-run with:
echo      python -m scripts.v1_9_9_stress_sim
echo  Inspect which Phase failed and which invariant did not hold.
echo  These are the strictest financial gates in the suite.
echo ============================================================
popd
endlocal
exit /b 1

:gate_fail_fuzzsmoke
echo.
echo ============================================================
echo  RELEASE AUDIT: FAIL (gate 4 of 4 - fuzz_simulator)
echo ============================================================
echo  Randomized fuzz simulation found a failure.  Re-run with:
echo      python -m scripts.fuzz_simulator --seed N --actions 100
echo  using the failing seed printed above.  The reproduction
echo  artifact (JSON action log) is saved in the temp dir.
echo  See docs\FUZZ_AUDIT.md for the fuzz framework guide.
echo ============================================================
popd
endlocal
exit /b 1
