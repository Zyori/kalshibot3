"""SQLAlchemy ORM models.

Importing this package brings every model into the Base.metadata registry so Alembic
autogenerate can see them. Order doesn't matter — SQLAlchemy resolves FK refs by string.
"""

from src.models.bet import Bet
from src.models.bet_fill import BetFill
from src.models.chat import ChatMessage
from src.models.game import Game
from src.models.market import Market
from src.models.position import Position
from src.models.suggestion import Suggestion

__all__ = ["Bet", "BetFill", "ChatMessage", "Game", "Market", "Position", "Suggestion"]
