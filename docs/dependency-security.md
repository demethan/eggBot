# Dependency security baseline

## Runtime target

EggBot targets Python 3.12. The previous Python 3.8 runtime could not resolve patched aiohttp releases and is not an acceptable deployment target for this update.

Production must not switch interpreters until the staging and rollback gates in the modernization epic are complete.

## CVE-2026-44431 remediation

The former lock included `requests`, which pulled in vulnerable `urllib3==2.0.3`. EggBot used requests only for Mojang username validation.

Username validation now uses the project's asynchronous aiohttp dependency. Neither `requests` nor `urllib3` is present in the EggBot runtime lock.

## Verification

The Python 3.12 lock resolves aiohttp 3.14.3. On 2026-08-01, the installed project environment was audited with:

```bash
pip-audit --path .venv/lib/python3.12/site-packages --progress-spinner off
```

Result: `No known vulnerabilities found`.

Security scanning must be repeated in CI and immediately before production deployment because advisory data changes over time.

The `Tests and dependency audit` GitHub Actions workflow runs the Python 3.12 test
suite and `pip-audit` for every pull request and every push to `master`. Its token has
read-only repository contents permission.
