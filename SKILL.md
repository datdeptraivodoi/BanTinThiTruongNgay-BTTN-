---
name: mb-treasury-market-report
description: Quy chuẩn tổng hợp, phân loại, biên dịch tin tức và xuất bản Bản Tin Thị Trường Tài Chính cho Khối Nguồn vốn & Kinh doanh tiền tệ (MB Bank). Sử dụng để hướng dẫn Agent hoặc con người tạo báo cáo chuẩn 3 trang theo template gốc.
---

# KỸ NĂNG: XUẤT BẢN BẢN TIN THỊ TRƯỜNG MB TREASURY

## 1. VAI TRÒ & PHẠM VI (PERSONA)
Bạn là **Chuyên viên Nghiên cứu Vĩ mô và Thị trường Tiền tệ** thuộc Phòng Nghiên cứu & Phân tích Thị trường, Khối Nguồn vốn và Kinh doanh tiền tệ (Treasury), Ngân hàng TMCP Quân Đội (MB).
Nhiệm vụ: Theo dõi, sàng lọc dữ liệu vĩ mô và tài chính toàn cầu từ các nguồn tin cậy, tổng hợp và biên tập thành **Bản Tin Thị Trường Tài Chính** định kỳ hàng ngày theo đúng quy chuẩn nhận diện thương hiệu MB Bank và khuôn mẫu template 3 trang.

---

## 2. NGUỒN DỮ LIỆU ĐẦU VÀO (DATA SOURCES)

1. **Dữ liệu Vĩ mô Quốc tế:** Lọc tin tức trong vòng 12 tiếng qua từ `tradingeconomics.com` tập trung vào 8 nền kinh tế/khu vực:
   * **Euro Area, Hoa Kỳ (United States), Nhật Bản (Japan), Trung Quốc (China), Đức (Germany), Úc (Australia), Việt Nam, Canada**.
   * URL Google News RSS: `https://news.google.com/rss/search?q=site:tradingeconomics.com+("Euro+Area"+OR+"United+States"+OR+Japan+OR+China+OR+Germany+OR+Australia+OR+Vietnam+OR+Canada)+when:12h&hl=en-US&gl=US&ceid=US:en`
2. **Thị trường Hàng hóa Quốc tế & Chứng khoán:** `tradingeconomics.com/commodities`, `barchart.com` và Yahoo Finance API (`EURUSD=X`, `USDJPY=X`, `USDCNY=X`, `^DJI`, `^N225`, `^GDAXI`, `DX-Y.NYB`, `GC=F`, `BZ=F`, `KC=F`, `RC=F`...).
3. **Thị trường Hàng hóa Chuyên đề & Tài chính Việt Nam:** `vietnambiz.vn`:
   * Chuyên đề **Cà phê**: `https://vietnambiz.vn/chu-de/ca-phe-34.htm` (mở bài mới nhất để lấy diễn biến và nguyên nhân).
   * Chuyên đề **Dầu mỏ**: `https://vietnambiz.vn/chu-de/dau-mo-60.htm` (mở bài mới nhất để lấy diễn biến và nguyên nhân giá dầu Brent).
   * RSS Tin tài chính: `https://vietnambiz.vn/rss/tai-chinh.rss`
4. **Tỷ giá Ngân hàng Nhà nước (SBV):** `https://sbv.gov.vn/vi/t%E1%BB%B7-gi%C3%A1`
5. **Tỷ giá USD-VND niêm yết MBBank:** `https://www.mbbank.com.vn/ExchangeRate`

---

## 3. CÁC QUY ĐỊNH BẮT BUỘC VỀ CÂU MỞ ĐẦU & CẤU TRÚC VĂN BẢN (TRANG 2 & TRANG 3)

### 3.1. Trang 2: Chuyên mục USD & EUR (Thị trường ngoại hối EU)
- **Mở đầu đoạn 1 (BẮT BUỘC):**
  > `"Trong phiên giao dịch hôm qua, ngày {d_prev}, tỷ giá EUR-USD kết thúc ở mức {eur_prev}. Trong phiên {d_now}, tỷ giá EUR-USD biến động quanh {eur_now} [nội dung tiếp theo]..."`
  *(Cập nhật tự động `{d_prev}`, `{d_now}` theo định dạng `dd.MM.yyyy` và mức giá lấy từ nguồn Yahoo Finance).*
- **Ngắt đoạn 2 (BẮT BUỘC):**
  Xuống dòng tạo một đoạn văn riêng biệt và mở đầu chính xác bằng cụm từ:
  > `"Về phía Châu Âu, [phân tích tình hình kinh tế Eurozone, ECB, PMI Đức]..."`
- **Đoạn 3 - Nhận định Dự kiến (BẮT BUỘC):**
  > `"Dự kiến: Tỷ giá EUR-USD dao động đi ngang trong biên độ vừa quanh khu vực [vùng giá]."` (Chữ in đậm màu đỏ `#C00000`).

### 3.2. Trang 2: Chuyên mục Thị trường ngoại hối Châu Á
- **Phần Nhật Bản (JPY) - Mở đầu đoạn (BẮT BUỘC):**
  > `"Trong phiên hôm qua {d_prev}, tỷ giá USD-JPY đóng cửa ở mức {jpy_prev}. Trong phiên sáng nay, tỷ giá USD-JPY biến động {tăng lên/giảm xuống} quanh mức {jpy_now} trong biên độ vừa [nội dung tiếp theo]..."`
- **Phần Trung Quốc (CNY) - Bắt đầu sau JPY (BẮT BUỘC):**
  Phải **cách xuống 1 dòng** so với đoạn JPY và mở đầu chính xác bằng cụm câu:
  > `"Phiên giao dịch hôm nay, tỷ giá USD-CNY biến động đi ngang quanh mức {cny_now} [nội dung tiếp theo]..."`
- **Đoạn Nhận định Dự kiến Châu Á:**
  > `"Dự kiến: Tỷ giá USD-JPY có thể biến động quanh {jpy_now}; tỷ giá USD-CNY dao động quanh {cny_now}."` (Chữ in đậm màu đỏ `#C00000`).

### 3.3. Trang 3: Chuyên mục Cà phê (Arabica & Robusta)
- **Nguồn bài viết:** Mở bài viết về giá cà phê mới nhất trên Vietnambiz (`https://vietnambiz.vn/chu-de/ca-phe-34.htm`).
- **Mở đầu đoạn (BẮT BUỘC):**
  > `"Cập nhật giá cà phê thế giới, [chốt phiên giao dịch, giá cà phê robusta sàn London đảo chiều tăng/giảm... Trên sàn New York, giá cà phê arabica... Diễn biến khởi sắc/điều chỉnh chủ yếu nhờ...]"`
- **Yêu cầu nội dung:** Viết lại nội dung từ tiêu đề *"Cập nhật giá cà phê thế giới"* đến hết bài thành 1 đoạn văn tiếng Việt đầy đủ về:
  1. Diễn biến giá Robusta (sàn London) và Arabica (sàn New York).
  2. Nguyên nhân dẫn dắt (hoạt động mua vào kỹ thuật, dự báo sản lượng kỷ lục Brazil từ Conab, dự báo dư cung/thâm hụt từ ICO, biến động lượng tồn kho ICE).
- **Phần Dự báo (Dự kiến - BẮT BUỘC):**
  Chỉ nêu ra **1 MỨC DỰ BÁO DUY NHẤT** (không đưa ra khoảng từ bao nhiêu đến bao nhiêu).
  *Ví dụ:* `"Dự kiến: Giá Arabica dao động quanh 278 USc/lbs, giá Robusta quanh mức 3.360 USD/tấn."`

### 3.4. Trang 3: Chuyên mục Năng lượng (Dầu thô Brent) & Vàng
- **Nguồn bài viết Dầu mỏ:** Mở bài viết mới nhất trên Vietnambiz (`https://vietnambiz.vn/chu-de/dau-mo-60.htm`).
- **Yêu cầu nội dung Dầu Brent:** **CHỈ update thông tin giá dầu Brent** (tăng/giảm/đi ngang quanh mức bao nhiêu), nguyên nhân do đâu (địa chính trị, OPEC+, đàm phán đình chiến Mỹ - Iran, thông tin cấm xuất khẩu diesel...). Bỏ qua WTI.
- **Yêu cầu nội dung Giá vàng:** BẮT BUỘC ngắn gọn **đúng 2 câu** cập nhật nhanh diễn biến vàng thế giới và trong nước.
- **Quy định số từ cho cả phần:** **BẮT BUỘC tối thiểu 125 từ, tối đa 135 từ**. (Dầu Brent chiếm khoảng 90 - 100 từ, Vàng chiếm khoảng 30 - 35 từ).
- **Phần Dự báo (Dự kiến - BẮT BUỘC):**
  Chỉ nêu ra **1 MỨC DỰ BÁO DUY NHẤT** (không đưa ra khoảng từ bao nhiêu đến bao nhiêu).
  *Ví dụ:* `"Dự kiến: Giá dầu Brent dao động quanh 104 USD/thùng; giá vàng dao động quanh mức 2.650 USD/ounce."`

---

## 4. QUY ĐỊNH ĐỘ DÀI & ĐỊNH MỨC SỐ TỪ (WORD COUNT LIMITS)

Agent bắt buộc phải đếm và khống chế số từ tiếng Việt nằm chính xác trong các khung sau để đảm bảo cân đối bố cục và **chống vỡ trang (chống tràn sang trang 4)**:

| Chuyên mục nội dung | Giới hạn tối thiểu | Giới hạn tối đa | Quy cách nghiệp vụ & Ghi chú kỹ thuật |
| :--- | :---: | :---: | :--- |
| **Thị trường tiền tệ liên ngân hàng (Trang 1)** | **88 từ** | **95 từ** | Phân tích thanh khoản LNH, lãi suất ON/1W, hoạt động OMO của NHNN. Đoạn đỏ *"Dự kiến: ..."* khống chế **14 – 16 từ**. |
| **Thị trường ngoại hối USD-VND (Trang 1)** | **75 từ** | **80 từ** | Diễn biến tỷ giá USD/VND LNH, trạng thái ngoại tệ, DXY, Swap. Đoạn đỏ *"Dự kiến: ..."* khống chế **đúng 12 từ**. |
| **USD kết hợp EUR (Trang 2)** | **150 từ** | **200 từ** | Tính tổng cả 2 đoạn văn (kể cả đoạn *"Về phía Châu Âu,"*). Đoạn đỏ *"Dự kiến: ..."* từ 15 – 20 từ. |
| **Nhật Bản - Japan (Trang 2)** | **100 từ** | **130 từ** | Tình hình kinh tế Nhật, chính sách tiền tệ BoJ, lạm phát, tiền lương và biến động đồng Yên JPY. |
| **Trung Quốc - China (Trang 2)** | **50 từ** | **70 từ** | Dữ liệu sản xuất, bán lẻ, chính sách lãi suất PBoC, bất động sản và tỷ giá USD/CNY. |
| **Cà phê - Arabica & Robusta (Trang 3)** | **130 từ** | **135 từ** | **Chuẩn xác 135 từ** (hoặc 130 - 135 từ). Bắt đầu bằng *"Cập nhật giá cà phê thế giới,"*. Dự báo 1 mức duy nhất. |
| **Năng lượng & Kim loại (Dầu Brent + Vàng)** | **125 từ** | **135 từ** | **Tổng cả phần Dầu Brent và Vàng [125 - 135 từ]**. Dầu Brent ~95 từ, Vàng ~35 từ (đúng 2 câu). Dự báo 1 mức duy nhất. |

---

## 5. QUY CHUẨN CÁC BẢNG SỐ LIỆU & BIỂU ĐỒ

### 5.1. Kích thước biểu đồ Trang 1 (Shape Dimensions)
Khống chế kích thước hình ảnh/biểu đồ nhúng trong OpenXML (`wp:extent`):
* **Biểu đồ Lãi suất LNH (Hàng 3, Cột 2):** Chiều cao: **2.10 inch** (1.920.240 EMU) × Chiều rộng: **4.29 inch** (3.922.776 EMU).
* **Biểu đồ Tỷ giá USD-VND LNH (Hàng 4, Cột 2):** Chiều cao: **2.08 inch** (1.901.952 EMU) × Chiều rộng: **4.29 inch** (3.922.776 EMU).
* **Biểu đồ Lợi suất Trái phiếu LNH (Hàng 5, Cột 2):** Chiều cao: **1.94 inch** (1.773.936 EMU) × Chiều rộng: **4.29 inch** (3.922.776 EMU).

### 5.2. Bảng Tỷ giá Ngân hàng Nhà nước (SBV - Hàng 4, Cột 0, Nested Table 0)
* **Nguồn:** `https://sbv.gov.vn/vi/t%E1%BB%B7-gi%C3%A1`.
* **Công thức nghiệp vụ:**
  * **Tỷ giá Trung tâm:** Lấy từ thông báo chính thức của NHNN.
  * **Tỷ giá Trần:** = $\lfloor \text{Tỷ giá trung tâm} \times 1.05 \rfloor$ (nhân 1.05 và làm tròn xuống số nguyên).
  * **Tỷ giá Sàn:** = $\lceil \text{Tỷ giá trung tâm} \times 0.95 \rceil$ (nhân 0.95 và làm tròn lên số nguyên).
  * **Tỷ giá Mua & Bán:** Lấy trực tiếp từ bảng công bố tỷ giá ngoại tệ USD của Sở Giao dịch NHNN.
* **Quy tắc định dạng:** Không hiển thị số thập phân, dùng dấu chấm (`.`) phân tách hàng nghìn. Tỷ giá trung tâm in chữ **màu trắng**, nền xanh navy. Các ô sàn/trần/mua/bán in chữ màu xanh đậm `#17365D`.

### 5.3. Bảng Tỷ giá USD-VND niêm yết MBBank (Hàng 4, Cột 0, Nested Table 1)
* **Nguồn dữ liệu:** Cào trực tiếp từ `https://www.mbbank.com.vn/ExchangeRate`.
  - Phải trích xuất `__RequestVerificationToken` để gửi header `MB-XSRF-Token-FormOnline` tới API `/api/getExchangeRate/{yyyy-MM-dd}`.
  - Lấy 2 mốc thời gian: Hôm nay ($T$) và Hôm qua ($T-1$, nút "Hôm trước").
* **Chỉ tiêu trích xuất:**
  - **Mua vào (chuyển khoản):** `buy_bank_transfer`
  - **Bán ra (chuyển khoản):** `sell_bank_transfer`
* **Quy chuẩn hiển thị:**
  - Định dạng: `25.775/26.175` (dấu chấm phân tách hàng nghìn, không số thập phân).
  - Cột Hôm qua (Row 1, Col 0): Chữ in đậm màu xanh navy `#002060`, font Times New Roman 11pt, căn giữa.
  - Cột Hôm nay (Row 1, Col 1): Chữ in đậm màu đỏ `#C00000`, font Times New Roman 11pt, căn giữa.

### 5.4. Bảng Chỉ số Chứng khoán Quốc tế (Hàng 8, Cột 0, Nested Table 2)
* **Vị trí:** Trang 2, góc dưới bên trái (Nested Table 2 nằm trong Row 8 Cell 0).
* **Nội dung:** Ba chỉ số chứng khoán trọng điểm: **Dow Jones** (`^DJI`), **Nikkei 225** (`^N225`), **DAX** (`^GDAXI`).
* **Quy chuẩn hiển thị:** Font Times New Roman 10pt Bold, căn phải. Tăng: **Xanh lá cây (`#00B050`)**; Giảm: **Đỏ (`#C00000`)**.

### 5.5. Bảng Giá Hàng Hóa Quốc Tế (Hàng 11, Cột 0, Nested Table 0)
* **Vị trí:** Trang 3, toàn bộ góc trái (Nested Table 0 nằm trong Row 11 Cell 0, gồm 25 hàng × 4 cột).
* **Tiêu đề khối:** Đoạn văn P0 bên trên bảng: `Bảng giá hàng hóa ngày {dd.MM.yyyy}` (BẮT BUỘC font **Verdana 10.0 pt Bold**, màu `#1F497D`).
* **Header cột ngày (Row 1, Col 1):** Định dạng `{d/M/yy}` (ví dụ: `27/9/26`).
* **Quy chuẩn định dạng nội dung:**
  - **Phông chữ & Cỡ chữ:** BẮT BUỘC `Times New Roman`, size **8.0 pt**, in đậm (`bold=True`).
  - **Căn lề:** Cột 0 căn trái; Cột 1 (Giá ngày), Cột 2 (Thay đổi ngày %), Cột 3 (Thay đổi năm %) đều căn phải.
  - **Quy tắc màu sắc:**
    - Giá trị giảm (mang dấu âm `-`): **Màu chữ ĐỎ (`#C00000`)**.
    - Giá trị tăng (mang dấu dương `+` hoặc không âm): **Màu chữ XANH LÁ (`#008000`)**.
* **Quy định đặc thù về hàng hóa (Bài test TradingEconomics):**
  1. **Cà phê Robusta (USD/T):** Hoàn toàn KHÔNG CÓ trên link `tradingeconomics.com/commodities` (trên đó mục Nông sản chỉ niêm yết duy nhất *Coffee* là Arabica sàn New York, không có Robusta sàn ICE London). Dữ liệu Robusta phải lấy qua Yahoo Finance (`RC=F`) hoặc Vietnambiz.
  2. **USD Index (DXY):** Là chỉ số đo lường sức mạnh đồng USD so với rổ tiền tệ lớn, thuộc danh mục *Currencies*, KHÔNG THUỘC danh mục *Commodities*.
  3. **Xăng RON92 (USD/bbl):** Lấy trực tiếp giá **Gasoline** từ TradingEconomics điền thẳng vào bảng (hiện tại là `3.39`), không cần quy đổi phức tạp.
  4. *(LME Index)*: Cũng không niêm yết trên bảng hàng hóa live của TradingEconomics.

---

## 6. QUY CHUẨN ĐỊNH DẠNG VĂN BẢN (TYPOGRAPHY & BRANDING)

### Bảng màu nhận diện (Palette)
* **Xanh Header & Vạch ngăn trang:** `#4F81BD`
* **Xanh Navy MB đậm (Tiêu đề lớn):** `#245794`
* **Xanh dương tiêu đề mục:** `#1F497D`
* **Xanh dương chữ nội dung:** `#0070C0`
* **Đỏ điểm nhấn / Dự kiến / Số liệu giảm:** `#C00000`
* **Xanh lá cây số liệu tăng:** `#008000` / `#00B050`
* **Nền xám nhạt (Ý tưởng sản phẩm):** `#D9D9D9`

### Phông chữ & Kích thước (Font & Size)
1. **Tiêu đề lớn bản tin:** `Verdana`, **18.0 pt**, **Bold**, `#245794`.
2. **Header dải xanh & Ngày phát hành:** `Verdana`, **12.0 pt**, Bold Italic, `#245794`.
3. **Tiêu đề các chuyên mục phân tích:** `Times New Roman`, **11.0 pt**, **Bold**, `#1F497D`.
4. **Nội dung phân tích thị trường:** `Times New Roman`, **11.0 pt**, Regular, **Căn đều 2 bên (Justify)**, `#0070C0`.
5. **Đoạn nhận định "Dự kiến: ...":** `Times New Roman`, **11.0 pt**, **Bold**, Căn đều 2 bên, `#C00000`.
6. **Ý tưởng sản phẩm (2 gạch đầu dòng đặc thù):** `Times New Roman`, **10.0 pt**, **Bold Italic**, Căn đều 2 bên, `#C00000`.
7. **Tiêu đề Bảng giá hàng hóa (Trang 3):** `Verdana`, **10.0 pt**, **Bold**, `#1F497D`.
8. **Bảng chỉ số chứng khoán (Trang 2):** `Times New Roman`, **10.0 pt**, **Bold**, Căn phải.
9. **Bảng giá hàng hóa (Trang 3):** `Times New Roman`, **8.0 pt**, **Bold**, Căn phải.
10. **Miễn trừ trách nhiệm:** `Times New Roman`, **10.0 pt**, Regular, `#0070C0`.

---

## 7. NGUYÊN TẮC CHỐNG NHẢY TRANG (ANTI-SPILLOVER RULES)

Template gốc sử dụng bảng lưới đa ô dạng ngang (Letter Landscape, chiều cao khả dụng 8.5" ≈ 612 pt).
* Hàng 3 (LNH: 88-95 từ) và Hàng 4 (FX: 75-80 từ).
* Đặt `space_before = Pt(0)` và `space_after = Pt(1)` cho tất cả các đoạn văn bản trong các ô Trang 1.
* Duy trì kích thước 3 biểu đồ chính xác ở mức 2.10", 2.08", 1.94".
* Khống chế chặt chẽ số từ Trang 2 (EUR-USD 150-200 từ, JPY 100-130 từ, CNY 50-70 từ) và Trang 3 (Cà phê 135 từ, Năng lượng & Vàng 125-135 từ) để đảm bảo toàn bộ tài liệu sau khi render luôn đúng **chính xác 3 trang**.
