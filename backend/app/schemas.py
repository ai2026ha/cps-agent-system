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

class PlayerCreate(BaseModel):
    player_id: str
    username: str
    password: str = Field(min_length=8)
    role_name: str
    server_name: str
    agent_id: int | None = None
    last_login_ip: str | None = None

class ProductCreate(BaseModel):
    sku: str
    name: str
    category: str = "product"
    price: Decimal
    stock: int = 0
    description: str = ""

class PlatformOrderCreate(BaseModel):
    order_no: str
    player_id: int
    agent_id: int | None = None
    amount: Decimal
    platform_coin: int
    payment_channel: str = "manual"
    pay_status: str = "paid"

class MallOrderCreate(BaseModel):
    order_no: str
    player_id: int
    agent_id: int | None = None
    product_id: int
    quantity: int = 1
    amount: Decimal
    pay_status: str = "paid"

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
