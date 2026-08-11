# V62 玩家中心：移除商城订单 + CDK兑换

## 本次调整

- 玩家中心移除“我的商城订单”展示与前端订单查询请求；后台商城订单查询保持不变。
- 玩家中心新增第五个功能入口：`CDK兑换`。
- 玩家输入 CDK 后，由当前登录玩家身份直接兑换，不允许前端提交/伪造 player_id。
- CDK 支持忽略大小写输入；兑换成功后记录玩家、兑换时间并更新批次已兑换数量。
- 已使用、无效或已停用批次的 CDK 会被后端拒绝。
- 沿用现有 `redemption_batches` / `redemption_codes` 数据结构，不新增数据库字段，不需要重建 PostgreSQL。

## 玩家中心入口顺序

`平台币充值 → 购买礼包 → 领取累充 → 特权卡 → CDK兑换`

## API

- `POST /api/player/cdk/redeem`
- 请求：`{"code":"CDK-..."}`
- 仅玩家 JWT 可使用，兑换记录自动绑定当前玩家。

## 检查

- 38 tests passed
- Python compile passed
- Admin JS syntax passed
- Player center JS syntax passed
