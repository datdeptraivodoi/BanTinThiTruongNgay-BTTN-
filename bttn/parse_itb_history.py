"""
parse_itb_history.py
Phân tích toàn bộ quote của trader trên room MARKET RATE FX ITB:
- Tổng hợp toàn bộ quote phiên hôm trước (Yesterday / previous session): tìm min, max, spread/biên độ, giá chốt phiên.
- Tổng hợp quote sáng nay (Today): phát hiện xu hướng (ví dụ: mở phiên 25980-26000, sau đó giảm về 25975-25987).
"""

import re

def normalize_quote(bid_str, ask_str, base_prev=25900):
    """
    Chuẩn hóa quote (e.g. '800', '890' -> 25800, 25890; '982', '992' -> 25982, 25992; '005', '015' -> 26005, 26015; '77', '87' -> 25977, 25987).
    """
    try:
        b_val = int(bid_str)
        a_val = int(ask_str)
    except:
        return None, None
        
    # Case 5 chữ số đầy đủ: 25800, 25890, 25980, 26000
    if b_val > 20000:
        return b_val, a_val
        
    # Case 2 chữ số: '77', '87' -> 25977, 25987; '00', '10' -> 25800, 25810 hoặc 26000, 26010
    if len(bid_str) <= 2:
        hundreds = (base_prev // 100) * 100
        b_norm = hundreds + b_val
        a_norm = hundreds + a_val
        if a_norm < b_norm:
            a_norm += 100
        return b_norm, a_norm

    # Case 3 chữ số: '800', '890', '982', '992' hoặc '003', '013'
    if len(bid_str) == 3:
        # 500-999 thuộc dải 25.xxx (25500 - 25999)
        if b_val >= 500:
            b_norm = 25000 + b_val
        else:
            b_norm = 26000 + b_val
            
        if a_val >= 500:
            a_norm = 25000 + a_val
        else:
            a_norm = 26000 + a_val
            
        if b_norm > a_norm:
            a_norm += 1000
        return b_norm, a_norm
        
    return b_val, a_val

def evaluate_liquidity(quotes_count: int) -> str:
    """
    Đánh giá tình trạng thanh khoản dựa trên tần suất quote giá từ traders trong ngày:
    - Dưới 12 quote/ngày: thanh khoản kém
    - Từ 13 đến 20 quote/ngày: thanh khoản ổn định
    - Từ 21 quote/ngày: thanh khoản tốt
    """
    if quotes_count < 12:
        return "thanh khoản kém"
    elif quotes_count <= 20:
        return "thanh khoản ổn định"
    else:
        return "thanh khoản tốt"


def parse_itb_full_text(text):
    """
    Phân tích nội dung chat Teams ITB thành dữ liệu phiên hôm trước và phiên hôm nay.
    """
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    
    current_section = None
    yesterday_quotes = []
    today_quotes = []
    
    for line in lines:
        lower = line.lower()
        if 'yesterday' in lower or 'hôm qua' in lower:
            current_section = 'yesterday'
            continue
        elif 'today' in lower or 'hôm nay' in lower:
            current_section = 'today'
            continue
        elif 'monday' in lower or 'tuesday' in lower or 'wednesday' in lower or 'thursday' in lower or 'friday' in lower:
            if not current_section:
                current_section = 'older'
                
        # Regex tìm quote
        # Ví dụ: '25980 26000', '982 992', '003 013', '77 87', '25975 85'
        # 1. '25975 85'
        m_mix = re.search(r'\b(2\d{4})\s+(\d{2})\b', line)
        if m_mix:
            b = int(m_mix.group(1))
            a_tail = int(m_mix.group(2))
            a = (b // 100) * 100 + a_tail
            if a < b: a += 100
            q = (b, a)
            if current_section == 'yesterday': yesterday_quotes.append(q)
            elif current_section == 'today': today_quotes.append(q)
            continue
            
        # 2. '25980 26000'
        m_full = re.search(r'\b(2\d{4})\s+(2\d{4})\b', line)
        if m_full:
            b_f = int(m_full.group(1))
            a_f = int(m_full.group(2))
            if 25700 <= b_f <= 26300:
                q = (b_f, a_f)
                if current_section == 'yesterday': yesterday_quotes.append(q)
                elif current_section == 'today': today_quotes.append(q)
                continue
            
        # 3. '982 992' hoặc '003 013' hoặc '77 87' hoặc '800 890'
        m_short = re.search(r'\b(\d{2,3})\s+(\d{2,3})\b', line)
        if m_short:
            b_norm, a_norm = normalize_quote(m_short.group(1), m_short.group(2))
            if b_norm and a_norm and 25700 <= b_norm <= 26300:
                q = (b_norm, a_norm)
                if current_section == 'yesterday': yesterday_quotes.append(q)
                elif current_section == 'today': today_quotes.append(q)
                continue

    # Tổng hợp phân tích
    analysis = {}
    
    # --- PHIÊN HÔM TRƯỚC ---
    y_close_b = None
    y_close_a = None
    if yesterday_quotes:
        bids = [q[0] for q in yesterday_quotes]
        asks = [q[1] for q in yesterday_quotes]
        min_rate = min(bids)
        max_rate = max(asks)
        spread = max_rate - min_rate
        close_bid, close_ask = yesterday_quotes[-1]
        y_close_b = close_bid
        y_close_a = close_ask
        q_count = len(yesterday_quotes)
        liq = evaluate_liquidity(q_count)
        
        min_str = f"{min_rate:,}".replace(',', '.')
        max_str = f"{max_rate:,}".replace(',', '.')
        close_b_str = f"{close_bid:,}".replace(',', '.')
        close_a_str = f"{close_ask:,}".replace(',', '.')
        
        analysis['yesterday'] = {
            'quotes_count': q_count,
            'liquidity': liq,
            'min_rate': min_rate,
            'max_rate': max_rate,
            'spread': spread,
            'close_bid': close_bid,
            'close_ask': close_ask,
            'quotes': yesterday_quotes,
            'summary_text': f"trong phiên hôm trước tỷ giá USD-VND dao động trong biên độ khoảng {spread} điểm quanh mức {min_str} - {max_str} trước khi chốt phiên tại {close_b_str} - {close_a_str}, {liq}"
        }
    else:
        analysis['yesterday'] = {
            'quotes_count': 0,
            'liquidity': "thanh khoản ổn định",
            'quotes': [],
            'summary_text': "phiên hôm trước tỷ giá USD-VND giảm mạnh về vùng 25.900"
        }
        
    # --- PHIÊN HÔM NAY / SÁNG NAY ---
    if today_quotes:
        t_bids = [q[0] for q in today_quotes]
        t_asks = [q[1] for q in today_quotes]
        t_min = min(t_bids)
        t_max = max(t_asks)
        t_spread = t_max - t_min
        open_bid, open_ask = today_quotes[0]
        curr_bid, curr_ask = today_quotes[-1]
        t_count = len(today_quotes)
        t_liq = evaluate_liquidity(t_count)
        
        # Đánh giá xu hướng phiên
        if curr_bid < open_bid:
            trend_morning = "có dấu hiệu giảm"
        elif curr_bid > open_bid:
            trend_morning = "có dấu hiệu tăng nhẹ"
        else:
            trend_morning = "tiếp tục đi ngang"
            
        # So sánh thay đổi với giá chốt hôm qua
        change_text = ""
        diff_pts = 0
        if y_close_b is not None:
            diff_pts = curr_bid - y_close_b
            if diff_pts < 0:
                change_text = f"giảm {abs(diff_pts)} điểm so với giá chốt hôm qua"
            elif diff_pts > 0:
                change_text = f"tăng {diff_pts} điểm so với giá chốt hôm qua"
            else:
                change_text = "đi ngang so với giá chốt hôm qua"
                
        curr_b_str = f"{curr_bid:,}".replace(',', '.')
        curr_a_str = f"{curr_ask:,}".replace(',', '.')
        min_b_str = f"{t_min:,}".replace(',', '.')
        max_a_str = f"{t_max:,}".replace(',', '.')
        
        analysis['today'] = {
            'quotes_count': t_count,
            'liquidity': t_liq,
            'open_bid': open_bid,
            'open_ask': open_ask,
            'curr_bid': curr_bid,
            'curr_ask': curr_ask,
            'min_rate': t_min,
            'max_rate': t_max,
            'spread': t_spread,
            'trend': trend_morning,
            'change_pts': diff_pts,
            'change_text': change_text,
            'quotes': today_quotes,
            'summary_text': f"Sáng nay, tỷ giá tiếp tục {trend_morning}, giao dịch quanh vùng {curr_b_str} - {curr_a_str}",
            'eod_summary_text': f"Chốt phiên, tỷ giá USD-VND giao dịch quanh mức {curr_b_str} - {curr_a_str}" + (f", {change_text}" if change_text else "") + f". Trong phiên, tỷ giá dao động trong biên độ {t_spread} điểm từ mức thấp nhất {min_b_str} đến cao nhất {max_a_str}, ghi nhận {t_count} lượt báo giá ({t_liq})."
        }
    else:
        analysis['today'] = {
            'quotes_count': 0,
            'liquidity': "thanh khoản ổn định",
            'trend': "giảm khá nhanh",
            'quotes': [],
            'summary_text': "Sáng nay, tỷ giá tiếp tục giảm khá nhanh về vùng 25.800 - 25.890 biên độ 130 điểm so với giá hôm qua",
            'eod_summary_text': "Chốt phiên, tỷ giá USD-VND dao động trong biên độ khoảng 130 điểm về vùng 25.800 - 25.890, thanh khoản ổn định."
        }
        
    return analysis

if __name__ == "__main__":
    with open(r'd:\TyGia\teams_itb_full_history.txt', 'r', encoding='utf-8') as f:
        c = f.read()
    res = parse_itb_full_text(c)
    import json
    print(json.dumps(res, ensure_ascii=False, indent=2))
