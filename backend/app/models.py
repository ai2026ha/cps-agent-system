from __future__ import annotations
from datetime import datetime, date
from decimal import Decimal
from sqlalchemy import String, Integer, DateTime, Date, Numeric, ForeignKey, Text, Boolean, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .database import Base


def now():
    return datetime.utcnow()

class AdminUser(Base):
    __tablename__ = "admin_users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), default="superadmin")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

class Agent(Base):
    __tablename__ = "agents"
    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    agent_name: Mapped[str] = mapped_column(String(100), index=True)
    invite_code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True, index=True)
    today_turnover: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    yesterday_turnover: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    total_turnover: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    commission_rate: Mapped[Decimal] = mapped_column(Numeric(6, 4), default=0)
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    parent: Mapped[Agent | None] = relationship(remote_side=[id], backref="children")

class Player(Base):
    __tablename__ = "players"
    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role_name: Mapped[str] = mapped_column(String(100), index=True)
    server_name: Mapped[str] = mapped_column(String(100), index=True)
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True, index=True)
    today_recharge: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    total_recharge: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_login_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    category: Mapped[str] = mapped_column(String(24), default="product")  # gift/product
    price: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    stock: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

class PlatformCoinOrder(Base):
    __tablename__ = "platform_coin_orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_no: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    platform_coin: Mapped[int] = mapped_column(Integer)
    payment_channel: Mapped[str] = mapped_column(String(48), default="manual")
    pay_status: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

class MallOrder(Base):
    __tablename__ = "mall_orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_no: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    pay_status: Mapped[str] = mapped_column(String(20), default="pending")
    delivery_status: Mapped[str] = mapped_column(String(20), default="waiting")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

class Shipment(Base):
    __tablename__ = "shipments"
    id: Mapped[int] = mapped_column(primary_key=True)
    mall_order_id: Mapped[int] = mapped_column(ForeignKey("mall_orders.id"), unique=True, index=True)
    provider: Mapped[str] = mapped_column(String(60), default="game-server")
    tracking_no: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(24), default="waiting")
    message: Mapped[str] = mapped_column(Text, default="")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)

class RedemptionBatch(Base):
    __tablename__ = "redemption_batches"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    total_count: Mapped[int] = mapped_column(Integer, default=0)
    redeemed_count: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

class RedemptionCode(Base):
    __tablename__ = "redemption_codes"
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("redemption_batches.id"), index=True)
    code: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="unused")
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"), nullable=True)
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

class Settlement(Base):
    __tablename__ = "settlements"
    __table_args__ = (UniqueConstraint("agent_id", "period_start", "period_end", name="uq_settlement_period"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    settlement_no: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"), index=True)
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    turnover: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    commission_rate: Mapped[Decimal] = mapped_column(Numeric(6, 4), default=0)
    commission_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

class RechargeRule(Base):
    __tablename__ = "recharge_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    threshold_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    reward_content: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

class ClaimRecord(Base):
    __tablename__ = "claim_records"
    __table_args__ = (UniqueConstraint("player_id", "rule_id", name="uq_player_rule_claim"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("recharge_rules.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="claimed")
    claimed_at: Mapped[datetime] = mapped_column(DateTime, default=now)

class MailRecord(Base):
    __tablename__ = "mail_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(160))
    content: Mapped[str] = mapped_column(Text)
    target_type: Mapped[str] = mapped_column(String(32), default="player")
    target_value: Mapped[str] = mapped_column(String(160), default="")
    send_status: Mapped[str] = mapped_column(String(20), default="queued")
    created_by: Mapped[str] = mapped_column(String(64), default="admin")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
