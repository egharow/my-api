"""Things that need a human look, each with its own comment thread.

Items are keyed by a fingerprint so the same problem is not raised twice across imports,
and an item the app raised is closed automatically when the condition clears
(for example a missing statement that arrives later).
"""
import sqlite3

from .db import audit, now

STATUSES = ("open", "explained", "resolved", "acknowledged")


def raise_item(conn: sqlite3.Connection, fingerprint: str, type_: str, title: str,
               detail: str = "", severity: str = "warning", subject: tuple[str, int] | None = None,
               batch_id: int | None = None) -> int:
    row = conn.execute("SELECT id, status, auto_resolved FROM discrepancies WHERE fingerprint = ?",
                       (fingerprint,)).fetchone()
    if row:
        if row["status"] == "resolved" and row["auto_resolved"]:
            conn.execute("UPDATE discrepancies SET status = 'open', auto_resolved = 0, "
                         "resolved_at = NULL WHERE id = ?", (row["id"],))
        conn.execute("UPDATE discrepancies SET title = ?, detail = ? WHERE id = ?",
                     (title, detail, row["id"]))
        return row["id"]
    cur = conn.execute(
        """INSERT INTO discrepancies (fingerprint, type, severity, title, detail, subject_type,
                                      subject_id, batch_id, created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (fingerprint, type_, severity, title, detail,
         subject[0] if subject else None, subject[1] if subject else None, batch_id, now()))
    return cur.lastrowid


def auto_resolve(conn: sqlite3.Connection, fingerprint: str, why: str) -> None:
    row = conn.execute("SELECT id, status FROM discrepancies WHERE fingerprint = ?",
                       (fingerprint,)).fetchone()
    if row and row["status"] in ("open", "explained"):
        conn.execute("UPDATE discrepancies SET status = 'resolved', auto_resolved = 1, "
                     "resolved_at = ? WHERE id = ?", (now(), row["id"]))
        add_comment(conn, row["id"], "app", f"Resolved automatically: {why}")


def add_comment(conn: sqlite3.Connection, item_id: int, author: str, body: str) -> int:
    if not body.strip():
        raise ValueError("comment is empty")
    cur = conn.execute(
        "INSERT INTO comments (discrepancy_id, author, body, created_at) VALUES (?,?,?,?)",
        (item_id, author, body.strip(), now()))
    audit(conn, "comment", "discrepancy", item_id, None, author)
    return cur.lastrowid


def edit_comment(conn: sqlite3.Connection, comment_id: int, author: str, body: str) -> None:
    row = conn.execute("SELECT author FROM comments WHERE id = ?", (comment_id,)).fetchone()
    if not row or row["author"] != author:
        raise PermissionError("you can only edit your own comments")
    conn.execute("UPDATE comments SET body = ?, edited_at = ? WHERE id = ?",
                 (body.strip(), now(), comment_id))


def delete_comment(conn: sqlite3.Connection, comment_id: int, author: str) -> None:
    row = conn.execute("SELECT author FROM comments WHERE id = ?", (comment_id,)).fetchone()
    if not row or row["author"] != author:
        raise PermissionError("you can only delete your own comments")
    conn.execute("UPDATE comments SET deleted = 1 WHERE id = ?", (comment_id,))


def set_status(conn: sqlite3.Connection, item_id: int, status: str, actor: str = "user",
               follow_up_date: str | None = None) -> None:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    conn.execute(
        "UPDATE discrepancies SET status = ?, follow_up_date = COALESCE(?, follow_up_date), "
        "resolved_at = CASE WHEN ? IN ('resolved','acknowledged') THEN ? ELSE NULL END WHERE id = ?",
        (status, follow_up_date, status, now(), item_id))
    audit(conn, f"status_{status}", "discrepancy", item_id, None, actor)


def open_manual(conn: sqlite3.Connection, txn_id: int, note: str, author: str = "user") -> int:
    """Start a thread on any transaction, not only ones the app flagged."""
    t = conn.execute("SELECT description, txn_date FROM transactions WHERE id = ?", (txn_id,)).fetchone()
    item_id = raise_item(conn, f"manual:{txn_id}", "manual", f"Note on {t['description']} ({t['txn_date']})",
                         "", "info", ("transaction", txn_id))
    add_comment(conn, item_id, author, note)
    return item_id


def list_items(conn: sqlite3.Connection, statuses: tuple[str, ...] = ("open",),
               today: str | None = None):
    marks = ",".join("?" * len(statuses))
    rows = conn.execute(
        f"""SELECT d.*, (SELECT COUNT(*) FROM comments c WHERE c.discrepancy_id = d.id
                         AND c.deleted = 0) AS comment_count
            FROM discrepancies d WHERE d.status IN ({marks})
            ORDER BY CASE d.severity WHEN 'error' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
                     d.created_at""", statuses).fetchall()
    if today is None:
        return rows
    return [r for r in rows if r["status"] == "open" or (r["follow_up_date"] and r["follow_up_date"] <= today)]


def thread(conn: sqlite3.Connection, item_id: int):
    return conn.execute(
        "SELECT * FROM comments WHERE discrepancy_id = ? AND deleted = 0 ORDER BY created_at, id",
        (item_id,)).fetchall()
