import json
import os
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
    if directory and (directory / "model-attempts.json").is_file():
        try:
            attempts_data = json.loads((directory / "model-attempts.json").read_text(encoding="utf-8"))
        except Exception:
            attempts_data = []

    validation_attempts = [
        att for att in attempts_data
        if att.get("status") == "validation_error" or att.get("issues")
    ]
    service_attempts = [
        att for att in attempts_data
        if att.get("status") == "service_error"
        or att.get("http_status") in (500, 502, 503, 504)
        or "503" in str(att.get("error_message", ""))
    ]
    skipped_attempts = [att for att in attempts_data if att.get("status") == "skipped"]

    has_validation_rejection = bool(validation_attempts) or bool(
        content_issues and any(i.code != "AI_INCOMPLETE" for i in content_issues)
    )
    has_503_in_attempts = any(
        a.get("http_status") == 503
        or "503" in str(a.get("error_message", ""))
        or "503" in str(a.get("error", ""))
        for a in attempts_data
    )
    is_gemini_503 = "503" in error_message or "503" in error_type or has_503_in_attempts
    has_service_error = bool(service_attempts) or is_gemini_503

    # 1. Header & Status Badge
    status_badges = {
        "sent": ("🚀 ĐÃ PHÁT HÀNH (SENT)", "Bản tin đã được kiểm chứng và gửi thành công qua email tới các bên liên quan."),
        "sent_test": ("🧪 ĐÃ GỬI THỬ NGHIỆM (TEST SENT)", "Bản tin đã được kiểm chứng và gửi thử nghiệm thành công tới email riêng chỉ định."),
        "validated_draft": ("📝 BẢN NHÁP ĐÃ KIỂM CHỨNG (VALIDATED DRAFT)", "Bản nháp đã tạo thành công; toàn bộ kiểm tra dữ liệu và nội dung đều đạt chuẩn."),
        "draft_with_issues": ("📝 BẢN NHÁP CÓ CẢNH BÁO (DRAFT WITH WARNINGS)", "Bản nháp đã tạo thành công; có một số lưu ý/cảnh báo về số liệu hoặc nội dung."),
        "blocked_timing": ("⚠️ CHẶN PHÁT HÀNH (BLOCKED - LỊCH CHẠY MUỘN)", "Lượt chạy ngoài khung giờ phát hành 12:00–15:00 VN. Đã dừng để tránh gửi bản tin trưa vào buổi tối."),
        "blocked_data": ("⚠️ CHẶN PHÁT HÀNH (BLOCKED - DỮ LIỆU)", "Không thể gửi email vì dữ liệu chưa đạt chuẩn kiểm tra (có số liệu quá hạn hoặc thiếu)."),
        "blocked_content": ("⚠️ CHẶN PHÁT HÀNH (BLOCKED - NỘI DUNG)", "Không thể gửi email vì nội dung chưa đạt chuẩn biên tập."),
        "collected": ("📦 ĐÃ THU THẬP DỮ LIỆU (COLLECTED)", "Thu thập và kiểm tra dữ liệu thành công."),
        "failed": ("❌ THẤT BẠI (FAILED)", "Quá trình thực thi gặp lỗi hệ thống hoặc ngoại lệ chưa xử lý."),
    }

    if status in ("failed", "blocked_content", "draft_with_issues") and has_validation_rejection and has_service_error:
        if send_requested or status == "blocked_content":
            title = "⚠️ CHẶN PHÁT HÀNH - NỘI DUNG BỊ TỪ CHỐI & GẶP LỖI DỊCH VỤ TRONG QUÁ TRÌNH SỬA"
            desc = (
                "Mô hình AI đã phản hồi ở lần gọi đầu nhưng bị Validator từ chối do vi phạm quy tắc biên tập. "
                "Trong các lần gọi tiếp theo để sửa lỗi, dịch vụ AI gặp lỗi kết nối/quá tải (Gemini HTTP 503). "
                "Bản nháp tài liệu (Word/PDF) bảo lưu số liệu đã được tạo để kiểm tra; việc gửi email bị chặn."
            )
        else:
            title = "📝 BẢN NHÁP CÓ CẢNH BÁO - NỘI DUNG BỊ TỪ CHỐI & GẶP LỖI DỊCH VỤ TRONG QUÁ TRÌNH SỬA"
            desc = (
                "Mô hình AI phản hồi bị Validator từ chối do vi phạm quy tắc biên tập. "
                "Các lần gọi tiếp theo để sửa gặp lỗi dịch vụ AI (HTTP 503). "
                "Bản nháp Word/PDF đã được tạo thành công với bảng số liệu đầy đủ."
            )
    elif status == "failed" and is_gemini_503:
        title = "❌ THẤT BẠI (FAILED) - Gemini HTTP 503; chưa có fallback khả dụng"
        desc = "Dịch vụ Gemini gặp lỗi HTTP 503 Service Unavailable (tạm thời quá tải). OpenRouter chưa hoạt động dự phòng (thiếu OPENROUTER_API_KEY hoặc chưa cấu hình trong GitHub Secrets)."
    else:
        title, desc = status_badges.get(status, (f"ℹ️ TRẠNG THÁI: {status.upper()}", ""))

    lines.append(f"# {title}\n")
    if desc:
        lines.append(f"> {desc}\n")

    # 2. Execution Overview Table
    as_of_str = manifest.get("as_of", "—")
    elapsed = manifest.get("elapsed_seconds", "—")
    commit = manifest.get("commit", "unknown")
    test_recipient = manifest.get("test_recipient")
    if test_recipient:
        mode_str = f"Gửi thử nghiệm (`--test-recipient {test_recipient}`)"
    elif send_requested:
        mode_str = "Phát hành chính thức (`--send`)"
    else:
        mode_str = "Xem trước / Bản nháp (`--dry-run`)"

    lines.append("### 📌 Thông tin lượt chạy\n")
    lines.append("| Thông tin | Giá trị |")
    lines.append("| :--- | :--- |")
    lines.append(f"| **Thời điểm chốt dữ liệu (as-of)** | `{as_of_str}` |")
    lines.append(f"| **Chế độ thực thi** | {mode_str} |")
    if test_recipient:
        lines.append(f"| **Email nhận thử nghiệm** | `{test_recipient}` |")
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

    # Include specific validation errors from earlier AI attempts if not already present
    for val_att in validation_attempts:
        r_num = val_att.get("content_round", val_att.get("attempt", 1))
        for iss in val_att.get("issues", []):
            code = iss.get("code", "VALIDATION")
            msg = iss.get("message", "")
            add_issue("LỖI NỘI DUNG", code, f"[Lần {r_num}] {msg}")

    # Include service errors from attempts
    for s_att in service_attempts:
        p_name = s_att.get("provider", "AI").title()
        r_num = s_att.get("content_round", s_att.get("attempt", 1))
        h_code = s_att.get("http_status")
        err_code = f"{p_name.upper()}_{h_code}" if h_code else f"{p_name.upper()}_NET_ERROR"
        err_msg = s_att.get("error_message") or s_att.get("error") or "Lỗi kết nối dịch vụ"
        add_issue("LỖI DỊCH VỤ", err_code, f"[Lần {r_num} {p_name}] {err_msg}")

    # Include skipped fallback notices
    for sk_att in skipped_attempts:
        p_name = sk_att.get("provider", "AI").title()
        err_msg = sk_att.get("error_message") or f"Chưa cấu hình API Key cho {p_name}"
        add_issue("CẢNH BÁO", "FALLBACK_KEY_MISSING", err_msg)

    if error_message and not any(iss[1] == "GEMINI_503" for iss in all_issues if is_gemini_503):
        code = "GEMINI_503" if is_gemini_503 else (error_type or "SYSTEM_ERROR")
        add_issue("LỖI", code, error_message)
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

    # 4. Model attempts table (if available)
    if directory and attempts_data:
        lines.append("### 🤖 Nhật ký gọi mô hình AI (Model Attempts)\n")
        lines.append("| Lần thử | Vòng sửa | Nhà cung cấp | Mô hình | Kết quả | Chi tiết kết quả & Kiểm định | Thời gian |")
        lines.append("| :---: | :---: | :--- | :--- | :---: | :--- | :---: |")
        for att in attempts_data:
            p = att.get("provider", "—").title()
            m = att.get("model", "—")
            num = att.get("attempt", 1)
            round_idx = att.get("content_round", "—")
            st = att.get("status")
            elapsed_sec = att.get("elapsed_seconds")
            elapsed_str = f"{elapsed_sec:.2f}s" if isinstance(elapsed_sec, (int, float)) else "—"

            if st == "valid":
                st_icon = "✅ Đạt chuẩn"
                detail = "Đạt 100% quy tắc kiểm định biên tập"
            elif st == "validation_error":
                st_icon = "⚠️ Bị từ chối KĐ"
                issues = att.get("issues", [])
                codes_list = list(dict.fromkeys(i.get("code") for i in issues if i.get("code")))
                code_summary = f" ({', '.join(codes_list[:4])}{'...' if len(codes_list) > 4 else ''})" if codes_list else ""
                detail = f"Validator từ chối ({len(issues)} lỗi){code_summary}"
            elif st == "service_error":
                code = att.get("http_status")
                st_icon = f"🔴 Lỗi dịch vụ ({code or 'Net'})"
                detail = att.get("error_message") or att.get("error") or "Lỗi kết nối / quá tải"
            elif st == "json_error":
                st_icon = "🔴 Lỗi JSON"
                detail = att.get("error_message") or "Phản hồi không phải JSON hợp lệ"
            elif st == "skipped":
                st_icon = "⚪ Bỏ qua"
                detail = att.get("error_message") or "Chưa cấu hình API Key"
            else:
                st_icon = "🔴 Thất bại"
                detail = att.get("error_message") or att.get("error") or "Lỗi không xác định"

            lines.append(f"| {num} | {round_idx} | **{p}** | `{m}` | {st_icon} | {detail} | {elapsed_str} |")
        lines.append("")

    if is_gemini_503 or any(a.get("http_status") == 503 or "503" in str(a.get("error_message", "")) for a in attempts_data):
        lines.append("> [!TIP]\n"
                     "> **Khắc phục sự cố khi Gemini quá tải (HTTP 503):** Hệ thống đã có cơ chế tự động chuyển sang mô hình dự phòng (OpenRouter). "
                     "Để kích hoạt, vui lòng kiểm tra **GitHub Repository -> Settings -> Secrets and variables -> Actions** và đảm bảo secret "
                     "**OPENROUTER_API_KEY** (hoặc **OPEN_ROUTER_API_KEY**) đã được thêm vào kho lưu trữ.\n")

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
        news_items = [s for s in snapshot.sources.values() if s.kind == "news"]
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
        is_draft_doc = manifest.get("status") not in ("sent", "validated_draft")
        doc_badge = "📝 Bản nháp (Draft)" if is_draft_doc else "✅ Chính thức (Official)"
        lines.append("### 📁 Tài liệu xuất xưởng (Artifacts)\n")
        lines.append(f"> Phân loại tài liệu: **{doc_badge}**\n")
        lines.append("| Tên tệp | Định dạng | Trạng thái & Ghi chú | SHA-256 Checksum |")
        lines.append("| :--- | :---: | :--- | :--- |")
        for fname, sha in artifacts.items():
            ext = Path(fname).suffix.upper().replace(".", "")
            note = "Bản nháp bảo lưu bảng số liệu & biểu đồ đã đối soát" if is_draft_doc else "Bản tin chính thức đã kiểm duyệt toàn diện"
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
