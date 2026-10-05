"""Starting categories and built-in rules. Learned and user rules are added on top."""
import sqlite3

from .db import now

# (name, parent, kind, neutral)
CATEGORIES = [
    ("Food", None, "expense", 0),
    ("Groceries", "Food", "expense", 0),
    ("Eating out", "Food", "expense", 0),
    ("Delivery", "Food", "expense", 0),
    ("Home", None, "expense", 0),
    ("Property tax", "Home", "expense", 0),
    ("Building committee", "Home", "expense", 0),
    ("Utilities", "Home", "expense", 0),
    ("Internet & TV", "Home", "expense", 0),
    ("Phone", "Home", "expense", 0),
    ("Household", "Home", "expense", 0),
    ("Insurance", None, "expense", 0),
    ("Car", None, "expense", 0),
    ("Fuel & parking", "Car", "expense", 0),
    ("Education", None, "expense", 0),
    ("Medical", None, "expense", 0),
    ("Donations", None, "expense", 0),
    ("Memberships", None, "expense", 0),
    ("Clothes", None, "expense", 0),
    ("Gifts", None, "expense", 0),
    ("Fun", None, "expense", 0),
    ("Beauty care", None, "expense", 0),
    ("Fines", None, "expense", 0),
    ("Subscriptions", None, "expense", 0),
    ("Miscellaneous", None, "expense", 0),
    ("Bank fees", None, "fee", 0),
    ("Mortgage", None, "debt", 0),
    ("Loans", None, "debt", 0),
    ("Salary", None, "income", 0),
    ("Reserve duty pay", None, "income", 0),
    ("Former employer", None, "income", 0),
    ("Bank grants & interest", None, "income", 0),
    ("Other income", None, "income", 0),
    ("Member card (בהצדעה)", None, "expense", 0),
    ("Card payment", None, "transfer", 1),
    ("Transfer to household", None, "transfer", 1),
    ("Transfer to savings", None, "saving", 1),
    ("Internal transfer", None, "transfer", 1),
    ("Uncategorised", None, "expense", 0),
]

# (pattern, category, set_kind, direction, expects_refund_days, auto_approve, note)
# Patterns are matched case-insensitively against the normalised description.
BUILTIN_RULES = [
    ("סופר שפע", "Groceries", None, None, None, 0, None),
    ("שופרסל", "Groceries", None, None, None, 0, None),
    ("תלם מרקט", "Groceries", None, None, None, 0, None),
    ("סיטי מרקט", "Groceries", None, None, None, 0, None),
    ("פרשמרקט", "Groceries", None, None, None, 0, None),
    ("כל - בו", "Groceries", None, None, None, 0, None),
    ("אטליז", "Groceries", None, None, None, 0, None),
    ("מאפיית", "Groceries", None, None, None, 0, None),
    ("מאפה", "Groceries", None, None, None, 0, None),
    ("קונדטוריה", "Groceries", None, None, None, 0, None),
    ("הדרך ללחם", "Groceries", None, None, None, 0, None),
    ("קרפור", "Groceries", None, None, None, 0, None),
    ("WOLT", "Delivery", None, None, None, 0, None),
    ("סופר פארם", "Medical", None, None, None, 0, "pharmacy; may also be household"),
    ("גוד פארם", "Medical", None, None, None, 0, None),
    ("קרן מכבי", "Medical", None, None, None, 0, None),
    ("ישיר-ביטוח", "Insurance", None, None, None, 0, None),
    ("ביטוח ישיר", "Insurance", None, None, None, 0, None),
    ("ישיר ביטוח", "Insurance", None, None, None, 0, None),
    ("בטוח חובה", "Insurance", None, None, None, 0, None),
    ("מגדל", "Insurance", None, None, None, 0, None),
    ("בזק", "Utilities", None, None, None, 0, None),
    ("פז גז", "Utilities", None, None, None, 0, None),
    ("NEXT TV", "Internet & TV", None, None, None, 0, None),
    ("019 מובייל", "Phone", None, None, None, 0, None),
    ("עיריית", "Property tax", None, None, None, 0, None),
    ("החב' העירונית", "Property tax", None, None, None, 0, None),
    ("DARIMPO", "Building committee", None, None, None, 0, None),
    ("חב\"ד", "Donations", None, None, None, 0, None),
    ("עזר מציון", "Donations", None, None, None, 0, None),
    ("נתינה בקליק", "Donations", None, None, None, 0, None),
    ("קרן עשור", "Donations", None, None, None, 0, None),
    ("ברק\" - משפחות", "Donations", None, None, None, 0, None),
    ("עמותת ברק", "Donations", None, None, None, 0, None),
    ("דרך ארץ", "Fuel & parking", None, None, None, 0, None),
    ("חניון", "Fuel & parking", None, None, None, 0, None),
    ("אלונית", "Fuel & parking", None, None, None, 0, None),
    ("H&M", "Clothes", None, None, None, 0, None),
    ("NEXT NEXT", "Clothes", None, None, None, 0, None),
    ("ANTHROPIC", "Subscriptions", None, None, None, 0, None),
    ("דמי חבר", "Memberships", None, None, None, 0, None),
    ("מועדון", "Memberships", None, None, None, 0, None),
    ("חבר שלי", "Memberships", None, None, None, 0, None),
    ("המרכז לגביית קנסות", "Fines", None, None, None, 0, None),
    ("דמי כרטיס", "Bank fees", "fee", None, None, 1, None),
    # Bit transfers move money between household accounts, not spending.
    ("BIT", "Transfer to household", "transfer", None, None, 1, "Bit transfers between household accounts"),
    # Bank statement lines
    ("מאסטרקרד", "Card payment", "card_payment", "out", None, 1, None),
    ("אקספ", "Card payment", "card_payment", "out", None, 1, None),
    ("הבינלאומי", "Card payment", "card_payment", "out", None, 1, None),
    # A member-card top-up with no statement to match: count the payment itself as spending.
    ("מקס איט", "Member card (בהצדעה)", None, "out", None, 1, "member card; statement optional"),
    ("פרעון הלוואה", "Loans", "loan", "out", None, 1, None),
    ("משכנת", "Mortgage", "mortgage", "out", None, 1, None),
    ("עמל", "Bank fees", "fee", "out", 14, 1, "channel commission; expect a refund"),
    ("מכתב חריגה", "Bank fees", "fee", "out", None, 1, None),
    ("ריבית חובה", "Bank fees", "interest", "out", None, 1, None),
    ("ריבית ידני", "Bank grants & interest", "income", "in", None, 1, None),
    ("מענק בנק", "Bank grants & interest", "income", "in", None, 1, None),
    ("העברה דיגיטל", "Internal transfer", "transfer", None, None, 0, "usually moved on to savings"),
    ("העברת זה\"ב", "Internal transfer", "transfer", None, None, 0, "foreign-currency transfer"),
    ("הע. אינטרנט", "Internal transfer", "transfer", None, None, 0, None),
    ("בנק הפועלים", "Internal transfer", "transfer", "out", None, 0, None),
]


BANK_ONLY = {"העברה דיגיטל", "העברת זה\"ב", "הע. אינטרנט", "מכתב חריגה", "בנק הפועלים"}


def seed_all(conn: sqlite3.Connection) -> None:
    ids: dict[str, int] = {}
    for name, parent, kind, neutral in CATEGORIES:
        cur = conn.execute(
            "INSERT INTO categories (name, parent_id, kind, neutral) VALUES (?,?,?,?)",
            (name, ids.get(parent), kind, neutral),
        )
        ids[name] = cur.lastrowid
    # Longer patterns first so specific entries beat generic ones such as "BIT".
    for pattern, cat, set_kind, direction, refund_days, auto, note in sorted(
            BUILTIN_RULES, key=lambda r: -len(r[0])):
        conn.execute(
            """INSERT INTO rules (source, pattern, direction, account_kind, category_id, set_kind,
                                  auto_approve, expects_refund_days, priority, note, created_at)
               VALUES ('builtin', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (pattern, direction, "bank" if (direction or pattern in BANK_ONLY) else None,
             ids[cat], set_kind, auto, refund_days, len(pattern), note, now()),
        )
    conn.commit()
