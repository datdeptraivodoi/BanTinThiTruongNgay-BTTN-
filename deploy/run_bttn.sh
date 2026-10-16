#!/bin/bash
# ==============================================================================
# BẢN TIN THỊ TRƯỜNG NGÀY (BTTN) - RUNNER HẰNG NGÀY TRÊN VPS
# Cron 11:30 thứ Hai - thứ Năm (thứ Sáu là bản tin tuần, quy trình riêng): tạo bản tin và gửi bản REVIEW cho người duyệt
# (REVIEW_RECIPIENTS trong .env). Còn lỗi kiểm tra thì vẫn gửi bản nháp kèm danh sách lỗi.
# Khi muốn gửi danh sách nhận chính thức: đổi BTTN_MODE=send trong .env.
# ==============================================================================

main() {
    set -e
    local REPO_DIR=/root/BTTN
    cd "$REPO_DIR"
    echo "======================================================================"
    echo "🕒 [$(date '+%Y-%m-%d %H:%M:%S')] Bắt đầu Bản tin thị trường ngày..."
    echo "======================================================================"

    if [ ! -f "$REPO_DIR/.env" ]; then
        echo "❌ Thiếu $REPO_DIR/.env. Chạy deploy/setup_vps_bttn.sh trước."
        exit 1
    fi
    local MODE
    MODE=$(grep -E '^BTTN_MODE=' .env | tail -1 | cut -d= -f2 | tr -d '"' || true)
    MODE=${MODE:-review}

    echo "⬇️  Lấy code mới nhất từ GitHub..."
    if ! git pull --rebase --autostash origin main; then
        echo "⚠️  Không pull được, chạy bằng code hiện có."
        git rebase --abort 2>/dev/null || true
    fi

    # Cập nhật thư viện khi requirements.txt / package-lock.json thay đổi
    # shellcheck disable=SC1091
    source "$REPO_DIR/.venv/bin/activate"
    if [ requirements.txt -nt .venv/.req-stamp ]; then
        echo "📦 Cập nhật thư viện Python..."
        pip install -q -r requirements.txt && touch .venv/.req-stamp
    fi
    if [ ! -d node_modules ] || [ package-lock.json -nt node_modules/.package-lock.json ]; then
        echo "📦 Cập nhật thư viện Node..."
        npm ci --ignore-scripts --no-audit --no-fund
    fi

    echo "🚀 Tạo bản tin (chế độ: $MODE)..."
    if [ "$MODE" = "send" ]; then
        python market_report.py --type midday --send
    else
        python market_report.py --type midday --review
    fi
    echo "✅ [$(date '+%Y-%m-%d %H:%M:%S')] Hoàn thành."
}

main "$@"
