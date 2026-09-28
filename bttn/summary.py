import os
from pathlib import Path

from .models import Issue, Snapshot, business_days_between


def generate_markdown_summary(
    manifest: dict,
    snapshot: Snapshot | None = None,
    data_issues: list[Issue] | None = None,
    content_issues: list[Issue] | None = None,
    timing_issue: str | None = None,
) -> str:
    lines = []
    status = manifest.get("status", "unknown")
    send_requested = manifest.get("send_requested", False)

    # 1. Header & Status Badge
    status_badges = {
        "sent": ("🚀 ĐÃ PHÁT HÀNH (SENT)", "Bản tin đã được kiểm chứng và gửi thành công qua email tới các bên liên quan."),
        "validated_draft": ("📝 BẢN NHÁP ĐÃ KIỂM CHỨNG (VALIDATED DRAFT)", "Bản nháp đã tạo thành công; toàn bộ kiểm tra dữ liệu và nội dung đều đạt chuẩn."),
        "draft_with_issues": ("📝 BẢN NHÁP CÓ CẢNH BÁO (DRAFT WITH WARNINGS)", "Bản nháp đã tạo thành công; có một số lưu ý/cảnh báo về số liệu hoặc nội dung."),
        "blocked_timing": ("⚠️ CHẶN PHÁT HÀNH (BLOCKED - LỊCH CHẠY MUỘN)", "Lượt chạy ngoài khung giờ phát hành 12:00–15:00 VN. Đã dừng để tránh gửi bản tin trưa vào buổi tối."),
        "blocked_data": ("⚠️ CHẶN PHÁT HÀNH (BLOCKED - DỮ LIỆU)", "Không thể gửi email vì dữ liệu chưa đạt chuẩn kiểm tra (có số liệu quá hạn hoặc thiếu)."),
        "blocked_content": ("⚠️ CHẶN PHÁT HÀNH (BLOCKED - NỘI DUNG)", "Không thể gửi email vì nội dung chưa đạt chuẩn biên tập."),
        "collected": ("📦 ĐÃ THU THẬP DỮ LIỆU (COLLECTED)", "Thu thập và kiểm tra dữ liệu thành công."),
        "failed": ("❌ THẤT BẠI (FAILED)", "Quá trình thực thi gặp lỗi hệ thống hoặc ngoại lệ chưa xử lý."),
    }

    title, desc = status_badges.get(status, (f"ℹ️ TRẠNG THÁI: {status.upper()}", ""))
    lines.append(f"# {title}\n")
    if desc:
        lines.append(f"> {desc}\n")

    # 2. Execution Overview Table
    as_of_str = manifest.get("as_of", "—")
    elapsed = manifest.get("elapsed_seconds", "—")
    commit = manifest.get("commit", "unknown")
    mode_str = "Phát hành chính thức (`--send`)" if send_requested else "Xem trước / Bản nháp (`--dry-run`)"

    lines.append("### 📌 Thông tin lượt chạy\n")
    lines.append("| Thông tin | Giá trị |")
    lines.append("| :--- | :--- |")
    lines.append(f"| **Thời điểm chốt dữ liệu (as-of)** | `{as_of_str}` |")
    lines.append(f"| **Chế độ thực thi** | {mode_str} |")
    lines.append(f"| **Trạng thái manifest** | `{status}` |")
    lines.append(f"| **Thời gian xử lý** | `{elapsed}s` |")
    lines.append(f"| **Commit Git** | `{commit[:8] if len(commit) >= 8 else commit}` |\n")

    # 3. Issues / Blocking Reasons
    all_issues = []
    if timing_issue:
        all_issues.append(("CẢNH BÁO", "TIMING_WINDOW", timing_issue))
    if data_issues:
        for iss in data_issues:
            all_issues.append((iss.severity.upper(), iss.code, iss.message))
    if content_issues:
        for iss in content_issues:
            all_issues.append((iss.severity.upper(), iss.code, iss.message))
    if manifest.get("error_type"):
        all_issues.append(("LỖI", manifest["error_type"], "Hệ thống gặp ngoại lệ trong quá trình chạy"))

    if all_issues:
        lines.append("### ⚠️ Vấn đề phát hiện & Lý do chặn\n")
        lines.append("| Mức độ | Mã kiểm tra | Chi tiết vấn đề |")
        lines.append("| :---: | :--- | :--- |")
        for sev, code, msg in all_issues:
            sev_icon = "🔴" if sev in ("ERROR", "LỖI") else "🟡"
            lines.append(f"| {sev_icon} {sev} | `{code}` | {msg} |")
        lines.append("")

    # 4. Source Freshness Table
    if snapshot:
        lines.append("### 🔍 Kiểm tra nguồn dữ liệu & Độ mới (Source Freshness)\n")
        lines.append("| Nguồn / Chỉ tiêu | Ngày số liệu | Độ mới (Ngày làm việc) | Trạng thái |")
        lines.append("| :--- | :---: | :---: | :---: |")

        # VIRA
        vira = snapshot.sources.get("vira")
        if vira:
            v_date = vira.published_at.strftime("%d/%m/%Y")
            v_b_days = business_days_between(vira.published_at.date(), snapshot.as_of.date())
            v_status = "✅ Hợp lệ" if vira.published_at.date() == snapshot.as_of.date() else f"⚠️ Ngày {v_date}"
            lines.append(f"| **VIRA Market Watch** | {v_date} | {v_b_days} ngày | {v_status} |")
        else:
            lines.append("| **VIRA Market Watch** | — | — | ❌ Thiếu ấn bản |")

        # VNIBOR VND
        vnd_on = snapshot.observations.get("VND_ON")
        if vnd_on:
            vnd_date = vnd_on.trading_date.strftime("%d/%m/%Y")
            vnd_b_days = business_days_between(vnd_on.trading_date, snapshot.as_of.date())
            vnd_status = "✅ Đạt chuẩn (≤ 3 ngày LV)" if vnd_b_days <= 3 else f"❌ Quá cũ ({vnd_b_days} ngày LV > 3)"
            lines.append(f"| **Lãi suất VNIBOR VND (9 kỳ hạn)** | {vnd_date} | {vnd_b_days} ngày | {vnd_status} |")
        else:
            lines.append("| **Lãi suất VNIBOR VND** | — | — | ❌ Thiếu dữ liệu |")

        # SBV
        sbv = snapshot.observations.get("SBV_CENTRAL")
        if sbv:
            sbv_date = sbv.trading_date.strftime("%d/%m/%Y")
            sbv_b_days = business_days_between(sbv.trading_date, snapshot.as_of.date())
            sbv_status = "✅ Hôm nay" if sbv.trading_date == snapshot.as_of.date() else f"⚠️ Phiên {sbv_date}"
            lines.append(f"| **Tỷ giá trung tâm NHNN (SBV)** | {sbv_date} | {sbv_b_days} ngày | {sbv_status} |")
        else:
            lines.append("| **Tỷ giá trung tâm NHNN (SBV)** | — | — | ❌ Thiếu dữ liệu |")

        # MBBank
        mb = snapshot.observations.get("MB_BUY")
        if mb:
            mb_date = mb.trading_date.strftime("%d/%m/%Y")
            mb_b_days = business_days_between(mb.trading_date, snapshot.as_of.date())
            mb_status = "✅ Hôm nay" if mb.trading_date == snapshot.as_of.date() else f"⚠️ Phiên {mb_date}"
            lines.append(f"| **Tỷ giá chuyển khoản MBBank** | {mb_date} | {mb_b_days} ngày | {mb_status} |")
        else:
            lines.append("| **Tỷ giá chuyển khoản MBBank** | — | — | ❌ Thiếu dữ liệu |")

        # News
        news_items = [s for s in snapshot.sources.values() if s.kind == "news"]
        news_status = f"✅ Đủ điều kiện ({len(news_items)} bài)" if len(news_items) >= 3 else f"⚠️ Thiếu ({len(news_items)}/3 bài)"
        lines.append(f"| **Tin tức vĩ mô (News)** | {len(news_items)} bài | — | {news_status} |")

        # Commodities / Yahoo Finance
        commodities = [k for k in ["BRENT", "GOLD", "EURUSD", "USDJPY"] if k in snapshot.observations]
        if commodities:
            lines.append(f"| **Thị trường quốc tế & Hàng hóa** | {len(commodities)} chỉ tiêu | — | ✅ Đã nạp |")

        lines.append("")

    # 5. Artifacts
    artifacts = manifest.get("artifacts", {})
    if artifacts:
        lines.append("### 📁 Tài liệu xuất xưởng (Artifacts)\n")
        lines.append("| Tên tệp | Định dạng | SHA-256 Checksum |")
        lines.append("| :--- | :---: | :--- |")
        for fname, sha in artifacts.items():
            ext = Path(fname).suffix.upper().replace(".", "")
            lines.append(f"| `{fname}` | **{ext}** | `{sha[:16]}...` |")
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
