# CPS 智能代理系统 — V66

V66 是一次**后端文件一致性修复版**。修复 Render 启动时报：

`ImportError: cannot import name 'CharacterClaimRecord' from 'app.models'`

原因是线上 `main.py` 已是 V64/V65，而 `models.py` 仍是旧版本。V66 将 `main.py / models.py / schemas.py / security.py / database.py / static / tests` 按同一版本打包，避免局部覆盖导致导入断层。

## 当前玩家中心规则

- 手机端玩家中心。
- 平台币充值、购买礼包、领取累充、特权卡、CDK兑换。
- 商城购买按所选区服角色记录订单。
- 真实平台币充值只计真实流水和代理分佣，不增加累充。
- 手工发放/收回平台币只调整余额，不计流水、分佣、累充。
- 角色购买网页礼包实际消耗的平台币，计入该角色当日累充和永久累充，不计真实流水与分佣。
- 领取累充时先选择区服角色，显示该角色当日累充与永久累充，再返回该角色可领取奖励。

## Render 部署

建议直接用 V66 的整个 `backend/` 覆盖 GitHub 仓库现有 `backend/`，不要只上传 `main.py` 或单个模型文件。无需删除 PostgreSQL。

启动命令保持：

`uvicorn app.main:app --host 0.0.0.0 --port $PORT`
