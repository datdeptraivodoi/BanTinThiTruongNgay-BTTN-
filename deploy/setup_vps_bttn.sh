#!/bin/bash
# ==============================================================================
# CÀI ĐẶT BTTN TRÊN VPS (chạy một lần, chạy lại nhiều lần cũng an toàn)
#   cd /root && git clone https://github.com/datdeptraivodoi/BanTinThiTruongNgay-BTTN-.git BTTN
#   bash /root/BTTN/deploy/setup_vps_bttn.sh
# ==============================================================================
set -e
REPO_DIR=/root/BTTN
cd "$REPO_DIR"

echo "== 1/6 Gói hệ thống: font Times New Roman, Python venv, Node.js"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
echo 'ttf-mscorefonts-installer msttcorefonts/accepted-mscorefonts-eula select true' | debconf-set-selections
apt-get install -y -qq ttf-mscorefonts-installer fonts-dejavu libgl1 python3-venv python3-pip >/dev/null
fc-cache -f >/dev/null
if [ "$(fc-match -f '%{family}' 'Times New Roman')" != "Times New Roman" ]; then
    echo "❌ Chưa cài được font Times New Roman (bản tin bắt buộc dùng font này)."; exit 1
fi
if ! command -v node >/dev/null || [ "$(node -p 'process.versions.node.split(".")[0]')" -lt 18 ]; then
    apt-get install -y -qq nodejs npm >/dev/null
fi
echo "   Node $(node -v), $(python3 --version)"

echo "== 2/6 Chrome cho biểu đồ"
CHROME=""
for c in /usr/bin/google-chrome /opt/google/chrome/chrome /usr/bin/chromium /usr/bin/chromium-browser; do
    [ -x "$c" ] && CHROME="$c" && break
done
if [ -z "$CHROME" ]; then
    echo "⚠️  Không thấy Chrome: biểu đồ sẽ dùng kiểu mplfinance cũ."
else
    echo "   Dùng $CHROME"
fi

echo "== 3/6 Thư viện Python (.venv)"
python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt
touch .venv/.req-stamp

echo "== 4/6 Thư viện Node"
npm ci --ignore-scripts --no-audit --no-fund

echo "== 5/6 File .env (key không hiện ra màn hình)"
[ -f .env ] || cp .env.example .env
chmod 600 .env
setval() {  # setval KEY VALUE
    if grep -qE "^$1=" .env; then
        python3 - "$1" "$2" <<'PY'
import re, sys
k, v = sys.argv[1], sys.argv[2]
p = '.env'; s = open(p, encoding='utf-8').read()
s = re.sub(rf'^{re.escape(k)}=.*$', f'{k}="{v}"', s, flags=re.M)
open(p, 'w', encoding='utf-8').write(s)
PY
    else
        echo "$1=\"$2\"" >> .env
    fi
}
getval() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- | tr -d '"'; }

[ -n "$(getval SENDER_EMAIL)" ] || setval SENDER_EMAIL "research.treasury2025@gmail.com"
if [ -z "$(getval SENDER_PASSWORD)" ] && [ -f /root/TyGia/.env ]; then
    GP=$(grep -E '^GMAIL_APP_PASSWORD=' /root/TyGia/.env | tail -1 | cut -d= -f2- | tr -d '"')
    [ -n "$GP" ] && setval SENDER_PASSWORD "$GP" && echo "   Đã dùng Gmail app password của BTS."
fi
if [ -z "$(getval NVIDIA_API_KEY)" ]; then
    read -r -s -p "   Dán NVIDIA_API_KEY rồi Enter (bỏ trống nếu chưa có): " NK; echo
    [ -n "$NK" ] && setval NVIDIA_API_KEY "$NK"
fi
if [ -z "$(getval TRADINGECONOMICS_API_KEY)" ]; then
    read -r -s -p "   Dán TRADINGECONOMICS_API_KEY rồi Enter (bỏ trống nếu không có): " TK; echo
    [ -n "$TK" ] && setval TRADINGECONOMICS_API_KEY "$TK"
fi
[ -n "$(getval REVIEW_RECIPIENTS)" ] || setval REVIEW_RECIPIENTS "datnh1@mbbank.com.vn"
[ -n "$(getval BTTN_MODE)" ] || setval BTTN_MODE "review"
[ -n "$CHROME" ] && setval CHROME_PATH "$CHROME"
echo "   Người duyệt: $(getval REVIEW_RECIPIENTS) · Chế độ: $(getval BTTN_MODE)"

echo "== 6/6 Lịch chạy 11:30 thứ Hai - thứ Sáu"
chmod +x deploy/run_bttn.sh
git config core.fileMode false
( crontab -l 2>/dev/null | grep -v 'run_bttn.sh'; \
  echo "30 11 * * 1-5 /root/BTTN/deploy/run_bttn.sh >> /var/log/bttn_daily.log 2>&1" ) | crontab -
crontab -l | grep run_bttn.sh

echo "✅ Xong. Chạy thử không gửi email:  cd /root/BTTN && .venv/bin/python market_report.py --create-only"
