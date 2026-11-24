"""兼容包名 ``app.settlement``，内容为携转交接结算的同一套实现。"""

from app.handover.domain import *  # noqa: F401,F403
from app.handover.domain import settle_handovers  # noqa: F401
from app.handover.store import SettlementStore  # noqa: F401

SettlementService = SettlementStore
