"""
MotoChat 数据库层 - SQLite + SQLAlchemy
"""
import os, logging, uuid
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, Boolean, Index, func
from sqlalchemy.orm import declarative_base, sessionmaker
from app.core.utils import ROOT

logger = logging.getLogger("motochat")
DB_PATH = None  # 由 init_db() 设置，避免模块加载时创建指向错误路径的连接

def _init_engine(db_dir=None):
    global DB_PATH, engine, SessionLocal
    if db_dir:
        DB_PATH = os.path.join(db_dir, "chat.db")
    if not DB_PATH:
        # 回退默认路径：项目根/userdata/database/chat.db
        DB_PATH = os.path.join(os.path.dirname(ROOT), "userdata", "database", "chat.db")
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    # SQLite 连接池优化：较小的 pool_size 减少锁竞争
    # 注意：check_same_thread=False 允许连接跨线程使用，但 SQLAlchemy 返回的
    # ORM 对象（Session、Model 实例）仍不应跨线程共享。
    # 当前项目每次请求通过 get_db() 创建独立 session，符合安全使用模式。
    eng = create_engine(
        f"sqlite:///{DB_PATH}",
        echo=False,
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
        pool_recycle=3600,
        pool_size=3,       # SQLite 写锁是数据库级别，过多连接增加竞争
        max_overflow=5     # 溢出连接上限
    )
    return eng

engine = None
SessionLocal = None
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
    conversation_id = Column(String(64), nullable=False)
    title = Column(String(200), default="提醒")
    content = Column(Text, nullable=False)
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.now)
    
    # 复合索引：加速 conversation_id + is_read 查询
    __table_args__ = (
        Index('ix_notification_cid_read', 'conversation_id', 'is_read'),
    )

class ReminderModel(Base):
    __tablename__ = "reminders"
    id = Column(String(32), primary_key=True)
    conversation_id = Column(String(64), index=True, nullable=False)
    content = Column(Text, nullable=False)
    target_time = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.now)


class TokenUsage(Base):
    """Token 用量记录表"""
    __tablename__ = "token_usage"
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(64), index=True, nullable=False)
    prompt_tokens = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)
    total_tokens = Column(Integer, default=0)
    model = Column(String(100), default="")
    created_at = Column(DateTime, default=datetime.now)

def init_db(db_dir=None):
    global engine, SessionLocal
    if db_dir or engine is None:
        # 无参调用且引擎未初始化时，用默认路径初始化，避免 create_all(None) 崩溃
        engine = _init_engine(db_dir)
        SessionLocal = sessionmaker(bind=engine)
    Base.metadata.create_all(engine)
    logger.info(f"数据库就绪: {DB_PATH}")

def _session():
    if SessionLocal is None:
        raise RuntimeError("数据库未初始化，请先调用 init_db()")
    return SessionLocal()

from contextlib import contextmanager

@contextmanager
def get_db():
    db = _session()
    try:
        yield db
        db.commit()
    except Exception as e:
        try:
            db.rollback()
        except Exception as rollback_err:
            logger.warning(f"数据库回滚也失败: {rollback_err}")
        raise
    finally:
        db.close()

def ensure_default(avatar="MONO"):
    with get_db() as db:
        c = db.get(Conversation, "default")
        if not c:
            db.add(Conversation(id="default", title="默认会话", avatar=avatar))
        return "default"

def delete_conversation_data(conversation_id: str):
    """删除会话及其消息/通知/提醒（用于删除用户时清理数据）"""
    if not conversation_id:
        return
    with get_db() as db:
        db.query(Message).filter(Message.conversation_id == conversation_id).delete()
        db.query(Notification).filter(Notification.conversation_id == conversation_id).delete()
        db.query(ReminderModel).filter(ReminderModel.conversation_id == conversation_id).delete()
        db.query(Conversation).filter(Conversation.id == conversation_id).delete()
    logger.info(f"已清理会话数据: {conversation_id}")

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
        # 同步更新会话的 updated_at，确保 list_conversations 排序正确
        c = db.get(Conversation, conversation_id)
        if c:
            c.updated_at = datetime.now()
        else:
            # 自动创建会话记录，防止新用户首次对话后会话不可见
            # 使用 merge 防止并发创建时 IntegrityError 导致整个事务回滚
            # 尝试获取当前活跃角色名，避免硬编码
            avatar_name = "MONO"
            try:
                from app.core.deps import get_chat_service
                _chat = get_chat_service()
                if _chat and hasattr(_chat, 'avatar_name'):
                    avatar_name = _chat.avatar_name
            except Exception as e:
                logger.debug(f"获取角色名失败: {e}")
            db.merge(Conversation(id=conversation_id, title="默认会话", avatar=avatar_name))
        db.flush()
        db.refresh(m)
        return m.id

def get_history(conversation_id, limit=200, offset=0):
    """获取聊天记录，按 id 升序返回。
    默认返回最新 limit 条；传 offset 可向前翻页（offset=200 跳过最新 200 条，取更早的）。
    limit=0 表示返回全部记录。
    
    返回：(msgs, has_more, next_offset)
        - msgs: 消息列表
        - has_more: 是否还有更多数据
        - next_offset: 下一页的 offset（如果 has_more=True）
    """
    with get_db() as db:
        base_q = db.query(Message).filter(Message.conversation_id == conversation_id)
        
        if limit == 0:
            # 返回全部
            rows = base_q.order_by(Message.id.asc()).all()
            msgs = [{"id": r.id, "role": r.role, "content_type": r.content_type,
                     "content": r.content, "created_at": r.created_at.isoformat()} for r in rows]
            return msgs, False, 0
        
        # 优化：查询 limit+1 条来判断是否有更多数据，避免单独的 COUNT 查询
        rows = (base_q.order_by(Message.id.desc())
                .limit(limit + 1)
                .offset(offset)
                .all())
        
        # 判断是否有更多数据
        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]
        
        # 反转顺序（从 id 降序改为升序）
        rows.reverse()
        
        msgs = [{"id": r.id, "role": r.role, "content_type": r.content_type,
                 "content": r.content, "created_at": r.created_at.isoformat()} for r in rows]
        
        # 计算下一页的 offset
        next_offset = offset + len(msgs) if has_more else 0
        
        return msgs, has_more, next_offset

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

# ─── Reminder persistence ─────────────────────────────────────────

def save_reminder(rid: str, cid: str, target_time, content: str):
    with get_db() as db:
        r = db.get(ReminderModel, rid)
        if r:
            r.target_time = target_time
            r.content = content
        else:
            db.add(ReminderModel(id=rid, conversation_id=cid, target_time=target_time, content=content))

def delete_reminder(rid: str):
    with get_db() as db:
        r = db.get(ReminderModel, rid)
        if r:
            db.delete(r)
            return True
        return False

def load_all_reminders():
    with get_db() as db:
        rows = db.query(ReminderModel).all()
        return [{"id": r.id, "conversation_id": r.conversation_id, "target_time": r.target_time, "content": r.content} for r in rows]


# ─── Token Usage ──────────────────────────────────────────────────

def save_token_usage(username: str, prompt_tokens: int, completion_tokens: int, total_tokens: int, model: str = ""):
    """保存一次 token 用量记录"""
    with get_db() as db:
        db.add(TokenUsage(
            username=username,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            model=model
        ))


def get_user_token_usage(username: str) -> dict:
    """获取指定用户的累计 token 用量"""
    with get_db() as db:
        result = db.query(
            func.sum(TokenUsage.prompt_tokens).label('prompt_tokens'),
            func.sum(TokenUsage.completion_tokens).label('completion_tokens'),
            func.sum(TokenUsage.total_tokens).label('total_tokens'),
            func.count(TokenUsage.id).label('request_count')
        ).filter(TokenUsage.username == username).first()
        
        if result and result.prompt_tokens:
            return {
                "prompt_tokens": result.prompt_tokens or 0,
                "completion_tokens": result.completion_tokens or 0,
                "total_tokens": result.total_tokens or 0,
                "request_count": result.request_count or 0
            }
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "request_count": 0}


def get_all_token_usage() -> list:
    """获取所有用户的累计 token 用量（管理员用）"""
    with get_db() as db:
        results = db.query(
            TokenUsage.username,
            func.sum(TokenUsage.prompt_tokens).label('prompt_tokens'),
            func.sum(TokenUsage.completion_tokens).label('completion_tokens'),
            func.sum(TokenUsage.total_tokens).label('total_tokens'),
            func.count(TokenUsage.id).label('request_count')
        ).group_by(TokenUsage.username).all()
        
        return [{
            "username": r.username,
            "prompt_tokens": r.prompt_tokens or 0,
            "completion_tokens": r.completion_tokens or 0,
            "total_tokens": r.total_tokens or 0,
            "request_count": r.request_count or 0
        } for r in results]
