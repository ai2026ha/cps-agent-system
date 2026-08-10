# V32 累积启动修复

本版修复 Render 启动错误：

`ImportError: cannot import name 'PlayerCoinLedger' from 'app.models'`

原因是 V31 小补丁只携带了 `main.py`，如果目标仓库的 `models.py` 仍是 V29 或更旧版本，就会出现主程序引用 `PlayerCoinLedger`、模型文件却没有该类的版本断层。

V32 完整版以 V31 完整源码为基线，并确保 `backend/app/models.py`、`schemas.py`、前端玩家管理文件与 V31 真实支付流水逻辑保持同一版本。

流水口径保持：仅真实已支付的平台币订单进入流水/充值/分佣；手工发放/收回平台币只变更余额并写审计，不进入流水。
