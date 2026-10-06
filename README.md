# Bản tin thị trường ngày BTTN

Pipeline Python tạo bản tin từ dữ liệu có nguồn, nội dung AI dạng JSON và template Word. Workflow bắt đầu **12:00 giờ Việt Nam, thứ Hai–thứ Sáu** (`05:00 UTC`). Đây là giờ trigger; GitHub Actions có thể xếp hàng nên không bảo đảm email đến đúng 12:00.

## Các thay đổi

- `bttn/sources.py`, `vira.py`: thu thập, lưu bản gốc và hash; mỗi số có nguồn, thời điểm, đơn vị, ngày giao dịch và cách tính.
- VIRA Market Watch: chọn ấn bản theo thời gian xuất bản, bỏ bài cũ ghim đầu trang và bài sau cutoff. Bảng ảnh được OCR cục bộ ở bốn cấu hình; một ô chỉ được dùng khi ít nhất hai lần đọc đồng ý và không có kết quả trái nhau. OCR đồng thuận vẫn không thay thế kiểm tra nghiệp vụ; thay đổi bố cục nguồn có thể khiến bản tin bị chặn.
- VNIBOR VND/USD, SOFR và lợi suất trái phiếu lấy từ VIRA. Lợi suất trái phiếu hiện là chuẩn **10 năm theo quốc gia**, không tự tạo đường cong nhiều kỳ hạn.
- Swap tham khảo = **lãi suất VND − lãi suất USD**, cùng kỳ hạn và cùng ấn bản, đơn vị **điểm phần trăm**. VND có ngày fixing riêng; USD ghi Last trong ấn bản, không khẳng định hai fixing cùng thời điểm. Đây không phải báo giá FX swap mua/bán.
- AI chỉ tạo nội dung theo schema. Các số trong văn xuôi phải dùng `{{OBSERVATION_ID}}`; Python thay bằng giá trị đã kiểm tra. Giới hạn từ, nguồn, cấu trúc đoạn và việc tạm dừng dự báo đều được kiểm tra trước khi dựng file.
- Mọi ô dữ liệu, bảng và biểu đồ động cũ trong template bị xóa trước khi dựng lại. Không dùng số mẫu làm fallback. Nội dung AI thực sự xuất hiện trong Word.
- Biến động ngày so với phiên hoàn tất liền trước, không dùng `chartPreviousClose` của toàn khoảng tải. Biến động năm ghi rõ **YoY**.
- Phần dự báo và ý tưởng sản phẩm để trống. Thiếu nguồn cho mục tùy chọn thì hiện `—`; thiếu dữ liệu bắt buộc thì dừng và không gửi email.
- PDF phải được xuất mới qua LibreOffice, đủ ba trang và không còn placeholder. Gửi thất bại trả mã lỗi. Ledger ngăn tự gửi lại khi SMTP có kết quả không chắc chắn.

## Nguồn và giới hạn hiện tại

| Nhóm | Nguồn | Khi thiếu |
|---|---|---|
| VNIBOR, SOFR, trái phiếu | [VIRA Market Watch](https://vira.org.vn/tin/Market-Watch.html) | Chặn nếu thiếu kỳ hạn bắt buộc hoặc chưa có ấn bản ngày phát hành |
| Tỷ giá trung tâm | [NHNN](https://sbv.gov.vn/vi/tỷ-giá) | Chặn; trang có thể trả Request Rejected |
| Tỷ giá chuyển khoản USD | [MBBank](https://www.mbbank.com.vn/ExchangeRate) | Chặn nếu thiếu ngày hiện tại; ngày trước thiếu thì để — |
| FX, chỉ số, một số hợp đồng hàng hóa | Yahoo Finance chart API | FX bắt buộc thiếu thì chặn; chỉ tiêu tùy chọn để — |
| Tin tức | RSS và bài VietnamBiz, VIRA; Google News RSS tìm Trading Economics | Cần ít nhất ba nguồn trong cửa sổ 36 giờ; RSS có thể chỉ cung cấp tóm tắt |

CRB Spot, LME Index, cao su, RON92 và kim loại LME chưa có adapter được xác minh nên để `—`. Không thay bằng chỉ số/hợp đồng khác chỉ vì tên gần giống. Dữ liệu futures Yahoo không phải báo giá spot và có thể có hiệu ứng đổi hợp đồng. Mỗi lần chạy lưu ngày/nguồn cụ thể trong `snapshot.json`; không coi tất cả là giá realtime.

Lịch ngày làm việc hiện bỏ thứ Bảy/Chủ nhật, chưa tích hợp lịch nghỉ lễ từng thị trường. Yêu cầu VIRA, NHNN và MB đúng ngày sẽ chặn phát hành khi chưa có số mới, kể cả ngày nghỉ. Không phát hành bản tin nếu nguồn bị lỗi; đây là hành vi chủ ý. Kiểm tra JSON/nguồn/số không chứng minh hoàn toàn mọi quan hệ nhân quả trong văn xuôi; vẫn cần review biên tập trước khi đổi model chính.

## Cài đặt và chạy

Python 3.12; cài LibreOffice và font **Times New Roman thực**. Không chấp nhận font thay thế Liberation Serif. PDF phải có đúng ba trang, đúng vị trí các mục và mọi chữ văn bản ở cỡ 11. Biểu đồ cũng dựng tại kích thước vật lý trong Word để nhãn giữ cỡ 11.

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
$env:LIBREOFFICE_PATH = 'C:\Program Files\LibreOffice\program\soffice.exe'
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python build_test_report.py
```

`build_test_report.py` chỉ dùng fixtures có nhãn KIỂM THỬ, qua cùng pipeline sản xuất, không gọi model/nguồn mạng và không gửi email. Fixtures nằm trong `tests/fixtures`, hoàn toàn tách dữ liệu sản xuất. `--send` từ chối snapshot fixture.

```powershell
# Kiểm tra nguồn, không gọi AI và không gửi
python market_report.py --collect-only
# Tạo bản xem trước (mặc định), có gọi model khi dữ liệu đạt
python market_report.py --dry-run
# Phát hành sau mọi kiểm tra; chỉ ngày làm việc 12:00–15:00 VN
python market_report.py --send
# Tái hiện bản đã lưu, không gọi nguồn hoặc AI
python market_report.py --snapshot path/to/snapshot.json --content path/to/content.json --dry-run
```

`--as-of` yêu cầu ISO timestamp có múi giờ. Muốn tái hiện chính xác giá intraday cũ phải dùng snapshot đã lưu, không lấy nến ngày hiện tại để suy ngược intraday. Mỗi lần chạy có thư mục riêng dưới `output/`, gồm raw sources, OCR, snapshot, validation, phản hồi model, biểu đồ, Word, PDF và manifest hash/thời gian. Lỗi trả mã khác không; `--collect-only` trả 2 khi dữ liệu chưa đạt. Không lưu API key vào các log này.

## Model và GitHub Actions

Cấu hình ít nhất một key: `GEMINI_API_KEY` (hoặc `GOOGLE_API_KEY`), `OPENROUTER_API_KEY` (tương thích secret cũ `Open_Router_API_Key`). Các tên model cấu hình qua `GEMINI_MODEL`, `OPENROUTER_MODEL`; giá trị mặc định giữ baseline Gemini và Ling Fin fallback trong `.env.example`. Tên model phải tồn tại và tài khoản phải có quyền gọi. Chưa tự động nâng Ling Fin thành model chính khi chưa có đánh giá trên bản tin thực tế.

Mỗi provider tối đa hai lần thử; đầu ra sai sẽ được yêu cầu sửa rồi mới chuyển fallback. Có thể chạy chỉ bằng OpenRouter mà không cần key Gemini. Bản free không gửi response_format bắt buộc; vẫn phải qua cùng Pydantic và kiểm tra nội dung. Không lấy reasoning_content làm bản tin. Nhật ký `model-attempts.json` ghi model, thời gian, usage và lỗi kiểm tra để so sánh sau này. Skills cải thiện chỉ dẫn, không tương đương fine-tuning trọng số.

Secrets gửi thư: `SENDER_EMAIL`, `SENDER_PASSWORD` (SMTP app password). Biến `RECIPIENTS` có thể cấu hình trong GitHub Repository Variables; mặc định giữ danh sách nhận cũ. Workflow schedule gọi `--send`. Chạy thủ công mặc định chỉ preview, bật `send_email` nếu muốn gửi; cùng giới hạn giờ và dữ liệu áp dụng. Workflow kiểm tra riêng chạy trên push/PR và không cần secrets.

`.state/` lưu trạng thái gửi theo ngày. Workflow dùng concurrency và Actions cache để lưu qua các lần chạy. Cache có thể bị xóa/evict và ledger ở máy khác không được đồng bộ; đây không phải bảo đảm exactly-once toàn cục. Nếu trạng thái `unknown`/`partial_or_unknown`, kiểm tra hộp thư người gửi và danh sách nhận trước khi xử lý ledger. Không xóa ledger để retry mù. Chạy nhiều môi trường sản xuất cần kho ledger bền vững dùng chung.

Không chạy workflow gửi thư chỉ để kiểm thử code. Xem `validation-data.json`, `validation-content.json` và `manifest.json` trong artifact khi workflow bị chặn.

## Quy tắc bố cục và báo giá trader

Tin nổi bật thứ nhất và thứ hai **35–45 từ**, tin thứ ba **12–45 từ**; liên ngân hàng **88–95 từ**, USD-VND **75–80 từ**. Đếm theo khoảng trắng sau khi thay placeholder; không tính số thứ tự, tiêu đề, nguồn và nhãn dự báo. Các giới hạn nằm trong `config/editorial_rules.json` và được dùng chung ở prompt, bước chuẩn hóa, sửa từng mục và validator. Renderer dùng nguyên nội dung biên tập; không ghi đè bằng tin mẫu, toàn văn VIRA hay dự báo cố định.

Bảng NHNN là 4 hàng × 3 cột, tỷ lệ 44/28/28, màu xanh `4F81BD`. Bảng liên ngân hàng có hai cột Hôm qua/Hôm nay, số hôm nay màu đỏ. Cặp mua/bán lấy từ trader, không lấy bảng niêm yết MBBank. “Hôm qua” là phiên làm việc liền trước (thứ Hai dùng thứ Sáu); ngày thực tế ghi dưới bảng. Lịch này chưa nhận diện nghỉ lễ.

Để nhập báo giá từ room **firm ALM**, tạo file riêng ngoài repo, ví dụ `/etc/bttn/trader_quotes.json`, theo cấu trúc `config/trader_quotes.example.json`. Thay `purpose` bằng `live`, ghi giá thực bằng số thập phân dùng dấu chấm, `quoted_at` có múi giờ và `source_url` là link tin nhắn Teams. Giữ cả báo giá phiên trước và hiện tại. Đặt `TRADER_QUOTES_PATH` trong môi trường chạy BTTN trỏ tới file này. Đây là đầu vào nhập thủ công; code chưa tự đọc room Teams và không xác thực nội dung tin nhắn.

Chỉ báo giá trong ngày, trước giờ chốt mới xuất hiện ở cột Hôm nay và bảng SWAP. Mỗi kỳ hạn chọn báo giá cuối cùng phù hợp. Giá mua lớn hơn giá bán, thiếu một vế hoặc thiếu múi giờ bị từ chối. File ví dụ mang nhãn fixture và không được dùng trong báo cáo live. Khi chưa nhập dữ liệu, hiển thị `—` và ghi cảnh báo, không lấy `config/swap_rates.json` làm giá thật. Chênh lệch VND − USD vẫn nằm trong snapshot dưới các mã `SWAP_*` để tham khảo; báo giá trader dùng mã `ALM_SWAP_*`.

Biểu đồ nội địa cũng chỉ dùng snapshot của kỳ báo cáo: lãi suất theo kỳ hạn VIRA, lịch sử báo giá trader USD-VND và so sánh lợi suất trái phiếu mười năm theo quốc gia. Không dựng chuỗi lịch sử bằng các file giá mẫu cũ. Times New Roman trên Linux có thể cài qua `ttf-mscorefonts-installer` theo điều khoản Microsoft; workflow kiểm tra font trước khi chạy.
