# V35 北京时间 + 代理登录时间补丁

从 V34 升级时，按目录结构覆盖到 GitHub 仓库根目录即可。

主要变化：
- 数据库仍保存 UTC；所有后台时间统一转换到 `Asia/Shanghai`（北京时间）显示。
- 玩家注册时间、最后登录时间显示北京时间。
- 代理列表新增“创建时间(北京时间)”和“最近登录(北京时间)”。
- 代理成功登录后自动写入最近登录时间。
- 旧 Render PostgreSQL 无需删除；启动时自动给 `agents` 补 `last_login_at` 字段。
- Render 配置显式加入 `BUSINESS_TIMEZONE=Asia/Shanghai`。

部署后建议 `Ctrl + F5` 强制刷新浏览器缓存。
