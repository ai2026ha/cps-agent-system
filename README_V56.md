# V56 累积后端文件一致性修复

本版本针对 Render 启动错误：

`ImportError: cannot import name 'PlayerMallPurchase' from 'app.schemas'`

原因是增量补丁只更新了 `main.py`，但线上 `schemas.py` 仍是旧版本，导致后端关键文件版本不一致。

V56 不改变 V55 已确认的业务规则，重点是把完整 backend 关键文件一次性同步，包括：

- `app/main.py`
- `app/models.py`
- `app/schemas.py`（包含 `PlayerMallPurchase`）
- `app/security.py`
- `app/database.py`
- `app/static/*`
- `tests/test_smoke.py`
- requirements 文件

部署时建议直接用本补丁的整个 `backend/` 目录覆盖 GitHub 中原有 `backend/`，不要只上传单个 Python 文件。
