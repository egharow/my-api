import sqlite3

from .db import audit, now

KINDS = ("card", "bank", "investment", "pension", "savings", "real_estate", "loan", "other")


def add_owner(conn: sqlite3.Connection, name: str, aliases: list[str] | None = None) -> int:
    row = conn.execute("SELECT id FROM owners WHERE name = ?", (name,)).fetchone()
    owner_id = row["id"] if row else conn.execute("INSERT INTO owners (name) VALUES (?)", (name,)).lastrowid
    for alias in aliases or []:
        conn.execute("INSERT OR REPLACE INTO owner_aliases (owner_id, alias) VALUES (?,?)", (owner_id, alias))
    conn.commit()
    return owner_id


def owner_id(conn: sqlite3.Connection, name: str | None) -> int | None:
    if not name:
        return None
    row = conn.execute("SELECT id FROM owners WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
    if not row:
        raise ValueError(f"unknown owner {name!r}; add it with `finance owner add`")
    return row["id"]


def add_account(conn: sqlite3.Connection, kind: str, issuer: str, label: str, owner: str | None = None,
                currency: str = "ILS", last4: str | None = None,
                pays_from: str | None = None) -> int:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    payer = None
    if pays_from:
        payer = find_account(conn, pays_from)["id"]
    cur = conn.execute(
        """INSERT INTO accounts (kind, issuer, label, last4, owner_id, currency, pays_from_account_id, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (kind, issuer.lower(), label, last4, owner_id(conn, owner), currency.upper(), payer, now()))
    audit(conn, "account_created", "account", cur.lastrowid, label)
    conn.commit()
    return cur.lastrowid


def find_account(conn: sqlite3.Connection, ref: str) -> sqlite3.Row:
    """Look up by label, or by last four digits when unambiguous."""
    rows = conn.execute("SELECT * FROM accounts WHERE label = ? COLLATE NOCASE", (ref,)).fetchall()
    if not rows:
        rows = conn.execute("SELECT * FROM accounts WHERE last4 = ?", (ref,)).fetchall()
    if len(rows) != 1:
        raise ValueError(f"{'no account' if not rows else 'several accounts'} match {ref!r}")
    return rows[0]


def set_owner(conn: sqlite3.Connection, ref: str, owner: str) -> None:
    acct = find_account(conn, ref)
    conn.execute("UPDATE accounts SET owner_id = ? WHERE id = ?", (owner_id(conn, owner), acct["id"]))
    audit(conn, "set_owner", "account", acct["id"], owner)
    conn.commit()


def set_pays_from(conn: sqlite3.Connection, card_ref: str, bank_ref: str) -> None:
    card, bank = find_account(conn, card_ref), find_account(conn, bank_ref)
    conn.execute("UPDATE accounts SET pays_from_account_id = ? WHERE id = ?", (bank["id"], card["id"]))
    conn.commit()


def set_counterparty(conn: sqlite3.Connection, txn_id: int, account_ref: str) -> None:
    """Say where a transfer went (for example the savings account), keeping net worth consistent."""
    acct = find_account(conn, account_ref)
    conn.execute("UPDATE transactions SET counterparty_account_id = ? WHERE id = ?", (acct["id"], txn_id))
    audit(conn, "set_counterparty", "transaction", txn_id, acct["label"])
    conn.commit()


def set_destination_rule(conn: sqlite3.Connection, pattern: str, account_ref: str,
                         category: str = "Transfer to savings") -> dict:
    """Say once that transfers matching `pattern` go to an account, for past and future ones.

    Past transactions in an already-submitted import are left alone (reopen it to change them).
    """
    from .normalize import norm_description
    acct = find_account(conn, account_ref)
    cat = conn.execute("SELECT id FROM categories WHERE name = ?", (category,)).fetchone()
    if not cat:
        raise ValueError(f"unknown category {category!r}")
    pat = norm_description(pattern)
    if conn.execute("SELECT 1 FROM rules WHERE source = 'user' AND pattern = ? AND counterparty_account_id = ?",
                    (pat, acct["id"])).fetchone():
        conn.execute("UPDATE rules SET category_id = ?, enabled = 1 WHERE source = 'user' AND pattern = ? AND counterparty_account_id = ?",
                     (cat["id"], pat, acct["id"]))      # saying it twice changes nothing
    else:
        conn.execute(
            """INSERT INTO rules (source, pattern, direction, account_kind, category_id, set_kind,
                                  counterparty_account_id, auto_approve, priority, note, created_at)
               VALUES ('user', ?, 'out', 'bank', ?, 'transfer', ?, 1, ?, ?, ?)""",
            (pat, cat["id"], acct["id"], 1000 + len(pat), f"transfers go to {acct['label']}", now()))
    cur = conn.execute(
        """UPDATE transactions SET counterparty_account_id = ?, category_id = ?, category_status = 'approved',
                  kind = 'transfer', proposal_basis = 'your destination rule', confidence = 1.0
           WHERE description_norm LIKE ? AND amount < 0 AND batch_id IN
                 (SELECT id FROM batches WHERE status != 'committed')""",
        (acct["id"], cat["id"], f"%{pat}%"))
    skipped = conn.execute(
        """SELECT COUNT(*) FROM transactions WHERE description_norm LIKE ? AND amount < 0 AND batch_id IN
           (SELECT id FROM batches WHERE status = 'committed')""", (f"%{pat}%",)).fetchone()[0]
    audit(conn, "destination_rule", "account", acct["id"], pat)
    conn.commit()
    return {"updated": cur.rowcount, "left_in_submitted_imports": skipped}


def set_owner_by_id(conn: sqlite3.Connection, account_id: int, owner: str | None) -> None:
    conn.execute("UPDATE accounts SET owner_id = ? WHERE id = ?", (owner_id(conn, owner), account_id))
    audit(conn, "set_owner", "account", account_id, owner or "none")
    conn.commit()


def add_alias(conn: sqlite3.Connection, owner: str, alias: str) -> None:
    alias = alias.strip()
    if not alias:
        raise ValueError("type the name exactly as it is printed on the statement")
    conn.execute("INSERT OR REPLACE INTO owner_aliases (owner_id, alias) VALUES (?, ?)", (owner_id(conn, owner), alias))
    conn.commit()


def remove_alias(conn: sqlite3.Connection, alias_id: int) -> None:
    conn.execute("DELETE FROM owner_aliases WHERE id = ?", (alias_id,))
    conn.commit()


def merge_accounts(conn: sqlite3.Connection, src_id: int, dst_id: int) -> None:
    """Fold a duplicate account into the one you keep: its balances, lines and links move over, and it is switched off."""
    if src_id == dst_id:
        raise ValueError("choose two different accounts")
    for table, key in (("balances", "as_of"), ("coverage", "month")):
        conn.execute(f"DELETE FROM {table} WHERE account_id = ? AND {key} IN (SELECT {key} FROM {table} WHERE account_id = ?)", (src_id, dst_id))
        conn.execute(f"UPDATE {table} SET account_id = ? WHERE account_id = ?", (dst_id, src_id))
    conn.execute("UPDATE transactions SET account_id = ? WHERE account_id = ?", (dst_id, src_id))
    conn.execute("UPDATE transactions SET counterparty_account_id = ? WHERE counterparty_account_id = ?", (dst_id, src_id))
    conn.execute("UPDATE rules SET counterparty_account_id = ? WHERE counterparty_account_id = ?", (dst_id, src_id))
    conn.execute("UPDATE accounts SET pays_from_account_id = ? WHERE pays_from_account_id = ?", (dst_id, src_id))
    conn.execute("UPDATE OR IGNORE statements SET account_id = ? WHERE account_id = ?", (dst_id, src_id))
    conn.execute("UPDATE OR IGNORE goal_accounts SET account_id = ? WHERE account_id = ?", (dst_id, src_id))
    conn.execute("DELETE FROM goal_accounts WHERE account_id = ?", (src_id,))
    conn.execute("UPDATE accounts SET active = 0 WHERE id = ?", (src_id,))
    conn.commit()
