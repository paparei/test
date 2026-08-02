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
        self.assertFalse(self.database.is_message_sent(message.message_id))

        self.database.mark_message_sent(message.message_id)

        self.assertTrue(self.database.is_message_sent(message.message_id))


if __name__ == "__main__":
    unittest.main()
