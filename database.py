import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional
from dataclasses import dataclass

@dataclass
class Chat:
    id_i: int
    email: Optional[str]
    product: int
    last_message: str
    cnt_msg: int
    cnt_new: int
    telegram_topic_id: Optional[int] = None

@dataclass
class Message:
    chat_id: int
    message_id: str
    content: str
    timestamp: datetime
    is_sent_to_telegram: bool = False

class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_db()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.db_path)
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
    
    def init_db(self):
        """Initialize the core database structure for Single-user mode"""
        with self._connect() as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS chats (
                    id_i INTEGER PRIMARY KEY, email TEXT, product INTEGER,
                    last_message TEXT, cnt_msg INTEGER, cnt_new INTEGER,
                    telegram_topic_id INTEGER, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, message_id TEXT UNIQUE,
                    content TEXT, timestamp TIMESTAMP, is_sent_to_telegram BOOLEAN DEFAULT FALSE,
                    FOREIGN KEY (chat_id) REFERENCES chats (id_i)
                )
            ''')
            
            # Tables required for the JSON-to-SQLite migration and optimized lookups
            conn.execute('CREATE TABLE IF NOT EXISTS topics (key TEXT PRIMARY KEY, data TEXT)')
            conn.execute('CREATE TABLE IF NOT EXISTS purchases (invoice_id TEXT PRIMARY KEY, data TEXT)')
            conn.execute('CREATE TABLE IF NOT EXISTS processed_reviews (review_id TEXT PRIMARY KEY, hash TEXT)')
            conn.execute('CREATE TABLE IF NOT EXISTS pending_topics (id INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT)')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS service_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS telegram_outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    dedupe_key TEXT UNIQUE,
                    text TEXT NOT NULL,
                    topic_id INTEGER NOT NULL,
                    chat_id INTEGER,
                    message_id TEXT,
                    parse_mode TEXT,
                    reply_markup TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at REAL NOT NULL,
                    last_error TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            conn.execute('CREATE INDEX IF NOT EXISTS idx_messages_chat_id ON messages(chat_id)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_messages_delivery ON messages(chat_id, is_sent_to_telegram)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_outbox_due ON telegram_outbox(next_attempt_at, id)')

    def get_setting(self, key: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute(
                'SELECT value FROM service_settings WHERE key = ?',
                (key,),
            ).fetchone()
            return str(row[0]) if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                '''
                INSERT INTO service_settings (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                ''',
                (key, str(value)),
            )
            
    def save_chat(self, chat: Chat) -> None:
        with self._connect() as conn:
            conn.execute('INSERT OR REPLACE INTO chats (id_i, email, product, last_message, cnt_msg, cnt_new, telegram_topic_id, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)', 
                        (chat.id_i, chat.email, chat.product, chat.last_message, chat.cnt_msg, chat.cnt_new, chat.telegram_topic_id))
    
    def get_chat(self, chat_id: int) -> Optional[Chat]:
        with self._connect() as conn:
            cursor = conn.execute('SELECT id_i, email, product, last_message, cnt_msg, cnt_new, telegram_topic_id FROM chats WHERE id_i = ?', (chat_id,))
            row = cursor.fetchone()
            return Chat(*row) if row else None

    def message_exists(self, message_id: str) -> bool:
        """Check if a message already exists in the SQLite database"""
        with self._connect() as conn:
            cursor = conn.execute('SELECT 1 FROM messages WHERE message_id = ?', (message_id,))
            return cursor.fetchone() is not None

    def save_message(self, message: Message) -> bool:
        try:
            timestamp = (
                message.timestamp.isoformat()
                if isinstance(message.timestamp, datetime)
                else str(message.timestamp)
            )
            with self._connect() as conn:
                conn.execute('INSERT INTO messages (chat_id, message_id, content, timestamp, is_sent_to_telegram) VALUES (?, ?, ?, ?, ?)',
                            (message.chat_id, message.message_id, message.content, timestamp, message.is_sent_to_telegram))
                return True
        except sqlite3.IntegrityError:
            return False

    def mark_message_sent(self, message_id: str) -> None:
        with self._connect() as conn:
            conn.execute('UPDATE messages SET is_sent_to_telegram = TRUE WHERE message_id = ?', (message_id,))

    def is_message_sent(self, message_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                'SELECT is_sent_to_telegram FROM messages WHERE message_id = ?',
                (message_id,),
            ).fetchone()
            return bool(row and row[0])
            
    def get_unsent_messages(self, chat_id: int) -> List[Message]:
        with self._connect() as conn:
            cursor = conn.execute('SELECT chat_id, message_id, content, timestamp, is_sent_to_telegram FROM messages WHERE chat_id = ? AND is_sent_to_telegram = FALSE ORDER BY timestamp ASC', (chat_id,))
            messages = []
            for row in cursor.fetchall():
                timestamp = row[3]
                if isinstance(timestamp, str):
                    try:
                        timestamp = datetime.fromisoformat(timestamp)
                    except ValueError:
                        timestamp = datetime.now()
                messages.append(Message(row[0], row[1], row[2], timestamp, bool(row[4])))
            return messages

    def enqueue_telegram_message(
        self,
        text: str,
        topic_id: int,
        chat_id: Optional[int] = None,
        message_id: Optional[str] = None,
        parse_mode: Optional[str] = None,
        reply_markup: Optional[str] = None,
        dedupe_key: Optional[str] = None,
        delay_seconds: float = 0,
    ) -> int:
        """Persist a Telegram delivery and return its outbox id."""
        next_attempt_at = time.time() + max(0, float(delay_seconds))
        with self._connect() as conn:
            if dedupe_key:
                conn.execute(
                    '''
                    INSERT INTO telegram_outbox
                        (dedupe_key, text, topic_id, chat_id, message_id, parse_mode, reply_markup, next_attempt_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(dedupe_key) DO UPDATE SET
                        text = excluded.text,
                        topic_id = excluded.topic_id,
                        chat_id = excluded.chat_id,
                        message_id = excluded.message_id,
                        parse_mode = excluded.parse_mode,
                        reply_markup = excluded.reply_markup,
                        next_attempt_at = MIN(telegram_outbox.next_attempt_at, excluded.next_attempt_at)
                    ''',
                    (dedupe_key, text, topic_id, chat_id, message_id, parse_mode, reply_markup, next_attempt_at),
                )
                row = conn.execute(
                    'SELECT id FROM telegram_outbox WHERE dedupe_key = ?',
                    (dedupe_key,),
                ).fetchone()
                return int(row[0])

            cursor = conn.execute(
                '''
                INSERT INTO telegram_outbox
                    (dedupe_key, text, topic_id, chat_id, message_id, parse_mode, reply_markup, next_attempt_at)
                VALUES (NULL, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (text, topic_id, chat_id, message_id, parse_mode, reply_markup, next_attempt_at),
            )
            return int(cursor.lastrowid)

    def get_due_telegram_messages(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                '''
                SELECT id, dedupe_key, text, topic_id, chat_id, message_id,
                       parse_mode, reply_markup, attempts, next_attempt_at
                FROM telegram_outbox
                WHERE next_attempt_at <= ?
                ORDER BY id ASC
                LIMIT ?
                ''',
                (time.time(), max(1, int(limit))),
            ).fetchall()
            return [dict(row) for row in rows]

    def reschedule_telegram_message(self, outbox_id: int, delay_seconds: float, error: str = '') -> None:
        next_attempt_at = time.time() + max(1, float(delay_seconds))
        with self._connect() as conn:
            conn.execute(
                '''
                UPDATE telegram_outbox
                SET attempts = attempts + 1, next_attempt_at = ?, last_error = ?
                WHERE id = ?
                ''',
                (next_attempt_at, error[:500], outbox_id),
            )

    def delete_telegram_message(self, outbox_id: int) -> None:
        with self._connect() as conn:
            conn.execute('DELETE FROM telegram_outbox WHERE id = ?', (outbox_id,))
