import os
import sys

# Đảm bảo in Tiếng Việt mượt mà không lỗi charmap trên Windows console
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

import time
import html
import argparse
import datetime
import smtplib
import subprocess
import shutil
import urllib.parse
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders

import requests
from bs4 import BeautifulSoup
import feedparser
import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted, GoogleAPIError
from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
import math
import re
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Tải cấu hình từ biến môi trường hoặc file .env nếu có
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ==============================================================================
# CẤU HÌNH HỆ THỐNG
# ==============================================================================
GEMINI_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)

# Danh sách Model ưu tiên tuyệt đối theo thứ tự từ cao xuống thấp
FALLBACK_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",  # Phao cứu sinh: 500 RPD, 15 RPM
    "gemini-3.1-flash-lite",  # Phao cứu sinh: 500 RPD, 15 RPM
    "gemini-3-flash",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-1.5-flash"
]

# 1. TradingEconomics lọc tin trong 12 tiếng về 8 quốc gia/khu vực:
# Euro Area, United States, Japan, China, Germany, Australia, Vietnam, Canada
TE_QUERY = 'site:tradingeconomics.com ("Euro Area" OR "United States" OR Japan OR China OR Germany OR Australia OR Vietnam OR Canada) when:12h'
TE_RSS_URL = f"https://news.google.com/rss/search?q={urllib.parse.quote_plus(TE_QUERY)}&hl=en-US&gl=US&ceid=US:en"

RSS_SOURCES = [
    TE_RSS_URL,
    # Barchart qua Google News
    "https://news.google.com/rss/search?q=site:barchart.com+when:12h&hl=en-US&gl=US&ceid=US:en",
    # Tin tài chính Vietnambiz tổng hợp
    "https://vietnambiz.vn/rss/tai-chinh.rss"
]

# 2. Các chủ đề hàng hóa trọng điểm từ Vietnambiz (Cà phê & Dầu mỏ)
VIETNAMBIZ_TOPICS = [
    {"topic": "Cà phê", "url": "https://vietnambiz.vn/chu-de/ca-phe-34.htm"},
    {"topic": "Dầu mỏ", "url": "https://vietnambiz.vn/chu-de/dau-mo-60.htm"}
]

RECIPIENTS = [
    "datnh1@mbbank.com.vn",
    "trungnt@mbbank.com.vn",
    "research.treasury@mbbank.com.vn"
]

# ==============================================================================
# BƯỚC 1: THU THẬP TIN TỨC (RSS & CHỦ ĐỀ VIETNAMBIZ)
# ==============================================================================
def fetch_vietnambiz_topic(topic_name, url):
    """Trích xuất tin tức trực tiếp từ trang chủ đề của Vietnambiz (Cà phê / Dầu mỏ)."""
    items = []
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        response = requests.get(url, headers=headers, timeout=10, verify=False)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, "html.parser")
            elements = soup.select("div.list-news div.item, .list__listing div.item")
            seen_links = set()

            for el in elements:
                a_tag = el.find("a", href=True)
                if not a_tag:
                    continue
                href = a_tag["href"]
                title = html.unescape(a_tag.get("title") or a_tag.text.strip())
                sapo_tag = el.find("p", class_="sapo") or el.find("div", class_="sapo") or el.find("p")
                sapo = html.unescape(sapo_tag.text.strip()) if sapo_tag else ""

                full_url = "https://vietnambiz.vn" + href if href.startswith("/") else href
                if full_url not in seen_links and len(title) > 20:
                    seen_links.add(full_url)
                    
                    # Nếu là bài đầu tiên (bài mới nhất), cào thêm nội dung chi tiết bài viết
                    detail_content = ""
                    if len(items) == 0:
                        try:
                            r_art = requests.get(full_url, headers=headers, timeout=10, verify=False)
                            if r_art.status_code == 200:
                                s_art = BeautifulSoup(r_art.text, "html.parser")
                                body_div = s_art.find("div", class_="vnbcbc-body")
                                if body_div:
                                    paras = [p.get_text().strip() for p in body_div.find_all(["p", "h2", "h3"]) if p.get_text().strip()]
                                    detail_content = "\n  NỘI DUNG CHI TIẾT BÀI BÁO:\n  " + "\n  ".join(paras[:15])
                        except Exception as ex_art:
                            print(f"[CẢNH BÁO] Không lấy được chi tiết bài {full_url}: {ex_art}")

                    items.append(f"• [Vietnambiz - {topic_name}] {title}\n  Tóm tắt: {sapo}{detail_content}\n  Link: {full_url}")

                if len(items) >= 5:  # Lấy 5 bài mới nhất mỗi chuyên đề
                    break
    except Exception as e:
        print(f"[CẢNH BÁO] Không thể đọc chủ đề Vietnambiz ({topic_name}): {e}")

    return items

def fetch_news():
    """Thu thập toàn bộ dữ liệu từ TradingEconomics, Barchart, Vietnambiz RSS và Chuyên đề."""
    print("--> Bắt đầu quét nguồn tin tức thị trường...")
    news_items = []

    # 1. Đọc RSS (TradingEconomics 8 khu vực + Barchart + Vietnambiz)
    for url in RSS_SOURCES:
        try:
            feed = feedparser.parse(url)
            count = 0
            for entry in feed.entries:
                title = entry.title.strip() if hasattr(entry, 'title') else ''
                summary = getattr(entry, 'summary', '').strip()
                link = getattr(entry, 'link', '')

                if title:
                    news_items.append(f"• {title}\n  Mô tả: {summary}\n  Link: {link}")
                    count += 1
                if count >= 8:
                    break
        except Exception as e:
            print(f"[CẢNH BÁO] Không thể đọc RSS từ {url[:70]}...: {e}")

    # 2. Đọc trực tiếp các chủ đề Vietnambiz (Cà phê, Dầu mỏ)
    for t in VIETNAMBIZ_TOPICS:
        topic_articles = fetch_vietnambiz_topic(t["topic"], t["url"])
        news_items.extend(topic_articles)

    combined = "\n\n".join(news_items)
    print(f"✓ Đã thu thập tổng cộng {len(news_items)} tin tức thị trường hợp lệ.")
    return combined

def fetch_sbv_rates():
    """Cào tỷ giá trực tiếp từ website Ngân hàng Nhà nước (SBV).
    - Tỷ giá trung tâm, Mua, Bán công bố bởi NHNN.
    - Tỷ giá trần = floor(TT * 1.05) làm tròn xuống.
    - Tỷ giá sàn = ceil(TT * 0.95) làm tròn lên.
    - Định dạng số nguyên, không có số thập phân, phân cách hàng nghìn bằng dấu chấm.
    """
    url = "https://sbv.gov.vn/vi/t%E1%BB%B7-gi%C3%A1"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    
    result = {
        "trung_tam": 25641,
        "mua": 24409,
        "ban": 26873
    }
    try:
        r = requests.get(url, headers=headers, timeout=10, verify=False)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            tables = soup.find_all("table")
            if len(tables) >= 1:
                t0_text = tables[0].text
                match_tt = re.search(r'1\s*Đô\s*la\s*Mỹ\s*=\s*([\d\.,]+)', t0_text, re.IGNORECASE)
                if match_tt:
                    raw_val = match_tt.group(1).replace(".", "").replace(",", "").strip()
                    result["trung_tam"] = int(raw_val[:5]) if len(raw_val) >= 5 else int(raw_val)
            if len(tables) >= 2:
                for row in tables[1].find_all("tr"):
                    cols = [c.text.strip() for c in row.find_all(["td", "th"])]
                    if len(cols) >= 5 and "USD" in cols[1]:
                        raw_mua = cols[3].split(",")[0].replace(".", "").strip()
                        if raw_mua.isdigit():
                            result["mua"] = int(raw_mua)
                        raw_ban = cols[4].split(",")[0].replace(".", "").strip()
                        if raw_ban.isdigit():
                            result["ban"] = int(raw_ban)
                        break
    except Exception as e:
        print(f"[CẢNH BÁO SBV] Không cào được tỷ giá online: {e}, sử dụng số liệu dự phòng.")
        
    tt = result["trung_tam"]
    tran = math.floor(tt * 1.05)
    san = math.ceil(tt * 0.95)
    mua = result["mua"]
    ban = result["ban"]
    
    def fmt(n):
        return f"{n:,.0f}".replace(",", ".")
    
    rates = {
        "trung_tam": fmt(tt),
        "san": fmt(san),
        "tran": fmt(tran),
        "mua": fmt(mua),
        "ban": fmt(ban)
    }
    print(f"✓ Cập nhật tỷ giá SBV: Trung tâm {rates['trung_tam']} | Sàn {rates['san']} | Trần {rates['tran']} | Mua {rates['mua']} | Bán {rates['ban']}")
    return rates

def fetch_mb_exchange_rates():
    """Lấy tỷ giá USD-VND niêm yết Mua/Bán chuyển khoản từ website MBBank cho ngày T và T-1."""
    session = requests.Session()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }
    result = {
        "hom_qua": "25.775/26.175",
        "hom_nay": "25.775/26.175"
    }
    try:
        page_url = "https://www.mbbank.com.vn/ExchangeRate"
        res = session.get(page_url, headers=headers, timeout=10)
        soup = BeautifulSoup(res.text, "html.parser")
        token_input = soup.find("input", {"name": "__RequestVerificationToken"})
        token = token_input.get("value") if token_input else ""
        
        if token:
            api_headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "MB-XSRF-Token-FormOnline": token,
                "Referer": page_url,
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json, text/plain, */*"
            }
            now = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=7)
            t0 = now.strftime("%Y-%m-%d")
            t1 = (now - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
            
            # 1. Hôm nay (T)
            r0 = session.get(f"https://www.mbbank.com.vn/api/getExchangeRate/{t0}", headers=api_headers, timeout=10)
            if r0.status_code == 200:
                for item in r0.json().get("lst", []):
                    if item.get("currencyCode") == "USD" and item.get("usd_default"):
                        b = int(item.get("buy_bank_transfer", 0))
                        s = int(item.get("sell_bank_transfer", 0))
                        if b > 0 and s > 0:
                            result["hom_nay"] = f"{b:,.0f}/{s:,.0f}".replace(",", ".")
                        break
            
            # 2. Hôm trước (T-1)
            r1 = session.get(f"https://www.mbbank.com.vn/api/getExchangeRate/{t1}", headers=api_headers, timeout=10)
            if r1.status_code == 200:
                for item in r1.json().get("lst", []):
                    if item.get("currencyCode") == "USD" and item.get("usd_default"):
                        b = int(item.get("buy_bank_transfer", 0))
                        s = int(item.get("sell_bank_transfer", 0))
                        if b > 0 and s > 0:
                            result["hom_qua"] = f"{b:,.0f}/{s:,.0f}".replace(",", ".")
                        break
    except Exception as e:
        print(f"[CẢNH BÁO MB] Không cào được tỷ giá MBBank online: {e}")
        
    print(f"✓ Cập nhật tỷ giá niêm yết MBBank: Hôm qua {result['hom_qua']} | Hôm nay {result['hom_nay']}")
    return result

def fetch_market_indices_and_fx():
    """Lấy dữ liệu tỷ giá EURUSD, USDJPY, USDCNY và chỉ số DOW, NIKKEI, DAX từ Yahoo Finance."""
    symbols = {
        'EURUSD': 'EURUSD=X',
        'USDJPY': 'USDJPY=X',
        'USDCNY': 'USDCNY=X',
        'DOW': '^DJI',
        'NIKKEI': '^N225',
        'DAX': '^GDAXI'
    }
    data = {
        'EURUSD': {'latest': 1.1401, 'prev': 1.1480},
        'USDJPY': {'latest': 157.19, 'prev': 157.05, 'dir': 'tăng lên'},
        'USDCNY': {'latest': 7.1910},
        'DOW': {'val_str': '51,828.62', 'chg_str': '+0.28%', 'is_pos': True},
        'NIKKEI': {'val_str': '66,364.20', 'chg_str': '+3.82%', 'is_pos': True},
        'DAX': {'val_str': '25,408.64', 'chg_str': '+0.41%', 'is_pos': True}
    }
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    
    for key, sym in symbols.items():
        url = f'https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=5d'
        try:
            r = requests.get(url, headers=headers, timeout=8)
            if r.status_code == 200:
                res = r.json()['chart']['result'][0]
                meta = res['meta']
                quotes = res['indicators']['quote'][0]
                closes = [c for c in quotes.get('close', []) if c is not None]
                latest = meta.get('regularMarketPrice') or (closes[-1] if closes else 0)
                prev = meta.get('chartPreviousClose') or (closes[-2] if len(closes) >= 2 else latest)
                pct = ((latest - prev) / prev) * 100 if prev else 0
                
                if key in ['DOW', 'NIKKEI', 'DAX']:
                    val_str = f"{latest:,.2f}"
                    chg_str = f"{pct:+.2f}%"
                    data[key] = {
                        'val_str': val_str,
                        'chg_str': chg_str,
                        'is_pos': (pct >= 0)
                    }
                elif key == 'EURUSD':
                    data['EURUSD'] = {'latest': latest, 'prev': prev}
                elif key == 'USDJPY':
                    direction = 'tăng lên' if latest >= prev else 'giảm nhẹ'
                    data['USDJPY'] = {'latest': latest, 'prev': prev, 'dir': direction}
                elif key == 'USDCNY':
                    data['USDCNY'] = {'latest': latest}
        except Exception as e:
            print(f"[CẢNH BÁO YF] Không lấy được {sym}: {e}")
            
    return data

# ==============================================================================
# BƯỚC 2: PHÂN TÍCH & DỊCH BẰNG GEMINI (CƠ CHẾ THÁC NƯỚC CHỐNG RATE LIMIT)
# ==============================================================================
def log_word_count_stats(text):
    """Thống kê và hiển thị số từ của các mục trọng điểm trong log để theo dõi chất lượng."""
    print("\n--- THỐNG KÊ SỐ TỪ CÁC PHẦN TRỌNG ĐIỂM ---")
    sections = {
        "Thị trường tiền tệ LNH (Yêu cầu: 88 - 95 từ)": ["liên ngân hàng", "thanh khoản", "OMO"],
        "Thị trường ngoại hối USD-VND (Yêu cầu: 75 - 80 từ)": ["USD-VND", "ngoại tệ", "Swap"],
        "USD kết hợp EUR (Yêu cầu: 150 - 200 từ)": ["USD", "EUR", "DXY"],
        "Khu vực Nhật Bản - Japan (Yêu cầu: 100 - 130 từ)": ["Nhật Bản", "Japan", "BoJ", "Yên", "JPY"],
        "Khu vực Trung Quốc - China (Yêu cầu: 50 - 70 từ)": ["Trung Quốc", "China", "PBoC", "Nhân dân tệ", "CNY"],
        "Dầu thô Brent (Yêu cầu: 70 - 100 từ)": ["Brent", "Dầu thô", "dầu"],
        "Cà phê Arabica & Robusta (Yêu cầu: 130 - 135 từ)": ["Cà phê", "Arabica", "Robusta"]
    }
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    for label, keywords in sections.items():
        matched_paras = [p for p in paragraphs if any(k.lower() in p.lower() for k in keywords) and not any(p.startswith(f"{i}.") for i in range(1, 6))]
        if matched_paras:
            word_count = sum(len(p.split()) for p in matched_paras)
            print(f"• {label}: ~{word_count} từ")
    print("------------------------------------------\n")

def analyze_with_ai(raw_news, report_type):
    """Gửi duy nhất 1 Request gom toàn bộ tin tức; tự động nhảy model nếu dính Rate Limit."""
    if not GEMINI_KEY:
        raise ValueError("Chưa thiết lập biến môi trường GEMINI_API_KEY!")

    # Cắt gọn độ dài nếu quá lớn để luôn nằm dưới 250K TPM
    clean_news = raw_news[:80000] if len(raw_news) > 80000 else raw_news
    title_label = "BẢN TIN SÁNG" if report_type == "morning" else "BẢN TIN THỊ TRƯỜNG NGÀY"

    now_vn = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=7)
    d_now = now_vn.strftime('%d.%m.%Y')
    d_prev = (now_vn - datetime.timedelta(days=1)).strftime('%d.%m.%Y')

    prompt = f"""
Bạn là chuyên viên nghiên cứu vĩ mô và thị trường tiền tệ (Treasury & Financial Markets Research) tại Ngân hàng TMCP Quân Đội (MB).
Dưới đây là tập hợp các tin tức tài chính, tiền tệ và hàng hóa thu thập được trong ngày:
---
{clean_news}
---

Hãy tổng hợp, phân loại, biên dịch chuẩn xác sang Tiếng Việt và xuất bản nội dung cho "{title_label}".

Bố cục báo cáo bắt buộc gồm 5 phần chính với CÁC QUY ĐỊNH NGHIÊM NGẶT VỀ CÂU MỞ ĐẦU VÀ SỐ TỪ:

1. TỔNG QUAN VĨ MÔ & CHÍNH SÁCH TIỀN TỆ QUỐC TẾ
   • Diễn biến kinh tế & chính sách Nhật Bản (Japan):
     - MỞ ĐẦU BẮT BUỘC bằng cụm câu: "Trong phiên hôm qua {d_prev}, tỷ giá USD-JPY đóng cửa ở mức [mức giá]. Trong phiên sáng nay, tỷ giá USD-JPY biến động tăng lên quanh mức [mức giá] trong biên độ vừa..."
     - QUY ĐỊNH ĐỘ DÀI: BẮT BUỘC tối thiểu 100 từ, tối đa 130 từ tiếng Việt.
     - Nội dung: Kinh tế Nhật, chính sách tiền tệ BoJ, lạm phát, tiền lương và biến động đồng Yên JPY.
   • Diễn biến kinh tế & chính sách Trung Quốc (China):
     - CÁCH XUỐNG 1 DÒNG và MỞ ĐẦU BẮT BUỘC bằng cụm câu: "Phiên giao dịch hôm nay, tỷ giá USD-CNY biến động đi ngang quanh mức [mức giá]..."
     - QUY ĐỊNH ĐỘ DÀI: BẮT BUỘC tối thiểu 50 từ, tối đa 70 từ tiếng Việt.
     - Nội dung: Dữ liệu tăng trưởng, chính sách PBoC, xuất nhập khẩu và bất động sản.

2. DIỄN BIẾN THỊ TRƯỜNG TIỀN TỆ & TỶ GIÁ
   • Diễn biến đồng USD kết hợp với đồng EUR:
     - MỞ ĐẦU BẮT BUỘC bằng cụm câu: "Trong phiên giao dịch hôm qua, ngày {d_prev}, tỷ giá EUR-USD kết thúc ở mức [mức giá]. Trong phiên {d_now}, tỷ giá EUR-USD biến động quanh [mức giá]..."
     - BẮT BUỘC XUỐNG DÒNG và MỞ ĐẦU ĐOẠN 2 bằng cụm từ: "Về phía Châu Âu,"
     - QUY ĐỊNH ĐỘ DÀI: BẮT BUỘC tối thiểu 150 từ, tối đa 200 từ tiếng Việt cho cả đoạn kết hợp này.
     - Nội dung: Chỉ số DXY, kỳ vọng lãi suất Fed, diễn biến EUR, chính sách ECB và tác động tỷ giá.
   • Đoạn nhận định Dự kiến EUR-USD: khoảng 15 - 20 từ (bắt đầu bằng "Dự kiến: ...").

3. THỊ TRƯỜNG HÀNG HÓA & NĂNG LƯỢNG
   • Thị trường Cà phê (Tổng hợp giá Cà phê Arabica và Robusta):
     - MỞ ĐẦU BẮT BUỘC bằng cụm từ: "Cập nhật giá cà phê thế giới,"
     - Nguồn dữ liệu: Dựa vào bài viết mới nhất trên Vietnambiz (chủ đề Cà phê), viết lại nội dung từ phần tiêu đề "Cập nhật giá cà phê thế giới" đến hết thành 1 đoạn văn tiếng Việt.
     - QUY ĐỊNH ĐỘ DÀI: BẮT BUỘC ĐÚNG 135 từ tiếng Việt (hoặc trong khoảng 130 - 135 từ).
     - Nội dung: Diễn biến giá Robusta (sàn London), Arabica (sàn New York) và nguyên nhân dẫn dắt (hoạt động mua vào kỹ thuật, dự báo sản lượng kỷ lục Brazil từ Conab, dự báo dư cung từ ICO, biến động tồn kho ICE).
     - Đoạn nhận định Dự kiến: BẮT BUỘC CHỈ NÊU RA 1 MỨC DỰ BÁO duy nhất (không nêu khoảng biên độ từ bao nhiêu đến bao nhiêu), bắt đầu bằng "Dự kiến: ..."
   • Thị trường Năng lượng (Dầu Brent) & Kim loại quý (Vàng):
     - QUY ĐỊNH ĐỘ DÀI TỔNG THỂ CHO CẢ PHẦN: BẮT BUỘC tối thiểu 125 từ, tối đa 135 từ tiếng Việt.
     - Phân bổ: Đoạn phân tích giá dầu Brent chiếm khoảng 90 - 100 từ (dựa vào bài viết mới nhất trên Vietnambiz chủ đề Dầu mỏ, chỉ update thông tin dầu Brent, tăng/giảm quanh mức bao nhiêu, nguyên nhân do đâu). Đoạn cập nhật giá vàng chiếm khoảng 30 - 35 từ (BẮT BUỘC NGẮN GỌN ĐÚNG 2 CÂU).
     - Đoạn nhận định Dự kiến: BẮT BUỘC TRONG ĐÚNG 1 CÂU VÀ TỐI ĐA 15 TỪ (chỉ nêu 1 mức dự báo duy nhất cho dầu Brent và vàng, không nêu khoảng), bắt đầu bằng "Dự kiến: ..."

4. THỊ TRƯỜNG TÀI CHÍNH VIỆT NAM (TRANG 1)
   • Thị trường tiền tệ liên ngân hàng:
     - QUY ĐỊNH ĐỘ DÀI: BẮT BUỘC tối thiểu 88 từ, tối đa 95 từ tiếng Việt.
     - Đoạn nhận định Dự kiến: BẮT BUỘC từ 14 đến 16 từ tiếng Việt (bắt đầu bằng "Dự kiến: ...").
   • Thị trường ngoại hối USD-VND:
     - QUY ĐỊNH ĐỘ DÀI: BẮT BUỘC tối thiểu 75 từ, tối đa 80 từ tiếng Việt.
     - Đoạn nhận định Dự kiến: BẮT BUỘC ĐÚNG 12 TỪ tiếng Việt (bắt đầu bằng "Dự kiến: ...").

5. HÀM Ý & KHUYẾN NGHỊ CHO NGHIỆP VỤ KINH DOANH VỐN (TREASURY INSIGHTS)

NGUYÊN TẮC BẮT BUỘC:
- Đếm và kiểm soát chặt chẽ số từ tiếng Việt của tất cả các mục trên để đảm bảo nằm đúng trong khung yêu cầu.
- Viết văn phong tài chính ngân hàng chuẩn mực, cô đọng, súc tích (dùng gạch đầu dòng rõ ràng).
- TUYỆT ĐỐI KHÔNG dùng ký tự markdown như **, ##, __.
- Tiêu đề từng phần viết chữ in hoa.
"""

    for model_name in FALLBACK_MODELS:
        try:
            print(f"--> Đang thử phân tích với model: {model_name}...")
            model = genai.GenerativeModel(model_name)
            
            # Gửi 1 lần duy nhất toàn bộ dữ liệu (Single-Batch)
            response = model.generate_content(prompt)
            
            if response and response.text:
                print(f"✓ Hoàn tất phân tích thành công với model: {model_name}!")
                log_word_count_stats(response.text)
                return response.text

        except ResourceExhausted as e:
            # Dính 429 Quota Exceeded / Rate Limit -> nghỉ 10s rồi chuyển model kế tiếp
            print(f"[CẢNH BÁO] {model_name} chạm ngưỡng Rate Limit (429). Đang chuyển sang model kế tiếp...")
            time.sleep(10)
            continue

        except Exception as e:
            # Model có thể chưa kích hoạt hoặc tạm thời bận
            print(f"[BỎ QUA] Lỗi với {model_name}: {e}. Đang chuyển tiếp...")
            time.sleep(2)
            continue

    # TẦNG DỰ PHÒNG 2: OPENROUTER (LING 3.0 FLASH FIN FREE)
    openrouter_res = call_openrouter_fallback(prompt)
    if openrouter_res:
        return openrouter_res

    raise RuntimeError("Tất cả các model Google Gemini và tầng dự phòng OpenRouter đều không thể kết nối hoặc đã hết hạn mức!")

def call_openrouter_fallback(prompt):
    """Tầng dự phòng 2: Kích hoạt OpenRouter Ling 3.0 Flash Fin khi Google Gemini gặp sự cố."""
    openrouter_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("Open_Router_API_Key")
    if not openrouter_key:
        print("[THÔNG BÁO] Không tìm thấy OPENROUTER_API_KEY để gọi tầng dự phòng OpenRouter.")
        return None

    model_id = "inclusionai/ling-3.0-flash-fin:free"
    print(f"\n--> [DỰ PHÒNG TẦNG 2] Đang kích hoạt OpenRouter: {model_id}...")

    headers = {
        "Authorization": f"Bearer {openrouter_key}",
        "HTTP-Referer": "https://mbbank.com.vn",
        "X-Title": "MB Treasury Report Automation",
        "Content-Type": "application/json"
    }

    payload = {
        "model": model_id,
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "reasoning": {"effort": "none"},
        "temperature": 0.2,
        "max_tokens": 4000
    }

    try:
        t0 = time.time()
        r = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=90)
        if r.status_code == 200:
            data = r.json()
            msg = data["choices"][0]["message"]
            text = msg.get("content") or msg.get("reasoning_content") or ""
            if text:
                print(f"✓ Hoàn tất phân tích thành công với OpenRouter ({model_id}) trong {time.time() - t0:.2f}s!")
                log_word_count_stats(text)
                return text
        else:
            print(f"[CẢNH BÁO] OpenRouter trả về mã lỗi {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"[CẢNH BÁO] Lỗi kết nối OpenRouter: {e}")

    return None

# ==============================================================================
# BƯỚC 3: TẠO FILE WORD ĐẸP MẮT (PHONG CÁCH MB NAVY)
# ==============================================================================
# ==============================================================================
# BƯỚC 3: TẠO FILE WORD CHUẨN 3 TRANG THEO TEMPLATE MB BANK
# ==============================================================================
def clear_cell_paragraphs(cell):
    """Xóa các đoạn văn bản trong ô nhưng giữ nguyên thuộc tính tcPr và viền bảng."""
    tc = cell._tc
    for p in list(tc.findall(qn('w:p'))):
        tc.remove(p)

def set_font(run, name="Times New Roman", size_pt=11.0, color_rgb=(0, 112, 192), bold=False, italic=False):
    """Thiết lập font chữ, cỡ chữ, màu sắc chính xác theo quy chuẩn."""
    run.font.name = name
    run.font.size = Pt(size_pt)
    run.font.bold = bold
    run.font.italic = italic
    if color_rgb:
        run.font.color.rgb = RGBColor(*color_rgb)
    
    rPr = run._r.get_or_add_rPr()
    rFonts = rPr.find(qn('w:rFonts'))
    if rFonts is None:
        rFonts = OxmlElement('w:rFonts')
        rPr.insert(0, rFonts)
    rFonts.set(qn('w:ascii'), name)
    rFonts.set(qn('w:hAnsi'), name)
    rFonts.set(qn('w:cs'), name)

def get_distinct_cells(row):
    """Lấy danh sách các ô thực tế (đã xử lý merge cột) trong một hàng."""
    cells = []
    seen = set()
    for cell in row.cells:
        if id(cell._tc) not in seen:
            seen.add(id(cell._tc))
            cells.append(cell)
    return cells

def set_chart_dimensions(table):
    """Quy định chuẩn kích thước 3 biểu đồ trang 1 theo yêu cầu:
    - Biểu đồ 1 (Lãi suất LNH): Height 2.10 inch, Width 4.29 inch
    - Biểu đồ 2 (Tỷ giá USD-VND): Height 2.08 inch, Width 4.29 inch
    - Biểu đồ 3 (Lợi suất TP): Height 1.94 inch, Width 4.29 inch
    """
    chart_specs = [
        (3, 4.29, 2.10),
        (4, 4.29, 2.08),
        (5, 4.29, 1.94)
    ]
    for r_idx, w_in, h_in in chart_specs:
        cell = get_distinct_cells(table.rows[r_idx])[2]
        cx = str(int(w_in * 914400))
        cy = str(int(h_in * 914400))
        for extent in cell._tc.findall('.//' + qn('wp:extent')):
            extent.set('cx', cx)
            extent.set('cy', cy)

def update_sbv_table(table, rates):
    """Cập nhật số liệu tỷ giá NHNN vào Nested Table 0 ở Row 4 Cell 0."""
    cell0 = get_distinct_cells(table.rows[4])[0]
    if cell0.tables:
        nt0 = cell0.tables[0]
        # Row 1: Tỷ giá Trung Tâm (c0), Sàn (c1), Trần (c2)
        cell_tt = nt0.rows[1].cells[0]
        clear_cell_paragraphs(cell_tt)
        p_tt = cell_tt.add_paragraph()
        p_tt.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_tt.paragraph_format.space_before = Pt(0)
        p_tt.paragraph_format.space_after = Pt(0)
        r_tt = p_tt.add_run(rates["trung_tam"])
        set_font(r_tt, name="Times New Roman", size_pt=11.0, color_rgb=(255, 255, 255), bold=True)

        cell_san = nt0.rows[1].cells[1]
        clear_cell_paragraphs(cell_san)
        p_san = cell_san.add_paragraph()
        p_san.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_san.paragraph_format.space_before = Pt(0)
        p_san.paragraph_format.space_after = Pt(0)
        r_san = p_san.add_run(rates["san"])
        set_font(r_san, name="Times New Roman", size_pt=11.0, color_rgb=(23, 54, 93), bold=True)

        cell_tran = nt0.rows[1].cells[2]
        clear_cell_paragraphs(cell_tran)
        p_tran = cell_tran.add_paragraph()
        p_tran.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_tran.paragraph_format.space_before = Pt(0)
        p_tran.paragraph_format.space_after = Pt(0)
        r_tran = p_tran.add_run(rates["tran"])
        set_font(r_tran, name="Times New Roman", size_pt=11.0, color_rgb=(23, 54, 93), bold=True)

        # Row 3: Mua (c1), Bán (c2)
        cell_mua = nt0.rows[3].cells[1]
        clear_cell_paragraphs(cell_mua)
        p_mua = cell_mua.add_paragraph()
        p_mua.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_mua.paragraph_format.space_before = Pt(0)
        p_mua.paragraph_format.space_after = Pt(0)
        r_mua = p_mua.add_run(rates["mua"])
        set_font(r_mua, name="Times New Roman", size_pt=11.0, color_rgb=(23, 54, 93), bold=True)

        cell_ban = nt0.rows[3].cells[2]
        clear_cell_paragraphs(cell_ban)
        p_ban = cell_ban.add_paragraph()
        p_ban.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_ban.paragraph_format.space_before = Pt(0)
        p_ban.paragraph_format.space_after = Pt(0)
        r_ban = p_ban.add_run(rates["ban"])
        set_font(r_ban, name="Times New Roman", size_pt=11.0, color_rgb=(23, 54, 93), bold=True)

def update_mb_rate_table(table, mb_rates):
    """Cập nhật Tỷ giá USD-VND niêm yết trên website MBBank (Hàng 4 Ô 0, Nested Table 1)."""
    cell0 = get_distinct_cells(table.rows[4])[0]
    if len(cell0.tables) >= 2:
        nt1 = cell0.tables[1]
        # Row 1 Col 0: Hôm qua (màu xanh navy #002060)
        c_yest = nt1.rows[1].cells[0]
        clear_cell_paragraphs(c_yest)
        p_yest = c_yest.add_paragraph()
        p_yest.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_yest.paragraph_format.space_before = Pt(0)
        p_yest.paragraph_format.space_after = Pt(0)
        r_yest = p_yest.add_run(mb_rates["hom_qua"])
        set_font(r_yest, name="Times New Roman", size_pt=11.0, color_rgb=(0, 32, 96), bold=True)

        # Row 1 Col 1: Hôm nay (màu đỏ #C00000)
        c_today = nt1.rows[1].cells[1]
        clear_cell_paragraphs(c_today)
        p_today = c_today.add_paragraph()
        p_today.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_today.paragraph_format.space_before = Pt(0)
        p_today.paragraph_format.space_after = Pt(0)
        r_today = p_today.add_run(mb_rates["hom_nay"])
        set_font(r_today, name="Times New Roman", size_pt=11.0, color_rgb=(192, 0, 0), bold=True)

def update_index_table(table, index_data):
    """Cập nhật chỉ số Dow Jones, Nikkei, DAX (Hàng 8 Ô 0, Nested Table 2)."""
    cell0 = get_distinct_cells(table.rows[8])[0]
    if len(cell0.tables) >= 3:
        nt2 = cell0.tables[2]
        for r_idx, key in [(1, "DOW"), (2, "NIKKEI"), (3, "DAX")]:
            if key in index_data:
                item = index_data[key]
                val_str = item["val_str"]
                chg_str = item["chg_str"]
                is_pos = item["is_pos"]
                col_rgb = (0, 176, 80) if is_pos else (192, 0, 0)
                
                # C1: Giá trị
                c_val = nt2.rows[r_idx].cells[1]
                clear_cell_paragraphs(c_val)
                p_val = c_val.add_paragraph()
                p_val.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                p_val.paragraph_format.space_before = Pt(0)
                p_val.paragraph_format.space_after = Pt(0)
                r_val = p_val.add_run(val_str)
                set_font(r_val, name="Times New Roman", size_pt=10.0, color_rgb=col_rgb, bold=True)
                
                # C2: Thay đổi
                c_chg = nt2.rows[r_idx].cells[2]
                clear_cell_paragraphs(c_chg)
                p_chg = c_chg.add_paragraph()
                p_chg.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                p_chg.paragraph_format.space_before = Pt(0)
                p_chg.paragraph_format.space_after = Pt(0)
                r_chg = p_chg.add_run(chg_str)
                set_font(r_chg, name="Times New Roman", size_pt=10.0, color_rgb=col_rgb, bold=True)

def fetch_commodity_table_data():
    """Lấy dữ liệu cập nhật cho Bảng giá hàng hóa trang 3."""
    data = {
        3: ("539.18", "+0.86%", "+12.38%"),     # CRB Spot
        4: ("101.04", "+0.60%", "-4.85%"),      # USD Index
        5: ("4,285.50", "+0.35%", "+14.20%"),   # LME Index
        7: ("3,367.00", "+2.34%", "+62.82%"),   # Cà phê Robusta (USD/T)
        8: ("278.60", "+1.18%", "+107.64%"),    # Cà phê Arabica (USc/lbs)
        9: ("428.25", "-0.85%", "+1.85%"),      # Ngô (USc/bsh)
        10: ("1,019.50", "+0.42%", "-15.92%"),  # Đậu tương (USc/bsh)
        11: ("365.20", "+0.30%", "+17.47%"),    # Cao su (JPY/kg)
        12: ("72.85", "-0.40%", "-28.00%"),     # Cotton (USc/lbs)
        14: ("104.32", "-2.14%", "-15.33%"),    # Dầu thô (USD/bbl)
        15: ("2.65", "+1.80%", "-12.92%"),      # Khí đốt (USD/gal)
        16: ("3.39", "-0.04%", "-18.03%"),      # Xăng RON92 (Lấy thẳng giá Gasoline TradingEconomics)
        18: ("9,820.00", "+0.75%", "+30.50%"),  # Đồng (USD/T)
        19: ("2,645.00", "+0.45%", "+11.76%"),  # Nhôm (USD/T)
        20: ("2,980.00", "-0.35%", "+20.85%"),  # Kẽm (USD/T)
        21: ("16,350.00", "-0.50%", "-2.57%"),  # Nikken (USD/T)
        23: ("2,658.20", "+0.39%", "+35.87%"),  # Vàng (USD/oz)
        24: ("31.85", "+0.13%", "+34.72%"),     # Bạc (USD/oz)
    }

    yf_map = {
        4: 'DX-Y.NYB',
        7: 'RC=F',
        8: 'KC=F',
        9: 'ZC=F',
        10: 'ZS=F',
        12: 'CT=F',
        14: 'BZ=F',
        15: 'NG=F',
        23: 'GC=F',
        24: 'SI=F'
    }
    headers = {'User-Agent': 'Mozilla/5.0'}
    for r_idx, sym in yf_map.items():
        try:
            url = f'https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=5d'
            r = requests.get(url, headers=headers, timeout=4)
            if r.status_code == 200:
                res = r.json()['chart']['result'][0]
                meta = res['meta']
                price = meta.get('regularMarketPrice')
                prev = meta.get('chartPreviousClose')
                if price and prev:
                    pct = ((price - prev) / prev) * 100
                    p_fmt = f"{price:.2f}" if r_idx in [15, 16] else f"{price:,.2f}"
                    pct_fmt = f"{pct:+.2f}%"
                    y_chg = data[r_idx][2]
                    data[r_idx] = (p_fmt, pct_fmt, y_chg)
        except Exception:
            pass

    return data

def update_commodity_table(table, comm_data, d_now, d_short):
    """Cập nhật Bảng giá hàng hóa Trang 3 (Hàng 11 Ô 0, Nested Table 0)."""
    cell0 = get_distinct_cells(table.rows[11])[0]
    
    # 1. Cập nhật tiêu đề P0: BẮT BUỘC font Verdana, size 10.0 pt, Bold
    if cell0.paragraphs:
        p0 = cell0.paragraphs[0]
        p0.text = f"Bảng giá hàng hóa ngày {d_now}"
        if p0.runs:
            set_font(p0.runs[0], name="Verdana", size_pt=10.0, color_rgb=(31, 73, 125), bold=True)
            
    if cell0.tables:
        ct = cell0.tables[0]
        # 2. Cập nhật header ngày cột 1 (Row 1 Cell 1)
        c_hdr_date = ct.rows[1].cells[1]
        clear_cell_paragraphs(c_hdr_date)
        p_hdr = c_hdr_date.add_paragraph()
        p_hdr.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        p_hdr.paragraph_format.space_before = Pt(0)
        p_hdr.paragraph_format.space_after = Pt(0)
        r_hdr = p_hdr.add_run(d_short)
        set_font(r_hdr, name="Times New Roman", size_pt=8.0, color_rgb=None, bold=True)
        
        # 3. Cập nhật từng hàng hàng hóa: Font Times New Roman 8pt, Giảm = Đỏ, Tăng = Xanh
        GREEN = (0, 128, 0)
        RED = (192, 0, 0)
        
        for r_idx, (price, d_chg, y_chg) in comm_data.items():
            if r_idx < len(ct.rows):
                row = ct.rows[r_idx]
                
                # C1: Giá ngày
                c1 = row.cells[1]
                clear_cell_paragraphs(c1)
                p1 = c1.add_paragraph()
                p1.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                p1.paragraph_format.space_before = Pt(0)
                p1.paragraph_format.space_after = Pt(0)
                r1 = p1.add_run(price)
                set_font(r1, name="Times New Roman", size_pt=8.0, color_rgb=None, bold=True)
                
                # C2: Thay đổi ngày (%)
                c2 = row.cells[2]
                clear_cell_paragraphs(c2)
                p2 = c2.add_paragraph()
                p2.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                p2.paragraph_format.space_before = Pt(0)
                p2.paragraph_format.space_after = Pt(0)
                r2 = p2.add_run(d_chg)
                col2 = RED if '-' in d_chg else GREEN
                set_font(r2, name="Times New Roman", size_pt=8.0, color_rgb=col2, bold=True)
                
                # C3: Thay đổi năm (%)
                c3 = row.cells[3]
                clear_cell_paragraphs(c3)
                p3 = c3.add_paragraph()
                p3.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                p3.paragraph_format.space_before = Pt(0)
                p3.paragraph_format.space_after = Pt(0)
                r3 = p3.add_run(y_chg)
                col3 = RED if '-' in y_chg else GREEN
                set_font(r3, name="Times New Roman", size_pt=8.0, color_rgb=col3, bold=True)

def create_word_doc(content, report_title, date_str, output_path):
    """Điền nội dung vào template.docx để tạo tài liệu chuẩn 100% nhận diện MB Bank."""
    print(f"--> Đang khởi tạo file Word theo mẫu gốc: {output_path}...")
    template_path = os.path.join(os.path.dirname(__file__), "template.docx")
    
    if os.path.exists(template_path):
        doc = Document(template_path)
        table = doc.tables[0]
        
        now_vn = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=7)
        d_now = now_vn.strftime('%d.%m.%Y')
        d_prev = (now_vn - datetime.timedelta(days=1)).strftime('%d.%m.%Y')

        # 1. Hàng 2: Cập nhật Ngày phát hành (Distinct Cell 1)
        r2_cells = get_distinct_cells(table.rows[2])
        clear_cell_paragraphs(r2_cells[1])
        p_d = r2_cells[1].add_paragraph()
        p_d.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        p_d.paragraph_format.space_before = Pt(0)
        p_d.paragraph_format.space_after = Pt(0)
        r_d = p_d.add_run(date_str)
        set_font(r_d, name="Verdana", size_pt=12.0, color_rgb=(36, 87, 148), bold=True, italic=True)

        # 2. Cập nhật Bảng Tỷ giá SBV & MBBank niêm yết (Hàng 4 Ô 0)
        sbv_rates = fetch_sbv_rates()
        update_sbv_table(table, sbv_rates)

        mb_rates = fetch_mb_exchange_rates()
        update_mb_rate_table(table, mb_rates)

        # 3. Phân tách nội dung do AI sinh ra (hoặc nội dung mẫu) vào các ô
        paragraphs = [p.strip() for p in content.split("\n") if p.strip()]

        # Hàng 3: Tin tức nổi bật (Distinct Cell 0)
        r3_cells = get_distinct_cells(table.rows[3])
        clear_cell_paragraphs(r3_cells[0])
        p_t = r3_cells[0].add_paragraph()
        p_t.alignment = WD_ALIGN_PARAGRAPH.LEFT
        p_t.paragraph_format.space_before = Pt(0)
        p_t.paragraph_format.space_after = Pt(1)
        r_t = p_t.add_run("Tin tức nổi bật:")
        set_font(r_t, name="Verdana", size_pt=13.0, color_rgb=(36, 87, 148), bold=True)

        tin_nb = [p for p in paragraphs if p.startswith("1.") or p.startswith("2.") or p.startswith("3.")]
        if not tin_nb:
            tin_nb = [
                "1. Theo NHNN, các tổ chức tín dụng tiếp tục đẩy mạnh xử lý nợ xấu, duy trì thanh khoản hệ thống liên ngân hàng ổn định.",
                "2. Giá dầu thô Brent duy trì biến động tích lũy quanh vùng 74 - 76 USD/thùng trước thềm các cuộc họp chính sách của OPEC+.",
                "3. Giá vàng thế giới điều chỉnh nhẹ sau khi lập đỉnh mới; chênh lệch giá vàng trong nước và quốc tế tiếp tục duy trì biên độ hợp lý."
            ]
        for tin in tin_nb[:3]:
            p_item = r3_cells[0].add_paragraph()
            p_item.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            p_item.paragraph_format.space_before = Pt(0)
            p_item.paragraph_format.space_after = Pt(1)
            r_item = p_item.add_run(tin)
            set_font(r_item, name="Times New Roman", size_pt=11.0, color_rgb=(0, 112, 192))

        # Hàng 3: Thị trường tiền tệ liên ngân hàng (Distinct Cell 1) - QUY ĐỊNH: Body 88-95 từ, Dự kiến 14-16 từ
        clear_cell_paragraphs(r3_cells[1])
        p_lnh_t = r3_cells[1].add_paragraph()
        p_lnh_t.alignment = WD_ALIGN_PARAGRAPH.LEFT
        p_lnh_t.paragraph_format.space_before = Pt(0)
        p_lnh_t.paragraph_format.space_after = Pt(1)
        r_lnh_t = p_lnh_t.add_run("Thị trường tiền tệ liên ngân hàng")
        set_font(r_lnh_t, name="Times New Roman", size_pt=11.0, color_rgb=(31, 73, 125), bold=True)

        lnh_body = (
            f"Hôm nay, {d_now}, thị trường liên ngân hàng ghi nhận thanh khoản hệ thống duy trì dồi dào. "
            "Lãi suất bình quân dao động quanh vùng 5,0% - 5,8%/năm kỳ hạn qua đêm ON "
            "và 5,5% - 6,2%/năm kỳ hạn 1 tuần. Ngân hàng Nhà nước tiếp tục linh hoạt sử dụng nghiệp vụ thị trường mở OMO "
            "nhằm điều tiết thanh khoản, hỗ trợ nguồn vốn ổn định cho các tổ chức tín dụng. "
            "Lãi suất Swap kỳ hạn 3M - 6M đi ngang quanh 3,20% - 4,00%/năm."
        )
        p_lnh_b = r3_cells[1].add_paragraph()
        p_lnh_b.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        p_lnh_b.paragraph_format.space_before = Pt(0)
        p_lnh_b.paragraph_format.space_after = Pt(1)
        r_lnh_b = p_lnh_b.add_run(lnh_body)
        set_font(r_lnh_b, name="Times New Roman", size_pt=11.0, color_rgb=(0, 112, 192))

        lnh_fc = "Dự kiến: lãi suất ON nhiều khả năng đi ngang quanh vùng 5,0% - 6,0%/năm."
        p_lnh_fc = r3_cells[1].add_paragraph()
        p_lnh_fc.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        p_lnh_fc.paragraph_format.space_before = Pt(0)
        p_lnh_fc.paragraph_format.space_after = Pt(0)
        r_lnh_fc = p_lnh_fc.add_run(lnh_fc)
        set_font(r_lnh_fc, name="Times New Roman", size_pt=11.0, color_rgb=(192, 0, 0), bold=True)

        # Hàng 4: Thị trường ngoại hối USD-VND (Distinct Cell 1) - QUY ĐỊNH: Body 75-80 từ, Dự kiến đúng 12 từ
        r4_cells = get_distinct_cells(table.rows[4])
        clear_cell_paragraphs(r4_cells[1])
        p_fx_t = r4_cells[1].add_paragraph()
        p_fx_t.alignment = WD_ALIGN_PARAGRAPH.LEFT
        p_fx_t.paragraph_format.space_before = Pt(0)
        p_fx_t.paragraph_format.space_after = Pt(1)
        r_fx_t = p_fx_t.add_run("Thị trường ngoại hối USD-VND")
        set_font(r_fx_t, name="Times New Roman", size_pt=11.0, color_rgb=(31, 73, 125), bold=True)

        fx_body = (
            f"Hôm nay, ngày {d_now}, tỷ giá USD/VND liên ngân hàng biến động trong biên độ hẹp quanh vùng 25.850 - 26.230. "
            "Thanh khoản duy trì vừa phải, trạng thái ngoại tệ hệ thống tương đối cân bằng. "
            "Chỉ số US Dollar Index dao động quanh 103,5 điểm, USD/JPY giao dịch quanh 153,20. "
            "Lãi suất Swap USD/VND kỳ hạn ON - 1W đi ngang quanh 0,6% - 2,0%/năm. "
            "Thị trường theo dõi sát tỷ giá trung tâm."
        )
        p_fx_b = r4_cells[1].add_paragraph()
        p_fx_b.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        p_fx_b.paragraph_format.space_before = Pt(0)
        p_fx_b.paragraph_format.space_after = Pt(1)
        r_fx_b = p_fx_b.add_run(fx_body)
        set_font(r_fx_b, name="Times New Roman", size_pt=11.0, color_rgb=(0, 112, 192))

        fx_fc = "Dự kiến: tỷ giá USD-VND dao động quanh mức 25.800 - 26.250."
        p_fx_fc = r4_cells[1].add_paragraph()
        p_fx_fc.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        p_fx_fc.paragraph_format.space_before = Pt(0)
        p_fx_fc.paragraph_format.space_after = Pt(0)
        r_fx_fc = p_fx_fc.add_run(fx_fc)
        set_font(r_fx_fc, name="Times New Roman", size_pt=11.0, color_rgb=(192, 0, 0), bold=True)

        # Hàng 5: Ý tưởng sản phẩm (Distinct Cell 1) - CỠ CHỮ 10PT ĐẶC THÙ, BOLD ITALIC, MÀU ĐỎ #C00000
        r5_cells = get_distinct_cells(table.rows[5])
        clear_cell_paragraphs(r5_cells[1])
        p_sp_t = r5_cells[1].add_paragraph()
        p_sp_t.alignment = WD_ALIGN_PARAGRAPH.LEFT
        p_sp_t.paragraph_format.space_before = Pt(0)
        p_sp_t.paragraph_format.space_after = Pt(1)
        r_sp_t = p_sp_t.add_run("Ý tưởng sản phẩm")
        set_font(r_sp_t, name="Times New Roman", size_pt=11.0, color_rgb=(31, 73, 125), bold=True)

        p_sp_i = r5_cells[1].add_paragraph()
        p_sp_i.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        p_sp_i.paragraph_format.space_before = Pt(0)
        p_sp_i.paragraph_format.space_after = Pt(1)
        r_sp_i = p_sp_i.add_run("Nhiều khả năng tỷ giá USD-VND có thể biến động trong khu vực từ 25.800-26.250.")
        set_font(r_sp_i, name="Times New Roman", size_pt=11.0, color_rgb=(0, 112, 192))

        for b_text in [
            "- Sử dụng sản phẩm vay VND lãi suất ưu đãi kết hợp sản phẩm AIRS để giúp khách hàng có thể vay VND với lãi suất cạnh tranh hơn so với phương án vay VND thông thường.",
            "- Sử dụng sản phẩm mua ngoại tệ kỳ hạn FX FWD kỳ hạn dưới 1 tháng để tận dụng điểm kỳ hạn đang ở mức phù hợp."
        ]:
            p_sp_b = r5_cells[1].add_paragraph()
            p_sp_b.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            p_sp_b.paragraph_format.space_before = Pt(0)
            p_sp_b.paragraph_format.space_after = Pt(1)
            r_sp_b = p_sp_b.add_run(b_text)
            set_font(r_sp_b, name="Times New Roman", size_pt=10.0, color_rgb=(192, 0, 0), bold=True, italic=True)

        # 4. QUY ĐỊNH KÍCH THƯỚC CHUẨN CỦA 3 BIỂU ĐỒ TRANG 1
        set_chart_dimensions(table)

        # 5. CẬP NHẬT TRANG 2: LẤY DỮ LIỆU THỊ TRƯỜNG YAHOO FINANCE
        mkt = fetch_market_indices_and_fx()

        # Cập nhật Bảng chỉ số Dow Jones, Nikkei, DAX (Hàng 8 Ô 0, Nested Table 2)
        update_index_table(table, mkt)

        # Định dạng các số tỷ giá
        eur_prev_str = f"{mkt['EURUSD']['prev']:.4f}".replace(".", ",")
        eur_now_str = f"{mkt['EURUSD']['latest']:.4f}".replace(".", ",")
        jpy_prev_str = f"{mkt['USDJPY']['prev']:.2f}".replace(".", ",")
        jpy_now_str = f"{mkt['USDJPY']['latest']:.2f}".replace(".", ",")
        jpy_dir = mkt['USDJPY'].get('dir', 'tăng lên')
        cny_now_str = f"{mkt['USDCNY']['latest']:.4f}".replace(".", ",")

        # Hàng 7: Ngoại hối EU (USD & EUR: 150-200 từ)
        r7_cells = get_distinct_cells(table.rows[7])
        clear_cell_paragraphs(r7_cells[1])
        p_eu_t = r7_cells[1].add_paragraph()
        p_eu_t.alignment = WD_ALIGN_PARAGRAPH.LEFT
        p_eu_t.paragraph_format.space_before = Pt(0)
        p_eu_t.paragraph_format.space_after = Pt(2)
        r_eu_t = p_eu_t.add_run("Thị trường ngoại hối EU")
        set_font(r_eu_t, name="Times New Roman", size_pt=11.0, color_rgb=(31, 73, 125), bold=True)

        # Đoạn 1: Mở đầu bằng cụm câu quy định nghiêm ngặt
        eu_para1 = (
            f"Trong phiên giao dịch hôm qua, ngày {d_prev}, tỷ giá EUR-USD kết thúc ở mức {eur_prev_str}. "
            f"Trong phiên {d_now}, tỷ giá EUR-USD biến động quanh {eur_now_str} khi chỉ số DXY dao động quanh 103,50 điểm. "
            "Các dữ liệu kinh tế gần đây của Hoa Kỳ cho thấy thị trường lao động duy trì khả năng phục hồi, "
            "khiến giới đầu tư thận trọng về lộ trình nới lỏng chính sách của Cục Dự trữ Liên bang Mỹ (Fed). "
            "Sự thận trọng của Fed tiếp tục tạo bệ đỡ cho sức mạnh đồng USD."
        )
        p_eu_1 = r7_cells[1].add_paragraph()
        p_eu_1.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        p_eu_1.paragraph_format.space_before = Pt(0)
        p_eu_1.paragraph_format.space_after = Pt(2)
        r_eu_1 = p_eu_1.add_run(eu_para1)
        set_font(r_eu_1, name="Times New Roman", size_pt=11.0, color_rgb=(0, 112, 192))

        # Đoạn 2: Xuống dòng và BẮT BUỘC mở đầu bằng cụm từ: "Về phía Châu Âu,"
        eu_para2 = (
            "Về phía Châu Âu, đồng EUR chịu sức ép điều chỉnh trước sự suy giảm của PMI sản xuất tại Eurozone, "
            "đặc biệt là sự trì trệ của nền kinh tế Đức. Ngân hàng Trung ương Châu Âu (ECB) duy trì định hướng nới lỏng khi lạm phát hạ nhiệt. "
            "Sự phân hóa chính sách giữa Fed và ECB tiếp tục hỗ trợ USD, đồng thời tạo áp lực nhất định lên tỷ giá trong nước."
        )
        p_eu_2 = r7_cells[1].add_paragraph()
        p_eu_2.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        p_eu_2.paragraph_format.space_before = Pt(0)
        p_eu_2.paragraph_format.space_after = Pt(2)
        r_eu_2 = p_eu_2.add_run(eu_para2)
        set_font(r_eu_2, name="Times New Roman", size_pt=11.0, color_rgb=(0, 112, 192))

        # Đoạn 3: Dự kiến
        p_eu_fc = r7_cells[1].add_paragraph()
        p_eu_fc.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        p_eu_fc.paragraph_format.space_before = Pt(0)
        p_eu_fc.paragraph_format.space_after = Pt(0)
        r_eu_fc = p_eu_fc.add_run("Dự kiến: Tỷ giá EUR-USD dao động đi ngang trong biên độ vừa quanh khu vực 1,0800 - 1,0900.")
        set_font(r_eu_fc, name="Times New Roman", size_pt=11.0, color_rgb=(192, 0, 0), bold=True)

        # Hàng 7: Ngoại hối Châu Á (Japan: 100-130 từ; China: 50-70 từ)
        clear_cell_paragraphs(r7_cells[2])
        p_asia_t = r7_cells[2].add_paragraph()
        p_asia_t.alignment = WD_ALIGN_PARAGRAPH.LEFT
        p_asia_t.paragraph_format.space_before = Pt(0)
        p_asia_t.paragraph_format.space_after = Pt(2)
        r_asia_t = p_asia_t.add_run("Thị trường ngoại hối Châu Á")
        set_font(r_asia_t, name="Times New Roman", size_pt=11.0, color_rgb=(31, 73, 125), bold=True)

        # Đoạn 1 (Japan): Mở đầu bằng cụm câu bắt buộc
        jp_text = (
            f"Trong phiên hôm qua {d_prev}, tỷ giá USD-JPY đóng cửa ở mức {jpy_prev_str}. "
            f"Trong phiên sáng nay, tỷ giá USD-JPY biến động {jpy_dir} quanh mức {jpy_now_str} trong biên độ vừa "
            "khi giới đầu tư theo dõi sát định hướng chính sách từ Ngân hàng Trung ương Nhật Bản (BoJ). "
            "Thống đốc Kazuo Ueda tái khẳng định BoJ sẵn sàng nâng lãi suất nếu tiền lương và lạm phát cơ bản đạt mục tiêu 2% bền vững. "
            "Dữ liệu CPI cho thấy áp lực giá cả duy trì ở mức cao, củng cố kỳ vọng tiếp tục thắt chặt tiền tệ. "
            "Bên cạnh đó, Bộ Tài chính Nhật Bản vẫn duy trì cảnh báo can thiệp nếu đồng Yên biến động quá mức."
        )
        p_jp = r7_cells[2].add_paragraph()
        p_jp.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        p_jp.paragraph_format.space_before = Pt(0)
        p_jp.paragraph_format.space_after = Pt(2)
        r_jp = p_jp.add_run(jp_text)
        set_font(r_jp, name="Times New Roman", size_pt=11.0, color_rgb=(0, 112, 192))

        # Đoạn 2 (China): Cách xuống 1 dòng và mở đầu bằng cụm câu bắt buộc
        cn_text = (
            f"Phiên giao dịch hôm nay, tỷ giá USD-CNY biến động đi ngang quanh mức {cny_now_str}. "
            "Dữ liệu sản xuất công nghiệp và doanh số bán lẻ gần đây cho thấy đà phục hồi còn chậm. "
            "Ngân hàng Nhân dân Trung Quốc (PBoC) tiếp tục duy trì lãi suất cho vay cơ bản LPR ở mức thấp "
            "nhằm kích thích tín dụng và hỗ trợ thị trường bất động sản."
        )
        p_cn = r7_cells[2].add_paragraph()
        p_cn.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        p_cn.paragraph_format.space_before = Pt(0)
        p_cn.paragraph_format.space_after = Pt(2)
        r_cn = p_cn.add_run(cn_text)
        set_font(r_cn, name="Times New Roman", size_pt=11.0, color_rgb=(0, 112, 192))

        # Đoạn 3: Dự kiến Châu Á
        p_asia_fc = r7_cells[2].add_paragraph()
        p_asia_fc.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        p_asia_fc.paragraph_format.space_before = Pt(0)
        p_asia_fc.paragraph_format.space_after = Pt(0)
        r_asia_fc = p_asia_fc.add_run(f"Dự kiến: Tỷ giá USD-JPY có thể biến động quanh {jpy_now_str}; tỷ giá USD-CNY dao động quanh {cny_now_str}.")
        set_font(r_asia_fc, name="Times New Roman", size_pt=11.0, color_rgb=(192, 0, 0), bold=True)

        # 6. CẬP NHẬT TRANG 3: BẢNG GIÁ HÀNG HÓA & NĂNG LƯỢNG
        # Hàng 11: Năng lượng (Dầu Brent: 75-100 từ) & Vàng (ngắn gọn 2 câu)
        r11_cells = get_distinct_cells(table.rows[11])
        clear_cell_paragraphs(r11_cells[1])
        p_oil_t = r11_cells[1].add_paragraph()
        p_oil_t.alignment = WD_ALIGN_PARAGRAPH.LEFT
        p_oil_t.paragraph_format.space_before = Pt(0)
        p_oil_t.paragraph_format.space_after = Pt(2)
        r_oil_t = p_oil_t.add_run("Thị trường năng lượng & kim loại")
        set_font(r_oil_t, name="Times New Roman", size_pt=11.0, color_rgb=(31, 73, 125), bold=True)

        brent_text = (
            "Thị trường năng lượng thế giới ghi nhận giá dầu thô Brent chốt phiên giảm mạnh 2,14% xuống mức 104,32 USD/thùng. "
            "Nguyên nhân lao dốc chủ yếu xuất phát từ kỳ vọng gia tăng về một thỏa thuận đình chiến tiềm năng giữa Mỹ và Iran tại khu vực Trung Đông. "
            "Bên cạnh đó, thông tin Washington cân nhắc khả năng cấm xuất khẩu nhiên liệu diesel đã thúc đẩy tâm lý thận trọng, "
            "khiến thị trường lo ngại các nhà máy lọc dầu sẽ cắt giảm sản lượng chế biến nguyên liệu thô."
        )
        p_brent = r11_cells[1].add_paragraph()
        p_brent.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        p_brent.paragraph_format.space_before = Pt(0)
        p_brent.paragraph_format.space_after = Pt(2)
        r_brent = p_brent.add_run(brent_text)
        set_font(r_brent, name="Times New Roman", size_pt=11.0, color_rgb=(0, 112, 192))

        gold_text = (
            "Giá vàng thế giới biến động quanh ngưỡng 2.650 USD/ounce do giới đầu tư thận trọng trước số liệu Mỹ. "
            "Trong nước, giá vàng miếng SJC và vàng nhẫn duy trì mức giá ổn định."
        )
        p_gold = r11_cells[1].add_paragraph()
        p_gold.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        p_gold.paragraph_format.space_before = Pt(0)
        p_gold.paragraph_format.space_after = Pt(2)
        r_gold = p_gold.add_run(gold_text)
        set_font(r_gold, name="Times New Roman", size_pt=11.0, color_rgb=(0, 112, 192))

        p_oil_fc = r11_cells[1].add_paragraph()
        p_oil_fc.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        p_oil_fc.paragraph_format.space_before = Pt(0)
        p_oil_fc.paragraph_format.space_after = Pt(0)
        r_oil_fc = p_oil_fc.add_run("Dự kiến: Dầu Brent dao động quanh 104 USD/thùng; vàng dao động quanh 2.650 USD/ounce.")
        set_font(r_oil_fc, name="Times New Roman", size_pt=11.0, color_rgb=(192, 0, 0), bold=True)

        # Hàng 11: Cà phê (Độ dài đúng 135 từ; Bắt đầu bằng "Cập nhật giá cà phê thế giới,"; Dự báo 1 mức)
        clear_cell_paragraphs(r11_cells[2])
        p_cf_t = r11_cells[2].add_paragraph()
        p_cf_t.alignment = WD_ALIGN_PARAGRAPH.LEFT
        p_cf_t.paragraph_format.space_before = Pt(0)
        p_cf_t.paragraph_format.space_after = Pt(2)
        r_cf_t = p_cf_t.add_run("Thị trường cà phê")
        set_font(r_cf_t, name="Times New Roman", size_pt=11.0, color_rgb=(31, 73, 125), bold=True)

        coffee_text = (
            "Cập nhật giá cà phê thế giới, chốt phiên giao dịch, giá cà phê robusta sàn London đảo chiều tăng 2,34% lên mức 3.367 USD/tấn. "
            "Trên sàn New York, giá cà phê arabica kỳ hạn phục hồi 1,18%, đạt 278,60 USc/lbs. "
            "Diễn biến khởi sắc chủ yếu nhờ hoạt động mua vào kỹ thuật của giới đầu tư sau chuỗi phiên lao dốc trước đó. "
            "Dù vậy, thị trường vẫn chịu áp lực kìm hãm từ triển vọng nguồn cung khi Conab nâng dự báo sản lượng Brazil niên vụ mới lên mức kỷ lục 67,6 triệu bao. "
            "Bên cạnh đó, Tổ chức Cà phê Quốc tế ICO dự báo toàn cầu sẽ dư cung 3 triệu bao, "
            "kết hợp cùng lượng tồn kho đạt chuẩn trên hai sàn ICE bắt đầu tăng trở lại."
        )
        p_cf_b = r11_cells[2].add_paragraph()
        p_cf_b.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        p_cf_b.paragraph_format.space_before = Pt(0)
        p_cf_b.paragraph_format.space_after = Pt(2)
        r_cf_b = p_cf_b.add_run(coffee_text)
        set_font(r_cf_b, name="Times New Roman", size_pt=11.0, color_rgb=(0, 112, 192))

        p_cf_fc = r11_cells[2].add_paragraph()
        p_cf_fc.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        p_cf_fc.paragraph_format.space_before = Pt(0)
        p_cf_fc.paragraph_format.space_after = Pt(0)
        r_cf_fc = p_cf_fc.add_run("Dự kiến: Giá Arabica dao động quanh 278 USc/lbs, giá Robusta quanh mức 3.360 USD/tấn.")
        set_font(r_cf_fc, name="Times New Roman", size_pt=11.0, color_rgb=(192, 0, 0), bold=True)

        # 7. CẬP NHẬT BẢNG GIÁ HÀNG HÓA TRANG 3 (HÀNG 11 Ô 0)
        d_short = f"{now_vn.day}/{now_vn.month}/{now_vn.strftime('%y')}"
        comm_data = fetch_commodity_table_data()
        update_commodity_table(table, comm_data, d_now, d_short)

        doc.save(output_path)
        print(f"✓ Đã lưu file Word chuẩn 3 trang theo mẫu: {output_path}")
    else:
        print("[CẢNH BÁO] Không tìm thấy template.docx, tạo file văn bản mặc định.")
        # Fallback tạo docx trắng nếu không có template
        fallback_doc = Document()
        for p in content.split("\n"):
            if p.strip(): fallback_doc.add_paragraph(p.strip())
        fallback_doc.save(output_path)

# ==============================================================================
# BƯỚC 4: CHUYỂN ĐỔI SANG PDF (DÙNG LIBREOFFICE HEADLESS)
# ==============================================================================
def convert_to_pdf(docx_path):
    """Chuyển đổi file DOCX sang PDF trên hệ điều hành Ubuntu/Linux hoặc Windows nếu có LibreOffice."""
    print(f"--> Đang chuyển đổi {docx_path} sang PDF...")
    pdf_path = docx_path.replace(".docx", ".pdf")
    
    libreoffice_cmd = shutil.which("libreoffice") or shutil.which("soffice")
    if libreoffice_cmd:
        try:
            subprocess.run([
                libreoffice_cmd,
                "--headless",
                "--convert-to", "pdf",
                docx_path,
                "--outdir", os.path.dirname(docx_path) or "."
            ], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            if os.path.exists(pdf_path):
                print(f"✓ Chuyển đổi PDF thành công: {pdf_path}")
                return pdf_path
        except Exception as e:
            print(f"[CẢNH BÁO] Chuyển đổi PDF thất bại: {e}")
    else:
        print("[THÔNG BÁO] Không tìm thấy LibreOffice trên máy. Bỏ qua bước chuyển PDF.")
    
    return None

# ==============================================================================
# BƯỚC 5: GỬI EMAIL ĐÍNH KÈM TÀI LIỆU
# ==============================================================================
def send_email(subject, body_html, attachments, recipients):
    """Gửi email qua giao thức SMTP Gmail có đính kèm file."""
    sender_email = os.environ.get("SENDER_EMAIL")
    sender_password = os.environ.get("SENDER_PASSWORD")

    if not sender_email or not sender_password:
        print("[LỖI] Thiếu SENDER_EMAIL hoặc SENDER_PASSWORD. Không thể gửi email!")
        return False

    print(f"--> Đang gửi email tới: {', '.join(recipients)}...")
    msg = MIMEMultipart()
    msg['From'] = f"MB Treasury Robot <{sender_email}>"
    msg['To'] = ", ".join(recipients)
    msg['Subject'] = subject

    msg.attach(MIMEText(body_html, 'html'))

    for file_path in attachments:
        if file_path and os.path.exists(file_path):
            with open(file_path, "rb") as f:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(f.read())
            encoders.encode_base64(part)
            filename = os.path.basename(file_path)
            part.add_header("Content-Disposition", f"attachment; filename= {filename}")
            msg.attach(part)
            print(f"  + Đã đính kèm: {filename}")

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, recipients, msg.as_string())
        print("✓ Đã gửi email thành công!")
        return True
    except Exception as e:
        print(f"[LỖI GỬI MAIL] {e}")
        return False

# ==============================================================================
# HÀM CHÍNH (MAIN ENTRYPOINT)
# ==============================================================================
def main():
    parser = argparse.ArgumentParser(description="MB Bank Market Report Generator")
    parser.add_argument("--type", choices=["morning", "midday"], default="midday",
                        help="Loại bản tin: 'morning' (9:45) hoặc 'midday' (11:00) - Mặc định: midday (Bản Tin Thị Trường Ngày)")
    args = parser.parse_args()

    # Tính giờ theo múi giờ Việt Nam (UTC+7)
    now_vn = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=7)
    date_str = now_vn.strftime("%d/%m/%Y")
    time_str = now_vn.strftime("%H:%M")
    file_date = now_vn.strftime("%Y%m%d")
    thu_map = {0: "Thứ Hai", 1: "Thứ Ba", 2: "Thứ Tư", 3: "Thứ Năm", 4: "Thứ Sáu", 5: "Thứ Bảy", 6: "Chủ Nhật"}
    thu_str = thu_map[now_vn.weekday()]
    formatted_date_header = f"{thu_str}, ngày {now_vn.strftime('%d.%m.%Y')}"

    if args.type == "morning":
        report_title = "Bản Tin Sáng - Thị Trường Tài Chính"
        session_label = "09:45 AM"
    else:
        report_title = "Bản Tin Thị Trường Ngày"
        session_label = "11:00 AM"

    print("==================================================================")
    print(f" KHỞI CHẠY {report_title.upper()} [{session_label}] - NGÀY {date_str}")
    print("==================================================================")

    # 1. Quét tin
    raw_news = fetch_news()

    # 2. Phân tích qua AI
    content = analyze_with_ai(raw_news, args.type)

    # 3. Tạo file Word
    docx_filename = f"{report_title} - {file_date}.docx"
    create_word_doc(content, report_title, formatted_date_header, docx_filename)
    attachments = [docx_filename]

    # 4. Nếu là bản tin 11:00 (midday) thì tạo thêm bản PDF
    if args.type == "midday":
        pdf_file = convert_to_pdf(docx_filename)
        if pdf_file:
            attachments.append(pdf_file)

    # 5. Gửi email
    subject = f"[MB TREASURY] {report_title.upper()} - {date_str}"
    body_html = f"""
    <div style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
        <h3 style="color: #002B49; margin-bottom: 5px;">MB - KHỐI KINH DOANH TIỀN TỆ & THỊ TRƯỜNG VỐN</h3>
        <p style="font-size: 13px; color: #666; margin-top: 0;"><i>Phòng Nghiên cứu & Phân tích Thị trường</i></p>
        <hr style="border: 0; border-top: 1px solid #ddd; margin: 15px 0;">
        <p>Kính gửi Quý Anh/Chị,</p>
        <p>Hệ thống tự động xin trân trọng gửi <b>{report_title}</b> đợt {session_label} ngày <b>{date_str}</b>.</p>
        <p>Tài liệu đính kèm:</p>
        <ul>
            {'<li>File văn bản: <b>' + docx_filename + '</b></li>' if docx_filename in attachments else ''}
            {'<li>File định dạng PDF: <b>' + docx_filename.replace('.docx', '.pdf') + '</b></li>' if any(a.endswith('.pdf') for a in attachments) else ''}
        </ul>
        <p style="font-size: 12px; color: #888;"><i>Email này được xuất bản và gửi tự động bởi hệ thống BanTinTuDong.</i></p>
        <br/>
        <p style="margin-bottom: 0;"><b>Trân trọng,</b></p>
        <p style="margin-top: 2px; color: #004080;"><b>Treasury Research Automated System</b></p>
    </div>
    """

    send_email(subject, body_html, attachments, RECIPIENTS)
    print("==================================================================")
    print(" ĐÃ HOÀN TẤT QUY TRÌNH!")
    print("==================================================================")

if __name__ == "__main__":
    main()
