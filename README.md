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

Python 3.12; cài LibreOffice, font Times New Roman hoặc Liberation Serif tương thích. PDF được kiểm tra lại do khác biệt font có thể làm đổi số trang.

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
# Phát hành sau mọi kiểm tra
python market_report.py --send
# Tái hiện bản đã lưu, không gọi nguồn hoặc AI
python market_report.py --snapshot path/to/snapshot.json --content path/to/content.json --dry-run
```

`--as-of` yêu cầu ISO timestamp có múi giờ. Muốn tái hiện chính xác giá intraday cũ phải dùng snapshot đã lưu, không lấy nến ngày hiện tại để suy ngược intraday. Mỗi lần chạy có thư mục riêng dưới `output/`, gồm raw sources, OCR, snapshot, validation, phản hồi model, biểu đồ, Word, PDF và manifest hash/thời gian. Lỗi trả mã khác không; `--collect-only` trả 2 khi dữ liệu chưa đạt. Không lưu API key vào các log này.

## Model và GitHub Actions

Cấu hình ít nhất một key: `GEMINI_API_KEY` (hoặc `GOOGLE_API_KEY`), `OPENROUTER_API_KEY` (tương thích secret cũ `Open_Router_API_Key`). Các tên model cấu hình qua `GEMINI_MODEL`, `ZENMUX_MODEL`, `OPENROUTER_MODEL`; giá trị mặc định giữ baseline Gemini và Ling Fin fallback trong `.env.example`. Tên model phải tồn tại và tài khoản phải có quyền gọi. Chưa tự động nâng Ling Fin thành model chính khi chưa có đánh giá trên bản tin thực tế.

Pipeline dịch và biên tập theo từng mục: Python chọn nguồn còn hạn, lọc trùng và giới hạn tối đa sáu bài bổ sung/mục; giữ observation và provenance. Mỗi mục gọi AI riêng theo schema Section. Python đếm độ dài sau thay placeholder, bỏ câu trùng và chỉ rút gọn bằng câu hoàn chỉnh khi không mất câu chứa số liệu. Không cắt giữa câu, không chèn văn bản mẫu hay số liệu cố định. Nếu chưa đạt, chỉ mục đó được viết lại (Gemini tối đa ba vòng, ZenMux/OpenRouter hai vòng). Các mục đã đạt được giữ nguyên. Giới hạn từ và kiểm tra nguồn/số liệu vẫn là điều kiện phát hành.

Hỗ trợ `ZENMUX_API_KEY` và `ZENMUX_MODEL` trực tiếp tại endpoint ZenMux; không cần OpenRouter. Nếu provider hết số dư hoặc hết lượt thử mạng, các mục còn lại dùng provider dự phòng, không gọi lại provider lỗi trong cùng lượt chạy. Nhật ký `model-attempts.json` ghi từng mục, vòng biên tập, usage, thời gian và lỗi. `evidence-*.json`, `prompt-*.txt`, `response-*.txt` lưu bằng chứng và đầu ra; `content-progress.json` lưu các mục đã đạt để kiểm tra. Đây chưa phải cơ chế tự tiếp tục giữa hai lượt chạy. Mục chưa đạt còn dấu nháp và chặn gửi thư. Không lấy reasoning_content làm bản tin. Skills cải thiện chỉ dẫn, không tương đương fine-tuning trọng số.

Secrets gửi thư: `SENDER_EMAIL`, `SENDER_PASSWORD` (SMTP app password). Biến `RECIPIENTS` có thể cấu hình trong GitHub Repository Variables; mặc định giữ danh sách nhận cũ. Workflow schedule gọi `--send`. Chạy thủ công mặc định chỉ preview, bật `send_email` nếu muốn gửi; cùng kiểm tra dữ liệu và nội dung áp dụng. Workflow kiểm tra riêng chạy trên push/PR và không cần secrets.

`.state/` lưu trạng thái gửi theo ngày. Workflow dùng concurrency và Actions cache để lưu qua các lần chạy. Cache có thể bị xóa/evict và ledger ở máy khác không được đồng bộ; đây không phải bảo đảm exactly-once toàn cục. Nếu trạng thái `unknown`/`partial_or_unknown`, kiểm tra hộp thư người gửi và danh sách nhận trước khi xử lý ledger. Không xóa ledger để retry mù. Chạy nhiều môi trường sản xuất cần kho ledger bền vững dùng chung.

Không chạy workflow gửi thư chỉ để kiểm thử code. Xem `validation-data.json`, `validation-content.json` và `manifest.json` trong artifact khi workflow bị chặn.
