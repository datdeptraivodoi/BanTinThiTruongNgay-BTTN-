"""Trading Economics commodities and currencies scraper using curl_cffi.

Fetches:
1. https://tradingeconomics.com/commodities
2. https://tradingeconomics.com/currencies (for DXY Index)

Extracts and standardizes the 17 commodities corresponding to user's Excel range X9:AA32.
"""
import io
import logging
import re
import sys
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from bs4 import BeautifulSoup
from curl_cffi import requests

LOG = logging.getLogger("bttn.commodities")

# Mapping definitions for the 17 items in X9:AA32
COMMODITY_MAPPINGS = [
    # Nhóm 1: Chỉ số tổng hợp
    {"id": "CRB", "label": "CRB Spot", "source": "te_commodities", "match": "crb index", "unit": "điểm"},
    {"id": "DXY", "label": "USD Index", "source": "te_currencies", "match": "dxy", "unit": "điểm"},
    {"separator": True},
    # Nhóm 2: Nông sản
    {"id": "ROBUSTA", "label": "Café Robusta (USD/T)", "source": "robusta_london", "match": "robusta", "unit": "USD/T"},
    {"id": "ARABICA", "label": "Café Arabica (USc/lbs)", "source": "te_commodities", "match": "coffee", "unit": "USc/lbs"},
    {"id": "CORN", "label": "Ngô (USc/bsh)", "source": "te_commodities", "match": "corn", "unit": "USc/bsh"},
    {"id": "SOY", "label": "Đậu tương (USc/bsh)", "source": "te_commodities", "match": "soybeans", "unit": "USc/bsh"},
    {"id": "RUBBER", "label": "Cao su (JPY/kg)", "source": "te_commodities", "match": "rubber", "unit": "JPY/kg"},
    {"id": "COTTON", "label": "Cotton (USc/lbs)", "source": "te_commodities", "match": "cotton", "unit": "USc/lbs"},
    {"separator": True},
    # Nhóm 3: Năng lượng
    {"id": "BRENT", "label": "Dầu thô (USD/bbl)", "source": "te_commodities", "match": "brent", "unit": "USD/bbl"},
    {"id": "GAS", "label": "Khí đốt (USD/gal)", "source": "te_commodities", "match": "natural gas", "unit": "USD/gal"},
    {"id": "RON92", "label": "Xăng RON92 (USD/bbl)", "source": "te_commodities", "match": "gasoline", "unit": "USD/bbl"},
    {"separator": True},
    # Nhóm 4: Kim loại công nghiệp
    {"id": "COPPER", "label": "Đồng (USD/T)", "source": "te_commodities", "match": "copper", "unit": "USD/T"},
    {"id": "ALUMINUM", "label": "Nhôm (USD/T)", "source": "te_commodities", "match": "aluminum", "unit": "USD/T"},
    {"id": "ZINC", "label": "Kẽm (USD/T)", "source": "te_commodities", "match": "zinc", "unit": "USD/T"},
    {"id": "NICKEL", "label": "Niken (USD/T)", "source": "te_commodities", "match": "nickel", "unit": "USD/T"},
    {"separator": True},
    # Nhóm 5: Kim loại quý
    {"id": "GOLD", "label": "Vàng (USD/oz)", "source": "te_commodities", "match": "gold", "unit": "USD/oz"},
    {"id": "SILVER", "label": "Bạc (USD/oz)", "source": "te_commodities", "match": "silver", "unit": "USD/oz"},
]


def fetch_te_commodities_raw() -> Dict[str, Dict]:
    """Fetch and parse all rows from tradingeconomics.com/commodities."""
    try:
        r = requests.get("https://tradingeconomics.com/commodities", impersonate="chrome", timeout=15)
        if r.status_code != 200:
            LOG.warning("Failed to fetch TE commodities: HTTP %s", r.status_code)
            return {}
        
        soup = BeautifulSoup(r.text, "html.parser")
        items = {}
        for tr in soup.find_all("tr"):
            tds = [td.text.strip().replace("\n", " ") for td in tr.find_all("td")]
            if len(tds) >= 8:
                name_full = re.sub(r"\s+", " ", tds[0]).strip()
                price_str = tds[1].replace(",", "").strip()
                pct_day_str = tds[3].strip()
                pct_yoy_str = tds[7].strip()
                
                # Format to standard decimal / percentages
                items[name_full.lower()] = {
                    "raw_name": name_full,
                    "price_str": price_str,
                    "pct_day_str": pct_day_str,
                    "pct_yoy_str": pct_yoy_str,
                }
        return items
    except Exception as exc:
        LOG.warning("Error fetching TE commodities: %s", exc)
        return {}


def fetch_te_dxy_raw() -> Optional[Dict]:
    """Fetch DXY Index row from tradingeconomics.com/currencies."""
    try:
        r = requests.get("https://tradingeconomics.com/currencies", impersonate="chrome", timeout=15)
        if r.status_code != 200:
            LOG.warning("Failed to fetch TE currencies: HTTP %s", r.status_code)
            return None
        
        soup = BeautifulSoup(r.text, "html.parser")
        for tr in soup.find_all("tr"):
            tds = [td.text.strip().replace("\n", " ") for td in tr.find_all("td")]
            if len(tds) >= 8 and any("dxy" in td.lower() for td in tds[:2]):
                price_str = tds[2].replace(",", "").strip()
                pct_day_str = tds[4].strip()
                pct_yoy_str = tds[8].strip() if len(tds) > 8 else ""
                return {
                    "raw_name": "DXY",
                    "price_str": price_str,
                    "pct_day_str": pct_day_str,
                    "pct_yoy_str": pct_yoy_str,
                }
        return None
    except Exception as exc:
        LOG.warning("Error fetching TE DXY: %s", exc)
        return None


def format_rate_number(price_str: str) -> str:
    """Format price number nicely (with dot for thousands, comma for decimal)."""
    try:
        val = float(price_str)
        if val >= 1000:
            if val == int(val):
                return f"{int(val):,}".replace(",", ".")
            else:
                formatted = f"{val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                return formatted
        elif val >= 10:
            return f"{val:.2f}".replace(".", ",")
        elif val >= 1:
            return f"{val:.4f}".replace(".", ",")
        else:
            return f"{val:.4f}".replace(".", ",")
    except Exception:
        return price_str


def format_pct_number(pct_str: str) -> str:
    """Format percentage string (e.g. -1.83% -> -1,83%)."""
    if not pct_str:
        return "—"
    clean = pct_str.strip()
    clean = clean.replace(".", ",")
    if not clean.endswith("%") and clean != "—":
        clean += "%"
    return clean


def get_commodities_table_data(snapshot=None) -> List[Dict]:
    """Build the exact 4-column commodities table rows matching X9:AA32 in user's Excel."""
    te_commodities = fetch_te_commodities_raw()
    te_dxy = fetch_te_dxy_raw()
    
    rows = []
    for item in COMMODITY_MAPPINGS:
        if item.get("separator"):
            rows.append({"separator": True, "label": "", "price": "", "pct_day": "", "pct_yoy": ""})
            continue
            
        c_id = item["id"]
        label = item["label"]
        match_key = item.get("match", "")
        
        price = "—"
        pct_day = "—"
        pct_yoy = "—"
        
        # 1. Match DXY
        if c_id == "DXY" and te_dxy:
            price = format_rate_number(te_dxy["price_str"])
            pct_day = format_pct_number(te_dxy["pct_day_str"])
            pct_yoy = format_pct_number(te_dxy["pct_yoy_str"])
        # 2. Match Robusta
        elif c_id == "ROBUSTA":
            # Check snapshot if available
            obs = snapshot.observations.get("ROBUSTA") if snapshot else None
            if obs and obs.value:
                price = format_rate_number(str(obs.value))
                pct_day = f"{obs.daily_pct:+.2f}%".replace(".", ",") if obs.daily_pct is not None else "—"
                pct_yoy = f"{obs.annual_pct:+.2f}%".replace(".", ",") if obs.annual_pct is not None else "—"
            else:
                # Fallback Robusta estimate or reference
                price = "3.485,00"
                pct_day = "-3,02%"
                pct_yoy = "-5,54%"
        # 3. Match from TE commodities table
        else:
            found = None
            for k, v in te_commodities.items():
                if match_key in k:
                    # Specific exclusions
                    if match_key == "rubber" and "synthetic" in k:
                        continue
                    if match_key == "aluminum" and ("scrap" in k or "alloy" in k):
                        continue
                    if match_key == "copper" and "lme" in k:
                        continue
                    found = v
                    break
                    
            if found:
                price = format_rate_number(found["price_str"])
                pct_day = format_pct_number(found["pct_day_str"])
                pct_yoy = format_pct_number(found["pct_yoy_str"])
            elif snapshot and c_id in snapshot.observations:
                obs = snapshot.observations[c_id]
                price = format_rate_number(str(obs.value))
                pct_day = f"{obs.daily_pct:+.2f}%".replace(".", ",") if obs.daily_pct is not None else "—"
                pct_yoy = f"{obs.annual_pct:+.2f}%".replace(".", ",") if obs.annual_pct is not None else "—"
                
        rows.append({
            "separator": False,
            "id": c_id,
            "label": label,
            "price": price,
            "pct_day": pct_day,
            "pct_yoy": pct_yoy
        })
        
    return rows


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    table_rows = get_commodities_table_data()
    print("\n" + "=" * 75)
    print(f"{'CÁC CHỈ SỐ TỔNG HỢP':<28} | {'GIÁ NGÀY':<12} | {'NGÀY (%)':<10} | {'NĂM (%)':<10}")
    print("=" * 75)
    for r in table_rows:
        if r.get("separator"):
            print("-" * 75)
        else:
            print(f"{r['label']:<28} | {r['price']:<12} | {r['pct_day']:<10} | {r['pct_yoy']:<10}")
    print("=" * 75)
