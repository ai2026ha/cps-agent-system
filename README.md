# CPS 智能代理系统 v1

这是一个可运行的 CPS/渠道代理后台第一版，覆盖：

- 渠道管理：下级渠道、自动上下级归属、渠道结算
- 玩家管理：玩家账号、角色、区服、充值与登录数据
- 订单管理：平台币订单、商城订单、发货查询
- 商品管理：礼包列表、商品列表、库存
- 兑换码管理：CDK 批次、生成、兑换与统计
- 累充管理：累充规则、领取记录
- 邮件管理：发送邮件、发送记录（当前为模拟投递，预留游戏服接口）
- 智能运营提醒：发货异常、低库存、待结算、同 IP 多账号风险提示


## V4 左侧菜单结构

后台左侧导航已改为一级总系统 + 二级分功能的树形菜单：

- 一级总系统点击后展开 / 收起，不直接切换业务页面。
- 二级分功能点击后进入对应页面，并保持高亮。
- 一级字体更大、更粗；二级字体更小，层级更清楚。
- 数据总览保留为独立入口。

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

包含：系统自动代理 ID、登录账号、密码哈希、代理名称、系统随机邀请码、自动上级归属、今日流水、昨日流水、总流水、佣金比例、状态。

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

## V8 侧栏图标优化

- 一级功能系统使用统一线性 SVG 图标，无外部 CDN 依赖。
- 当前所属一级菜单增加左侧高亮条。
- 菜单 hover / 激活时图标与文字同步高亮。
- 二级分支保持纯文字，继续维持清晰的层级关系。

## V9 三级代理规则

- 超级管理员只能创建一级代理。
- 一级代理只能创建二级代理。
- 二级代理只能创建三级代理。
- 三级代理为末级，不能继续创建代理。
- 新增代理时必须设置“可开通下级代理数量”；三级代理固定为 0。
- 代理 ID、邀请码、上级归属继续由后端自动生成/绑定。
- 已有 Render/PostgreSQL 数据库会在启动时自动补充 `agent_level` 和 `subagent_limit` 字段，无需删库重建；旧账号使用兼容额度，三级代理自动设为 0。

## V9 渠道层级与查询
- 代理等级固定为三级：超管→一级代理→二级代理→三级代理。
- 创建下级时由系统自动绑定上级归属；三级代理不能继续创建下级。
- 每个一级/二级代理可设置直属下级开通数量上限，达到上限后前后端都会禁止继续创建。
- 代理ID与邀请码均由系统自动生成并保证唯一。
- 下级渠道页支持代理账号、代理ID、上级代理，以及流水自定义日期区间查询；选择日期后显示“查询区间流水”。
- 超级管理员的下级渠道查询范围为全部三级代理；代理账号仅能查看自己的直属下级。


## V10 超管与代理等级选择

- `ADMIN_USERNAME` 对应的后台账号在启动时会自动校正为 `superadmin`，兼容旧 Render PostgreSQL 数据库。
- 后台右上角显示当前账号及“超级管理员/代理等级”身份。
- 新增代理的“代理等级”下拉框不再展示不可用的灰色等级，而是要求主动选择当前账号唯一有权开通的等级：超管→一级、一级→二级、二级→三级。
- 三级代理仍是末级，不能继续开通下级。后端继续执行越级校验。


## V11 一级代理上级显示

- 超级管理员创建的一级代理，数据库 `parent_id` 仍保持为空，避免伪造代理父节点。
- 后台“下级渠道”中一级代理的“上级代理”统一显示为 **超管**。
- 二级、三级代理继续显示真实上级代理的代理ID。
- “上级代理查询”输入“超管”或“超级管理员”可直接筛选一级代理。

## V12 渠道管理调整

- 代理ID统一为 `A1`、`A2`、`A3`…，系统自动生成；历史 `AGxxxxxxxx` 会在部署启动时按代理内部序号自动迁移。
- 邀请码与代理ID完全一致。
- 代理列表不再显示“剩余名额”，下级代理额度限制仍由系统后台校验。
- 流水查询支持“昨日 / 今日 / 自定义时间”。
- 新增代理的登录账号、登录密码默认留空，并使用新密码表单属性避免浏览器自动填入旧凭据。
