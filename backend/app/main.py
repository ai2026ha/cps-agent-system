import os
import secrets
import shutil
import time as time_module
from datetime import datetime, date, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo
from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, inspect, text
from sqlalchemy.orm import Session

from .database import Base, engine, SessionLocal, get_db
from .models import (
    AdminUser, Agent, Player, Product, PlatformCoinOrder, MallOrder, Shipment,
    RedemptionBatch, RedemptionCode, Settlement, RechargeRule, ClaimRecord, MailRecord,
)
from .schemas import (
    LoginIn, AgentCreate, AgentUpdate, PlayerCreate, ProductCreate, PlatformOrderCreate, MallOrderCreate,
    ShipmentCreate, RedemptionBatchCreate, GenerateCodesIn, RedeemIn, SettlementCreate,
    RechargeRuleCreate, ClaimCreate, MailCreate,
)
from .security import hash_password, verify_password, create_token, current_admin, current_channel_user, current_user

app = FastAPI(title="CPS 智能代理系统", version="1.0.0")
STATIC_DIR = Path(__file__).parent / "static"
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
    return v.isoformat(sep=" ", timespec="seconds") if v else None


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
        "mail.view", "mail.send", "system.rebuild", "system.metrics",
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
            "subagent_limit": int(agent.subagent_limit or 0), "permissions": perms,
        }
    return {
        "username": principal.username, "role": principal.role, "actor_type": "admin",
        "agent_id": None, "agent_level": 0, "subagent_limit": None, "permissions": perms,
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
    ensure_agent_public_identity_format()
    seed_admin()

@app.get("/healthz")
def healthz():
    return {"status": "ok"}

@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")

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
        """数据总览流水：只统计已支付的平台币订单。

        今日/昨日按实际支付时间归档；兼容历史数据里 paid_at 为空的已支付订单，
        此时退回 created_at 作为时间口径。商城订单不计入数据总览流水。
        """
        platform_q = scope_order_query(
            db.query(func.coalesce(func.sum(PlatformCoinOrder.amount), 0)).filter(PlatformCoinOrder.pay_status == "paid"),
            PlatformCoinOrder,
        )
        paid_time = func.coalesce(PlatformCoinOrder.paid_at, PlatformCoinOrder.created_at)
        if start_dt is not None:
            platform_q = platform_q.filter(paid_time >= start_dt)
        if end_dt is not None:
            platform_q = platform_q.filter(paid_time < end_dt)
        return Decimal(platform_q.scalar() or 0)

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
    return {
        "id": a.id, "agent_id": a.agent_id, "username": a.username, "agent_name": a.agent_name,
        "invite_code": a.invite_code, "parent_id": a.parent_id,
        # 一级代理在数据库中没有普通代理父节点，但业务展示上归属于超级管理员。
        "parent_agent_id": a.parent.agent_id if a.parent else None,
        "parent_agent_display": a.parent.agent_id if a.parent else "超管",
        "agent_level": int(a.agent_level or 1), "agent_level_name": agent_level_name(a.agent_level),
        "subagent_limit": limit, "subagent_count": used,
        "today_turnover": money(a.today_turnover), "yesterday_turnover": money(a.yesterday_turnover),
        "total_turnover": money(a.total_turnover), "commission_rate": float(a.commission_rate or 0),
        "status": a.status, "created_at": dt(a.created_at),
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
    """按业务时区自然日区间汇总代理已支付流水，结束日期包含当天。"""
    start_dt, end_dt = business_date_bounds(start_date, end_date)
    p = db.query(func.coalesce(func.sum(PlatformCoinOrder.amount), 0)).filter(
        PlatformCoinOrder.agent_id == agent_pk, PlatformCoinOrder.pay_status == "paid"
    )
    m = db.query(func.coalesce(func.sum(MallOrder.amount), 0)).filter(
        MallOrder.agent_id == agent_pk, MallOrder.pay_status == "paid"
    )
    if start_dt:
        p = p.filter(PlatformCoinOrder.created_at >= start_dt)
        m = m.filter(MallOrder.created_at >= start_dt)
    if end_dt:
        p = p.filter(PlatformCoinOrder.created_at < end_dt)
        m = m.filter(MallOrder.created_at < end_dt)
    return Decimal(p.scalar() or 0) + Decimal(m.scalar() or 0)


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
        if level == 1:
            if requested not in {"", "SUPERADMIN", "超管", "超级管理员"}:
                raise HTTPException(400, "一级代理只能归属超管")
            target.parent_id = None
        else:
            if requested in {"", "SUPERADMIN", "超管", "超级管理员"}:
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
    today = date.today(); today_start = datetime.combine(today, time.min)
    yesterday_start = datetime.combine(date.fromordinal(today.toordinal()-1), time.min)
    for a in db.query(Agent).all():
        def total_between(start=None, end=None):
            p = db.query(func.coalesce(func.sum(PlatformCoinOrder.amount), 0)).filter(PlatformCoinOrder.agent_id == a.id, PlatformCoinOrder.pay_status == "paid")
            m = db.query(func.coalesce(func.sum(MallOrder.amount), 0)).filter(MallOrder.agent_id == a.id, MallOrder.pay_status == "paid")
            if start: p = p.filter(PlatformCoinOrder.created_at >= start); m = m.filter(MallOrder.created_at >= start)
            if end: p = p.filter(PlatformCoinOrder.created_at < end); m = m.filter(MallOrder.created_at < end)
            return Decimal(p.scalar() or 0) + Decimal(m.scalar() or 0)
        a.today_turnover = total_between(today_start)
        a.yesterday_turnover = total_between(yesterday_start, today_start)
        a.total_turnover = total_between()
    db.commit()
    return {"message": "代理流水已重算"}

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
    start = datetime.combine(body.period_start, time.min)
    end = datetime.combine(date.fromordinal(body.period_end.toordinal()+1), time.min)
    p = db.query(func.coalesce(func.sum(PlatformCoinOrder.amount), 0)).filter(PlatformCoinOrder.agent_id == agent.id, PlatformCoinOrder.pay_status == "paid", PlatformCoinOrder.created_at >= start, PlatformCoinOrder.created_at < end).scalar()
    m = db.query(func.coalesce(func.sum(MallOrder.amount), 0)).filter(MallOrder.agent_id == agent.id, MallOrder.pay_status == "paid", MallOrder.created_at >= start, MallOrder.created_at < end).scalar()
    turnover = Decimal(p or 0) + Decimal(m or 0)
    amount = turnover * Decimal(agent.commission_rate or 0)
    row = Settlement(settlement_no="ST" + datetime.utcnow().strftime("%Y%m%d%H%M%S") + secrets.token_hex(2).upper(), agent_id=agent.id,
                     period_start=body.period_start, period_end=body.period_end, turnover=turnover,
                     commission_rate=agent.commission_rate, commission_amount=amount)
    db.add(row)
    try: db.commit()
    except Exception:
        db.rollback(); raise HTTPException(409, "该代理此结算周期已存在")
    return {"id": row.id, "commission_amount": money(amount), "message": "结算单已生成"}

# ---------- 玩家管理 ----------
@app.get("/api/players")
def list_players(keyword: str = "", db: Session = Depends(get_db), principal=Depends(require_permission("players.view"))):
    q = db.query(Player)
    if principal.actor_type == "agent": q = q.filter(Player.agent_id.in_(scoped_agent_ids(db, principal)))
    if keyword:
        like = f"%{keyword}%"
        q = q.filter((Player.player_id.like(like)) | (Player.username.like(like)) | (Player.role_name.like(like)) | (Player.server_name.like(like)))
    rows = q.order_by(Player.id.desc()).all()
    return [{"id": p.id, "player_id": p.player_id, "username": p.username, "role_name": p.role_name, "server_name": p.server_name,
             "agent_id": p.agent_id, "today_recharge": money(p.today_recharge), "total_recharge": money(p.total_recharge),
             "last_login_at": dt(p.last_login_at), "last_login_ip": p.last_login_ip, "created_at": dt(p.created_at)} for p in rows]

@app.post("/api/players")
def create_player(body: PlayerCreate, db: Session = Depends(get_db), _=Depends(current_admin)):
    if body.agent_id and not db.get(Agent, body.agent_id): raise HTTPException(400, "所属代理不存在")
    if db.query(Player).filter((Player.player_id == body.player_id) | (Player.username == body.username)).first(): raise HTTPException(409, "玩家ID或账号已存在")
    row = Player(player_id=body.player_id, username=body.username, password_hash=hash_password(body.password), role_name=body.role_name,
                 server_name=body.server_name, agent_id=body.agent_id, last_login_ip=body.last_login_ip, last_login_at=datetime.utcnow())
    db.add(row); db.commit(); db.refresh(row)
    return {"id": row.id, "message": "玩家创建成功"}

# ---------- 订单管理 ----------
def apply_paid_recharge(db: Session, player_id: int, agent_id: int | None, amount: Decimal):
    player = db.get(Player, player_id)
    if not player: raise HTTPException(404, "玩家不存在")
    player.today_recharge = Decimal(player.today_recharge or 0) + amount
    player.total_recharge = Decimal(player.total_recharge or 0) + amount
    if agent_id:
        agent = db.get(Agent, agent_id)
        if not agent: raise HTTPException(404, "代理不存在")
        agent.today_turnover = Decimal(agent.today_turnover or 0) + amount
        agent.total_turnover = Decimal(agent.total_turnover or 0) + amount

@app.get("/api/orders/platform")
def platform_orders(db: Session = Depends(get_db), principal=Depends(require_permission("orders.view"))):
    q = db.query(PlatformCoinOrder)
    if principal.actor_type == "agent": q = q.filter(PlatformCoinOrder.agent_id.in_(scoped_agent_ids(db, principal)))
    rows = q.order_by(PlatformCoinOrder.id.desc()).all()
    return [{"id": x.id, "order_no": x.order_no, "player_id": x.player_id, "agent_id": x.agent_id, "amount": money(x.amount),
             "platform_coin": x.platform_coin, "payment_channel": x.payment_channel, "pay_status": x.pay_status, "created_at": dt(x.created_at), "paid_at": dt(x.paid_at)} for x in rows]

@app.post("/api/orders/platform")
def create_platform_order(body: PlatformOrderCreate, db: Session = Depends(get_db), _=Depends(current_admin)):
    if db.query(PlatformCoinOrder).filter(PlatformCoinOrder.order_no == body.order_no).first(): raise HTTPException(409, "订单号已存在")
    if not db.get(Player, body.player_id): raise HTTPException(404, "玩家不存在")
    if body.agent_id and not db.get(Agent, body.agent_id): raise HTTPException(404, "代理不存在")
    row = PlatformCoinOrder(**body.model_dump(), paid_at=datetime.utcnow() if body.pay_status == "paid" else None)
    db.add(row)
    if body.pay_status == "paid": apply_paid_recharge(db, body.player_id, body.agent_id, Decimal(body.amount))
    db.commit(); db.refresh(row)
    return {"id": row.id, "message": "平台币订单创建成功"}

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
        apply_paid_recharge(db, body.player_id, body.agent_id, Decimal(body.amount))
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
