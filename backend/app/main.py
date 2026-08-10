import os
import secrets
import shutil
import time as time_module
from datetime import datetime, date, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo
from fastapi import FastAPI, Depends, HTTPException, Query, Header
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, inspect, text
from sqlalchemy.orm import Session

from .database import Base, engine, SessionLocal, get_db
from .models import (
    AdminUser, Agent, Player, PlayerCharacter, PlayerCoinLedger, Product, PlatformCoinOrder, MallOrder, Shipment,
    RedemptionBatch, RedemptionCode, Settlement, RechargeRule, ClaimRecord, MailRecord,
)
from .schemas import (
    LoginIn, AgentCreate, AgentUpdate, PlayerRegister, PlayerAdminUpdate, ProductCreate, PlatformRechargeOrderCreate, PlatformPaymentSuccess, MallOrderCreate,
    ShipmentCreate, RedemptionBatchCreate, GenerateCodesIn, RedeemIn, SettlementCreate,
    RechargeRuleCreate, ClaimCreate, MailCreate,
)
from .security import hash_password, verify_password, create_token, current_admin, current_channel_user, current_user

app = FastAPI(title="CPS 智能代理系统", version="1.0.0")
STATIC_DIR = Path(__file__).parent / "static"
SUPERADMIN_REGISTRATION_CODE = "SUPERADMIN"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
BUSINESS_TZ = ZoneInfo(os.getenv("BUSINESS_TIMEZONE", "Asia/Shanghai"))


def business_today() -> date:
    return datetime.now(BUSINESS_TZ).date()


def business_date_bounds(start_date: date | None, end_date: date | None):
    """把业务时区自然日边界转换为数据库使用的 UTC naive datetime。"""
    start_dt = None
    end_dt = None
    if start_date:
        local_start = datetime.combine(start_date, time.min, tzinfo=BUSINESS_TZ)
        start_dt = local_start.astimezone(timezone.utc).replace(tzinfo=None)
    if end_date:
        local_end = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=BUSINESS_TZ)
        end_dt = local_end.astimezone(timezone.utc).replace(tzinfo=None)
    return start_dt, end_dt



def _read_text_file(path: str) -> str | None:
    try:
        return Path(path).read_text().strip()
    except (OSError, ValueError):
        return None


def _allowed_cpu_count() -> float:
    """返回当前容器/进程允许使用的 CPU 核数，至少为 1。"""
    try:
        status = _read_text_file('/proc/self/status') or ''
        for line in status.splitlines():
            if line.startswith('Cpus_allowed_list:'):
                spec = line.split(':', 1)[1].strip()
                total = 0
                for part in spec.split(','):
                    if not part:
                        continue
                    if '-' in part:
                        a, b = part.split('-', 1)
                        total += int(b) - int(a) + 1
                    else:
                        total += 1
                if total > 0:
                    return float(total)
    except Exception:
        pass
    return float(max(os.cpu_count() or 1, 1))


def _cpu_capacity() -> float:
    """优先读取 cgroup CPU 配额，让 Render/Docker 百分比更接近实例真实上限。"""
    cpu_max = _read_text_file('/sys/fs/cgroup/cpu.max')
    if cpu_max:
        parts = cpu_max.split()
        if len(parts) >= 2 and parts[0] != 'max':
            try:
                quota, period = float(parts[0]), float(parts[1])
                if quota > 0 and period > 0:
                    return max(quota / period, 0.01)
            except ValueError:
                pass
    return _allowed_cpu_count()


def _read_cgroup_cpu_seconds() -> float | None:
    stat = _read_text_file('/sys/fs/cgroup/cpu.stat')
    if stat:
        for line in stat.splitlines():
            key, *values = line.split()
            if key == 'usage_usec' and values:
                try:
                    return float(values[0]) / 1_000_000.0
                except ValueError:
                    return None
    # cgroup v1 fallback
    raw = _read_text_file('/sys/fs/cgroup/cpuacct/cpuacct.usage')
    if raw:
        try:
            return float(raw) / 1_000_000_000.0
        except ValueError:
            return None
    return None


def _read_proc_cpu_snapshot() -> tuple[float, float] | None:
    stat = _read_text_file('/proc/stat')
    if not stat:
        return None
    first = stat.splitlines()[0].split()
    if not first or first[0] != 'cpu':
        return None
    try:
        values = [float(x) for x in first[1:]]
    except ValueError:
        return None
    total = sum(values)
    idle = (values[3] if len(values) > 3 else 0) + (values[4] if len(values) > 4 else 0)
    return total, idle


def _cpu_percent(sample_seconds: float = 0.10) -> float | None:
    """短采样获取实时 CPU 使用率；优先按容器 CPU 配额归一化。"""
    c1 = _read_cgroup_cpu_seconds()
    if c1 is not None:
        t1 = time_module.monotonic()
        time_module.sleep(sample_seconds)
        c2 = _read_cgroup_cpu_seconds()
        t2 = time_module.monotonic()
        if c2 is not None and t2 > t1:
            pct = (c2 - c1) / (t2 - t1) / _cpu_capacity() * 100.0
            return round(min(max(pct, 0.0), 100.0), 1)

    p1 = _read_proc_cpu_snapshot()
    if p1 is None:
        return None
    time_module.sleep(sample_seconds)
    p2 = _read_proc_cpu_snapshot()
    if p2 is None:
        return None
    total_delta = p2[0] - p1[0]
    idle_delta = p2[1] - p1[1]
    if total_delta <= 0:
        return None
    pct = (total_delta - idle_delta) / total_delta * 100.0
    return round(min(max(pct, 0.0), 100.0), 1)


def _memory_metrics() -> tuple[float | None, int | None, int | None]:
    """返回 (百分比, 已用字节, 总字节)，优先使用 cgroup 容器内存限制。"""
    current = _read_text_file('/sys/fs/cgroup/memory.current')
    maximum = _read_text_file('/sys/fs/cgroup/memory.max')
    if current and maximum and maximum != 'max':
        try:
            used = int(current)
            total = int(maximum)
            if total > 0:
                return round(min(max(used / total * 100.0, 0.0), 100.0), 1), used, total
        except ValueError:
            pass

    # cgroup v1 fallback
    current = _read_text_file('/sys/fs/cgroup/memory/memory.usage_in_bytes')
    maximum = _read_text_file('/sys/fs/cgroup/memory/memory.limit_in_bytes')
    if current and maximum:
        try:
            used = int(current)
            total = int(maximum)
            # 某些无上限环境会返回极大的哨兵值，遇到这种情况退回 /proc/meminfo。
            if 0 < total < 1 << 60:
                return round(min(max(used / total * 100.0, 0.0), 100.0), 1), used, total
        except ValueError:
            pass

    meminfo = _read_text_file('/proc/meminfo')
    if meminfo:
        values = {}
        for line in meminfo.splitlines():
            if ':' not in line:
                continue
            key, value = line.split(':', 1)
            try:
                values[key] = int(value.strip().split()[0]) * 1024
            except (ValueError, IndexError):
                continue
        total = values.get('MemTotal')
        available = values.get('MemAvailable')
        if total and available is not None:
            used = max(total - available, 0)
            return round(min(max(used / total * 100.0, 0.0), 100.0), 1), used, total
    return None, None, None


def _disk_metrics() -> tuple[float | None, int | None, int | None]:
    try:
        root = os.path.abspath(os.sep)
        usage = shutil.disk_usage(root)
        pct = usage.used / usage.total * 100.0 if usage.total else 0.0
        return round(min(max(pct, 0.0), 100.0), 1), int(usage.used), int(usage.total)
    except OSError:
        return None, None, None


def _to_mb(value: int | None) -> float | None:
    return round(value / 1024 / 1024, 1) if value is not None else None


def _to_gb(value: int | None) -> float | None:
    return round(value / 1024 / 1024 / 1024, 2) if value is not None else None


def money(v):
    return float(v or 0)

def dt(v):
    """统一把数据库时间按北京时间显示。

    系统数据库中的历史/现有 DateTime 均按 UTC naive 保存；如果未来某个驱动返回
    aware datetime，也会先规范到 UTC，再转换到 Asia/Shanghai。这样 Render 部署在
    任何地区都不会影响后台显示时间。
    """
    if not v:
        return None
    if v.tzinfo is None:
        utc_value = v.replace(tzinfo=timezone.utc)
    else:
        utc_value = v.astimezone(timezone.utc)
    return utc_value.astimezone(BUSINESS_TZ).strftime("%Y-%m-%d %H:%M:%S")


def utc_now_naive() -> datetime:
    """数据库统一保存 UTC naive，展示时再转换为北京时间。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def real_paid_platform_turnover(
    db: Session,
    *,
    agent_ids: list[int] | None = None,
    player_ids: list[int] | None = None,
    start_dt: datetime | None = None,
    end_dt: datetime | None = None,
) -> Decimal:
    """统一流水口径：仅统计真实支付成功的平台币订单金额。

    真实支付订单必须同时满足 pay_status='paid' 且 paid_at 不为空。
    超管手工发放/收回平台币只写 PlayerCoinLedger，不进入订单表，因此永远不会
    被本函数计入流水，也不会产生代理分佣。商城订单同样不计入流水。
    """
    q = db.query(func.coalesce(func.sum(PlatformCoinOrder.amount), 0)).filter(
        PlatformCoinOrder.pay_status == "paid",
        PlatformCoinOrder.paid_at.is_not(None),
    )
    if agent_ids is not None:
        q = q.filter(PlatformCoinOrder.agent_id.in_(agent_ids))
    if player_ids is not None:
        q = q.filter(PlatformCoinOrder.player_id.in_(player_ids))
    if start_dt is not None:
        q = q.filter(PlatformCoinOrder.paid_at >= start_dt)
    if end_dt is not None:
        q = q.filter(PlatformCoinOrder.paid_at < end_dt)
    return Decimal(q.scalar() or 0)


# ---------- 统一后台权限模型（V20） ----------
# 所有账号共用同一个登录入口和后台地址；登录后按角色/代理等级返回权限清单。
# 菜单边界：
# - 超级管理员：全部系统。
# - 一级/二级代理：数据总览、渠道管理、玩家管理、订单管理。
# - 三级代理：数据总览、玩家管理、订单管理；三级为末级，不拥有任何渠道管理权限。
PERMISSION_MATRIX = {
    "superadmin": {
        "dashboard.view", "channels.view", "channels.create", "channels.edit_basic", "channels.edit_full",
        "settlements.view", "settlements.manage", "players.view", "players.manage",
        "orders.view", "orders.manage", "shipments.view", "shipments.manage",
        "products.view", "products.manage", "cdk.view", "cdk.manage",
        "recharge.view", "recharge.manage", "claims.view", "claims.manage",
        "mail.view", "mail.send", "system.rebuild", "system.metrics", "payment.test",
    },
    "agent_1": {
        "dashboard.view", "channels.view", "channels.create", "channels.edit_basic", "settlements.view",
        "players.view", "orders.view", "shipments.view",
    },
    "agent_2": {
        "dashboard.view", "channels.view", "channels.create", "channels.edit_basic", "settlements.view",
        "players.view", "orders.view", "shipments.view",
    },
    # 三级代理为末级：保留数据总览、玩家与订单查看权限，不能进入渠道管理，也不能新增代理。
    "agent_3": {
        "dashboard.view", "players.view", "orders.view", "shipments.view",
    },
}


def permission_key(principal, db: Session) -> str:
    if principal.actor_type == "admin":
        return "superadmin" if principal.role == "superadmin" else "admin"
    agent = db.get(Agent, principal.agent_pk)
    level = int(agent.agent_level or 1) if agent else 1
    return f"agent_{min(max(level, 1), 3)}"


def permissions_for(principal, db: Session) -> set[str]:
    return set(PERMISSION_MATRIX.get(permission_key(principal, db), set()))


def require_permission(code: str):
    def dependency(principal=Depends(current_user), db: Session = Depends(get_db)):
        if code not in permissions_for(principal, db):
            raise HTTPException(403, "当前账号无此操作权限")
        return principal
    return dependency


def scoped_agent_ids(db: Session, principal, include_self: bool = True) -> list[int]:
    """返回当前账号可查看的代理树主键。超管用空列表表示不限制。"""
    if principal.actor_type != "agent":
        return []
    root = int(principal.agent_pk)
    result = [root] if include_self else []
    frontier = [root]
    while frontier:
        children = [x[0] for x in db.query(Agent.id).filter(Agent.parent_id.in_(frontier)).all()]
        if not children:
            break
        result.extend(children)
        frontier = children
    return list(dict.fromkeys(result))


def identity_payload(principal, db: Session) -> dict:
    perms = sorted(permissions_for(principal, db))
    if principal.actor_type == "agent":
        agent = db.get(Agent, principal.agent_pk)
        return {
            "username": principal.username, "role": principal.role, "actor_type": "agent",
            "agent_id": agent.agent_id, "agent_level": int(agent.agent_level or 1),
            "subagent_limit": int(agent.subagent_limit or 0),
            "registration_path": f"/register/{agent.invite_code}",
            "permissions": perms,
        }
    return {
        "username": principal.username, "role": principal.role, "actor_type": "admin",
        "agent_id": None, "agent_level": 0, "subagent_limit": None,
        "registration_path": f"/register/{SUPERADMIN_REGISTRATION_CODE}" if principal.role == "superadmin" else None,
        "permissions": perms,
    }

def seed_admin():
    """确保部署配置中的后台账号始终是超级管理员。

    旧版本数据库里如果已经存在同名管理员，也会自动把 role 升级/修正为
    superadmin，避免仅新建数据库时才生效。密码不会在每次启动时覆盖。
    """
    db = SessionLocal()
    try:
        username = os.getenv("ADMIN_USERNAME", "admin")
        password = os.getenv("ADMIN_PASSWORD", "ChangeMe123!")
        admin = db.query(AdminUser).filter(AdminUser.username == username).first()
        if not admin:
            db.add(AdminUser(username=username, password_hash=hash_password(password), role="superadmin"))
            db.commit()
            return
        changed = False
        if admin.role != "superadmin":
            admin.role = "superadmin"
            changed = True
        if not admin.enabled:
            admin.enabled = True
            changed = True
        if changed:
            db.commit()
    finally:
        db.close()

def ensure_agent_hierarchy_columns():
    """兼容已上线数据库：为旧 agents 表补充三级代理字段并回填层级。"""
    inspector = inspect(engine)
    if "agents" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("agents")}
    with engine.begin() as conn:
        if "agent_level" not in columns:
            conn.execute(text("ALTER TABLE agents ADD COLUMN agent_level INTEGER"))
        if "subagent_limit" not in columns:
            conn.execute(text("ALTER TABLE agents ADD COLUMN subagent_limit INTEGER"))
        if "last_login_at" not in columns:
            conn.execute(text("ALTER TABLE agents ADD COLUMN last_login_at TIMESTAMP"))

        # 旧账号以前没有额度概念，为避免升级后突然无法继续开通，先给兼容额度；
        # 新创建账号必须显式设置额度。
        conn.execute(text("UPDATE agents SET subagent_limit = 9999 WHERE subagent_limit IS NULL"))
        rows = conn.execute(text("SELECT id, parent_id FROM agents")).mappings().all()
        parents = {int(r["id"]): (int(r["parent_id"]) if r["parent_id"] is not None else None) for r in rows}

        cache = {}
        def resolve_level(agent_pk, seen=None):
            if agent_pk in cache:
                return cache[agent_pk]
            seen = set() if seen is None else seen
            if agent_pk in seen:
                return 1
            seen.add(agent_pk)
            parent_pk = parents.get(agent_pk)
            level = 1 if parent_pk is None else min(resolve_level(parent_pk, seen) + 1, 3)
            cache[agent_pk] = level
            return level

        for agent_pk in parents:
            level = resolve_level(agent_pk)
            conn.execute(text("UPDATE agents SET agent_level = :level WHERE id = :id"), {"level": level, "id": agent_pk})
        # 三级代理是末级。
        conn.execute(text("UPDATE agents SET subagent_limit = 0 WHERE agent_level >= 3"))


def ensure_platform_order_columns():
    """V46：平台币订单改为充值支付自动记录，并补充商品/发货字段。

    历史已支付订单在旧版本中已经即时增加过玩家平台币余额，因此迁移时将其标记为
    发货成功，避免升级后误触“补发”造成重复到账。
    """
    inspector = inspect(engine)
    if "platform_coin_orders" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("platform_coin_orders")}
    with engine.begin() as conn:
        if "product_name" not in columns:
            conn.execute(text("ALTER TABLE platform_coin_orders ADD COLUMN product_name VARCHAR(120) DEFAULT '平台币充值'"))
        if "delivery_status" not in columns:
            conn.execute(text("ALTER TABLE platform_coin_orders ADD COLUMN delivery_status VARCHAR(20) DEFAULT 'pending'"))
        if "delivery_message" not in columns:
            conn.execute(text("ALTER TABLE platform_coin_orders ADD COLUMN delivery_message VARCHAR(255) DEFAULT ''"))
        if "delivered_at" not in columns:
            conn.execute(text("ALTER TABLE platform_coin_orders ADD COLUMN delivered_at TIMESTAMP"))
        conn.execute(text("UPDATE platform_coin_orders SET product_name = '平台币充值' WHERE product_name IS NULL OR product_name = ''"))
        conn.execute(text("UPDATE platform_coin_orders SET delivery_status = 'pending' WHERE delivery_status IS NULL OR delivery_status = ''"))
        # 旧版 paid 订单创建时已经把平台币直接加到余额，因此一律视为历史已发货。
        conn.execute(text("UPDATE platform_coin_orders SET delivery_status = 'success', delivered_at = COALESCE(delivered_at, paid_at, created_at), delivery_message = CASE WHEN delivery_message IS NULL OR delivery_message = '' THEN '历史订单已到账' ELSE delivery_message END WHERE pay_status = 'paid' AND delivery_status <> 'success'"))
        conn.execute(text("UPDATE platform_coin_orders SET delivery_message = '' WHERE delivery_message IS NULL"))


def ensure_player_admin_columns():
    """兼容已上线数据库：补充玩家状态与平台币余额字段。

    首次增加平台币余额字段时，会用历史已支付平台币订单回填余额。后续启动不会
    重算，因此不会覆盖超管手工发放/收回后的实际余额。
    """
    inspector = inspect(engine)
    if "players" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("players")}
    added_balance = "platform_coin_balance" not in columns
    with engine.begin() as conn:
        if "status" not in columns:
            conn.execute(text("ALTER TABLE players ADD COLUMN status VARCHAR(20) DEFAULT 'active'"))
        if added_balance:
            conn.execute(text("ALTER TABLE players ADD COLUMN platform_coin_balance BIGINT DEFAULT 0"))
        conn.execute(text("UPDATE players SET status = 'active' WHERE status IS NULL OR status = ''"))
        conn.execute(text("UPDATE players SET platform_coin_balance = 0 WHERE platform_coin_balance IS NULL"))
        if added_balance and "platform_coin_orders" in inspector.get_table_names():
            rows = conn.execute(text("SELECT player_id, COALESCE(SUM(platform_coin), 0) AS coins FROM platform_coin_orders WHERE pay_status = 'paid' GROUP BY player_id")).mappings().all()
            for row in rows:
                conn.execute(
                    text("UPDATE players SET platform_coin_balance = :coins WHERE id = :player_id"),
                    {"coins": int(row["coins"] or 0), "player_id": int(row["player_id"])},
                )


def ensure_player_character_data():
    """V34：把玩家角色改为一对多的“区服 + 角色名”绑定数据。

    新表由 Base.metadata.create_all 自动创建。对于历史玩家，如果旧 players 表里的
    role_name/server_name 已经是有效角色数据，则迁移为一条主角色记录。旧字段继续
    保留仅用于兼容，不再作为玩家列表的展示数据源。
    """
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "players" not in tables or "player_characters" not in tables:
        return
    db = SessionLocal()
    try:
        existing_player_ids = {row[0] for row in db.query(PlayerCharacter.player_id).distinct().all()}
        legacy_players = db.query(Player).filter(~Player.id.in_(existing_player_ids)).all() if existing_player_ids else db.query(Player).all()
        changed = False
        for player in legacy_players:
            role = (player.role_name or "").strip()
            server = (player.server_name or "").strip()
            if not role or not server or role == "未绑定" or server == "未绑定":
                continue
            db.add(PlayerCharacter(
                player_id=player.id,
                role_name=role,
                server_name=server,
                is_primary=True,
                last_seen_at=player.last_login_at,
            ))
            changed = True
        if changed:
            db.commit()
    finally:
        db.close()


def player_character_payloads(db: Session, player_ids: list[int]) -> dict[int, list[dict]]:
    """批量读取玩家角色，主角色优先，其次按最近记录和主键排序。"""
    if not player_ids:
        return {}
    rows = (
        db.query(PlayerCharacter)
        .filter(PlayerCharacter.player_id.in_(player_ids))
        .order_by(PlayerCharacter.player_id.asc(), PlayerCharacter.is_primary.desc(), PlayerCharacter.id.asc())
        .all()
    )
    result: dict[int, list[dict]] = {}
    for row in rows:
        result.setdefault(row.player_id, []).append({
            "id": row.id,
            "role_name": row.role_name,
            "server_name": row.server_name,
            "is_primary": bool(row.is_primary),
            "last_seen_at": dt(row.last_seen_at),
        })
    return result


def sync_real_payment_aggregates():
    """把历史缓存统计纠正为“真实已支付平台币订单”口径。

    旧版本曾把已支付商城订单加入代理流水/玩家充值。升级时统一重算代理流水与
    玩家充值金额，但绝不重算 platform_coin_balance，因此超管手工发放/收回的
    平台币余额会原样保留。
    """
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "platform_coin_orders" not in tables:
        return

    # 兼容早期版本：已支付订单若缺 paid_at，用创建时间回填一次。之后所有统计
    # 都严格依赖 paid_at，避免仅修改状态但没有支付时间的数据被误算。
    with engine.begin() as conn:
        conn.execute(text(
            "UPDATE platform_coin_orders SET paid_at = created_at "
            "WHERE pay_status = 'paid' AND paid_at IS NULL"
        ))

    db = SessionLocal()
    try:
        today = business_today()
        yesterday = today - timedelta(days=1)
        today_start, tomorrow_start = business_date_bounds(today, today)
        yesterday_start, _ = business_date_bounds(yesterday, yesterday)

        if "agents" in tables:
            for agent in db.query(Agent).all():
                ids = [agent.id]
                agent.today_turnover = real_paid_platform_turnover(
                    db, agent_ids=ids, start_dt=today_start, end_dt=tomorrow_start
                )
                agent.yesterday_turnover = real_paid_platform_turnover(
                    db, agent_ids=ids, start_dt=yesterday_start, end_dt=today_start
                )
                agent.total_turnover = real_paid_platform_turnover(db, agent_ids=ids)

        if "players" in tables:
            for player in db.query(Player).all():
                ids = [player.id]
                player.today_recharge = real_paid_platform_turnover(
                    db, player_ids=ids, start_dt=today_start, end_dt=tomorrow_start
                )
                player.total_recharge = real_paid_platform_turnover(db, player_ids=ids)
        db.commit()
    finally:
        db.close()


def ensure_agent_public_identity_format():
    """把历史代理统一迁移为 A1/A2/A3...，并让邀请码始终等于代理ID。

    A 后面的数字直接采用 agents 主键，因此天然唯一；数据库内其它业务表都通过
    agents.id 外键关联，不依赖旧的 AGxxxxxxxx 字符串，迁移不会破坏订单/玩家归属。
    """
    inspector = inspect(engine)
    if "agents" not in inspector.get_table_names():
        return
    with engine.begin() as conn:
        rows = conn.execute(text("SELECT id, agent_id, invite_code FROM agents ORDER BY id")).mappings().all()
        if not rows:
            return
        needs_migration = any(
            str(r["agent_id"] or "") != f"A{int(r['id'])}" or str(r["invite_code"] or "") != f"A{int(r['id'])}"
            for r in rows
        )
        if not needs_migration:
            return

        # 两阶段更新，避免历史数据里恰好存在 A1/A2 等值时触发 unique 冲突。
        prefix = "TMP" + secrets.token_hex(4).upper()
        for r in rows:
            temp_value = f"{prefix}{int(r['id'])}"
            conn.execute(
                text("UPDATE agents SET agent_id = :v, invite_code = :v WHERE id = :id"),
                {"v": temp_value, "id": int(r["id"])},
            )
        for r in rows:
            public_id = f"A{int(r['id'])}"
            conn.execute(
                text("UPDATE agents SET agent_id = :v, invite_code = :v WHERE id = :id"),
                {"v": public_id, "id": int(r["id"])},
            )


@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)
    ensure_agent_hierarchy_columns()
    ensure_platform_order_columns()
    ensure_player_admin_columns()
    ensure_player_character_data()
    ensure_agent_public_identity_format()
    sync_real_payment_aggregates()
    seed_admin()

@app.get("/healthz")
def healthz():
    return {"status": "ok"}

@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/register/{invite_code}")
def player_registration_page(invite_code: str):
    # 邀请码/代理ID由页面内的公开 API 再次校验，这里只负责返回注册页面。
    return FileResponse(STATIC_DIR / "register.html")

@app.post("/api/auth/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    admin = db.query(AdminUser).filter(AdminUser.username == body.username, AdminUser.enabled.is_(True)).first()
    if admin and verify_password(body.password, admin.password_hash):
        from .security import Principal
        principal = Principal(username=admin.username, role=admin.role, actor_type="admin")
        return {
            "access_token": create_token(admin.username, admin.role, actor_type="admin"),
            "token_type": "bearer", **identity_payload(principal, db),
        }

    agent = db.query(Agent).filter(Agent.username == body.username, Agent.status == "active").first()
    if agent and verify_password(body.password, agent.password_hash):
        # 最近登录时间统一记录为 UTC，API 输出时由 dt() 转为北京时间。
        agent.last_login_at = utc_now_naive()
        db.commit()
        from .security import Principal
        principal = Principal(username=agent.username, role="agent", actor_type="agent", agent_pk=agent.id, agent_id=agent.agent_id)
        return {
            "access_token": create_token(agent.username, "agent", actor_type="agent", actor_id=agent.id),
            "token_type": "bearer", **identity_payload(principal, db),
        }
    raise HTTPException(401, "账号或密码错误")

@app.get("/api/auth/me")
def auth_me(principal=Depends(current_user), db: Session = Depends(get_db)):
    return identity_payload(principal, db)

@app.get("/api/dashboard")
def dashboard(db: Session = Depends(get_db), principal=Depends(require_permission("dashboard.view"))):
    """统一后台数据总览：超管与代理按角色返回不同指标。"""
    today = business_today()
    yesterday = today - timedelta(days=1)
    today_start, tomorrow_start = business_date_bounds(today, today)
    yesterday_start, _ = business_date_bounds(yesterday, yesterday)
    agent_ids = scoped_agent_ids(db, principal)

    def scope_player_query(q):
        if principal.actor_type == "agent":
            q = q.filter(Player.agent_id.in_(agent_ids))
        return q

    def scope_order_query(q, model):
        if principal.actor_type == "agent":
            q = q.filter(model.agent_id.in_(agent_ids))
        return q

    def registration_count(start_dt=None, end_dt=None):
        q = scope_player_query(db.query(func.count(Player.id)))
        if start_dt is not None:
            q = q.filter(Player.created_at >= start_dt)
        if end_dt is not None:
            q = q.filter(Player.created_at < end_dt)
        return int(q.scalar() or 0)

    def turnover_between(start_dt=None, end_dt=None):
        """数据总览流水：统一只统计真实支付成功的平台币订单。"""
        scope_ids = agent_ids if principal.actor_type == "agent" else None
        return real_paid_platform_turnover(
            db, agent_ids=scope_ids, start_dt=start_dt, end_dt=end_dt
        )

    total_turnover = turnover_between()
    yesterday_turnover = turnover_between(yesterday_start, today_start)
    today_turnover = turnover_between(today_start, tomorrow_start)

    common = {
        "total_registrations": registration_count(),
        "yesterday_registrations": registration_count(yesterday_start, today_start),
        "today_registrations": registration_count(today_start, tomorrow_start),
        "total_turnover": money(total_turnover),
        "yesterday_turnover": money(yesterday_turnover),
        "today_turnover": money(today_turnover),
    }

    # 超管只展示平台全局注册、流水，以及待发货/异常和已兑换 CDK。
    if principal.actor_type != "agent":
        return {
            "dashboard_type": "superadmin",
            **common,
            "pending_abnormal": db.query(MallOrder).filter(MallOrder.delivery_status.in_(["waiting", "failed"])).count(),
            "redeemed_cdk": db.query(RedemptionCode).filter(RedemptionCode.status == "redeemed").count(),
        }

    # 一级/二级/三级代理展示自己权限树范围内的注册、流水与分佣。
    current_agent = db.get(Agent, principal.agent_pk)
    if not current_agent:
        raise HTTPException(401, "代理账号不存在")
    rate = Decimal(current_agent.commission_rate or 0)
    return {
        "dashboard_type": "agent",
        **common,
        "commission_rate": float(rate),
        "yesterday_commission": money(yesterday_turnover * rate),
        "today_commission": money(today_turnover * rate),
        "total_commission": money(total_turnover * rate),
    }


@app.get("/api/system/metrics")
def system_metrics(principal=Depends(require_permission("system.metrics"))):
    """超管实时资源监控。Render/Docker 下优先读取 cgroup；单项读取失败时其余指标仍返回。"""
    try:
        cpu = _cpu_percent()
    except Exception:
        cpu = None
    try:
        memory_pct, memory_used, memory_total = _memory_metrics()
    except Exception:
        memory_pct, memory_used, memory_total = None, None, None
    try:
        disk_pct, disk_used, disk_total = _disk_metrics()
    except Exception:
        disk_pct, disk_used, disk_total = None, None, None
    return {
        "cpu_percent": cpu,
        "memory_percent": memory_pct,
        "memory_used_mb": _to_mb(memory_used),
        "memory_total_mb": _to_mb(memory_total),
        "disk_percent": disk_pct,
        "disk_used_gb": _to_gb(disk_used),
        "disk_total_gb": _to_gb(disk_total),
        "updated_at": datetime.now(BUSINESS_TZ).isoformat(timespec="seconds"),
    }

# ---------- 渠道管理 ----------
def agent_identity_from_pk(agent_pk: int) -> str:
    return f"A{agent_pk}"


def agent_level_name(level: int | None) -> str:
    return {1: "一级代理", 2: "二级代理", 3: "三级代理"}.get(level, "未知等级")


def serialize_agent(a: Agent, db: Session):
    used = db.query(Agent).filter(Agent.parent_id == a.id).count()
    limit = int(a.subagent_limit or 0)
    today = business_today()
    yesterday = today - timedelta(days=1)
    today_start, tomorrow_start = business_date_bounds(today, today)
    yesterday_start, _ = business_date_bounds(yesterday, yesterday)
    direct_ids = [a.id]
    today_turnover = real_paid_platform_turnover(db, agent_ids=direct_ids, start_dt=today_start, end_dt=tomorrow_start)
    yesterday_turnover = real_paid_platform_turnover(db, agent_ids=direct_ids, start_dt=yesterday_start, end_dt=today_start)
    total_turnover = real_paid_platform_turnover(db, agent_ids=direct_ids)
    # 注册人数只统计通过该代理专属注册链接直接归属到该代理的玩家。
    registered_count = db.query(Player).filter(Player.agent_id == a.id).count()
    return {
        "id": a.id, "agent_id": a.agent_id, "username": a.username, "agent_name": a.agent_name,
        "invite_code": a.invite_code, "parent_id": a.parent_id,
        # 一级代理在数据库中没有普通代理父节点，但业务展示上归属于超级管理员。
        "parent_agent_id": a.parent.agent_id if a.parent else None,
        "parent_agent_display": a.parent.agent_id if a.parent else "超管",
        "agent_level": int(a.agent_level or 1), "agent_level_name": agent_level_name(a.agent_level),
        "subagent_limit": limit, "subagent_count": used, "registered_count": registered_count,
        "today_turnover": money(today_turnover), "yesterday_turnover": money(yesterday_turnover),
        "total_turnover": money(total_turnover), "commission_rate": float(a.commission_rate or 0),
        "status": a.status, "registration_path": f"/register/{a.invite_code}",
        "created_at": dt(a.created_at), "last_login_at": dt(a.last_login_at),
    }


def creation_capabilities(db: Session, principal):
    if principal.actor_type != "agent":
        if principal.role != "superadmin":
            return {
                "current_level": 0, "current_level_name": "管理员",
                "allowed_child_level": None, "allowed_child_level_name": None,
                "subagent_limit": None, "subagent_count": 0,
                "subagent_remaining": 0, "can_create": False,
                "reason": "仅超级管理员可以开通一级代理",
            }
        return {
            "current_level": 0, "current_level_name": "超级管理员",
            "allowed_child_level": 1, "allowed_child_level_name": "一级代理",
            "subagent_limit": None, "subagent_count": db.query(Agent).filter(Agent.parent_id.is_(None)).count(),
            "subagent_remaining": None, "can_create": True,
            "reason": "超级管理员可开通一级代理",
        }

    agent = db.get(Agent, principal.agent_pk)
    if not agent:
        raise HTTPException(401, "代理账号不存在")
    level = int(agent.agent_level or 1)
    used = db.query(Agent).filter(Agent.parent_id == agent.id).count()
    limit = int(agent.subagent_limit or 0)
    if level >= 3:
        return {
            "current_level": level, "current_level_name": agent_level_name(level),
            "allowed_child_level": None, "allowed_child_level_name": None,
            "subagent_limit": 0, "subagent_count": used, "subagent_remaining": 0,
            "can_create": False, "reason": "三级代理为末级代理，不能继续开通下级代理",
        }
    remaining = max(limit - used, 0)
    return {
        "current_level": level, "current_level_name": agent_level_name(level),
        "allowed_child_level": level + 1, "allowed_child_level_name": agent_level_name(level + 1),
        "subagent_limit": limit, "subagent_count": used, "subagent_remaining": remaining,
        "can_create": remaining > 0,
        "reason": "可以继续开通下级代理" if remaining > 0 else "直属下级代理名额已用完",
    }


@app.get("/api/agents/capabilities")
def agent_capabilities(db: Session = Depends(get_db), principal=Depends(require_permission("channels.view"))):
    return creation_capabilities(db, principal)


def agent_turnover_between(db: Session, agent_pk: int, start_date: date | None = None, end_date: date | None = None) -> Decimal:
    """按业务时区自然日区间统计真实已支付平台币流水，结束日期包含当天。"""
    start_dt, end_dt = business_date_bounds(start_date, end_date)
    return real_paid_platform_turnover(
        db, agent_ids=[agent_pk], start_dt=start_dt, end_dt=end_dt
    )


@app.get("/api/agents")
def list_agents(
    keyword: str = "",
    agent_account: str = "",
    public_agent_id: str = "",
    parent: str = "",
    turnover_period: str = "",
    turnover_start: date | None = Query(default=None),
    turnover_end: date | None = Query(default=None),
    db: Session = Depends(get_db),
    principal=Depends(require_permission("channels.view")),
):
    # 超级管理员可管理/查询全部三级渠道；一级/二级代理严格限制为自己的直属下级。
    q = db.query(Agent)
    if principal.actor_type == "agent":
        q = q.filter(Agent.parent_id == principal.agent_pk)

    turnover_period = turnover_period.strip().lower()
    if turnover_period not in {"", "today", "yesterday", "custom"}:
        raise HTTPException(400, "流水查询类型无效")
    if turnover_period == "today":
        turnover_start = turnover_end = business_today()
    elif turnover_period == "yesterday":
        yesterday = business_today() - timedelta(days=1)
        turnover_start = turnover_end = yesterday
    elif turnover_period == "custom":
        if not turnover_start or not turnover_end:
            raise HTTPException(400, "自定义流水查询需要选择开始日期和结束日期")
    elif turnover_start or turnover_end:
        # 兼容 V9-V11 已经存在的查询链接。
        turnover_period = "custom"

    if turnover_start and turnover_end and turnover_end < turnover_start:
        raise HTTPException(400, "流水查询结束日期不能早于开始日期")

    if keyword:
        like = f"%{keyword.strip()}%"
        q = q.filter((Agent.agent_name.like(like)) | (Agent.agent_id.like(like)) | (Agent.username.like(like)))
    if agent_account:
        q = q.filter(Agent.username.like(f"%{agent_account.strip()}%"))
    if public_agent_id:
        q = q.filter(Agent.agent_id.like(f"%{public_agent_id.strip()}%"))
    if parent:
        parent_term = parent.strip()
        # “超管”是一级代理的业务上级展示；数据库仍保持 parent_id=NULL，
        # 避免伪造一个普通代理记录作为超级管理员。
        if "超管" in parent_term or "超级管理员" in parent_term:
            q = q.filter(Agent.parent_id.is_(None))
        else:
            parent_like = f"%{parent_term}%"
            parent_ids = [x[0] for x in db.query(Agent.id).filter(
                (Agent.agent_id.like(parent_like)) |
                (Agent.username.like(parent_like)) |
                (Agent.agent_name.like(parent_like))
            ).all()]
            if not parent_ids:
                return []
            q = q.filter(Agent.parent_id.in_(parent_ids))

    result = []
    for a in q.order_by(Agent.id.desc()).all():
        row = serialize_agent(a, db)
        if turnover_period:
            row["period_turnover"] = money(agent_turnover_between(db, a.id, turnover_start, turnover_end))
            row["turnover_period"] = turnover_period
            row["turnover_start"] = str(turnover_start) if turnover_start else None
            row["turnover_end"] = str(turnover_end) if turnover_end else None
        result.append(row)
    return result


@app.post("/api/agents")
def create_agent(body: AgentCreate, db: Session = Depends(get_db), principal=Depends(require_permission("channels.create"))):
    caps = creation_capabilities(db, principal)
    expected_level = caps["allowed_child_level"]
    if not caps["can_create"]:
        raise HTTPException(403, caps["reason"])
    if body.agent_level != expected_level:
        raise HTTPException(403, f"当前账号只能开通{caps['allowed_child_level_name']}")
    if body.agent_level == 3 and body.subagent_limit != 0:
        raise HTTPException(400, "三级代理为末级代理，可开通下级代理数量必须为 0")

    # 最终额度校验在数据库事务里锁定上级代理，避免并发创建时突破名额上限。
    parent_id = None
    if principal.actor_type == "agent":
        parent = db.query(Agent).filter(Agent.id == principal.agent_pk).with_for_update().first()
        if not parent or parent.status != "active":
            raise HTTPException(401, "代理账号不存在或已停用")
        parent_id = parent.id
        used = db.query(Agent).filter(Agent.parent_id == parent.id).count()
        if int(parent.agent_level or 1) >= 3:
            raise HTTPException(403, "三级代理为末级代理，不能继续开通下级代理")
        if used >= int(parent.subagent_limit or 0):
            raise HTTPException(403, "直属下级代理名额已用完")

    # 归属不从前端接收：谁创建，谁就是上级。管理员创建的是一级代理。
    if db.query(Agent).filter(Agent.username == body.username).first():
        raise HTTPException(409, "代理登录账号已存在")

    # 先用一次性临时值插入以取得数据库主键，再把公开代理ID和邀请码统一设置为 A{主键}。
    # 这样即使并发创建，也不会出现两个请求同时拿到同一个 A 编号。
    temp_identity = "TMP" + secrets.token_hex(10).upper()
    row = Agent(
        agent_id=temp_identity,
        username=body.username,
        password_hash=hash_password(body.password),
        agent_name=body.agent_name,
        invite_code=temp_identity,
        parent_id=parent_id,
        agent_level=body.agent_level,
        subagent_limit=0 if body.agent_level == 3 else body.subagent_limit,
        commission_rate=body.commission_rate,
    )
    db.add(row)
    db.flush()
    public_identity = agent_identity_from_pk(row.id)
    if db.query(Agent).filter(Agent.id != row.id, Agent.agent_id == public_identity).first():
        db.rollback()
        raise HTTPException(500, "代理ID生成冲突，请重试")
    row.agent_id = public_identity
    row.invite_code = public_identity
    db.commit(); db.refresh(row)
    return {
        "id": row.id,
        "agent_id": row.agent_id,
        "invite_code": row.invite_code,
        "agent_level": row.agent_level,
        "agent_level_name": agent_level_name(row.agent_level),
        "subagent_limit": row.subagent_limit,
        "parent_agent_id": row.parent.agent_id if row.parent else None,
        "parent_agent_display": row.parent.agent_id if row.parent else "超管",
        "registration_path": f"/register/{row.invite_code}",
        "message": f"{agent_level_name(row.agent_level)}创建成功，代理ID：{row.agent_id}，邀请码：{row.invite_code}",
    }


def can_manage_agent(db: Session, principal, target: Agent) -> bool:
    """超管可管理全部代理；普通代理只能管理自己的直属下级。"""
    if principal.actor_type == "admin":
        return principal.role == "superadmin"
    return target.parent_id == principal.agent_pk


def assert_manage_agent(db: Session, principal, target: Agent):
    if not can_manage_agent(db, principal, target):
        raise HTTPException(403, "只能编辑自己有权限管理的代理")


def would_create_parent_cycle(db: Session, target_pk: int, new_parent_pk: int) -> bool:
    """防御性循环检测。三级层级正常情况下不会触发，但数据库异常时也必须拦截。"""
    seen = set()
    current = new_parent_pk
    while current is not None and current not in seen:
        if current == target_pk:
            return True
        seen.add(current)
        row = db.get(Agent, current)
        current = row.parent_id if row else None
    return False


def parent_candidates_for_agent(db: Session, target: Agent):
    level = int(target.agent_level or 1)
    if level == 1:
        return [{"value": "SUPERADMIN", "label": "超管"}]
    required_parent_level = level - 1
    rows = db.query(Agent).filter(Agent.agent_level == required_parent_level).order_by(Agent.id.asc()).all()
    result = []
    for row in rows:
        if row.id == target.id:
            continue
        used = db.query(Agent).filter(Agent.parent_id == row.id, Agent.id != target.id).count()
        # 当前上级始终保留在选项中（即使它已被封禁）；其它上级必须是正常状态且还有名额。
        if row.id != target.parent_id:
            if row.status != "active" or used >= int(row.subagent_limit or 0):
                continue
        status_suffix = " · 已封禁" if row.status != "active" else ""
        result.append({
            "value": row.agent_id,
            "label": f"{row.agent_id} · {row.agent_name} · {row.username}{status_suffix}",
        })
    return result


@app.get("/api/agents/{agent_pk}/edit-options")
def agent_edit_options(agent_pk: int, db: Session = Depends(get_db), principal=Depends(require_permission("channels.edit_basic"))):
    target = db.get(Agent, agent_pk)
    if not target:
        raise HTTPException(404, "代理不存在")
    assert_manage_agent(db, principal, target)
    is_superadmin = principal.actor_type == "admin" and principal.role == "superadmin"
    return {
        "agent": serialize_agent(target, db),
        "edit_mode": "superadmin" if is_superadmin else "direct_parent",
        "can_full_edit": is_superadmin,
        "can_change_parent": is_superadmin,
        "editable_fields": (
            ["agent_name", "commission_rate", "password", "status", "subagent_limit", "parent_agent_id"]
            if is_superadmin else ["agent_name", "commission_rate"]
        ),
        "parent_options": parent_candidates_for_agent(db, target) if is_superadmin else [],
    }


@app.patch("/api/agents/{agent_pk}")
def update_agent(agent_pk: int, body: AgentUpdate, db: Session = Depends(get_db), principal=Depends(require_permission("channels.edit_basic"))):
    target = db.query(Agent).filter(Agent.id == agent_pk).with_for_update().first()
    if not target:
        raise HTTPException(404, "代理不存在")
    assert_manage_agent(db, principal, target)

    fields = body.model_fields_set
    is_superadmin = principal.actor_type == "admin" and principal.role == "superadmin"
    # 普通代理只能维护自己直属下级的代理名称与佣金比例。
    # 密码、后台状态、下级额度、归属关系均为超管专属权限，后端必须强制拦截，不能只靠前端隐藏。
    allowed_fields = (
        {"agent_name", "commission_rate", "password", "status", "subagent_limit", "parent_agent_id"}
        if is_superadmin else {"agent_name", "commission_rate"}
    )
    forbidden_fields = fields - allowed_fields
    if forbidden_fields:
        raise HTTPException(403, "普通代理只能修改直属下级的代理名称和佣金比例")

    if "agent_name" in fields:
        new_name = (body.agent_name or "").strip()
        if not new_name:
            raise HTTPException(400, "代理名称不能为空")
        target.agent_name = new_name

    if "password" in fields and body.password:
        target.password_hash = hash_password(body.password)

    if "status" in fields:
        if body.status not in {"active", "disabled"}:
            raise HTTPException(400, "后台状态只能选择正常或封禁")
        target.status = body.status

    if "commission_rate" in fields and body.commission_rate is not None:
        target.commission_rate = body.commission_rate

    if "subagent_limit" in fields and body.subagent_limit is not None:
        level = int(target.agent_level or 1)
        new_limit = int(body.subagent_limit)
        opened = db.query(Agent).filter(Agent.parent_id == target.id).count()
        if level >= 3 and new_limit != 0:
            raise HTTPException(400, "三级代理为末级代理，可开通下级代理数量必须为 0")
        if new_limit < opened:
            raise HTTPException(400, f"可开通下级代理数量不能小于已开通数量 {opened}")
        target.subagent_limit = 0 if level >= 3 else new_limit

    if "parent_agent_id" in fields:
        level = int(target.agent_level or 1)
        requested = (body.parent_agent_id or "").strip().upper()
        # 空值明确表示“保持当前归属”，避免编辑其它字段时误改代理关系。
        if requested:
            if level == 1:
                if requested not in {"SUPERADMIN", "超管", "超级管理员"}:
                    raise HTTPException(400, "一级代理只能归属超管")
                target.parent_id = None
            else:
                if requested in {"SUPERADMIN", "超管", "超级管理员"}:
                    raise HTTPException(400, f"{agent_level_name(level)}必须归属{agent_level_name(level - 1)}")
                new_parent = db.query(Agent).filter(func.upper(Agent.agent_id) == requested).with_for_update().first()
                if not new_parent:
                    raise HTTPException(404, "新的上级代理不存在")
                if new_parent.id == target.id:
                    raise HTTPException(400, "代理不能归属自己")
                if int(new_parent.agent_level or 1) != level - 1:
                    raise HTTPException(400, f"{agent_level_name(level)}只能归属{agent_level_name(level - 1)}")
                if new_parent.status != "active":
                    raise HTTPException(400, "新的上级代理已封禁，不能接收下级")
                if would_create_parent_cycle(db, target.id, new_parent.id):
                    raise HTTPException(400, "更改归属会形成循环关系")
                if target.parent_id != new_parent.id:
                    used = db.query(Agent).filter(Agent.parent_id == new_parent.id, Agent.id != target.id).count()
                    if used >= int(new_parent.subagent_limit or 0):
                        raise HTTPException(400, "新的上级代理可开通下级数量已满")
                    target.parent_id = new_parent.id

    db.commit()
    db.refresh(target)
    return {"message": "代理资料修改成功", "agent": serialize_agent(target, db)}


@app.get("/api/agents/{agent_pk}/subagents")
def subagents(agent_pk: int, db: Session = Depends(get_db), principal=Depends(require_permission("channels.view"))):
    agent = db.get(Agent, agent_pk)
    if not agent:
        raise HTTPException(404, "代理不存在")
    if principal.actor_type == "agent" and agent.id != principal.agent_pk:
        raise HTTPException(403, "只能查看自己的直属下级")
    rows = db.query(Agent).filter(Agent.parent_id == agent_pk).order_by(Agent.id.desc()).all()
    return [serialize_agent(a, db) for a in rows]

@app.post("/api/agents/rebuild-turnover")
def rebuild_turnover(db: Session = Depends(get_db), _=Depends(current_admin)):
    # 与数据总览、结算完全同口径：仅真实已支付的平台币订单。
    today = business_today()
    yesterday = today - timedelta(days=1)
    today_start, tomorrow_start = business_date_bounds(today, today)
    yesterday_start, _ = business_date_bounds(yesterday, yesterday)
    for a in db.query(Agent).all():
        ids = [a.id]
        a.today_turnover = real_paid_platform_turnover(db, agent_ids=ids, start_dt=today_start, end_dt=tomorrow_start)
        a.yesterday_turnover = real_paid_platform_turnover(db, agent_ids=ids, start_dt=yesterday_start, end_dt=today_start)
        a.total_turnover = real_paid_platform_turnover(db, agent_ids=ids)
    db.commit()
    return {"message": "代理流水已按真实支付平台币订单重算"}

@app.get("/api/channel-settlements")
def channel_daily_turnover(
    account: str = "",
    public_agent_id: str = "",
    agent_level: int | None = Query(default=None, ge=1, le=3),
    # V42 正式支持开始/结束日期区间。date 仅保留给旧页面兼容。
    settlement_date: date | None = Query(default=None, alias="date"),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    db: Session = Depends(get_db),
    principal=Depends(require_permission("settlements.view")),
):
    """展示代理真实支付流水：默认总流水，支持北京时间单日/日期区间筛选。

    超管可查看全部代理，并可按一级/二级/三级筛选。普通代理只查看自己代理
    树中的下级代理；等级筛选只能选择当前账号有权查看的下级代理等级。一级代理
    可筛二级/三级，二级代理可筛三级；三级代理无渠道结算权限。

    流水只统计 pay_status=paid 且 paid_at 非空的平台币订单；手工平台币调整、
    商城订单、未支付订单均不会进入本接口。
    """
    # 兼容 V41 的 ?date=YYYY-MM-DD。如果同时传新旧参数，必须表示同一天，
    # 防止旧缓存页面与新页面混用时出现口径不确定。
    if settlement_date is not None:
        if start_date is not None and start_date != settlement_date:
            raise HTTPException(400, "旧版日期参数与开始日期不一致")
        if end_date is not None and end_date != settlement_date:
            raise HTTPException(400, "旧版日期参数与结束日期不一致")
        start_date = start_date or settlement_date
        end_date = end_date or settlement_date

    # 只填写一端时按单日处理，避免用户漏点一个日期后得到意外的无限区间。
    if start_date is not None and end_date is None:
        end_date = start_date
    elif end_date is not None and start_date is None:
        start_date = end_date

    if start_date is not None and end_date is not None and start_date > end_date:
        raise HTTPException(400, "开始日期不能晚于结束日期")

    if principal.actor_type == "agent":
        current_agent = db.get(Agent, principal.agent_pk)
        current_level = int(current_agent.agent_level or 1) if current_agent else 3
        allowed_filter_levels = {1: {2, 3}, 2: {3}, 3: set()}.get(current_level, set())
        if agent_level is not None and agent_level not in allowed_filter_levels:
            allowed_names = "、".join(agent_level_name(level) for level in sorted(allowed_filter_levels)) or "无下级代理"
            raise HTTPException(403, f"当前账号等级查询只能选择{allowed_names}")

    if start_date is None and end_date is None:
        start_dt, end_dt = None, None
        period_type = "total"
        period_label = "全部时间"
    else:
        start_dt, end_dt = business_date_bounds(start_date, end_date)
        if start_date == end_date:
            period_type = "day"
            period_label = str(start_date)
        else:
            period_type = "range"
            period_label = f"{start_date} 至 {end_date}"

    q = db.query(Agent)
    if principal.actor_type == "agent":
        visible_ids = scoped_agent_ids(db, principal, include_self=False)
        if not visible_ids:
            return {
                "period_type": period_type,
                "period_label": period_label,
                "date": str(start_date) if start_date and start_date == end_date else None,
                "start_date": str(start_date) if start_date else None,
                "end_date": str(end_date) if end_date else None,
                "rows": [],
            }
        q = q.filter(Agent.id.in_(visible_ids))

    account = account.strip()
    public_agent_id = public_agent_id.strip()
    if account:
        q = q.filter(Agent.username.like(f"%{account}%"))
    if public_agent_id:
        q = q.filter(Agent.agent_id.like(f"%{public_agent_id}%"))
    if agent_level is not None:
        q = q.filter(Agent.agent_level == agent_level)

    agents = q.order_by(Agent.id.asc()).all()
    agent_ids = [a.id for a in agents]
    turnover_map: dict[int, Decimal] = {}
    if agent_ids:
        filters = [
            PlatformCoinOrder.agent_id.in_(agent_ids),
            PlatformCoinOrder.pay_status == "paid",
            PlatformCoinOrder.paid_at.is_not(None),
        ]
        if start_dt is not None:
            filters.append(PlatformCoinOrder.paid_at >= start_dt)
        if end_dt is not None:
            filters.append(PlatformCoinOrder.paid_at < end_dt)
        paid_rows = (
            db.query(
                PlatformCoinOrder.agent_id,
                func.coalesce(func.sum(PlatformCoinOrder.amount), 0),
            )
            .filter(*filters)
            .group_by(PlatformCoinOrder.agent_id)
            .all()
        )
        turnover_map = {int(agent_pk): Decimal(amount or 0) for agent_pk, amount in paid_rows if agent_pk is not None}

    rows = []
    for agent in agents:
        turnover = turnover_map.get(agent.id, Decimal("0"))
        rate = Decimal(agent.commission_rate or 0)
        rows.append({
            "id": agent.id,
            "agent_id": agent.agent_id,
            "username": agent.username,
            "agent_name": agent.agent_name,
            "agent_level": int(agent.agent_level or 1),
            "agent_level_name": agent_level_name(agent.agent_level),
            "period_type": period_type,
            "period_label": period_label,
            "date": str(start_date) if start_date and start_date == end_date else None,
            "start_date": str(start_date) if start_date else None,
            "end_date": str(end_date) if end_date else None,
            "turnover": money(turnover),
            "commission_rate": float(rate),
            # 保留接口兼容字段；渠道结算表不展示佣金金额。
            "commission_amount": money(turnover * rate),
        })

    rows.sort(key=lambda row: (-Decimal(str(row["turnover"])), int(row["id"])))
    return {
        "period_type": period_type,
        "period_label": period_label,
        "date": str(start_date) if start_date and start_date == end_date else None,
        "start_date": str(start_date) if start_date else None,
        "end_date": str(end_date) if end_date else None,
        "rows": rows,
    }


@app.get("/api/settlements")
def list_settlements(db: Session = Depends(get_db), principal=Depends(require_permission("settlements.view"))):
    q = db.query(Settlement)
    if principal.actor_type == "agent": q = q.filter(Settlement.agent_id.in_(scoped_agent_ids(db, principal)))
    rows = q.order_by(Settlement.id.desc()).all()
    return [{"id": x.id, "settlement_no": x.settlement_no, "agent_id": x.agent_id, "period_start": str(x.period_start), "period_end": str(x.period_end),
             "turnover": money(x.turnover), "commission_rate": float(x.commission_rate), "commission_amount": money(x.commission_amount), "status": x.status, "paid_at": dt(x.paid_at)} for x in rows]

@app.post("/api/settlements")
def create_settlement(body: SettlementCreate, db: Session = Depends(get_db), _=Depends(current_admin)):
    agent = db.get(Agent, body.agent_id)
    if not agent: raise HTTPException(404, "代理不存在")
    if body.period_end < body.period_start: raise HTTPException(400, "结算结束日期不能早于开始日期")
    start, end = business_date_bounds(body.period_start, body.period_end)
    turnover = real_paid_platform_turnover(
        db, agent_ids=[agent.id], start_dt=start, end_dt=end
    )
    amount = turnover * Decimal(agent.commission_rate or 0)
    row = Settlement(settlement_no="ST" + datetime.utcnow().strftime("%Y%m%d%H%M%S") + secrets.token_hex(2).upper(), agent_id=agent.id,
                     period_start=body.period_start, period_end=body.period_end, turnover=turnover,
                     commission_rate=agent.commission_rate, commission_amount=amount)
    db.add(row)
    try: db.commit()
    except Exception:
        db.rollback(); raise HTTPException(409, "该代理此结算周期已存在")
    return {"id": row.id, "commission_amount": money(amount), "message": "结算单已生成"}

# ---------- 玩家管理 / 公开注册 ----------
def player_identity_from_pk(player_pk: int) -> str:
    return f"P{player_pk}"


def registration_channel(db: Session, invite_code: str):
    """解析公开注册链接。

    SUPERADMIN 是平台/超管直属注册渠道，对应玩家不绑定普通代理（agent_id=NULL）；
    A1/A2/... 等邀请码则绑定对应且处于启用状态的代理。
    """
    code = (invite_code or "").strip()
    if code.upper() == SUPERADMIN_REGISTRATION_CODE:
        return None
    agent = db.query(Agent).filter(Agent.invite_code == code, Agent.status == "active").first()
    if not agent:
        raise HTTPException(404, "注册地址无效或该代理后台已被封禁")
    return agent


@app.get("/api/public/registration/{invite_code}")
def registration_info(invite_code: str, db: Session = Depends(get_db)):
    agent = registration_channel(db, invite_code)
    if agent is None:
        return {
            "channel_type": "superadmin",
            "agent_id": "超管",
            "agent_name": "总平台直属",
            "agent_level": 0,
            "agent_level_name": "超级管理员",
        }
    return {
        "channel_type": "agent",
        "agent_id": agent.agent_id,
        "agent_name": agent.agent_name,
        "agent_level": int(agent.agent_level or 1),
        "agent_level_name": agent_level_name(agent.agent_level),
    }


@app.post("/api/public/registration/{invite_code}")
def register_player(invite_code: str, body: PlayerRegister, db: Session = Depends(get_db)):
    agent = registration_channel(db, invite_code)
    username = body.username.strip()
    if db.query(Player).filter(Player.username == username).first():
        raise HTTPException(409, "玩家账号已存在")

    # 玩家ID由系统生成。超管注册链接注册的玩家直属平台（agent_id=NULL），
    # 代理注册链接注册的玩家自动绑定对应代理。
    temp_player_id = "TMPP" + secrets.token_hex(10).upper()
    row = Player(
        player_id=temp_player_id,
        username=username,
        password_hash=hash_password(body.password),
        role_name="未绑定",
        server_name="未绑定",
        agent_id=agent.id if agent else None,
        today_recharge=0,
        total_recharge=0,
        last_login_at=None,
        last_login_ip=None,
    )
    db.add(row)
    db.flush()
    row.player_id = player_identity_from_pk(row.id)
    db.commit(); db.refresh(row)
    return {
        "id": row.id,
        "player_id": row.player_id,
        "username": row.username,
        "agent_id": agent.agent_id if agent else "超管",
        "agent_name": agent.agent_name if agent else "总平台直属",
        "channel_type": "agent" if agent else "superadmin",
        "message": "注册成功",
    }


@app.get("/api/players")
def list_players(
    account: str = "",
    role: str = "",
    parent: str = "",
    keyword: str = "",
    db: Session = Depends(get_db),
    principal=Depends(require_permission("players.view")),
):
    q = db.query(Player)
    if principal.actor_type == "agent":
        q = q.filter(Player.agent_id.in_(scoped_agent_ids(db, principal)))

    account = account.strip()
    role = role.strip()
    parent = parent.strip()
    keyword = keyword.strip()
    if account:
        q = q.filter(Player.username.like(f"%{account}%"))
    if role:
        role_like = f"%{role}%"
        role_player_ids = db.query(PlayerCharacter.player_id).filter(PlayerCharacter.role_name.like(role_like))
        q = q.filter(Player.id.in_(role_player_ids))
    if keyword:
        like = f"%{keyword}%"
        character_player_ids = db.query(PlayerCharacter.player_id).filter(
            (PlayerCharacter.role_name.like(like)) | (PlayerCharacter.server_name.like(like))
        )
        q = q.filter(
            (Player.player_id.like(like)) |
            (Player.username.like(like)) |
            (Player.id.in_(character_player_ids))
        )
    if parent:
        normalized = parent.lower()
        if normalized in {"超管", "超级管理员", "superadmin", "admin", "总平台"}:
            q = q.filter(Player.agent_id.is_(None))
        else:
            like = f"%{parent}%"
            agent_rows = db.query(Agent.id).filter(
                (Agent.agent_id.like(like)) | (Agent.username.like(like)) | (Agent.agent_name.like(like))
            ).all()
            agent_ids = [row[0] for row in agent_rows]
            q = q.filter(Player.agent_id.in_(agent_ids)) if agent_ids else q.filter(text("1=0"))

    rows = q.order_by(Player.id.desc()).all()
    agent_ids = {p.agent_id for p in rows if p.agent_id is not None}
    agents = {
        a.id: {"agent_id": a.agent_id, "username": a.username, "agent_name": a.agent_name}
        for a in db.query(Agent).filter(Agent.id.in_(agent_ids)).all()
    } if agent_ids else {}
    character_map = player_character_payloads(db, [p.id for p in rows])
    return [{
        "id": p.id,
        "player_id": p.player_id,
        "username": p.username,
        "characters": character_map.get(p.id, []),
        "primary_role_name": (character_map.get(p.id, [{}])[0].get("role_name") if character_map.get(p.id) else "未绑定"),
        "agent_id": p.agent_id,
        "agent_public_id": agents.get(p.agent_id, {}).get("agent_id") if p.agent_id is not None else "超管",
        "agent_account": agents.get(p.agent_id, {}).get("username") if p.agent_id is not None else "admin",
        "agent_name": agents.get(p.agent_id, {}).get("agent_name") if p.agent_id is not None else "超级管理员",
        "today_recharge": money(p.today_recharge),
        "total_recharge": money(p.total_recharge),
        "platform_coin_balance": int(p.platform_coin_balance or 0),
        "status": p.status or "active",
        "last_login_at": dt(p.last_login_at),
        "last_login_ip": p.last_login_ip,
        "created_at": dt(p.created_at),
    } for p in rows]


def player_owner_options(db: Session, current_agent_id: int | None = None):
    options = [{"value": "SUPERADMIN", "label": "超管"}]
    rows = db.query(Agent).order_by(Agent.agent_level.asc(), Agent.id.asc()).all()
    for agent in rows:
        if agent.status != "active" and agent.id != current_agent_id:
            continue
        options.append({
            "value": agent.agent_id,
            "label": f"{agent.agent_id}｜{agent.agent_name}｜{agent.username}",
        })
    return options


@app.get("/api/players/{player_pk}/edit")
def player_edit_info(
    player_pk: int,
    db: Session = Depends(get_db),
    _=Depends(require_permission("players.manage")),
):
    player = db.get(Player, player_pk)
    if not player:
        raise HTTPException(404, "玩家不存在")
    owner = db.get(Agent, player.agent_id) if player.agent_id else None
    return {
        "id": player.id,
        "player_id": player.player_id,
        "username": player.username,
        "status": player.status or "active",
        "owner_agent_id": owner.agent_id if owner else "SUPERADMIN",
        "owner_display": owner.agent_id if owner else "超管",
        "platform_coin_balance": int(player.platform_coin_balance or 0),
        "owner_options": player_owner_options(db, player.agent_id),
    }


@app.patch("/api/players/{player_pk}")
def update_player(
    player_pk: int,
    body: PlayerAdminUpdate,
    db: Session = Depends(get_db),
    principal=Depends(require_permission("players.manage")),
):
    # players.manage 只分配给超级管理员。普通代理即使绕过前端也不能修改玩家。
    player = db.get(Player, player_pk)
    if not player:
        raise HTTPException(404, "玩家不存在")

    fields = body.model_fields_set
    if "password" in fields and body.password:
        player.password_hash = hash_password(body.password)
    if "status" in fields:
        if body.status not in {"active", "disabled"}:
            raise HTTPException(400, "玩家状态仅支持 active / disabled")
        player.status = body.status
    if "owner_agent_id" in fields:
        owner_code = (body.owner_agent_id or "").strip()
        # 空值明确表示“保持当前归属”，只有超管主动选中新归属时才修改。
        if owner_code:
            if owner_code.upper() == "SUPERADMIN" or owner_code in {"超管", "超级管理员"}:
                player.agent_id = None
            else:
                owner = db.query(Agent).filter(func.upper(Agent.agent_id) == owner_code.upper()).first()
                if not owner:
                    raise HTTPException(404, "目标归属代理不存在")
                if owner.status != "active":
                    raise HTTPException(400, "目标归属代理已被封禁")
                player.agent_id = owner.id

    if "coin_action" in fields and body.coin_action:
        if body.coin_action not in {"issue", "reclaim"}:
            raise HTTPException(400, "平台币操作仅支持发放或收回")
        amount = int(body.coin_amount or 0)
        if amount <= 0:
            raise HTTPException(400, "请输入大于 0 的平台币数量")
        current = int(player.platform_coin_balance or 0)
        delta = amount if body.coin_action == "issue" else -amount
        if current + delta < 0:
            raise HTTPException(400, f"平台币余额不足，当前余额为 {current}")
        player.platform_coin_balance = current + delta
        db.flush()
        db.add(PlayerCoinLedger(
            player_id=player.id,
            action=body.coin_action,
            delta=delta,
            balance_after=int(player.platform_coin_balance),
            operator=principal.username,
            note="超管后台手工发放" if body.coin_action == "issue" else "超管后台手工收回",
        ))

    db.commit(); db.refresh(player)
    return {
        "message": "玩家资料修改成功",
        "id": player.id,
        "player_id": player.player_id,
        "status": player.status,
        "platform_coin_balance": int(player.platform_coin_balance or 0),
    }

# ---------- 订单管理 ----------
def apply_paid_platform_recharge(db: Session, player_id: int, agent_id: int | None, amount: Decimal):
    """仅真实已支付的平台币订单调用。手工发币/商城订单禁止调用。"""
    player = db.get(Player, player_id)
    if not player:
        raise HTTPException(404, "玩家不存在")
    player.today_recharge = Decimal(player.today_recharge or 0) + amount
    player.total_recharge = Decimal(player.total_recharge or 0) + amount
    if agent_id:
        agent = db.get(Agent, agent_id)
        if not agent:
            raise HTTPException(404, "代理不存在")
        agent.today_turnover = Decimal(agent.today_turnover or 0) + amount
        agent.total_turnover = Decimal(agent.total_turnover or 0) + amount


def normalize_payment_method(value: str) -> str:
    raw = (value or "").strip().lower()
    mapping = {
        "wechat": "wechat", "wx": "wechat", "weixin": "wechat", "微信": "wechat",
        "alipay": "alipay", "ali": "alipay", "支付宝": "alipay",
    }
    method = mapping.get(raw)
    if not method:
        raise HTTPException(400, "支付方式只支持微信或支付宝")
    return method


def require_payment_secret(x_payment_secret: str | None = Header(default=None)):
    """支付系统回调密钥。未配置时拒绝调用，避免公网伪造充值。"""
    expected = (os.getenv("PAYMENT_CALLBACK_SECRET") or "").strip()
    if not expected:
        raise HTTPException(503, "支付回调密钥未配置")
    if not x_payment_secret or not secrets.compare_digest(str(x_payment_secret), expected):
        raise HTTPException(401, "支付回调认证失败")
    return True


def generate_platform_order_no() -> str:
    local_now = datetime.now(BUSINESS_TZ)
    return f"PC{local_now.strftime('%Y%m%d%H%M%S')}{secrets.token_hex(3).upper()}"


def platform_order_status(row: PlatformCoinOrder) -> str:
    """平台币订单状态只反映支付状态；发货结果由 delivery_status 单独展示。"""
    return "paid" if row.pay_status == "paid" else "unpaid"


def _platform_delivery_ledger(db: Session, row: PlatformCoinOrder):
    """查找该订单已经成功入账的唯一业务凭证，用于补发幂等确认。"""
    return (
        db.query(PlayerCoinLedger)
        .filter(
            PlayerCoinLedger.player_id == row.player_id,
            PlayerCoinLedger.action == "recharge",
            PlayerCoinLedger.note == f"平台币订单 {row.order_no}",
        )
        .order_by(PlayerCoinLedger.id.desc())
        .first()
    )


def deliver_platform_order(db: Session, row: PlatformCoinOrder, operator: str = "system") -> bool:
    """
    给已支付订单发放平台币。

    发货状态必须以“实际入账事务是否成功提交”为准：
    - 仅支付成功，不代表发货成功；
    - 玩家余额增加 + 入账流水写入 + 订单 success 必须在同一数据库事务提交；
    - 事务提交失败则回滚，并把订单持久化为 failed；
    - 若已存在该订单的入账流水，则视为已确认到账，避免补发重复加币。
    """
    if row.pay_status != "paid":
        raise HTTPException(400, "未支付订单不能发货")

    # 已成功订单直接返回；同时兼容状态异常但已有入账流水的幂等恢复。
    existing_ledger = _platform_delivery_ledger(db, row)
    if row.delivery_status == "success" or existing_ledger:
        if existing_ledger and row.delivery_status != "success":
            row.delivery_status = "success"
            row.delivery_message = "平台币到账已确认"
            row.delivered_at = row.delivered_at or existing_ledger.created_at or utc_now_naive()
            db.commit(); db.refresh(row)
        return True

    player = db.get(Player, row.player_id)
    if not player:
        row.delivery_status = "failed"
        row.delivery_message = "发货失败：玩家不存在"
        row.delivered_at = None
        db.commit(); db.refresh(row)
        return False
    coins = int(row.platform_coin or 0)
    if coins <= 0:
        row.delivery_status = "failed"
        row.delivery_message = "发货失败：平台币数量无效"
        row.delivered_at = None
        db.commit(); db.refresh(row)
        return False

    try:
        player.platform_coin_balance = int(player.platform_coin_balance or 0) + coins
        db.add(PlayerCoinLedger(
            player_id=player.id,
            action="recharge",
            delta=coins,
            balance_after=int(player.platform_coin_balance),
            operator=operator,
            note=f"平台币订单 {row.order_no}",
        ))
        row.delivery_status = "success"
        row.delivery_message = "平台币到账已确认"
        row.delivered_at = utc_now_naive()
        # 只有整个入账事务真正提交成功，后台才允许显示“发货成功”。
        db.commit(); db.refresh(row)
        return row.delivery_status == "success"
    except Exception as exc:
        db.rollback()
        fresh = db.get(PlatformCoinOrder, row.id)
        # 极端情况下若事务其实已提交而响应阶段异常，用入账流水做幂等确认。
        if fresh and _platform_delivery_ledger(db, fresh):
            fresh.delivery_status = "success"
            fresh.delivery_message = "平台币到账已确认"
            fresh.delivered_at = fresh.delivered_at or utc_now_naive()
            db.commit(); db.refresh(fresh)
            return True
        if fresh:
            fresh.delivery_status = "failed"
            fresh.delivery_message = f"发货失败：{str(exc)[:180]}"
            fresh.delivered_at = None
            db.commit(); db.refresh(fresh)
        return False


def _create_platform_recharge_order(db: Session, body: PlatformRechargeOrderCreate, *, test_mode: bool = False):
    """创建平台币充值待支付订单。生产支付接口与超管支付测试页共用同一业务入口。"""
    player = db.query(Player).filter(Player.username == body.player_account).first()
    if not player:
        raise HTTPException(404, "玩家账号不存在")
    if player.status != "active":
        raise HTTPException(403, "玩家账号已封禁，不能充值")
    method = normalize_payment_method(body.payment_method)
    requested = (body.order_no or "").strip()
    order_no = requested or generate_platform_order_no()
    if test_mode and not requested:
        order_no = "TEST" + order_no
    if db.query(PlatformCoinOrder).filter(PlatformCoinOrder.order_no == order_no).first():
        raise HTTPException(409, "订单号已存在")
    row = PlatformCoinOrder(
        order_no=order_no,
        player_id=player.id,
        agent_id=player.agent_id,
        product_name=body.product_name.strip(),
        amount=body.amount,
        platform_coin=body.platform_coin,
        payment_channel=method,
        pay_status="pending",
        delivery_status="pending",
        delivery_message="测试下单，等待模拟支付" if test_mode else "",
    )
    db.add(row)
    db.commit(); db.refresh(row)
    return row


def _mark_platform_order_paid(db: Session, row: PlatformCoinOrder, *, operator: str = "payment-callback"):
    """
    将订单按支付成功处理。支付确认和发货确认分成两个事务：
    支付成功先落库并计入真实流水；随后再尝试发货。这样即使发货失败，
    订单仍保持“已支付”，发货列单独显示“失败”，由超管补发。
    """
    first_paid = row.pay_status != "paid"
    if first_paid:
        row.pay_status = "paid"
        row.paid_at = utc_now_naive()
        apply_paid_platform_recharge(db, row.player_id, row.agent_id, Decimal(row.amount))
        db.commit(); db.refresh(row)

    if row.delivery_status == "pending":
        deliver_platform_order(db, row, operator=operator)
        row = db.get(PlatformCoinOrder, row.id)
    else:
        db.refresh(row)

    return {
        "id": row.id,
        "order_no": row.order_no,
        "status": platform_order_status(row),
        "delivery_status": row.delivery_status,
        "message": "支付成功，平台币到账已确认" if row.delivery_status == "success" else "支付成功，但发货失败，请后台补发",
    }


@app.post("/api/payment/platform-orders")
def create_platform_recharge_order(
    body: PlatformRechargeOrderCreate,
    db: Session = Depends(get_db),
    _=Depends(require_payment_secret),
):
    """由玩家充值页面/支付服务创建待支付订单，不提供后台手工新增入口。"""
    row = _create_platform_recharge_order(db, body)
    return {"id": row.id, "order_no": row.order_no, "status": "unpaid", "message": "充值订单已创建，等待支付"}


@app.post("/api/payment/platform-orders/paid")
def platform_payment_success(
    body: PlatformPaymentSuccess,
    db: Session = Depends(get_db),
    _=Depends(require_payment_secret),
):
    """支付成功回调：第一次成功回调才计流水/充值，并自动发放平台币。幂等处理重复回调。"""
    row = db.query(PlatformCoinOrder).filter(PlatformCoinOrder.order_no == body.order_no).first()
    if not row:
        raise HTTPException(404, "平台币订单不存在")
    return _mark_platform_order_paid(db, row, operator="payment-callback")


@app.get("/api/payment-test/players")
def payment_test_players(
    keyword: str = Query(default=""),
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
    _=Depends(require_permission("payment.test")),
):
    """V47：仅超管支付测试页使用，搜索可充值的正常玩家。"""
    q = db.query(Player).filter(Player.status == "active")
    key = (keyword or "").strip()
    if key:
        like = f"%{key}%"
        q = q.filter((Player.username.ilike(like)) | (Player.player_id.ilike(like)))
    rows = q.order_by(Player.id.desc()).limit(limit).all()
    agent_ids = {p.agent_id for p in rows if p.agent_id is not None}
    agents = {a.id: a for a in db.query(Agent).filter(Agent.id.in_(agent_ids)).all()} if agent_ids else {}
    return [{
        "id": p.id,
        "player_id": p.player_id,
        "username": p.username,
        "owner_agent_id": agents[p.agent_id].agent_id if p.agent_id in agents else "超管",
        "owner_agent_name": agents[p.agent_id].agent_name if p.agent_id in agents else "总平台",
        "platform_coin_balance": int(p.platform_coin_balance or 0),
    } for p in rows]


@app.post("/api/payment-test/orders")
def payment_test_create_order(
    body: PlatformRechargeOrderCreate,
    db: Session = Depends(get_db),
    principal=Depends(require_permission("payment.test")),
):
    """V47：超管模拟玩家下单。不会绕过正式订单校验。"""
    row = _create_platform_recharge_order(db, body, test_mode=True)
    return {
        "id": row.id,
        "order_no": row.order_no,
        "status": "unpaid",
        "payment_method": row.payment_channel,
        "message": "模拟下单成功，订单已进入平台币订单列表",
    }


@app.post("/api/payment-test/orders/{order_no}/pay")
def payment_test_pay_order(
    order_no: str,
    db: Session = Depends(get_db),
    principal=Depends(require_permission("payment.test")),
):
    """V47：超管模拟支付成功，走与正式支付回调相同的计费/发货逻辑。"""
    row = db.query(PlatformCoinOrder).filter(PlatformCoinOrder.order_no == order_no).first()
    if not row:
        raise HTTPException(404, "测试订单不存在")
    if not str(row.order_no).startswith("TEST"):
        raise HTTPException(403, "支付测试页只能操作 TEST 测试订单")
    return _mark_platform_order_paid(db, row, operator=f"payment-test:{principal.username}")


@app.get("/api/orders/platform")
def platform_orders(
    order_no: str | None = Query(default=None),
    account: str | None = Query(default=None),
    payment_method: str | None = Query(default=None),
    status: str | None = Query(default=None),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    db: Session = Depends(get_db),
    principal=Depends(require_permission("orders.view")),
):
    q = db.query(PlatformCoinOrder, Player).join(Player, Player.id == PlatformCoinOrder.player_id)
    if principal.actor_type == "agent":
        q = q.filter(PlatformCoinOrder.agent_id.in_(scoped_agent_ids(db, principal)))
    if order_no and order_no.strip():
        q = q.filter(PlatformCoinOrder.order_no.ilike(f"%{order_no.strip()}%"))
    if account and account.strip():
        q = q.filter(Player.username.ilike(f"%{account.strip()}%"))
    if payment_method and payment_method.strip():
        q = q.filter(PlatformCoinOrder.payment_channel == normalize_payment_method(payment_method))
    # V50：平台币充值订单支持按北京时间创建日期查询。只填一端时按单日处理。
    if start_date is not None and end_date is None:
        end_date = start_date
    elif end_date is not None and start_date is None:
        start_date = end_date
    if start_date is not None and end_date is not None:
        if start_date > end_date:
            raise HTTPException(400, "开始日期不能晚于结束日期")
        start_dt, end_dt = business_date_bounds(start_date, end_date)
        if start_dt is not None:
            q = q.filter(PlatformCoinOrder.created_at >= start_dt)
        if end_dt is not None:
            q = q.filter(PlatformCoinOrder.created_at < end_dt)

    wanted_status = (status or "").strip().lower()
    if wanted_status:
        if wanted_status == "unpaid":
            q = q.filter(PlatformCoinOrder.pay_status != "paid")
        elif wanted_status == "paid":
            # 支付状态与发货状态彻底分离：已支付筛选包含发货成功和发货失败的订单。
            q = q.filter(PlatformCoinOrder.pay_status == "paid")
        else:
            raise HTTPException(400, "状态只支持未支付、已支付")

    rows = q.order_by(PlatformCoinOrder.created_at.desc(), PlatformCoinOrder.id.desc()).all()
    return [{
        "id": order.id,
        "order_no": order.order_no,
        "player_account": player.username,
        "product_name": order.product_name or "平台币充值",
        "amount": money(order.amount),
        "payment_method": order.payment_channel,
        "status": platform_order_status(order),
        "pay_status": order.pay_status,
        "delivery_status": order.delivery_status,
        "delivery_message": order.delivery_message or "",
        "created_at": dt(order.created_at),
        "paid_at": dt(order.paid_at),
        "delivered_at": dt(order.delivered_at),
    } for order, player in rows]


@app.post("/api/orders/platform/{order_id}/resend")
def resend_platform_order(
    order_id: int,
    db: Session = Depends(get_db),
    principal=Depends(require_permission("orders.manage")),
):
    row = db.get(PlatformCoinOrder, order_id)
    if not row:
        raise HTTPException(404, "平台币订单不存在")
    if row.pay_status != "paid":
        raise HTTPException(400, "未支付订单不能补发")
    if row.delivery_status == "success":
        raise HTTPException(409, "该订单已经发货成功，无需补发")
    delivered = deliver_platform_order(db, row, operator=principal.username)
    db.commit(); db.refresh(row)
    if not delivered or row.delivery_status != "success":
        raise HTTPException(500, row.delivery_message or "补发失败")
    return {"message": "平台币补发成功", "order_no": row.order_no, "delivery_status": row.delivery_status}


@app.post("/api/orders/platform")
def disabled_manual_platform_order(_=Depends(require_permission("orders.manage"))):
    raise HTTPException(405, "平台币订单由玩家充值支付流程自动生成，后台禁止手工添加")


@app.get("/api/orders/mall")
def mall_orders(db: Session = Depends(get_db), principal=Depends(require_permission("orders.view"))):
    q = db.query(MallOrder)
    if principal.actor_type == "agent": q = q.filter(MallOrder.agent_id.in_(scoped_agent_ids(db, principal)))
    rows = q.order_by(MallOrder.id.desc()).all()
    return [{"id": x.id, "order_no": x.order_no, "player_id": x.player_id, "agent_id": x.agent_id, "product_id": x.product_id, "quantity": x.quantity,
             "amount": money(x.amount), "pay_status": x.pay_status, "delivery_status": x.delivery_status, "created_at": dt(x.created_at), "paid_at": dt(x.paid_at)} for x in rows]

@app.post("/api/orders/mall")
def create_mall_order(body: MallOrderCreate, db: Session = Depends(get_db), _=Depends(current_admin)):
    if db.query(MallOrder).filter(MallOrder.order_no == body.order_no).first(): raise HTTPException(409, "订单号已存在")
    product = db.get(Product, body.product_id)
    if not product: raise HTTPException(404, "商品不存在")
    if product.stock < body.quantity: raise HTTPException(400, "库存不足")
    if not db.get(Player, body.player_id): raise HTTPException(404, "玩家不存在")
    row = MallOrder(**body.model_dump(), paid_at=datetime.utcnow() if body.pay_status == "paid" else None)
    db.add(row)
    if body.pay_status == "paid":
        product.stock -= body.quantity
        # 商城订单不属于平台币充值流水，也不增加玩家累计充值/代理分佣。
    db.commit(); db.refresh(row)
    return {"id": row.id, "message": "商城订单创建成功"}

@app.get("/api/shipments")
def shipments(db: Session = Depends(get_db), principal=Depends(require_permission("shipments.view"))):
    q = db.query(MallOrder)
    if principal.actor_type == "agent": q = q.filter(MallOrder.agent_id.in_(scoped_agent_ids(db, principal)))
    orders = q.order_by(MallOrder.id.desc()).all()
    result = []
    for o in orders:
        s = db.query(Shipment).filter(Shipment.mall_order_id == o.id).first()
        result.append({"mall_order_id": o.id, "order_no": o.order_no, "delivery_status": o.delivery_status,
                       "provider": s.provider if s else None, "tracking_no": s.tracking_no if s else None,
                       "shipment_status": s.status if s else "not_created", "message": s.message if s else "", "sent_at": dt(s.sent_at) if s else None})
    return result

@app.post("/api/shipments")
def create_shipment(body: ShipmentCreate, db: Session = Depends(get_db), _=Depends(current_admin)):
    order = db.get(MallOrder, body.mall_order_id)
    if not order: raise HTTPException(404, "商城订单不存在")
    if order.pay_status != "paid": raise HTTPException(400, "订单尚未支付，不能发货")
    row = db.query(Shipment).filter(Shipment.mall_order_id == order.id).first()
    if not row:
        row = Shipment(mall_order_id=order.id)
        db.add(row)
    row.provider = body.provider; row.tracking_no = body.tracking_no; row.status = body.status; row.message = body.message
    if body.status in ("sent", "success"):
        row.sent_at = datetime.utcnow(); order.delivery_status = "sent"
    elif body.status == "failed": order.delivery_status = "failed"
    db.commit();
    return {"message": "发货状态已更新"}

# ---------- 商品管理 ----------
@app.get("/api/products")
def products(category: str = "", db: Session = Depends(get_db), _=Depends(require_permission("products.view"))):
    q = db.query(Product)
    if category: q = q.filter(Product.category == category)
    rows = q.order_by(Product.id.desc()).all()
    return [{"id": x.id, "sku": x.sku, "name": x.name, "category": x.category, "price": money(x.price), "stock": x.stock,
             "description": x.description, "enabled": x.enabled, "created_at": dt(x.created_at)} for x in rows]

@app.post("/api/products")
def create_product(body: ProductCreate, db: Session = Depends(get_db), _=Depends(current_admin)):
    if body.category not in ("gift", "product"): raise HTTPException(400, "category 只能是 gift 或 product")
    if db.query(Product).filter(Product.sku == body.sku).first(): raise HTTPException(409, "SKU 已存在")
    row = Product(**body.model_dump()); db.add(row); db.commit(); db.refresh(row)
    return {"id": row.id, "message": "商品创建成功"}

# ---------- 兑换码管理 ----------
@app.get("/api/redemption-batches")
def redemption_batches(db: Session = Depends(get_db), _=Depends(require_permission("cdk.view"))):
    rows = db.query(RedemptionBatch).order_by(RedemptionBatch.id.desc()).all()
    return [{"id": x.id, "name": x.name, "total_count": x.total_count, "redeemed_count": x.redeemed_count,
             "unused_count": x.total_count - x.redeemed_count, "enabled": x.enabled, "created_at": dt(x.created_at)} for x in rows]

@app.post("/api/redemption-batches")
def create_redemption_batch(body: RedemptionBatchCreate, db: Session = Depends(get_db), _=Depends(current_admin)):
    if db.query(RedemptionBatch).filter(RedemptionBatch.name == body.name).first(): raise HTTPException(409, "CDK 名称已存在")
    row = RedemptionBatch(name=body.name); db.add(row); db.commit(); db.refresh(row)
    return {"id": row.id, "message": "CDK 批次创建成功"}

@app.post("/api/redemption-batches/{batch_id}/generate")
def generate_codes(batch_id: int, body: GenerateCodesIn, db: Session = Depends(get_db), _=Depends(current_admin)):
    batch = db.get(RedemptionBatch, batch_id)
    if not batch: raise HTTPException(404, "CDK 批次不存在")
    codes = []
    for _i in range(body.count):
        code = f"{body.prefix}-{secrets.token_hex(6).upper()}"
        while db.query(RedemptionCode).filter(RedemptionCode.code == code).first(): code = f"{body.prefix}-{secrets.token_hex(6).upper()}"
        db.add(RedemptionCode(batch_id=batch.id, code=code)); codes.append(code)
    batch.total_count += body.count; db.commit()
    return {"generated": body.count, "codes": codes[:100], "note": "接口最多预览前100个，数据库已保存全部兑换码"}

@app.post("/api/redeem")
def redeem(body: RedeemIn, db: Session = Depends(get_db), _=Depends(current_admin)):
    code = db.query(RedemptionCode).filter(RedemptionCode.code == body.code).first()
    if not code: raise HTTPException(404, "兑换码不存在")
    if code.status != "unused": raise HTTPException(409, "兑换码已使用")
    if not db.get(Player, body.player_id): raise HTTPException(404, "玩家不存在")
    batch = db.get(RedemptionBatch, code.batch_id)
    if not batch.enabled: raise HTTPException(400, "该兑换码批次已停用")
    code.status = "redeemed"; code.player_id = body.player_id; code.redeemed_at = datetime.utcnow(); batch.redeemed_count += 1
    db.commit(); return {"message": "兑换成功"}

# ---------- 累充管理 ----------
@app.get("/api/recharge-rules")
def recharge_rules(db: Session = Depends(get_db), _=Depends(require_permission("recharge.view"))):
    rows = db.query(RechargeRule).order_by(RechargeRule.threshold_amount.asc()).all()
    return [{"id": x.id, "name": x.name, "threshold_amount": money(x.threshold_amount), "reward_content": x.reward_content, "enabled": x.enabled, "created_at": dt(x.created_at)} for x in rows]

@app.post("/api/recharge-rules")
def create_recharge_rule(body: RechargeRuleCreate, db: Session = Depends(get_db), _=Depends(current_admin)):
    row = RechargeRule(**body.model_dump()); db.add(row); db.commit(); db.refresh(row)
    return {"id": row.id, "message": "累充规则创建成功"}

@app.get("/api/claims")
def claims(db: Session = Depends(get_db), principal=Depends(require_permission("claims.view"))):
    q = db.query(ClaimRecord)
    if principal.actor_type == "agent":
        player_ids = [x[0] for x in db.query(Player.id).filter(Player.agent_id.in_(scoped_agent_ids(db, principal))).all()]
        q = q.filter(ClaimRecord.player_id.in_(player_ids)) if player_ids else q.filter(text("1=0"))
    rows = q.order_by(ClaimRecord.id.desc()).all()
    return [{"id": x.id, "player_id": x.player_id, "rule_id": x.rule_id, "status": x.status, "claimed_at": dt(x.claimed_at)} for x in rows]

@app.post("/api/claims")
def create_claim(body: ClaimCreate, db: Session = Depends(get_db), _=Depends(current_admin)):
    player = db.get(Player, body.player_id); rule = db.get(RechargeRule, body.rule_id)
    if not player or not rule: raise HTTPException(404, "玩家或累充规则不存在")
    if Decimal(player.total_recharge or 0) < Decimal(rule.threshold_amount): raise HTTPException(400, "玩家累计充值未达到领取门槛")
    if db.query(ClaimRecord).filter(ClaimRecord.player_id == body.player_id, ClaimRecord.rule_id == body.rule_id).first(): raise HTTPException(409, "该奖励已经领取")
    row = ClaimRecord(**body.model_dump()); db.add(row); db.commit(); db.refresh(row)
    return {"id": row.id, "message": "领取成功"}

# ---------- 邮件管理 ----------
@app.get("/api/mails")
def mails(db: Session = Depends(get_db), _=Depends(current_admin)):
    rows = db.query(MailRecord).order_by(MailRecord.id.desc()).all()
    return [{"id": x.id, "title": x.title, "content": x.content, "target_type": x.target_type, "target_value": x.target_value,
             "send_status": x.send_status, "created_by": x.created_by, "sent_at": dt(x.sent_at), "created_at": dt(x.created_at)} for x in rows]

@app.post("/api/mails")
def send_mail(body: MailCreate, db: Session = Depends(get_db), admin=Depends(current_admin)):
    # 第一版记录发送任务。接入游戏服 API/消息队列后，在这里实际投递。
    row = MailRecord(**body.model_dump(), send_status="sent", created_by=admin.username, sent_at=datetime.utcnow())
    db.add(row); db.commit(); db.refresh(row)
    return {"id": row.id, "message": "邮件发送记录已创建（当前为模拟发送）"}

# ---------- 智能辅助 ----------
@app.get("/api/intelligence/alerts")
def intelligence_alerts(db: Session = Depends(get_db), principal=Depends(require_permission("dashboard.view"))):
    alerts = []
    ids = scoped_agent_ids(db, principal)
    failed_q = db.query(MallOrder).filter(MallOrder.delivery_status == "failed")
    if principal.actor_type == "agent": failed_q = failed_q.filter(MallOrder.agent_id.in_(ids))
    failed_shipments = failed_q.count()
    if failed_shipments: alerts.append({"level": "high", "type": "shipment", "message": f"有 {failed_shipments} 笔商城订单发货失败，需要处理"})
    # 商品管理只属于超管后台，代理数据总览不展示全局库存告警。
    if principal.actor_type != "agent":
        low_stock = db.query(Product).filter(Product.enabled.is_(True), Product.stock <= 5).count()
        if low_stock: alerts.append({"level": "medium", "type": "stock", "message": f"有 {low_stock} 个商品库存低于或等于 5"})
    # 只有具备渠道结算查看权限的账号才展示结算提醒；三级代理不显示。
    if "settlements.view" in permissions_for(principal, db):
        pending_q = db.query(Settlement).filter(Settlement.status == "pending")
        if principal.actor_type == "agent": pending_q = pending_q.filter(Settlement.agent_id.in_(ids))
        pending_settle = pending_q.count()
        if pending_settle: alerts.append({"level": "medium", "type": "settlement", "message": f"有 {pending_settle} 笔渠道结算待处理"})
    suspicious_q = db.query(Player.last_login_ip, func.count(Player.id)).filter(Player.last_login_ip.isnot(None))
    if principal.actor_type == "agent": suspicious_q = suspicious_q.filter(Player.agent_id.in_(ids))
    suspicious = suspicious_q.group_by(Player.last_login_ip).having(func.count(Player.id) >= 5).all()
    for ip, count in suspicious: alerts.append({"level": "medium", "type": "risk", "message": f"IP {ip} 关联 {count} 个玩家账号，建议复核"})
    if not alerts: alerts.append({"level": "ok", "type": "system", "message": "当前未发现需要优先处理的异常"})
    return alerts
