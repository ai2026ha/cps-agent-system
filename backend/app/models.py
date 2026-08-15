from __future__ import annotations
from datetime import datetime, date
from decimal import Decimal
from sqlalchemy import String, Integer, BigInteger, DateTime, Date, Numeric, ForeignKey, Text, Boolean, UniqueConstraint
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

class SystemSetting(Base):
    __tablename__ = "system_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class AdminIPWhitelist(Base):
    """允许访问管理后台的来源 IP。存在至少一条记录后白名单立即生效。"""
    __tablename__ = "admin_ip_whitelist"
    id: Mapped[int] = mapped_column(primary_key=True)
    ip_address: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    note: Mapped[str] = mapped_column(String(120), default="")
    created_by: Mapped[str] = mapped_column(String(64), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class AdminLoginIPState(Base):
    """后台登录失败计数与自动拉黑状态。blocked_at 非空表示需超管手工解除。"""
    __tablename__ = "admin_login_ip_states"
    id: Mapped[int] = mapped_column(primary_key=True)
    ip_address: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    window_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_failed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    blocked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    block_reason: Mapped[str] = mapped_column(String(255), default="")

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
    agent_level: Mapped[int] = mapped_column(Integer, default=1, index=True)
    subagent_limit: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="active")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
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
    status: Mapped[str] = mapped_column(String(20), default="active")
    platform_coin_balance: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

class PlayerCharacter(Base):
    __tablename__ = "player_characters"
    __table_args__ = (
        UniqueConstraint("player_id", "server_name", "role_name", name="uq_player_server_role"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    role_name: Mapped[str] = mapped_column(String(100), index=True)
    server_name: Mapped[str] = mapped_column(String(100), index=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

class PlayerCoinLedger(Base):
    __tablename__ = "player_coin_ledger"
    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    action: Mapped[str] = mapped_column(String(24), index=True)
    delta: Mapped[int] = mapped_column(BigInteger)
    balance_after: Mapped[int] = mapped_column(BigInteger)
    operator: Mapped[str] = mapped_column(String(64), default="system")
    note: Mapped[str] = mapped_column(String(255), default="")
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
    # V91: 礼包可同时配置日/周/月/永久四种限购；0 表示该周期不限购。
    daily_limit: Mapped[int] = mapped_column(Integer, default=0)
    weekly_limit: Mapped[int] = mapped_column(Integer, default=0)
    monthly_limit: Mapped[int] = mapped_column(Integer, default=0)
    lifetime_limit: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

class GameItem(Base):
    """游戏道具库。item_code 保存游戏服实际使用的道具ID/代码。"""
    __tablename__ = "game_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    item_code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    category: Mapped[str] = mapped_column(String(64), default="普通道具", index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class ProductGameItem(Base):
    __tablename__ = "product_game_items"
    __table_args__ = (UniqueConstraint("product_id", "game_item_id", name="uq_product_game_item"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    game_item_id: Mapped[int] = mapped_column(ForeignKey("game_items.id"), index=True)
    quantity: Mapped[int] = mapped_column(BigInteger, default=1)


class PlatformCoinOrder(Base):
    __tablename__ = "platform_coin_orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_no: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True, index=True)
    product_name: Mapped[str] = mapped_column(String(120), default="平台币充值")
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    platform_coin: Mapped[int] = mapped_column(Integer)
    # 数据库字段名沿用 payment_channel 以兼容历史版本，业务含义为支付方式：wechat/alipay。
    payment_channel: Mapped[str] = mapped_column(String(48), default="wechat")
    pay_status: Mapped[str] = mapped_column(String(20), default="pending")
    delivery_status: Mapped[str] = mapped_column(String(20), default="pending")
    delivery_message: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

class MallOrder(Base):
    __tablename__ = "mall_orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_no: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    # V57: 商城订单必须固化购买时选择的具体区服角色。character_id 用于追溯，
    # role_name/server_name 是订单快照，避免玩家后续改名/转服后历史订单显示错误。
    character_id: Mapped[int | None] = mapped_column(ForeignKey("player_characters.id"), nullable=True, index=True)
    role_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    server_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
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
    # V96: 0 表示不限制同一角色兑换该批次不同 CDK 的次数。
    per_character_limit: Mapped[int] = mapped_column(Integer, default=0)
    # 历史批次升级时为 False；V96 新建/编辑奖励后为 True，用于兼容旧的无奖励 CDK。
    reward_required: Mapped[bool] = mapped_column(Boolean, default=False)
    # UTC naive；为空表示永不过期。
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class RedemptionBatchGameItem(Base):
    __tablename__ = "redemption_batch_game_items"
    __table_args__ = (UniqueConstraint("batch_id", "game_item_id", name="uq_redemption_batch_game_item"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("redemption_batches.id"), index=True)
    game_item_id: Mapped[int] = mapped_column(ForeignKey("game_items.id"), index=True)
    quantity: Mapped[int] = mapped_column(BigInteger, default=1)


class RedemptionCode(Base):
    __tablename__ = "redemption_codes"
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("redemption_batches.id"), index=True)
    code: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="unused")
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"), nullable=True)
    # V67: 玩家中心兑换 CDK 时必须选择具体角色/区服。
    # character_id 用于追溯，role_name/server_name 保存兑换时快照，避免后续改名/转服影响历史记录。
    character_id: Mapped[int | None] = mapped_column(ForeignKey("player_characters.id"), nullable=True, index=True)
    role_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    server_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 兑换当时的奖励快照，避免管理员后续编辑批次后改写历史兑换记录。
    reward_content: Mapped[str] = mapped_column(Text, default="")
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
    # V99: permanent=永久累充；daily=每日累充（北京时间自然日重置）。
    recharge_type: Mapped[str] = mapped_column(String(20), default="permanent", index=True)
    # 兼容历史规则的文本奖励；V98 新规则优先使用 recharge_rule_game_items 结构化道具。
    reward_content: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class RechargeRuleGameItem(Base):
    __tablename__ = "recharge_rule_game_items"
    __table_args__ = (UniqueConstraint("rule_id", "game_item_id", name="uq_recharge_rule_game_item"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("recharge_rules.id"), index=True)
    game_item_id: Mapped[int] = mapped_column(ForeignKey("game_items.id"), index=True)
    quantity: Mapped[int] = mapped_column(BigInteger, default=1)


class ClaimRecord(Base):
    __tablename__ = "claim_records"
    __table_args__ = (UniqueConstraint("player_id", "rule_id", name="uq_player_rule_claim"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("recharge_rules.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="claimed")
    claimed_at: Mapped[datetime] = mapped_column(DateTime, default=now)

class CharacterClaimRecord(Base):
    """V64：玩家中心累充奖励按具体区服角色领取。

    旧 claim_records 保留用于兼容历史后台领取记录；新玩家中心使用本表，
    同一个玩家的不同区服角色可以分别达成并领取同一条累充奖励。
    """
    __tablename__ = "character_claim_records"
    __table_args__ = (
        UniqueConstraint("player_id", "character_id", "rule_id", name="uq_player_character_rule_claim"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    character_id: Mapped[int] = mapped_column(ForeignKey("player_characters.id"), index=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("recharge_rules.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="claimed")
    claimed_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class DailyRechargeClaimRecord(Base):
    """V99：每日累充按北京时间自然日、具体角色独立领取。

    character_id 使用普通整数而不是外键，并约定 0 代表未绑定角色的历史账号，
    这样可以建立稳定的四字段唯一约束，避免 NULL 在数据库中允许重复。
    """
    __tablename__ = "daily_recharge_claim_records"
    __table_args__ = (
        UniqueConstraint("player_id", "character_id", "rule_id", "claim_date", name="uq_daily_recharge_claim"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    character_id: Mapped[int] = mapped_column(Integer, default=0, index=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("recharge_rules.id"), index=True)
    claim_date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(20), default="claimed")
    claimed_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class PrivilegeCardRule(Base):
    """V71：周卡/月卡/年卡配置。价格单位为平台币，每日奖励由超管配置。"""
    __tablename__ = "privilege_card_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    card_type: Mapped[str] = mapped_column(String(20), index=True)  # week/month/year
    duration_days: Mapped[int] = mapped_column(Integer)
    price_coins: Mapped[int] = mapped_column(BigInteger)
    daily_reward_content: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class PrivilegeCardGameItem(Base):
    __tablename__ = "privilege_card_game_items"
    __table_args__ = (UniqueConstraint("rule_id", "game_item_id", name="uq_privilege_game_item"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("privilege_card_rules.id"), index=True)
    game_item_id: Mapped[int] = mapped_column(ForeignKey("game_items.id"), index=True)
    quantity: Mapped[int] = mapped_column(BigInteger, default=1)


class PrivilegeCardPurchase(Base):
    """玩家按具体区服角色购买的特权卡。奖励/价格保存购买时快照。"""
    __tablename__ = "privilege_card_purchases"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_no: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    character_id: Mapped[int] = mapped_column(ForeignKey("player_characters.id"), index=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("privilege_card_rules.id"), index=True)
    role_name: Mapped[str] = mapped_column(String(100))
    server_name: Mapped[str] = mapped_column(String(100))
    card_name: Mapped[str] = mapped_column(String(120))
    card_type: Mapped[str] = mapped_column(String(20), index=True)
    duration_days: Mapped[int] = mapped_column(Integer)
    price_coins: Mapped[int] = mapped_column(BigInteger)
    daily_reward_content: Mapped[str] = mapped_column(Text, default="")
    start_date: Mapped[date] = mapped_column(Date, index=True)
    end_date: Mapped[date] = mapped_column(Date, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class PrivilegeCardClaim(Base):
    __tablename__ = "privilege_card_claims"
    __table_args__ = (UniqueConstraint("purchase_id", "claim_date", name="uq_privilege_purchase_claim_date"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    purchase_id: Mapped[int] = mapped_column(ForeignKey("privilege_card_purchases.id"), index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    character_id: Mapped[int] = mapped_column(ForeignKey("player_characters.id"), index=True)
    claim_date: Mapped[date] = mapped_column(Date, index=True)
    reward_content: Mapped[str] = mapped_column(Text, default="")
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
