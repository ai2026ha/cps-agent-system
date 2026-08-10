import os
import secrets
from datetime import datetime, date, time
from decimal import Decimal
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func
from sqlalchemy.orm import Session

from .database import Base, engine, SessionLocal, get_db
from .models import (
    AdminUser, Agent, Player, Product, PlatformCoinOrder, MallOrder, Shipment,
    RedemptionBatch, RedemptionCode, Settlement, RechargeRule, ClaimRecord, MailRecord,
)
from .schemas import (
    LoginIn, AgentCreate, PlayerCreate, ProductCreate, PlatformOrderCreate, MallOrderCreate,
    ShipmentCreate, RedemptionBatchCreate, GenerateCodesIn, RedeemIn, SettlementCreate,
    RechargeRuleCreate, ClaimCreate, MailCreate,
)
from .security import hash_password, verify_password, create_token, current_admin

app = FastAPI(title="CPS 智能代理系统", version="1.0.0")
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def money(v):
    return float(v or 0)

def dt(v):
    return v.isoformat(sep=" ", timespec="seconds") if v else None

def seed_admin():
    db = SessionLocal()
    try:
        username = os.getenv("ADMIN_USERNAME", "admin")
        password = os.getenv("ADMIN_PASSWORD", "ChangeMe123!")
        if not db.query(AdminUser).filter(AdminUser.username == username).first():
            db.add(AdminUser(username=username, password_hash=hash_password(password), role="superadmin"))
            db.commit()
    finally:
        db.close()

@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)
    seed_admin()

@app.get("/healthz")
def healthz():
    return {"status": "ok"}

@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")

@app.post("/api/auth/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.query(AdminUser).filter(AdminUser.username == body.username, AdminUser.enabled.is_(True)).first()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "账号或密码错误")
    return {"access_token": create_token(user.username, user.role), "token_type": "bearer", "username": user.username, "role": user.role}

@app.get("/api/dashboard")
def dashboard(db: Session = Depends(get_db), _=Depends(current_admin)):
    today = date.today()
    start = datetime.combine(today, time.min)
    platform_today = db.query(func.coalesce(func.sum(PlatformCoinOrder.amount), 0)).filter(PlatformCoinOrder.pay_status == "paid", PlatformCoinOrder.created_at >= start).scalar()
    mall_today = db.query(func.coalesce(func.sum(MallOrder.amount), 0)).filter(MallOrder.pay_status == "paid", MallOrder.created_at >= start).scalar()
    pending_shipments = db.query(MallOrder).filter(MallOrder.delivery_status.in_(["waiting", "failed"])).count()
    return {
        "agents": db.query(Agent).count(),
        "players": db.query(Player).count(),
        "today_turnover": money(platform_today + mall_today),
        "platform_orders": db.query(PlatformCoinOrder).count(),
        "mall_orders": db.query(MallOrder).count(),
        "pending_shipments": pending_shipments,
        "cdk_unused": db.query(RedemptionCode).filter(RedemptionCode.status == "unused").count(),
        "cdk_redeemed": db.query(RedemptionCode).filter(RedemptionCode.status == "redeemed").count(),
    }

# ---------- 渠道管理 ----------
@app.get("/api/agents")
def list_agents(keyword: str = "", db: Session = Depends(get_db), _=Depends(current_admin)):
    q = db.query(Agent)
    if keyword:
        like = f"%{keyword}%"
        q = q.filter((Agent.agent_name.like(like)) | (Agent.agent_id.like(like)) | (Agent.username.like(like)))
    rows = q.order_by(Agent.id.desc()).all()
    return [{
        "id": a.id, "agent_id": a.agent_id, "username": a.username, "agent_name": a.agent_name,
        "invite_code": a.invite_code, "parent_id": a.parent_id,
        "parent_agent_id": a.parent.agent_id if a.parent else None,
        "today_turnover": money(a.today_turnover), "yesterday_turnover": money(a.yesterday_turnover),
        "total_turnover": money(a.total_turnover), "commission_rate": float(a.commission_rate or 0),
        "status": a.status, "created_at": dt(a.created_at),
    } for a in rows]

@app.post("/api/agents")
def create_agent(body: AgentCreate, db: Session = Depends(get_db), _=Depends(current_admin)):
    if body.parent_id and not db.get(Agent, body.parent_id):
        raise HTTPException(400, "上级代理不存在")
    if db.query(Agent).filter((Agent.agent_id == body.agent_id) | (Agent.username == body.username) | (Agent.invite_code == body.invite_code)).first():
        raise HTTPException(409, "代理ID、账号或邀请码已存在")
    row = Agent(agent_id=body.agent_id, username=body.username, password_hash=hash_password(body.password), agent_name=body.agent_name,
                invite_code=body.invite_code, parent_id=body.parent_id, commission_rate=body.commission_rate)
    db.add(row); db.commit(); db.refresh(row)
    return {"id": row.id, "message": "代理创建成功"}

@app.get("/api/agents/{agent_pk}/subagents")
def subagents(agent_pk: int, db: Session = Depends(get_db), _=Depends(current_admin)):
    if not db.get(Agent, agent_pk): raise HTTPException(404, "代理不存在")
    rows = db.query(Agent).filter(Agent.parent_id == agent_pk).order_by(Agent.id.desc()).all()
    return [{"id": a.id, "agent_id": a.agent_id, "agent_name": a.agent_name, "today_turnover": money(a.today_turnover), "total_turnover": money(a.total_turnover)} for a in rows]

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
def list_settlements(db: Session = Depends(get_db), _=Depends(current_admin)):
    rows = db.query(Settlement).order_by(Settlement.id.desc()).all()
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
def list_players(keyword: str = "", db: Session = Depends(get_db), _=Depends(current_admin)):
    q = db.query(Player)
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
def platform_orders(db: Session = Depends(get_db), _=Depends(current_admin)):
    rows = db.query(PlatformCoinOrder).order_by(PlatformCoinOrder.id.desc()).all()
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
def mall_orders(db: Session = Depends(get_db), _=Depends(current_admin)):
    rows = db.query(MallOrder).order_by(MallOrder.id.desc()).all()
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
def shipments(db: Session = Depends(get_db), _=Depends(current_admin)):
    orders = db.query(MallOrder).order_by(MallOrder.id.desc()).all()
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
def products(category: str = "", db: Session = Depends(get_db), _=Depends(current_admin)):
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
def redemption_batches(db: Session = Depends(get_db), _=Depends(current_admin)):
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
def recharge_rules(db: Session = Depends(get_db), _=Depends(current_admin)):
    rows = db.query(RechargeRule).order_by(RechargeRule.threshold_amount.asc()).all()
    return [{"id": x.id, "name": x.name, "threshold_amount": money(x.threshold_amount), "reward_content": x.reward_content, "enabled": x.enabled, "created_at": dt(x.created_at)} for x in rows]

@app.post("/api/recharge-rules")
def create_recharge_rule(body: RechargeRuleCreate, db: Session = Depends(get_db), _=Depends(current_admin)):
    row = RechargeRule(**body.model_dump()); db.add(row); db.commit(); db.refresh(row)
    return {"id": row.id, "message": "累充规则创建成功"}

@app.get("/api/claims")
def claims(db: Session = Depends(get_db), _=Depends(current_admin)):
    rows = db.query(ClaimRecord).order_by(ClaimRecord.id.desc()).all()
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
def intelligence_alerts(db: Session = Depends(get_db), _=Depends(current_admin)):
    alerts = []
    failed_shipments = db.query(MallOrder).filter(MallOrder.delivery_status == "failed").count()
    if failed_shipments: alerts.append({"level": "high", "type": "shipment", "message": f"有 {failed_shipments} 笔商城订单发货失败，需要处理"})
    low_stock = db.query(Product).filter(Product.enabled.is_(True), Product.stock <= 5).count()
    if low_stock: alerts.append({"level": "medium", "type": "stock", "message": f"有 {low_stock} 个商品库存低于或等于 5"})
    pending_settle = db.query(Settlement).filter(Settlement.status == "pending").count()
    if pending_settle: alerts.append({"level": "medium", "type": "settlement", "message": f"有 {pending_settle} 笔渠道结算待处理"})
    suspicious = db.query(Player.last_login_ip, func.count(Player.id)).filter(Player.last_login_ip.isnot(None)).group_by(Player.last_login_ip).having(func.count(Player.id) >= 5).all()
    for ip, count in suspicious: alerts.append({"level": "medium", "type": "risk", "message": f"IP {ip} 关联 {count} 个玩家账号，建议复核"})
    if not alerts: alerts.append({"level": "ok", "type": "system", "message": "当前未发现需要优先处理的异常"})
    return alerts
