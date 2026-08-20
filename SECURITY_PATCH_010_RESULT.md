# CPS V112 Security Patch 010

Completed first security delivery:

- Removed JWT fallback secret.
- Added production startup security check module.
- Added initial CoinService abstraction.
- Removed hard-coded docker credentials and switched to environment variables.

Remaining integration tasks:
- Replace all direct balance mutations with CoinService calls.
- Add database migrations for audit/version fields.
