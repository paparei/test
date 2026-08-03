import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from database import Database, Message


class DatabaseOutboxTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "bot.db")
        self.database = Database(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_outbox_deduplicates_retryable_delivery(self):
        first_id = self.database.enqueue_telegram_message(
            text="first",
            topic_id=10,
            dedupe_key="review:1:hash",
        )
        second_id = self.database.enqueue_telegram_message(
            text="updated",
            topic_id=10,
            dedupe_key="review:1:hash",
        )

        self.assertEqual(first_id, second_id)
        due = self.database.get_due_telegram_messages()
        self.assertEqual(1, len(due))
        self.assertEqual("updated", due[0]["text"])

        self.database.reschedule_telegram_message(first_id, 60, "rate limited")
        self.assertEqual([], self.database.get_due_telegram_messages())

        self.database.delete_telegram_message(first_id)
        connection = sqlite3.connect(self.db_path)
        try:
            count = connection.execute("SELECT COUNT(*) FROM telegram_outbox").fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(0, count)

    def test_message_delivery_status_is_persisted(self):
        message = Message(
            chat_id=123,
            message_id="message-1",
            content="hello",
            timestamp=datetime.now(),
        )
        self.assertTrue(self.database.save_message(message))
        self.assertFalse(
            self.database.is_message_sent(message.message_id, chat_id=message.chat_id)
        )

        self.database.mark_message_sent(message.message_id, chat_id=message.chat_id)

        self.assertTrue(
            self.database.is_message_sent(message.message_id, chat_id=message.chat_id)
        )

    def test_message_ids_are_unique_within_each_chat(self):
        first = Message(101, "1", "first", datetime.now())
        second = Message(202, "1", "second", datetime.now())

        self.assertTrue(self.database.save_message(first))
        self.assertTrue(self.database.save_message(second))
        self.database.mark_message_sent("1", chat_id=101)

        self.assertTrue(self.database.is_message_sent("1", chat_id=101))
        self.assertFalse(self.database.is_message_sent("1", chat_id=202))

    def test_legacy_global_message_constraint_is_migrated(self):
        legacy_path = str(Path(self.temp_dir.name) / "legacy.db")
        connection = sqlite3.connect(legacy_path)
        try:
            connection.execute('''
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER,
                    message_id TEXT UNIQUE,
                    content TEXT,
                    timestamp TIMESTAMP,
                    is_sent_to_telegram BOOLEAN DEFAULT FALSE
                )
            ''')
            connection.execute(
                "INSERT INTO messages (chat_id, message_id, content, timestamp) VALUES (?, ?, ?, ?)",
                (101, "1", "legacy", datetime.now().isoformat()),
            )
            connection.commit()
        finally:
            connection.close()

        migrated = Database(legacy_path)

        self.assertTrue(migrated.message_exists("1", chat_id=101))
        self.assertTrue(
            migrated.save_message(Message(202, "1", "new chat", datetime.now()))
        )


if __name__ == "__main__":
    unittest.main()
