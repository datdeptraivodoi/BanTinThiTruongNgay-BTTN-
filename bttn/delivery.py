"""Fail-closed SMTP delivery with a durable, conservative outbox ledger."""
import hashlib
import json
import os
import smtplib
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

DEFAULT_REVIEW_RECIPIENTS = "datnh1@mbbank.com.vn"


def review_recipients():
    """Người duyệt bản tin trong giai đoạn review (biến REVIEW_RECIPIENTS, phân tách bằng dấu phẩy)."""
    raw = os.getenv("REVIEW_RECIPIENTS") or DEFAULT_REVIEW_RECIPIENTS
    return [r.strip() for r in raw.split(",") if r.strip()]


def _smtp_send(message, sender, password):
    with smtplib.SMTP_SSL(os.getenv("SMTP_HOST", "smtp.gmail.com"), int(os.getenv("SMTP_PORT", "465")), timeout=30) as server:
        server.login(sender, password)
        return server.send_message(message)


def send_report(snapshot, attachments: list[Path], state_dir: Path, recipients: list[str],
                review: bool = False, issues: list | None = None):
    """
    Gửi bản tin Word.

    review=True: gửi cho người duyệt, kể cả bản nháp còn lỗi kiểm tra; tiêu đề có [REVIEW],
    nội dung email liệt kê các lỗi. Ledger riêng theo giờ chạy nên không chặn lần gửi chính thức.
    """
    sender = os.getenv("SENDER_EMAIL")
    password = os.getenv("SENDER_PASSWORD")
    if not sender or not password or not recipients:
        raise ValueError("Missing SMTP credentials or recipients")
    if len(attachments) != 1 or attachments[0].suffix.lower() != ".docx" or not attachments[0].is_file():
        raise ValueError("Exactly one validated Word (.docx) attachment is required")
    state_dir.mkdir(parents=True, exist_ok=True)
    key = snapshot.as_of.strftime("%Y%m%d") + "-midday"
    if review:
        key += "-review-" + datetime.now().strftime("%H%M%S")
    ledger = state_dir / f"{key}.json"
    record = {
        "key": key,
        "status": "prepared",
        "recipients": recipients,
        "review": review,
        "sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in attachments},
    }
    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    subject = f"[MB TREASURY] BẢN TIN THỊ TRƯỜNG NGÀY - {snapshot.as_of:%d/%m/%Y}"
    if review:
        subject = "[REVIEW] " + subject + (f" ({len(issues)} vấn đề cần xem)" if issues else "")
    message["Subject"] = subject
    message["Message-ID"] = f"<bttn-{key}@{sender.split('@')[-1]}>"
    if review:
        lines = [
            "Bản tin thị trường ngày để review (chưa gửi danh sách nhận chính thức).",
            f"Dữ liệu chốt lúc {snapshot.as_of:%H:%M %d/%m/%Y}.",
            "",
        ]
        if issues:
            lines.append(f"Bản nháp còn {len(issues)} vấn đề cần xem:")
            for i in issues[:30]:
                lines.append(f"- [{i.get('severity', '')}] {i.get('code', '')}: {i.get('message', '')}")
            if len(issues) > 30:
                lines.append(f"- … và {len(issues) - 30} vấn đề khác (xem validation-*.json trên VPS).")
        else:
            lines.append("Bản tin đã qua toàn bộ kiểm tra tự động.")
        message.set_content("\n".join(lines))
    else:
        message.set_content(
            f"Kính gửi Quý Anh/Chị,\n\n"
            f"Bản tin chốt dữ liệu lúc {snapshot.as_of:%H:%M %d/%m/%Y} đính kèm.\n"
            f"Dấu — là dữ liệu chưa được xác minh."
            f"\n\nTrân trọng."
        )
    for path in attachments:
        message.add_attachment(path.read_bytes(), maintype="application",
                               subtype="vnd.openxmlformats-officedocument.wordprocessingml.document", filename=path.name)
    # Exclusive creation refuses duplicates, including uncertain prior attempts.
    with ledger.open("x", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)

    def persist(status):
        record["status"] = status
        temp = ledger.with_suffix(".tmp")
        temp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(ledger)
    try:
        with smtplib.SMTP_SSL(os.getenv("SMTP_HOST", "smtp.gmail.com"), int(os.getenv("SMTP_PORT", "465")), timeout=30) as server:
            server.login(sender, password)
            persist("sending")
            refused = server.send_message(message)
            if refused:
                persist("partial_or_unknown")
                raise RuntimeError("Some recipients were refused; inspect delivery ledger before retrying")
            persist("sent")
    except Exception:
        if record["status"] == "prepared":
            ledger.unlink()  # SMTP DATA not attempted; a retry is safe.
        elif record["status"] == "sending":
            persist("unknown")
        raise
    return ledger


def send_failure_alert(as_of, error: str, run_dir: Path, recipients: list[str]):
    """Báo cho người duyệt khi lần chạy hỏng trước khi dựng được file Word."""
    sender = os.getenv("SENDER_EMAIL")
    password = os.getenv("SENDER_PASSWORD")
    if not sender or not password or not recipients:
        return False
    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message["Subject"] = f"[REVIEW] BTTN {as_of:%d/%m/%Y}: lần chạy lỗi, chưa có file Word"
    message.set_content(
        f"Lần chạy BTTN lúc {datetime.now():%H:%M %d/%m/%Y} không dựng được file Word.\n\n"
        f"Lỗi: {error}\n\nThư mục chạy trên VPS: {run_dir}\n(xem manifest.json và snapshot.json để biết chi tiết)."
    )
    try:
        _smtp_send(message, sender, password)
        return True
    except Exception:
        return False
