from sqlalchemy.orm import Session


class CoinService:
    """Single entry point for future coin mutations."""

    @staticmethod
    def add_coin(db: Session, player, amount: int, reason: str, operator_id=None):
        if amount <= 0:
            raise ValueError('amount must be positive')
        before = int(player.platform_coin_balance or 0)
        player.platform_coin_balance = before + amount
        return before, player.platform_coin_balance

    @staticmethod
    def consume_coin(db: Session, player, amount: int, reason: str):
        if amount <= 0:
            raise ValueError('amount must be positive')
        before = int(player.platform_coin_balance or 0)
        if before < amount:
            raise ValueError('insufficient balance')
        player.platform_coin_balance = before - amount
        return before, player.platform_coin_balance
