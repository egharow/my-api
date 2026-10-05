"""Read everything waiting in inbox/, add it to a new draft batch, and file the originals.

Safe to re-run: files already imported are recognised by content and set aside as duplicates,
and overlapping statements only add the lines that are new.
"""
import hashlib
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import categorize, fx, reconcile
from . import discrepancies as dx
from .archive import plan_path
from .backup import make_backup, prune
from .config import Home
from .db import audit, now
from .normalize import norm_description
from .parsers.base import ParsedFile, ParsedStatement, ParsedTxn
from .parsers.detect import Unrecognised, parse_file

ISSUER_NAMES = {"isracard": "Isracard", "amex": "Amex", "leumi": "Leumi"}
SUPPORTED = {".xlsx", ".xls", ".pdf"}


@dataclass
class FileResult:
    name: str
    status: str                 # imported | duplicate | unrecognised
    detail: str = ""
    new_txns: int = 0
    skipped_txns: int = 0
    archived_to: str | None = None


@dataclass
class ImportReport:
    batch_id: int | None
    files: list[FileResult] = field(default_factory=list)

    @property
    def imported(self) -> list[FileResult]:
        return [f for f in self.files if f.status == "imported"]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _match_owner(conn: sqlite3.Connection, holder_text: str) -> int | None:
    for alias in conn.execute("SELECT owner_id, alias FROM owner_aliases"):
        if alias["alias"] in holder_text:
            return alias["owner_id"]
    return None


def ensure_account(conn: sqlite3.Connection, st: ParsedStatement) -> int:
    row = conn.execute("SELECT id, owner_id FROM accounts WHERE issuer = ? AND last4 = ?",
                       (st.issuer, st.last4)).fetchone()
    owner = _match_owner(conn, st.holder_text)
    if row:
        if row["owner_id"] is None and owner is not None:
            conn.execute("UPDATE accounts SET owner_id = ? WHERE id = ?", (owner, row["id"]))
        return row["id"]
    label = f"{ISSUER_NAMES.get(st.issuer, st.issuer.title())} {st.last4}"
    cur = conn.execute(
        "INSERT INTO accounts (kind, issuer, label, last4, owner_id, currency, created_at) VALUES (?,?,?,?,?,?,?)",
        (st.account_kind, st.issuer, label, st.last4, owner, st.currency, now()))
    audit(conn, "account_created", "account", cur.lastrowid, label, "import")
    return cur.lastrowid


def _owner_labels(conn: sqlite3.Connection, parsed: ParsedFile) -> dict[str, str]:
    out = {}
    for st in parsed.statements:
        r = conn.execute(
            "SELECT o.name FROM accounts a JOIN owners o ON o.id = a.owner_id WHERE a.issuer = ? AND a.last4 = ?",
            (st.issuer, st.last4)).fetchone()
        if r:
            out[st.last4] = r[0].lower()
    return out


def _dedupe_key(account_id: int, kind: str, t: ParsedTxn, billing: date | None) -> str:
    if kind == "card" and t.voucher:
        return f"card|{account_id}|{t.voucher}|{t.txn_date}|{t.amount:.2f}|{billing}"
    bal = f"{t.balance_after:.2f}" if t.balance_after is not None else ""
    return f"{kind}|{account_id}|{t.txn_date}|{norm_description(t.description)}|{t.amount:.2f}|{bal}|{t.voucher or ''}|{billing}"


def _insert_statement(conn, batch_id: int, source_file_id: int, st: ParsedStatement,
                      account_id: int) -> tuple[int, int, int]:
    """Returns (statement_id, new_txns, skipped_txns)."""
    stmt = None
    if st.billing_date:
        stmt = conn.execute("SELECT id, stated_total FROM statements WHERE account_id = ? AND billing_date = ?",
                            (account_id, st.billing_date.isoformat())).fetchone()
    if stmt:
        statement_id = stmt["id"]
        if st.stated_total is not None and abs((stmt["stated_total"] or 0) - st.stated_total) > 0.005:
            dx.raise_item(
                conn, f"stmt_changed:{statement_id}", "statement_changed",
                f"A newer file for the same billing date shows a different total "
                f"(₪{stmt['stated_total']:,.2f} → ₪{st.stated_total:,.2f})",
                "Likely a later export of the same statement. The newer total was kept.",
                "warning", ("statement", statement_id), batch_id)
            conn.execute("UPDATE statements SET stated_total = ? WHERE id = ?", (st.stated_total, statement_id))
    else:
        cur = conn.execute(
            """INSERT INTO statements (source_file_id, account_id, billing_date, period_start, period_end,
                                       stated_total, currency) VALUES (?,?,?,?,?,?,?)""",
            (source_file_id, account_id, st.billing_date and st.billing_date.isoformat(),
             st.period_start and st.period_start.isoformat(), st.period_end and st.period_end.isoformat(),
             st.stated_total, st.currency))
        statement_id = cur.lastrowid

    new = skipped = 0
    for t in st.txns:
        key = _dedupe_key(account_id, st.account_kind, t, st.billing_date)
        # Cards book into the month they are billed; the purchase date is kept alongside.
        month_basis = st.billing_date or t.txn_date
        try:
            cur = conn.execute(
                """INSERT INTO transactions
                   (batch_id, account_id, statement_id, dedupe_key, txn_date, billing_date, budget_month,
                    description, description_norm, detail, amount, currency, orig_amount, orig_currency,
                    fx_rate, voucher, balance_after, is_recurring, instalment_no, instalment_total,
                    instalment_full, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (batch_id, account_id, statement_id, key, t.txn_date.isoformat(),
                 st.billing_date and st.billing_date.isoformat(), month_basis.strftime("%Y-%m"),
                 t.description, norm_description(t.description), t.detail, t.amount, t.currency,
                 t.orig_amount, t.orig_currency,
                 (t.amount / t.orig_amount) if t.orig_amount and t.orig_currency != t.currency else None,
                 t.voucher, t.balance_after, int(t.is_recurring), t.instalment_no, t.instalment_total,
                 t.instalment_full, now()))
        except sqlite3.IntegrityError:
            skipped += 1
            continue
        categorize.classify(conn, cur.lastrowid)
        new += 1
    return statement_id, new, skipped


def _check_statement(conn, batch_id: int, statement_id: int, st: ParsedStatement, label: str) -> None:
    if st.account_kind == "card":
        computed = conn.execute("SELECT -SUM(amount) FROM transactions WHERE statement_id = ?",
                                (statement_id,)).fetchone()[0] or 0.0
        conn.execute("UPDATE statements SET computed_total = ? WHERE id = ?", (computed, statement_id))
        fp = f"stmt_total:{statement_id}"
        if st.stated_total is not None and abs(computed - st.stated_total) > 0.005:
            dx.raise_item(
                conn, fp, "statement_total_mismatch",
                f"{label}: lines add up to ₪{computed:,.2f} but the statement says ₪{st.stated_total:,.2f}",
                "Some lines may be missing from the file.", "error", ("statement", statement_id), batch_id)
        else:
            dx.auto_resolve(conn, fp, "lines now add up to the statement total")
    else:
        bad = reconcile.chain_problems(st.txns)
        fp = f"chain:{statement_id}"
        if bad:
            dx.raise_item(
                conn, fp, "balance_chain",
                f"{label}: running balance does not add up on {', '.join(d.strftime('%d.%m.%Y') for d in bad)}",
                "A transaction may be missing or altered on these days.", "error",
                ("statement", statement_id), batch_id)


def _planned_names(conn, parsed) -> dict[str, str]:
    return _owner_labels(conn, parsed)


def _set_aside(path: Path, folder: Path, reason: str | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / path.name
    n = 2
    while dest.exists():
        dest = folder / f"{path.stem}-{n}{path.suffix}"
        n += 1
    shutil.move(str(path), dest)
    if reason:
        dest.with_name(dest.name + ".txt").write_text(reason, encoding="utf-8")
    return dest


def import_inbox(home: Home, conn: sqlite3.Connection, today: date | None = None,
                 batch_id: int | None = None) -> ImportReport:
    today = today or date.today()
    home.ensure()
    files = sorted(p for p in home.inbox.iterdir() if p.is_file() and not p.name.startswith("."))
    report = ImportReport(batch_id=batch_id)
    if not files:
        return report
    backup_done = False

    for path in files:
        if path.suffix.lower() not in SUPPORTED:
            _set_aside(path, home.unrecognised, f"Unsupported file type {path.suffix!r}")
            report.files.append(FileResult(path.name, "unrecognised", "unsupported file type"))
            continue
        digest = sha256(path)
        if conn.execute("SELECT 1 FROM source_files WHERE sha256 = ?", (digest,)).fetchone():
            dest = _set_aside(path, home.duplicates)
            report.files.append(FileResult(path.name, "duplicate", "already imported", archived_to=str(dest)))
            continue
        try:
            parsed = parse_file(path)
        except Unrecognised as exc:
            _set_aside(path, home.unrecognised, str(exc))
            report.files.append(FileResult(path.name, "unrecognised", str(exc)))
            continue

        if not backup_done:
            make_backup(conn, home, "pre-import")
            prune(home)
            backup_done = True
        if report.batch_id is None:
            cur = conn.execute("INSERT INTO batches (created_at) VALUES (?)", (now(),))
            report.batch_id = cur.lastrowid
        batch_id = report.batch_id

        archived: Path | None = None
        try:
            account_ids = [ensure_account(conn, st) for st in parsed.statements]
            start, end = parsed.period
            archived = plan_path(home, parsed, path, _planned_names(conn, parsed))
            archived.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, archived)
            cur = conn.execute(
                """INSERT INTO source_files (sha256, original_name, issuer, file_kind, archived_path,
                                             period_start, period_end, batch_id, imported_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (digest, path.name, parsed.issuer, parsed.file_kind,
                 str(archived.relative_to(home.root)), start and start.isoformat(),
                 end and end.isoformat(), batch_id, now()))
            sf_id = cur.lastrowid
            new = skipped = 0
            for st, acct in zip(parsed.statements, account_ids):
                statement_id, n, s = _insert_statement(conn, batch_id, sf_id, st, acct)
                new += n
                skipped += s
                label = conn.execute("SELECT label FROM accounts WHERE id = ?", (acct,)).fetchone()[0]
                _check_statement(conn, batch_id, statement_id, st, label)
            audit(conn, "file_imported", "source_file", sf_id, f"{path.name}: {new} new, {skipped} already known", "import")
            conn.commit()
        except Exception:
            conn.rollback()
            if archived and archived.exists():
                archived.unlink()
            raise
        path.unlink()
        report.files.append(FileResult(path.name, "imported", f"{parsed.issuer} {parsed.file_kind}",
                                       new, skipped, str(archived.relative_to(home.root))))

    if report.batch_id is not None:
        fx.record_card_rates(conn)
        reconcile.run(conn, report.batch_id, today.isoformat())
    return report
