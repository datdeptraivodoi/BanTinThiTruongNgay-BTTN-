#!/bin/bash
# ==============================================================================
# BẢN TIN THỊ TRƯỜNG TUẦN - RUNNER THỨ SÁU TRÊN VPS
# Cron 11:30 thứ Sáu: lấy dữ liệu cả tuần, viết + dịch, dựng 2 file Word (VIE, ENG)
# và gửi bản REVIEW cho người duyệt (REVIEW_RECIPIENTS trong .env).
# Trước 11:30 team cập nhật trên GitHub: config/weekly_inputs.json và ảnh Reuters
# trong inputs/weekly/<YYYY-MM-DD của thứ Sáu>/ (eurusd, usdjpy, arabica, robusta, brent, gasoline .png).
# ==============================================================================

main() {
    set -e
    local REPO_DIR=/root/BTTN
    cd "$REPO_DIR"
    echo "======================================================================"
    echo "🕒 [$(date '+%Y-%m-%d %H:%M:%S')] Bắt đầu Bản tin thị trường tuần..."
    echo "======================================================================"
    if [ ! -f "$REPO_DIR/.env" ]; then
        echo "❌ Thiếu $REPO_DIR/.env. Chạy deploy/setup_vps_bttn.sh trước."
        exit 1
    fi
    echo "⬇️  Lấy code, số liệu team nhập và ảnh Reuters mới nhất từ GitHub..."
    if ! git pull --rebase --autostash origin main; then
        echo "⚠️  Không pull được, chạy bằng bản hiện có."
        git rebase --abort 2>/dev/null || true
    fi
    # shellcheck disable=SC1091
    source "$REPO_DIR/.venv/bin/activate"
    if [ requirements.txt -nt .venv/.req-stamp ]; then
        pip install -q -r requirements.txt && touch .venv/.req-stamp
    fi
    python weekly_report.py --collect --review
    echo "✅ [$(date '+%Y-%m-%d %H:%M:%S')] Hoàn thành."
}

main "$@"
