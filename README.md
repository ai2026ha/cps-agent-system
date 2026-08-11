# CPS 智能代理系统 — V67

V67 在 V66 累积一致性修复版基础上，新增 **CDK 兑换角色/区服绑定**。

## V67 更新

- 玩家中心进入“CDK兑换”后，必须先选择当前账号下的区服 / 角色，再输入 CDK。
- 后端强制校验所选 `character_id` 必须属于当前登录玩家，不能通过接口伪造其他玩家角色。
- CDK 兑换成功时在 `redemption_codes` 中保存：`player_id`、`character_id`、`role_name`、`server_name`、`redeemed_at`。
- `role_name` / `server_name` 保存兑换时快照，后续角色改名或转服不会改写历史兑换归属。
- 历史已兑换 CDK 因没有可靠角色证据，不猜测、不自动回填角色。
- Render 启动时自动补充 `redemption_codes.character_id / role_name / server_name` 字段，无需删除 PostgreSQL。

## 当前玩家中心规则

- 纯手机端玩家中心。
- 功能顺序：平台币充值 → 购买礼包 → 领取累充 → 特权卡 → CDK兑换。
- 商城购买按所选区服角色记录订单。
- 真实平台币充值只计真实流水和代理分佣，不增加累充。
- 手工发放/收回平台币只调整余额，不计流水、分佣、累充。
- 角色购买网页礼包实际消耗的平台币，计入该角色当日累充和永久累充，不计真实流水与分佣。
- 领取累充时先选择区服角色，显示该角色当日累充与永久累充，再返回该角色可领取奖励。
- CDK兑换时先选择区服角色，兑换记录绑定该角色。

## Render 部署

如果你还没有提交 V66，直接使用 V67 完整版即可，不需要先上传 V66。

建议整个 `backend/` 一次性覆盖 GitHub 仓库现有 `backend/`，不要只上传 `main.py` 或单个模型文件。无需删除 PostgreSQL。

启动命令保持：

`uvicorn app.main:app --host 0.0.0.0 --port $PORT`
