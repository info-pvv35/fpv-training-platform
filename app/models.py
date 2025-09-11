from sqlalchemy import Column, Integer, String, DateTime, func
from bot.database.db import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(Integer, unique=True, nullable=False)
    name = Column(String(64))
    created_at = Column(DateTime, server_default=func.now())  # теперь func есть