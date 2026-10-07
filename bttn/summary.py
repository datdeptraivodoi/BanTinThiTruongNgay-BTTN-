import json
import os
from datetime import timedelta
from pathlib import Path

from .models import Issue, Snapshot, business_days_between
from .validation import expected_session_date


def generate_markdown_summary(
    manifest: dict,
    snapshot: Snapshot | None = None,
    data_issues: list[Issue] | None = None,
    content_issues: list[Issue] | None = None,
    timing_issue: str | None = None,
    directory: Path | None = None,
) -> str:
    lines = []
    status = manifest.get("status", "unknown")
    error_message = manifest.get("error_message", "")
    error_type = manifest.get("error_type", "")
    send_requested = manifest.get("send_requested", False)

    # Load attempts data if available
    attempts_data = []
    if directory and (directory / "translation-attempts.json").is_file():
        try:
            attempts_data = json.loads((directory / "translation-attempts.json").read_text(encoding="utf-8"))
        except Exception:
            attempts_data = []

    # 1. Header & Status Badge
    status_badges = {
        "sent": ("🚀 ĐÃ GỬI FILE WORD (SENT)", "File Word đã qua các kiểm tra tự động và gửi thành công tới danh sách email được cấu hình."),
        "word_created": ("📄 ĐÃ TẠO FILE WORD — CHƯA GỬI EMAIL", "File Word đã tạo và qua các kiểm tra tự động. Lượt này không gửi email và không tạo PDF."),
        "validated_draft": ("📝 BẢN NHÁP ĐÃ KIỂM CHỨNG (VALIDATED DRAFT)", "Bản nháp đã tạo thành công; toàn bộ kiểm tra dữ liệu và nội dung đều đạt chuẩn."),
        "draft_with_issues": ("📝 BẢN NHÁP CÓ CẢNH BÁO (DRAFT WITH WARNINGS)", "Bản nháp đã tạo thành công; có một số lưu ý/cảnh báo về số liệu hoặc nội dung."),
        "blocked_timing": ("⚠️ CHẶN PHÁT HÀNH (BLOCKED - LỊCH CHẠY MUỘN)", "Lượt chạy ngoài khung giờ phát hành 12:00–15:00 VN. Đã dừng để tránh gửi bản tin trưa vào buổi tối."),
        "blocked_data": ("⚠️ CHẶN PHÁT HÀNH (BLOCKED - DỮ LIỆU)", "Không thể gửi email vì dữ liệu chưa đạt chuẩn kiểm tra (có số liệu quá hạn hoặc thiếu)."),
        "blocked_content": ("⚠️ CHẶN PHÁT HÀNH (BLOCKED - NỘI DUNG)", "Không thể gửi email vì nội dung chưa đạt chuẩn biên tập."),
        "collected": ("📦 ĐÃ THU THẬP DỮ LIỆU (COLLECTED)", "Thu thập và kiểm tra dữ liệu thành công."),
        "failed": ("❌ THẤT BẠI (FAILED)", "Quá trình thực thi gặp lỗi hệ thống hoặc ngoại lệ chưa xử lý."),
    }

    status_badges.update({
        "translated_news_pending_review": ("📝 ĐÃ DỊCH TIN — CHƯA REVIEW THỦ CÔNG", "Bản dịch đã qua kiểm tra kỹ thuật; review nghĩa là tùy chọn trong luồng gửi Word trực tiếp."),
        "translated_news_approved": ("📝 BẢN DỊCH ĐÃ REVIEW", "Bản dịch đã review; lượt này không dựng báo cáo hoặc gửi email."),
        "blocked_translation": ("⚠️ CHƯA ĐỦ BẢN DỊCH", "Thiếu bài hoặc bản dịch đạt kiểm tra; xem translations.json để biết từng nguồn."),
    })
    title, desc = status_badges.get(status, (f"ℹ️ TRẠNG THÁI: {status.upper()}", ""))

    lines.append(f"# {title}\n")
    if desc:
        lines.append(f"> {desc}\n")

    # 2. Execution Overview Table
    as_of_str = manifest.get("as_of", "—")
    elapsed = manifest.get("elapsed_seconds", "—")
    commit = manifest.get("commit", "unknown")
    if send_requested:
        mode_str = "Gửi file Word đến danh sách email (`--send`)"
    else:
        mode_str = "Chỉ tạo file Word (`--create-only`)"

    lines.append("### 📌 Thông tin lượt chạy\n")
    lines.append("| Thông tin | Giá trị |")
    lines.append("| :--- | :--- |")
    lines.append(f"| **Thời điểm chốt dữ liệu (as-of)** | `{as_of_str}` |")
    lines.append(f"| **Chế độ thực thi** | {mode_str} |")
    if manifest.get("output_format"):
        lines.append("| **Định dạng tài liệu** | Word (.docx); PDF tạm ngừng |")
    if manifest.get("recipients"):
        lines.append(f"| **Email nhận bản tin** | {', '.join(manifest['recipients'])} |")
    lines.append(f"| **Trạng thái manifest** | `{status}` |")
    lines.append(f"| **Thời gian xử lý** | `{elapsed}s` |")
    lines.append(f"| **Commit Git** | `{commit[:8] if len(commit) >= 8 else commit}` |\n")

    # 3. Issues / Blocking Reasons
    all_issues = []
    seen = set()

    def add_issue(sev, code, msg):
        key = (code, msg)
        if key not in seen:
            seen.add(key)
            all_issues.append((sev, code, msg))

    if timing_issue:
        add_issue("CẢNH BÁO", "TIMING_WINDOW", timing_issue)
    if data_issues:
        for iss in data_issues:
            add_issue(iss.severity.upper(), iss.code, iss.message)
    if content_issues:
        for iss in content_issues:
            add_issue(iss.severity.upper(), iss.code, iss.message)

    # Past failed attempts can recover on retry. Only the final manifest and
    # current validation issues determine whether the report is blocked.
    if error_message:
        add_issue("LỖI", error_type or "SYSTEM_ERROR", error_message)
    elif error_type and not all_issues:
        add_issue("LỖI", error_type, "Hệ thống gặp ngoại lệ trong quá trình chạy")

    if all_issues:
        lines.append("### ⚠️ Vấn đề phát hiện & Lý do chặn\n")
        lines.append("| Mức độ | Mã kiểm tra | Chi tiết vấn đề |")
        lines.append("| :---: | :--- | :--- |")
        for sev, code, msg in all_issues:
            sev_icon = "🔴" if sev in ("ERROR", "LỖI", "LỖI NỘI DUNG", "LỖI DỊCH VỤ") else "🟡"
            lines.append(f"| {sev_icon} {sev} | `{code}` | {msg} |")
        lines.append("")

    if attempts_data:
        lines.append("### Nhật ký dịch NVIDIA\n")
        lines.append("Các lần lỗi dưới đây có thể đã phục hồi khi thử lại; trạng thái cuối cùng nằm ở đầu trang.\n")
        lines.append("| Lần | Mô hình | Kết quả | Mã lỗi | Thời gian |")
        lines.append("| :---: | :--- | :--- | :--- | :---: |")
        for att in attempts_data:
            elapsed_sec = att.get("elapsed_seconds", 0)
            lines.append(f"| {att.get('attempt', '—')} | `{att.get('model', '—')}` | {att.get('status', '—')} | `{att.get('error') or '—'}` | {elapsed_sec}s |")
        lines.append("")

    # 5. Source Freshness Table
    if snapshot:
        ref_date = expected_session_date(snapshot.as_of, snapshot)
        lines.append(f"### 🔍 Kiểm tra nguồn dữ liệu & Độ mới (Phiên kỳ vọng: {ref_date.strftime('%d/%m/%Y')})\n")
        lines.append("| Nguồn / Chỉ tiêu | Ngày số liệu | Độ mới (Ngày làm việc) | Trạng thái |")
        lines.append("| :--- | :---: | :---: | :---: |")

        # VIRA
        vira = snapshot.sources.get("vira")
        if vira:
            v_date = vira.published_at.strftime("%d/%m/%Y")
            v_b_days = business_days_between(vira.published_at.date(), ref_date)
            v_status = "✅ Hợp lệ" if vira.published_at.date() == ref_date else f"⚠️ Ngày {v_date}"
            lines.append(f"| **VIRA Market Watch** | {v_date} | {v_b_days} ngày | {v_status} |")
        else:
            lines.append("| **VIRA Market Watch** | — | — | ❌ Thiếu ấn bản |")

        # VNIBOR VND
        vnd_on = snapshot.observations.get("VND_ON")
        if vnd_on:
            vnd_date = vnd_on.trading_date.strftime("%d/%m/%Y")
            vnd_b_days = business_days_between(vnd_on.trading_date, ref_date)
            vnd_status = "✅ Đạt chuẩn (≤ 3 ngày LV)" if vnd_b_days <= 3 else f"❌ Quá cũ ({vnd_b_days} ngày LV > 3)"
            lines.append(f"| **Lãi suất VNIBOR VND (9 kỳ hạn)** | {vnd_date} | {vnd_b_days} ngày | {vnd_status} |")
        else:
            lines.append("| **Lãi suất VNIBOR VND** | — | — | ❌ Thiếu dữ liệu |")

        # SBV
        sbv = snapshot.observations.get("SBV_CENTRAL")
        if sbv:
            sbv_date = sbv.trading_date.strftime("%d/%m/%Y")
            sbv_b_days = business_days_between(sbv.trading_date, ref_date)
            sbv_status = "✅ Phiên chuẩn" if sbv.trading_date == ref_date else f"⚠️ Phiên {sbv_date}"
            lines.append(f"| **Tỷ giá trung tâm NHNN (SBV)** | {sbv_date} | {sbv_b_days} ngày | {sbv_status} |")
        else:
            lines.append("| **Tỷ giá trung tâm NHNN (SBV)** | — | — | ❌ Thiếu dữ liệu |")

        # MBBank
        mb = snapshot.observations.get("MB_BUY")
        if mb:
            mb_date = mb.trading_date.strftime("%d/%m/%Y")
            mb_b_days = business_days_between(mb.trading_date, ref_date)
            mb_status = "✅ Phiên chuẩn" if mb.trading_date == ref_date else f"⚠️ Phiên {mb_date}"
            lines.append(f"| **Tỷ giá chuyển khoản MBBank** | {mb_date} | {mb_b_days} ngày | {mb_status} |")
        else:
            lines.append("| **Tỷ giá chuyển khoản MBBank** | — | — | ❌ Thiếu dữ liệu |")

        # News
        news_items = [s for s in snapshot.sources.values() if s.kind == "news" and s.text_scope in ("article", "excerpt")
                      and timedelta(0) <= snapshot.as_of - s.published_at <= timedelta(hours=36)]
        news_status = f"✅ Đủ điều kiện ({len(news_items)} bài)" if len(news_items) >= 3 else f"⚠️ Thiếu ({len(news_items)}/3 bài)"
        lines.append(f"| **Tin tức vĩ mô (News)** | {len(news_items)} bài | — | {news_status} |")

        # Commodities / Yahoo Finance
        commodities = [k for k in ["BRENT", "GOLD", "EURUSD", "USDJPY"] if k in snapshot.observations]
        if commodities:
            lines.append(f"| **Thị trường quốc tế & Hàng hóa** | {len(commodities)} chỉ tiêu | — | ✅ Đã nạp |")

        lines.append("")

    # 6. Artifacts
    artifacts = manifest.get("artifacts", {})
    if artifacts:
        is_draft_doc = status not in ("sent", "word_created")
        doc_badge = "📝 Bản nháp có cảnh báo" if is_draft_doc else "✅ Word đã gửi email" if status == "sent" else "📄 Word đã tạo; chưa gửi email"
        lines.append("### 📁 Tài liệu xuất xưởng (Artifacts)\n")
        lines.append(f"> Phân loại tài liệu: **{doc_badge}**\n")
        lines.append("| Tên tệp | Định dạng | Trạng thái & Ghi chú | SHA-256 Checksum |")
        lines.append("| :--- | :---: | :--- | :--- |")
        for fname, sha in artifacts.items():
            ext = Path(fname).suffix.upper().replace(".", "")
            note = "Bản nháp bảo lưu bảng số liệu & biểu đồ đã đối soát" if is_draft_doc else "Bản tin đã qua các kiểm tra được cấu hình và phát hành"
            lines.append(f"| `{fname}` | **{ext}** | {note} | `{sha[:16]}...` |")
        lines.append("\n> 💡 *Bạn có thể tải các tệp tài liệu này tại phần **Artifacts** của lượt chạy trên GitHub Actions.*")

    lines.append("")
    return "\n".join(lines)


def write_step_summary(
    manifest: dict,
    snapshot: Snapshot | None = None,
    data_issues: list[Issue] | None = None,
    content_issues: list[Issue] | None = None,
    timing_issue: str | None = None,
    directory: Path | None = None,
) -> str:
    summary_md = generate_markdown_summary(
        manifest=manifest,
        snapshot=snapshot,
        data_issues=data_issues,
        content_issues=content_issues,
        timing_issue=timing_issue,
        directory=directory,
    )
    if directory:
        try:
            (directory / "summary.md").write_text(summary_md, encoding="utf-8")
        except OSError:
            pass

    summary_file = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_file:
        try:
            with open(summary_file, "a", encoding="utf-8") as f:
                f.write(summary_md + "\n\n")
        except OSError:
            pass

    return summary_md
