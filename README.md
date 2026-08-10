# CPS 智能代理系统 v1

这是一个可运行的 CPS/渠道代理后台第一版，覆盖：

- 渠道管理：代理数据库、上下级渠道、渠道结算
- 玩家管理：玩家账号、角色、区服、充值与登录数据
- 订单管理：平台币订单、商城订单、发货查询
- 商品管理：礼包列表、商品列表、库存
- 兑换码管理：CDK 批次、生成、兑换与统计
- 累充管理：累充规则、领取记录
- 邮件管理：发送邮件、发送记录（当前为模拟投递，预留游戏服接口）
- 智能运营提醒：发货异常、低库存、待结算、同 IP 多账号风险提示

## 安全说明

系统**不会明文保存管理员、代理、玩家密码**，数据库只保存 Argon2 密码哈希。生产环境必须修改 `JWT_SECRET` 与管理员默认密码，并使用 HTTPS。

## Windows 双击启动（推荐）

1. 安装 **Python 3.11 / 3.12**，安装时勾选 `Add python.exe to PATH`。
2. 解压项目后，直接双击根目录的 **`启动系统.bat`**。
3. 第一次启动会自动创建 `.venv` 并安装依赖；后续直接启动。
4. 服务启动成功后会自动打开浏览器；也可以双击 **`打开后台.bat`**。
5. 后台地址：`http://127.0.0.1:8000`

默认管理员：

- 账号：`admin`
- 密码：`ChangeMe123!`

> 启动后的黑色命令窗口必须保持打开。要停止系统，在该窗口按 `Ctrl+C` 或直接关闭窗口。

> Windows 双击版默认使用本地 SQLite 数据库 `backend/cps.db`，不需要安装 PostgreSQL 或 Docker。


## GitHub + Render 公网部署（推荐）

项目根目录已经提供 `render.yaml`，可以通过 Render Blueprint 一次创建 Web Service + PostgreSQL。

### 1. 上传 GitHub

新建一个 GitHub 仓库，把 `cps-agent-system` 目录中的文件全部上传到仓库根目录。请不要上传 `.env`、本地 `*.db` 或 `.venv`，项目中的 `.gitignore` 已经默认忽略这些文件。

### 2. 在 Render 部署

1. 登录 Render，并连接你的 GitHub。
2. 新建 **Blueprint**，选择刚才的 GitHub 仓库。
3. Render 会读取仓库根目录的 `render.yaml`。
4. 首次创建时，Render 会要求你填写 `ADMIN_PASSWORD`，请使用强密码。
5. 创建完成后等待 Web Service 部署成功。
6. 打开 Render 分配的 `https://xxx.onrender.com` 地址即可访问 CPS 后台。

`render.yaml` 默认使用 **Free Web Service + Free Render Postgres**，适合测试。Render 免费 PostgreSQL 会在创建 30 天后到期，因此真实运营不要长期使用免费数据库。正式运营可以在 Blueprint 创建时改用付费资源，或者使用项目中的 `render-production.yaml` 作为生产配置参考。

### Render 部署结构

- GitHub：保存 CPS 源代码和版本历史。
- Render Web Service：运行 FastAPI 后台和管理界面。
- Render PostgreSQL：保存代理、玩家、订单、CDK、结算、邮件等业务数据。
- 每次 GitHub 默认分支有新提交，Render 自动重新部署 Web Service。

### 重要

- 不要把管理员密码、JWT Secret、数据库密码直接提交到 GitHub。
- `JWT_SECRET` 由 Render 自动生成。
- `ADMIN_PASSWORD` 在首次 Blueprint 部署时由你在 Render 页面输入。
- Render 免费 Web Service 长时间无访问会休眠，下一次打开可能需要等待实例重新启动。
- CPS 进入正式运营后，数据库应切换到付费 Render Postgres，并建立备份策略。

## 快速启动：Docker

```bash
cd cps-agent-system
docker compose up
```

浏览器打开：`http://localhost:8000`

默认管理员：

- 账号：`admin`
- 密码：`ChangeMe123!`

上线前必须修改 `docker-compose.yml` 中的默认密码与 JWT_SECRET。

## 本地 Python 启动

```bash
cd backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

默认使用 SQLite：`backend/cps.db`。生产推荐 PostgreSQL。

## 核心数据表

- `admin_users` 管理员
- `agents` 代理/渠道
- `players` 玩家
- `platform_coin_orders` 平台币订单
- `mall_orders` 商城订单
- `shipments` 发货记录
- `products` 礼包/商品
- `redemption_batches` CDK 批次
- `redemption_codes` 单个 CDK
- `settlements` 渠道结算
- `recharge_rules` 累充规则
- `claim_records` 领取记录
- `mail_records` 邮件发送记录

## 代理数据库字段

包含：代理 ID、登录账号、密码哈希、代理名称、邀请码、上级代理、今日流水、昨日流水、总流水、佣金比例、状态。

## 玩家数据库字段

包含：玩家 ID、账号、密码哈希、角色名、区服、所属代理、今日充值、总充值、最后登录时间、登录 IP。

## 下一阶段推荐

1. 接游戏登录/充值回调，订单由平台自动入库，避免后台人工录入。
2. 接游戏服发货 API，实现商品自动发货、失败重试。
3. 接游戏服邮件 API，实现单玩家、区服、全服邮件。
4. 增加 RBAC 权限：超级管理员 / 财务 / 客服 / 渠道管理员。
5. 增加代理独立后台，只能看自己的玩家、订单、下级渠道和佣金。
6. 增加结算审核、打款、账变流水与财务对账。
7. 增加风控：IP、设备、异常充值、订单频率、批量账号识别。
8. 对接支付渠道并实现签名验签、幂等、防重复回调。
