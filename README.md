# HỆ THỐNG TỔNG HỢP BẢN TIN THỊ TRƯỜNG NGÀY (BTTN) - MB TREASURY

Hệ thống tự động hóa 100% xuất bản **Bản Tin Thị Trường Ngày** cho Khối Kinh doanh Tiền tệ & Thị trường Vốn (MB Treasury), tích hợp Google Gemini AI, tự động thu thập số liệu đa nguồn, điền biểu mẫu Word (`.docx`) chuẩn nhận diện thương hiệu MB, chuyển đổi PDF và gửi email định kỳ.

---

## ⏰ Lịch Phát Hành Định Kỳ (Thứ 2 - Thứ 6)
* **11:00 AM (Giờ Việt Nam):** Tự động khởi chạy, quét dữ liệu thị trường, sinh file **Word (`.docx`)** và **PDF (`.pdf`)** chuẩn 3 trang $\rightarrow$ Gửi email tự động tới các hòm thư nội bộ.

---

## 📋 Cấu Trúc Báo Cáo Chuẩn 3 Trang & Giới Hạn Nghiệp Vụ
Báo cáo tuân thủ nghiêm ngặt theo mẫu chuẩn [`template.docx`](template.docx) và quy tắc tại [`SKILL.md`](SKILL.md):

| Trang | Chuyên mục | Nguồn dữ liệu & Chỉ tiêu | Giới hạn độ dài / Format |
| :---: | :--- | :--- | :--- |
| **Trang 1** | **Tỷ giá NHNN** | Web SBV (`sbv.gov.vn`) | Tỷ giá Trung tâm, Trần (+5%), Sàn (-5%), Mua & Bán |
| **Trang 1** | **Tỷ giá MBBank** | API MBBank (`mbbank.com.vn`) | Giá Mua CK / Bán CK ngày $T$ và $T-1$ |
| **Trang 1** | **TT Tiền tệ LNH** | Nghiệp vụ OMO, Lãi suất LNH | **88 – 95 từ**; Nhận định Dự kiến **14 – 16 từ** |
| **Trang 1** | **TT Ngoại hối USD-VND**| Diễn biến tỷ giá liên ngân hàng | **75 – 80 từ**; Nhận định Dự kiến **đúng 12 từ** |
| **Trang 1** | **Biểu đồ thị trường** | 3 Biểu đồ Lãi suất, Tỷ giá, Trái phiếu| Kích thước chuẩn OpenXML (2.10", 2.08", 1.94") |
| **Trang 2** | **USD kết hợp EUR** | DXY, Fed, ECB, Tỷ giá EUR-USD | **150 – 200 từ**; Mở đầu chuẩn phiên $T-1$ và $T$ |
| **Trang 2** | **Nhật Bản (JPY)** | BoJ, Tiền lương, Lạm phát, USD-JPY | **100 – 130 từ**; Mở đầu chuẩn tỷ giá đóng cửa |
| **Trang 2** | **Trung Quốc (CNY)** | PBoC, Tăng trưởng, BĐS, USD-CNY | **50 – 70 từ**; Mở đầu biến động USD-CNY |
| **Trang 2** | **Chỉ số CK Quốc tế** | Dow Jones, Nikkei 225, DAX | Times New Roman 10pt Bold, Xanh tăng / Đỏ giảm |
| **Trang 3** | **Cà phê (Robusta & Arabica)**| Vietnambiz & Sàn quốc tế | **Đúng 135 từ**; Bắt đầu "Cập nhật giá cà phê thế giới," |
| **Trang 3** | **Năng lượng & Kim loại**| Dầu Brent (Vietnambiz) & Giá Vàng| **125 – 135 từ** (Dầu ~95 từ, Vàng ~35 từ đúng 2 câu) |
| **Trang 3** | **Bảng Giá Hàng Hóa** | 18 mặt hàng (TradingEconomics/Yahoo)| Font Times New Roman 8pt Bold, Xanh tăng / Đỏ giảm |

---

## 🛡️ Cơ Chế Bảo Vệ & Chống Rate Limit Tuyệt Đối
1. **Single-Batch Request:** Toàn bộ tin tức trong phiên gom lại thành 1 yêu cầu duy nhất $\rightarrow$ Tiết kiệm tối đa hạn ngạch API.
2. **Cơ chế Thác nước AI (Waterfall Fallback):**
   * Ưu tiên 1: `gemini-3.8-flash`
   * Dự phòng 2: `gemini-3.7-flash`
   * Dự phòng 3: `gemini-3.6-flash`
   * Dự phòng 4: `gemini-3.5-flash`
   * Phao cứu sinh: `gemini-3.5-flash-lite` (500 RPD, 15 RPM)
   * Kế tiếp: `gemini-3.1-flash-lite`, `gemini-3-flash`, `gemini-2.5-flash`, `gemini-1.5-flash`.
   * Tự động bắt mã lỗi `429 (ResourceExhausted)` để đổi model tự động, đảm bảo báo cáo xuất bản đúng giờ.

---

## 🚀 Thiết Lập Chạy Tự Động Trên GitHub Actions

### 1. Cấu hình Secrets trên GitHub
Vào **Settings** $\rightarrow$ **Secrets and variables** $\rightarrow$ **Actions** $\rightarrow$ Nhấn **New repository secret**:
* `GEMINI_API_KEY`: API Key lấy từ [Google AI Studio](https://aistudio.google.com/).
* `SENDER_EMAIL`: Địa chỉ Gmail gửi bản tin (ví dụ: `your-email@gmail.com`).
* `SENDER_PASSWORD`: Mật khẩu ứng dụng 16 ký tự của Gmail (App Password).

### 2. Kiểm tra chạy thử (Manual Trigger)
1. Vào tab **Actions** trên GitHub repository.
2. Chọn workflow **MB Treasury Automated Market Report (BTTN)**.
3. Nhấn **Run workflow** $\rightarrow$ Chọn `midday` $\rightarrow$ Nhấn nút xanh **Run workflow**.
4. Quá trình chạy mất khoảng 1-2 phút; hệ thống sẽ gửi email đính kèm file DOCX và PDF chuẩn nhận diện MB Bank.
