"""Gửi email bản tin tuần: 2 file Word (VIE, ENG). Giai đoạn review chỉ gửi người duyệt."""
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

DOCX = "application", "vnd.openxmlformats-officedocument.wordprocessingml.document"


def send_weekly(files: list[Path], friday_label: str, recipients: list[str], *, review=True, issues=()):
    sender, password = os.getenv("SENDER_EMAIL"), os.getenv("SENDER_PASSWORD")
    if not sender or not password or not recipients:
        raise ValueError("Thiếu SENDER_EMAIL/SENDER_PASSWORD hoặc người nhận")
    if len(files) != 2 or any(p.suffix.lower() != ".docx" or not p.is_file() for p in files):
        raise ValueError("Cần đúng 2 file Word (VIE và ENG)")
    msg = EmailMessage()
    msg["From"], msg["To"] = sender, ", ".join(recipients)
    subject = f"[MB TREASURY] BẢN TIN THỊ TRƯỜNG TUẦN {friday_label}"
    if review:
        subject = "[REVIEW] " + subject + (f" ({len(issues)} vấn đề cần xem)" if issues else "")
    msg["Subject"] = subject
    if review:
        lines = ["Bản tin thị trường tuần để review (chưa gửi danh sách nhận chính thức).", ""]
        if issues:
            lines.append(f"Còn {len(issues)} vấn đề cần xem:")
            lines += [f"- [{i.get('severity', '')}] {i.get('code', '')}: {i.get('message', '')}" for i in list(issues)[:40]]
        else:
            lines.append("Bản tin đã qua toàn bộ kiểm tra tự động.")
        msg.set_content("\n".join(lines))
    else:
        msg.set_content("Kính gửi Quý Anh/Chị,\n\nBản tin thị trường tuần (bản tiếng Việt và tiếng Anh) đính kèm.\n\nTrân trọng.")
    for path in files:
        msg.add_attachment(path.read_bytes(), maintype=DOCX[0], subtype=DOCX[1], filename=path.name)
    with smtplib.SMTP_SSL(os.getenv("SMTP_HOST", "smtp.gmail.com"), int(os.getenv("SMTP_PORT", "465")), timeout=30) as s:
        s.login(sender, password)
        refused = s.send_message(msg)
    if refused:
        raise RuntimeError(f"Một số người nhận bị từ chối: {sorted(refused)}")
