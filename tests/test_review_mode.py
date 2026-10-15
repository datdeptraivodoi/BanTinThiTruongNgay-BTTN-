"""Chế độ --review: luôn gửi cho người duyệt, kể cả bản nháp còn lỗi."""
from unittest.mock import Mock

from bttn import delivery, pipeline, rendering
from tests.test_word_workflow import manifest, run_args, write_snapshot


def test_review_sends_to_reviewer_even_with_issues(tmp_path, monkeypatch):
    _, path = write_snapshot(tmp_path, live=True)
    send = Mock()
    monkeypatch.delenv("REVIEW_RECIPIENTS", raising=False)
    monkeypatch.setenv("RECIPIENTS", "official@example.com")
    monkeypatch.setattr(delivery, "send_report", send)
    monkeypatch.setattr(rendering, "convert_and_validate", Mock())
    assert pipeline.main(["--review", *run_args(tmp_path, path)]) == 0
    assert send.call_args.args[3] == ["datnh1@mbbank.com.vn"]          # không gửi danh sách chính thức
    assert send.call_args.kwargs["review"] is True
    m = manifest(tmp_path)
    assert m["status"] in ("review_sent", "review_sent_with_issues")
    assert m["issue_count"] == len(send.call_args.kwargs["issues"])


def test_review_recipients_from_env(monkeypatch):
    monkeypatch.setenv("REVIEW_RECIPIENTS", "a@example.com, b@example.com")
    assert delivery.review_recipients() == ["a@example.com", "b@example.com"]


def test_review_failure_sends_alert(tmp_path, monkeypatch):
    alert = Mock(return_value=True)
    monkeypatch.setattr(delivery, "send_failure_alert", alert)
    missing = tmp_path / "khong-co.json"
    assert pipeline.main(["--review", "--snapshot", str(missing), "--output-dir", str(tmp_path / "output"),
                          "--state-dir", str(tmp_path / "state")]) == 1
    assert alert.called
    assert manifest(tmp_path)["failure_alert_sent"] is True


def test_review_email_subject_and_issue_list(tmp_path, monkeypatch):
    snapshot, _ = write_snapshot(tmp_path, live=True)
    monkeypatch.setenv("SENDER_EMAIL", "sender@example.com")
    monkeypatch.setenv("SENDER_PASSWORD", "test-only-password")
    server = Mock()
    server.send_message.return_value = {}
    ctx = Mock()
    ctx.__enter__ = Mock(return_value=server)
    ctx.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(delivery.smtplib, "SMTP_SSL", Mock(return_value=ctx))
    docx = tmp_path / "BTTN-20260415.docx"
    docx.write_bytes(b"x")
    issues = [{"severity": "error", "code": "STALE_SOURCE", "message": "VIRA chưa có ấn bản hôm nay"}]
    ledger = delivery.send_report(snapshot, [docx], tmp_path / "state", ["r@example.com"], review=True, issues=issues)
    msg = server.send_message.call_args.args[0]
    assert msg["Subject"].startswith("[REVIEW] [MB TREASURY] BẢN TIN THỊ TRƯỜNG NGÀY")
    assert "(BẢN NHÁP: 1 vấn đề)" in msg["Subject"]
    body = msg.get_body(preferencelist=("plain",)).get_content()
    assert "STALE_SOURCE: VIRA chưa có ấn bản hôm nay" in body
    assert "-review-" in ledger.name
    # Ledger review không chặn lần gửi chính thức cùng ngày
    assert not (tmp_path / "state" / f"{snapshot.as_of:%Y%m%d}-midday.json").exists()
