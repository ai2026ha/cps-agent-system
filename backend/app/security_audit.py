"""V120 BUILD11 security audit helper.
Lightweight audit sink; production deployments can replace this with DB/queue storage.
"""
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger("cps.security.audit")


def record_security_event(event: str, *, user: str | None = None, ip: str | None = None, result: str = "success", detail: str | None = None):
    payload = {
        "event": event,
        "user": user,
        "ip": ip,
        "result": result,
        "detail": detail,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    logger.info(json.dumps(payload, ensure_ascii=False))
