"""
MotoChat 数据库层 - SQLite + SQLAlchemy
"""
import os, logging, uuid
from datetime import datetime
from typing import List, Dict
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker

logger = logging.getLogger("motochat")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(ROOT, "userdata", "database", "chat.db")

def _init_engine(db_dir=None):
    global DB_PATH, engine, SessionLocal
    if db_dir:
        DB_PATH = os.path.join(db_dir, "chat.db")
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    eng = create_engine(f"sqlite:///{DB_PATH}", echo=False)
    return eng

engine = _init_engine()
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

class Conversation(Base):
    __tablename__ = "conversations"
    id = Column(String(64), primary_key=True)
    title = Column(String(200), nullable=False, default="默认会话")
    avatar = Column(String(100), nullable=False, default="MONO")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(String(64), index=True, nullable=False)
    role = Column(String(20), nullable=False)
    content_type = Column(String(20), default="text")
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.now)

class Notification(Base):
    __tablename__ = "notifications"
    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(String(64), index=True, nullable=False)
    title = Column(String(200), default="提醒")
    content = Column(Text, nullable=False)
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.now)

def init_db(db_dir=None):
    global engine, SessionLocal
    if db_dir:
        engine = _init_engine(db_dir)
        SessionLocal = sessionmaker(bind=engine)
    Base.metadata.create_all(engine)
    logger.info(f"数据库就绪: {DB_PATH}")

def _session():
    return SessionLocal()

from contextlib import contextmanager

@contextmanager
def get_db():
    db = _session()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

def ensure_default(avatar="MONO"):
    with get_db() as db:
        c = db.get(Conversation, "default")
        if not c:
            db.add(Conversation(id="default", title="默认会话", avatar=avatar))
        return "default"

def list_conversations():
    with get_db() as db:
        rows = db.query(Conversation).order_by(Conversation.updated_at.desc()).all()
        return [{"id": r.id, "title": r.title, "avatar": r.avatar,
                 "created_at": r.created_at.isoformat()} for r in rows]

def create_conversation(title, avatar):
    cid = uuid.uuid4().hex[:12]
    with get_db() as db:
        db.add(Conversation(id=cid, title=title, avatar=avatar))
    return cid

def save_message(conversation_id, role, content, content_type="text"):
    with get_db() as db:
        m = Message(conversation_id=conversation_id, role=role,
                    content_type=content_type, content=content)
        db.add(m)
        db.flush()
        db.refresh(m)
        return m.id

def get_history(conversation_id, limit=200, offset=0):
    with get_db() as db:
        rows = (db.query(Message)
                .filter(Message.conversation_id == conversation_id)
                .order_by(Message.id.asc())
                .offset(offset).limit(limit).all())
        return [{"id": r.id, "role": r.role, "content_type": r.content_type,
                 "content": r.content, "created_at": r.created_at.isoformat()} for r in rows]

def save_notification(conversation_id, title, content):
    with get_db() as db:
        n = Notification(conversation_id=conversation_id, title=title, content=content)
        db.add(n)
        db.flush()
        db.refresh(n)
        return n.id

def get_unread_notifications(cid):
    with get_db() as db:
        rows = (db.query(Notification)
                .filter(Notification.conversation_id == cid, Notification.is_read == False)
                .order_by(Notification.id.asc()).all())
        return [{"id": r.id, "title": r.title, "content": r.content,
                 "created_at": r.created_at.isoformat()} for r in rows]

def mark_read(cid):
    with get_db() as db:
        db.query(Notification).filter(
            Notification.conversation_id == cid, Notification.is_read == False
        ).update({"is_read": True})

def get_message_count():
    with get_db() as db:
        return db.query(Message).count()


def get_message_count_for_conversation(conversation_id: str) -> int:
    with get_db() as db:
        return (
            db.query(Message)
            .filter(Message.conversation_id == conversation_id)
            .count()
        )
