# V32 累积修复补丁

## 修复内容

修复 Render 报错：

`ImportError: cannot import name 'PlayerCoinLedger' from 'app.models'`

V31 的小补丁只包含 `backend/app/main.py`，若 GitHub 仓库中的 `models.py` 没有同步到 V30/V31，就会导致启动失败。

本补丁是累积补丁，请把补丁内 `backend/` 目录覆盖到 GitHub 仓库根目录对应的 `backend/`。

它同时带上：
- `main.py`
- `models.py`（包含 PlayerCoinLedger）
- `schemas.py`
- 玩家管理前端文件
- 注册页
- 最新测试

## 保留规则

- 流水只统计真实支付成功的平台币订单。
- 商城订单不进入流水。
- 手工发放/收回平台币不进入流水、充值统计或分佣，只改变玩家余额并写审计记录。
- 普通代理不能编辑玩家。
