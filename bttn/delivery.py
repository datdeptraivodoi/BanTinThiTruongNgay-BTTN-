"""Fail-closed SMTP delivery with a durable, conservative outbox ledger."""
import hashlib
import json
import os
import smtplib
import uuid
from email.message import EmailMessage
from pathlib import Path


def send_report(snapshot, attachments: list[Path], state_dir: Path, recipients: list[str], is_test: bool = False):
    sender = os.getenv("SENDER_EMAIL")
    password = os.getenv("SENDER_PASSWORD")
    if not sender or not password or not recipients:
        raise ValueError("Missing SMTP credentials or recipients")
    if len(attachments) != 2 or any(not path.is_file() for path in attachments):
        raise ValueError("Both validated DOCX and PDF attachments are required")
    state_dir.mkdir(parents=True, exist_ok=True)
    if is_test:
        key = f"{snapshot.as_of:%Y%m%d}-test-{uuid.uuid4().hex[:8]}"
    else:
        key = snapshot.as_of.strftime("%Y%m%d") + "-midday"
    ledger = state_dir / f"{key}.json"
    record = {
        "key": key,
        "status": "prepared",
        "recipients": recipients,
        "is_test": is_test,
        "sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in attachments},
    }
    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    prefix = "[TEST / GỬI THỬ] " if is_test else ""
    message["Subject"] = f"{prefix}[MB TREASURY] BẢN TIN THỊ TRƯỜNG NGÀY - {snapshot.as_of:%d/%m/%Y}"
    message["Message-ID"] = f"<bttn-{key}@{sender.split('@')[-1]}>"
    test_note = (
        "\n\n*** ĐÂY LÀ EMAIL THỬ NGHIỆM ĐƯỢC GỬI ĐẾN ĐỊA CHỈ RIÊNG ĐỂ KIỂM TRA ĐỊNH DẠNG. "
        "KHÔNG PHẢI BẢN PHÁT HÀNH CHÍNH THỨC. ***\n"
        if is_test
        else ""
    )
    message.set_content(
        f"Kính gửi Quý Anh/Chị,\n\n"
        f"Bản tin chốt dữ liệu lúc {snapshot.as_of:%H:%M %d/%m/%Y} đính kèm.\n"
        f"Phần dự báo tạm để trống. Dấu — là dữ liệu chưa được xác minh."
        f"{test_note}\n\nTrân trọng."
    )
    for path in attachments:
        subtype = "pdf" if path.suffix == ".pdf" else "vnd.openxmlformats-officedocument.wordprocessingml.document"
        message.add_attachment(path.read_bytes(), maintype="application", subtype=subtype, filename=path.name)
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
