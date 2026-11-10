from __future__ import annotations

import smtplib
from email.message import EmailMessage


def _fmt(dt) -> str:
    return dt.strftime("%a %d %b %Y %H:%M UTC") if dt else "?"


def build_digest(sales: list, articles: list, watched: list, weekly: bool) -> str:
    lines = ["# Concert presale digest", ""]
    if sales:
        lines.append("## Upcoming presales & on-sales (Ticketmaster, official)")
        for s in sales:
            ends = " -> ends %s" % _fmt(s["ends"]) if s.get("ends") else ""
            lines.append(
                "- **%s** - %s: %s, %s (%s) on %s\n  %s starts **%s**%s\n  %s"
                % (s["artist"], s["sale"], s.get("venue") or "?", s.get("city") or "?", s["country"],
                   s.get("date") or "TBA", s["sale"], _fmt(s["starts"]), ends, s.get("sale_url") or s.get("url") or "")
            )
        lines.append("")
    if articles:
        lines.append("## Tour announcements (from articles; verify dates before relying on them)")
        for a in articles:
            info = a.get("info") or {}
            lines.append("- **%s** - %s\n  %s" % (a["artist"], info.get("tour_name") or info.get("_title") or "", a["url"]))
            for p in info.get("presales") or []:
                bits = [p.get("name"), p.get("how_to_get_access"), p.get("starts") and "starts " + p["starts"],
                        p.get("signup_deadline") and "signup by " + p["signup_deadline"], p.get("signup_url")]
                lines.append("  - presale: " + "; ".join(b for b in bits if b))
            if info.get("general_onsale"):
                lines.append("  - general on-sale: %s" % info["general_onsale"])
            if info.get("quote"):
                lines.append('  - article says: "%s"' % info["quote"].strip())
        lines.append("")
    if weekly:
        lines.append("## Watching this week (Pollstar top sellers)")
        lines.append(", ".join(watched) or "(none)")
        lines.append("")
    if not sales and not articles:
        lines.append("No new presales found.")
    lines.append("You still register and buy yourself - this tool only alerts you.")
    return "\n".join(lines)


def send(cfg, subject: str, body: str) -> bool:
    with open(cfg.digest_path, "w") as f:
        f.write(body + "\n")
    print(body)
    if not (cfg.smtp_host and cfg.email_to and cfg.email_from):
        print("\n(email not configured; digest saved to %s)" % cfg.digest_path)
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.email_from
    msg["To"] = cfg.email_to
    msg.set_content(body)
    with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=30) as smtp:
        smtp.starttls()
        if cfg.smtp_user:
            smtp.login(cfg.smtp_user, cfg.smtp_password)
        smtp.send_message(msg)
    print("\nEmailed digest to %s" % cfg.email_to)
    return True
