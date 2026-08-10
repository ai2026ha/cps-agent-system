from datetime import date
from decimal import Decimal
from pydantic import BaseModel, Field

class LoginIn(BaseModel):
    username: str
    password: str

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



class PlayerAdminUpdate(BaseModel):
    password: str | None = Field(default=None, min_length=8, max_length=128)
    status: str | None = None
    owner_agent_id: str | None = None
    coin_action: str | None = None
    coin_amount: int | None = Field(default=None, ge=1, le=2_000_000_000)

class ProductCreate(BaseModel):
    sku: str
    name: str
    category: str = "product"
    price: Decimal
    stock: int = 0
    description: str = ""

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
    quantity: int = Field(default=1, ge=1, le=99)

class ShipmentCreate(BaseModel):
    mall_order_id: int
    provider: str = "game-server"
    tracking_no: str | None = None
    status: str = "sent"
    message: str = ""

class RedemptionBatchCreate(BaseModel):
    name: str

class GenerateCodesIn(BaseModel):
    count: int = Field(gt=0, le=10000)
    prefix: str = "CDK"

class RedeemIn(BaseModel):
    code: str
    player_id: int

class SettlementCreate(BaseModel):
    agent_id: int
    period_start: date
    period_end: date

class RechargeRuleCreate(BaseModel):
    name: str
    threshold_amount: Decimal
    reward_content: str

class ClaimCreate(BaseModel):
    player_id: int
    rule_id: int

class MailCreate(BaseModel):
    title: str
    content: str
    target_type: str = "player"
    target_value: str = ""
