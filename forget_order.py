import sqlite3
import sys

if len(sys.argv) < 2:
    print("Usage: python3 forget_order.py <invoice_id>")
    sys.exit(1)

invoice_id = sys.argv[1]
topic_key = f"purchase_{invoice_id}"
db_path = "ggsel_bot.db"

try:
    with sqlite3.connect(db_path) as conn:
        # Remove from purchases table
        cur = conn.execute("DELETE FROM purchases WHERE invoice_id = ?", (invoice_id,))
        purchases_deleted = cur.rowcount
        
        # Remove from topics table
        cur = conn.execute("DELETE FROM topics WHERE key = ?", (topic_key,))
        topics_deleted = cur.rowcount
        
        print(f"✅ Successfully forgot order #{invoice_id}!")
        print(f"Deleted {purchases_deleted} record from purchases and {topics_deleted} from topics.")
        print("⏳ The bot will process this as a NEW order within the next 30-60 seconds.")
except Exception as e:
    print(f"❌ Error: {e}")
