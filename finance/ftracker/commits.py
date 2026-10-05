"""Submit: lock an import and record exactly what was recorded and when."""
import json
import sqlite3
from datetime import date

from . import balances, expected
from .backup import make_backup
from .config import Home
from .db import audit, now


class NeedsAcknowledgement(Exception):
    def __init__(self, issues: list[str]):
        super().__init__("; ".join(issues))
        self.issues = issues


def readiness(conn: sqlite3.Connection, batch_id: int, today: str) -> list[str]:
    """What is still open for this import. Items belong to the import that raised them."""
    issues = []
    n = conn.execute("SELECT COUNT(*) FROM transactions WHERE batch_id = ? AND category_status = 'proposed'",
                     (batch_id,)).fetchone()[0]
    if n:
        issues.append(f"{n} transaction(s) still have only a proposed category")
    for d in conn.execute("SELECT title FROM discrepancies WHERE status = 'open' AND type != 'manual' "
                          "AND (batch_id = ? OR batch_id IS NULL)", (batch_id,)):
        issues.append(f"open: {d['title']}")
    has_files = conn.execute("SELECT 1 FROM source_files WHERE batch_id = ?", (batch_id,)).fetchone()
    if has_files:    # the checklist of expected files only matters when statements are being submitted
        for e in expected.expected_files(conn, today):
            if e["status"] in ("missing", "due"):
                issues.append(f"{e['status']}: {e['source']} {e['period']} ({e['detail']})".replace("  ", " "))
    return issues


def _summary(conn: sqlite3.Connection, batch_id: int, today: str, issues: list[str], acknowledged: bool) -> dict:
    files = [dict(r) for r in conn.execute(
        "SELECT original_name, sha256, issuer, file_kind, period_start, period_end FROM source_files WHERE batch_id = ?",
        (batch_id,))]
    stmts = [dict(r) for r in conn.execute(
        """SELECT a.label, s.billing_date, s.period_start, s.period_end, s.stated_total, s.computed_total,
                  s.matched_bank_txn_id IS NOT NULL AS matched_to_bank
           FROM statements s JOIN accounts a ON a.id = s.account_id
           JOIN source_files f ON f.id = s.source_file_id WHERE f.batch_id = ?""", (batch_id,))]
    by_status = {r[0]: r[1] for r in conn.execute(
        "SELECT category_status, COUNT(*) FROM transactions WHERE batch_id = ? GROUP BY category_status", (batch_id,))}
    disc = {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM discrepancies GROUP BY status")}
    months = [r[0] for r in conn.execute(
        "SELECT DISTINCT budget_month FROM transactions WHERE batch_id = ? ORDER BY 1", (batch_id,))]
    return {
        "files": files, "statements": stmts, "transactions_by_status": by_status,
        "months_touched": months, "discrepancies": disc,
        "balances_recorded": conn.execute("SELECT COUNT(*) FROM balances WHERE batch_id = ?", (batch_id,)).fetchone()[0],
        "open_issues_at_submit": issues, "acknowledged": acknowledged, "as_of": today,
    }


def submit(conn: sqlite3.Connection, home: Home, batch_id: int, actor: str = "user",
           acknowledge: bool = False, today: str | None = None) -> int:
    today = today or date.today().isoformat()
    batch = conn.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
    if not batch:
        raise ValueError(f"no import #{batch_id}")
    if batch["status"] == "committed":
        raise ValueError(f"import #{batch_id} is already submitted")
    issues = readiness(conn, batch_id, today)
    if issues and not acknowledge:
        raise NeedsAcknowledgement(issues)
    version = (conn.execute("SELECT MAX(version) FROM commits WHERE batch_id = ?", (batch_id,)).fetchone()[0] or 0) + 1
    committed_at = now()
    conn.execute("UPDATE batches SET status = 'committed', committed_at = ? WHERE id = ?", (committed_at, batch_id))
    summary = _summary(conn, batch_id, today, issues, acknowledge and bool(issues))
    reason = None
    if version > 1:
        reason = conn.execute("SELECT note FROM batches WHERE id = ?", (batch_id,)).fetchone()[0]
    cur = conn.execute(
        "INSERT INTO commits (batch_id, version, committed_at, reason, summary_json) VALUES (?,?,?,?,?)",
        (batch_id, version, committed_at, reason, json.dumps(summary, ensure_ascii=False)))
    audit(conn, "submit", "batch", batch_id, f"version {version}", actor)
    conn.commit()
    backup = make_backup(conn, home, f"batch{batch_id}-v{version}")
    conn.execute("UPDATE commits SET backup_path = ? WHERE id = ?", (str(backup.relative_to(home.root)), cur.lastrowid))
    conn.commit()
    return cur.lastrowid


def reopen(conn: sqlite3.Connection, batch_id: int, reason: str, actor: str = "user") -> None:
    if not reason.strip():
        raise ValueError("say why you are reopening it")
    batch = conn.execute("SELECT status FROM batches WHERE id = ?", (batch_id,)).fetchone()
    if not batch or batch["status"] != "committed":
        raise ValueError("only a submitted import can be reopened")
    conn.execute("UPDATE batches SET status = 'reopened', note = ? WHERE id = ?", (reason.strip(), batch_id))
    audit(conn, "reopen", "batch", batch_id, reason, actor)
    conn.commit()


def history(conn: sqlite3.Connection):
    return conn.execute(
        """SELECT c.id, c.batch_id, c.version, c.committed_at, c.reason, c.backup_path
           FROM commits c ORDER BY c.committed_at DESC""").fetchall()
