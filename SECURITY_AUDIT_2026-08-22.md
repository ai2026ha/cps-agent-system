# CPS Security Audit — 2026-08-22

## Scope

Reviewed the complete uploaded source tree: FastAPI/SQLAlchemy backend, authentication and authorization, player/payment flows, file import, browser rendering, deployment configuration, dependencies, and tests.

## Remediated findings

1. **Forwarded-IP spoofing (high):** IP allow/deny and login blocking previously trusted `CF-Connecting-IP` and the leftmost `X-Forwarded-For` value from any caller. The application now trusts only the socket peer by default and uses the right-side trust boundary when `TRUSTED_PROXY_HOPS` is explicitly configured.
2. **Known vulnerable dependencies (high):** Upgraded FastAPI/Starlette, PyJWT, python-multipart, and pytest. `pip-audit` now reports no known vulnerabilities for `backend/requirements.txt`.
3. **Weak JWT context binding (medium):** Tokens now contain and validate issuer and audience claims, require all security claims, allow only five seconds of clock skew, and cap configured access/refresh lifetimes.
4. **Excessive production access-token lifetime (medium):** Deployment defaults changed from 720 minutes to 30 minutes.
5. **Player authentication and registration abuse (medium):** Added server-side rate limits for player login, captcha issuance, and registration, including `Retry-After` responses.
6. **SQL scanner finding:** Annotated the only dynamic migration literals as dialect-owned constants; no user-controlled SQL construction was found.
7. **Build hygiene:** Removed bundled Python bytecode/cache artifacts from the deliverable.

## Verification

- Python compile check: passed.
- New security regression tests: 5 passed.
- Existing suite under upgraded dependencies: 79 passed, 6 failed.
- Dependency audit: no known vulnerabilities found.
- Bandit high-severity scan: no high-severity findings.

The six remaining legacy test failures are not new application regressions: four assert an obsolete V107 build identifier even though the uploaded application identifies itself as V120, and two intentionally assume that arbitrary `X-Forwarded-For` input is trusted. Those two expectations conflict with the spoofing fix. The source package should update those legacy assertions before using a fully green CI gate.

## Residual operational risks

- Process-local token revocation and abuse-rate state do not coordinate across multiple workers/replicas and reset on restart. For horizontally scaled production, move these stores to Redis or another shared TTL store.
- The payment callback uses a static shared secret. Rotate it regularly, keep it out of source/logs, restrict the endpoint at the network edge, and prefer a timestamped HMAC signature with replay protection when integrating the real payment provider.
- Application security cannot eliminate infrastructure compromise. Production still requires TLS at the edge, database/network allowlists, encrypted backups, secret rotation, monitoring/alerting, and prompt dependency updates.
- `TRUSTED_PROXY_HOPS` must exactly match the deployment topology. Render manifests use `1`; direct/local deployments use `0`.

No audit can guarantee that future intrusion is impossible. This release materially reduces the identified attack surface and is suitable for staged deployment after secrets and proxy topology are verified.
