from datetime import date, datetime
from decimal import Decimal
from pydantic import BaseModel, Field

class LoginIn(BaseModel):
    username: str
    password: str

class AdminPasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)
    confirm_password: str = Field(min_length=8, max_length=128)

class AdminCreate(BaseModel):
    username: str = Field(min_length=4, max_length=64)
    password: str = Field(min_length=8, max_length=128)

class SystemBrandingUpdate(BaseModel):
    backend_name: str = Field(min_length=1, max_length=40)
    player_center_name: str = Field(min_length=1, max_length=40)
    backend_logo: str = Field(default="dragon-spiral", min_length=1, max_length=64)

class IPWhitelistCreate(BaseModel):
    ip_address: str = Field(min_length=2, max_length=64)
    note: str = Field(default="", max_length=120)

class AgentCreate(BaseModel):
    username: str
    password: str = Field(min_length=8)
    agent_name: str
    agent_level: int = Field(ge=1, le=3)
    subagent_limit: int = Field(ge=0, le=9999)
    commission_rate: Decimal = Field(default=Decimal("0"), ge=0, le=1)

class AgentUpdate(BaseModel):
    agent_name: str | None = Field(default=None, min_length=1, max_length=100)
    password: str | None = Field(default=None, min_length=8)
    status: str | None = None
    commission_rate: Decimal | None = Field(default=None, ge=0, le=1)
    subagent_limit: int | None = Field(default=None, ge=0, le=9999)
    parent_agent_id: str | None = None

class PlayerRegister(BaseModel):
    username: str = Field(min_length=4, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    captcha_token: str = Field(min_length=20, max_length=2048)
    captcha_answer: str = Field(min_length=1, max_length=16)



class PlayerAdminUpdate(BaseModel):
    password: str | None = Field(default=None, min_length=8, max_length=128)
    status: str | None = None
    owner_agent_id: str | None = None
    coin_action: str | None = None
    coin_amount: int | None = Field(default=None, ge=1, le=2_000_000_000)

class RewardItemInput(BaseModel):
    item_id: int = Field(gt=0)
    quantity: int = Field(gt=0, le=2_000_000_000)


class GameItemCreate(BaseModel):
    item_code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    category: str = Field(default="普通道具", max_length=64)
    enabled: bool = True


class GameItemUpdate(BaseModel):
    item_code: str | None = Field(default=None, min_length=1, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    category: str | None = Field(default=None, max_length=64)
    enabled: bool | None = None


class ProductCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    category: str = "product"
    price: Decimal
    stock: int = Field(default=0, ge=0)
    description: str = ""
    items: list[RewardItemInput] = Field(default_factory=list)
    # 礼包四类限购可同时生效；0 表示不限购。普通商品会被后端归零。
    daily_limit: int = Field(default=0, ge=0, le=2_000_000_000)
    weekly_limit: int = Field(default=0, ge=0, le=2_000_000_000)
    monthly_limit: int = Field(default=0, ge=0, le=2_000_000_000)
    lifetime_limit: int = Field(default=0, ge=0, le=2_000_000_000)
    enabled: bool = True


class ProductUpdate(BaseModel):
    sort_order: int | None = Field(default=None, ge=1, le=2_000_000_000)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    price: Decimal | None = None
    stock: int | None = Field(default=None, ge=0)
    description: str | None = None
    items: list[RewardItemInput] | None = None
    daily_limit: int | None = Field(default=None, ge=0, le=2_000_000_000)
    weekly_limit: int | None = Field(default=None, ge=0, le=2_000_000_000)
    monthly_limit: int | None = Field(default=None, ge=0, le=2_000_000_000)
    lifetime_limit: int | None = Field(default=None, ge=0, le=2_000_000_000)
    enabled: bool | None = None

class PlatformRechargeOrderCreate(BaseModel):
    """由玩家充值/支付系统创建的待支付平台币订单，后台管理端不能手工创建。"""
    order_no: str | None = Field(default=None, max_length=64)
    player_account: str = Field(min_length=4, max_length=64)
    product_name: str = Field(min_length=1, max_length=120)
    amount: Decimal = Field(gt=0)
    platform_coin: int = Field(gt=0, le=2_000_000_000)
    payment_method: str


class PlatformPaymentSuccess(BaseModel):
    """支付平台成功回调。订单号来自充值下单阶段。"""
    order_no: str = Field(min_length=1, max_length=64)

class MallOrderCreate(BaseModel):
    """历史后台手工商城订单结构，仅保留兼容；V52 起接口禁止人工造单。"""
    order_no: str
    player_id: int
    agent_id: int | None = None
    product_id: int
    quantity: int = 1
    amount: Decimal
    pay_status: str = "paid"

class PlayerMallPurchase(BaseModel):
    quantity: int = Field(default=1, ge=1, le=1)
    # V63：每笔玩家商城购买固定只能购买 1 个礼包；前后端双重限制。
    # 玩家有多个区服角色时必须明确选择购买角色；只有 0/1 个角色时后端可兼容自动处理。
    character_id: int | None = Field(default=None, gt=0)


class PrivilegeCardCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    card_type: str
    price_coins: int = Field(gt=0, le=2_000_000_000)
    daily_reward_content: str = Field(default="", max_length=5000)
    items: list[RewardItemInput] = Field(default_factory=list)
    enabled: bool = True

class PrivilegeCardUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    card_type: str | None = None
    price_coins: int | None = Field(default=None, gt=0, le=2_000_000_000)
    daily_reward_content: str | None = Field(default=None, max_length=5000)
    items: list[RewardItemInput] | None = None
    enabled: bool | None = None

class PlayerPrivilegePurchase(BaseModel):
    character_id: int = Field(gt=0)

class PlayerBehaviorMallPurchase(BaseModel):
    character_id: int = Field(gt=0)
    product_id: int = Field(gt=0)

class PlayerBehaviorPrivilegePurchase(BaseModel):
    character_id: int = Field(gt=0)
    card_id: int = Field(gt=0)

class PlayerBehaviorCumulativeClaim(BaseModel):
    character_id: int = Field(gt=0)
    rule_id: int = Field(gt=0)

class ShipmentCreate(BaseModel):
    mall_order_id: int
    provider: str = "game-server"
    tracking_no: str | None = None
    status: str = "sent"
    message: str = ""

class RedemptionBatchCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    per_character_limit: int = Field(default=1, ge=0, le=100000)
    expires_at: datetime | None = None
    items: list[RewardItemInput] = Field(default_factory=list)
    enabled: bool = True


class RedemptionBatchUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    per_character_limit: int | None = Field(default=None, ge=0, le=100000)
    expires_at: datetime | None = None
    items: list[RewardItemInput] | None = None
    enabled: bool | None = None


class GenerateCodesIn(BaseModel):
    count: int = Field(gt=0, le=10000)
    prefix: str = "CDK"

class RedeemIn(BaseModel):
    code: str
    player_id: int

class PlayerCDKRedeem(BaseModel):
    code: str = Field(min_length=1, max_length=80)
    character_id: int = Field(gt=0)

class SettlementCreate(BaseModel):
    agent_id: int
    period_start: date
    period_end: date

class RechargeRuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    recharge_type: str = Field(default="permanent", pattern="^(daily|permanent)$")
    threshold_amount: Decimal = Field(gt=0)
    reward_content: str = Field(default="", max_length=5000)
    items: list[RewardItemInput] = Field(default_factory=list)
    enabled: bool = True


class RechargeRuleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    recharge_type: str | None = Field(default=None, pattern="^(daily|permanent)$")
    threshold_amount: Decimal | None = Field(default=None, gt=0)
    reward_content: str | None = Field(default=None, max_length=5000)
    items: list[RewardItemInput] | None = None
    enabled: bool | None = None


class ClaimCreate(BaseModel):
    player_id: int
    rule_id: int

class MailCreate(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    content: str = Field(default="", max_length=10000)
    target_type: str = Field(pattern="^(server|character)$")
    target_server_name: str | None = Field(default=None, max_length=100)
    target_character_id: int | None = Field(default=None, gt=0)
    items: list[RewardItemInput] = Field(default_factory=list)
