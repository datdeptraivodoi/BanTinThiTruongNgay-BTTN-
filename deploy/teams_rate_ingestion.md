# Hướng dẫn tích hợp tự động dữ liệu từ phòng Microsoft Teams "MARKET RATE FX ITB"

Tài liệu này hướng dẫn cách cấu hình Microsoft Power Automate (trong gói Microsoft 365 của MB Bank) để tự động bắt tin nhắn báo giá tỷ giá & lãi suất SWAP từ phòng Teams **MARKET RATE FX ITB** và đồng bộ về GitHub Actions cho pipeline BTTN.

---

## 1. Định dạng tin nhắn Trader tại phòng Teams

Traders thường gửi các dòng tin nhắn theo một trong các định dạng sau:

```text
MARKET RATE FX ITB - 08/10/2026
USD/VND: 26005 / 26015
SWAP:
ON: -2,30 / -1,80
1W: -1,00 / -0.50
2W: -0,30 / 0,20
1M: 0,70 / 1,20
3M: 2,00 / 2,50
6M: 2,60 / 3,10
```

*Hệ thống pipeline BTTN đã được trang bị bộ parser linh hoạt (regex tokenized) tự động nhận diện:*
- Cặp tỷ giá USD/VND Interbank (ví dụ `26005 / 26015` hoặc `26005 26015`).
- Các kỳ hạn SWAP: `ON`, `1W`, `2W`, `1M`, `3M`, `6M`.
- Hỗ trợ cả dấu phẩy `,` và dấu chấm `.` thập phân, số âm có dấu trừ `-` hoặc trong ngoặc đơn `(2.30)`.

---

## 2. Thiết lập Power Automate Flow (Cách chuẩn hóa đề xuất)

### Bước 1: Tạo Automated Cloud Flow
1. Truy cập [Power Automate](https://make.powerautomate.com/) bằng tài khoản MB Bank.
2. Chọn **Create** > **Automated cloud flow**.
3. Đặt tên flow: `BTTN - Sync Swap Rates from Teams MARKET RATE FX ITB`.
4. Chọn Trigger: **When a new channel message is added** (Microsoft Teams).

### Bước 2: Cấu hình Trigger
- **Team**: Chọn Team chứa phòng chat Treasury / FX Interbank.
- **Channel**: Chọn kênh `MARKET RATE FX ITB`.

### Bước 3: Điều kiện lọc (Condition)
- Thêm bước **Condition**:
  - `Message body content` contains `ON`
  - AND `Message body content` contains `1W`
  *(Giúp flow chỉ kích hoạt khi có báo giá SWAP, bỏ qua các tin chat thảo luận thông thường)*.

### Bước 4: Đẩy dữ liệu về GitHub (Chọn 1 trong 2 phương án)

#### Phương án A: Cập nhật file qua GitHub REST API (Khuyên dùng)
- Thêm action **HTTP**:
  - **Method**: `PUT`
  - **URI**: `https://api.github.com/repos/datdeptraivodoi/BanTinThiTruongNgay-BTTN-/contents/data/swap_data_today.json`
  - **Headers**:
    ```json
    {
      "Authorization": "Bearer <GITHUB_PERSONAL_ACCESS_TOKEN>",
      "Accept": "application/vnd.github.v3+json",
      "User-Agent": "PowerAutomate-BTTN"
    }
    ```
  - **Body**:
    ```json
    {
      "message": "Update swap rates from Teams MARKET RATE FX ITB",
      "content": "<base64 encoded content of the message or JSON>"
    }
    ```

#### Phương án B: Cập nhật qua Secret Gist & biến `SWAP_DATA_URL` (Bảo mật & Không sinh commit thừa)
1. Trên GitHub cá nhân, tạo một **Secret Gist** có tên `swap_data_today.json`.
2. Lấy link Raw URL của Gist: `https://gist.githubusercontent.com/datdeptraivodoi/<GIST_ID>/raw/swap_data_today.json`.
3. Vào repo GitHub: **Settings** > **Secrets and variables** > **Actions** > Thêm Secret hoặc Variable `SWAP_DATA_URL` trỏ tới Raw URL trên.
4. Trong Power Automate, thêm action **HTTP** gọi API cập nhật Gist:
   - **Method**: `PATCH`
   - **URI**: `https://api.github.com/gists/<GIST_ID>`
   - **Headers**:
     ```json
     {
       "Authorization": "Bearer <GITHUB_PERSONAL_ACCESS_TOKEN>",
       "Accept": "application/vnd.github.v3+json",
       "User-Agent": "PowerAutomate-BTTN"
     }
     ```
   - **Body**:
     ```json
     {
       "files": {
         "swap_data_today.json": {
           "content": "{\"date\": \"@{utcNow('yyyy-MM-dd')}\", \"text\": \"@{triggerOutputs()?['body/body/content']}\"}"
         }
       }
     }
     ```

---

## 3. Cơ chế Fallback an toàn

Trong trường hợp sáng hôm đó trader chưa kịp gửi tin vào phòng Teams trước giờ phát hành (11:30–12:00), hệ thống:
1. Tự động đọc dữ liệu từ `config/swap_rates.json` làm báo giá tham khảo.
2. Điền đầy đủ số liệu vào bảng Lãi suất SWAP trong file Word/PDF (không để trống hay gạch ngang `—`).
3. Ghi log cảnh báo `TRADER_SWAP_REFERENCE` trong báo cáo manifest để kiểm toán nội bộ.
