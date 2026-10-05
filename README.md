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
| Tin tức | RSS theo chủ đề, trang chuyên mục VietnamBiz/VIRA và toàn văn tại nhà xuất bản được cho phép | Cần ít nhất ba bài khác nhau đủ nội dung; thiếu tin từng chủ đề được ghi rõ |

CRB Spot, LME Index, cao su, RON92 và kim loại LME chưa có adapter được xác minh nên để `—`. Không thay bằng chỉ số/hợp đồng khác chỉ vì tên gần giống. Dữ liệu futures Yahoo không phải báo giá spot và có thể có hiệu ứng đổi hợp đồng. Mỗi lần chạy lưu ngày/nguồn cụ thể trong `snapshot.json`; không coi tất cả là giá realtime.

Lịch ngày làm việc hiện bỏ thứ Bảy/Chủ nhật, chưa tích hợp lịch nghỉ lễ từng thị trường. Yêu cầu VIRA, NHNN và MB đúng ngày sẽ chặn phát hành khi chưa có số mới, kể cả ngày nghỉ. Không phát hành bản tin nếu nguồn bị lỗi; đây là hành vi chủ ý. Kiểm tra JSON/nguồn/số không chứng minh hoàn toàn mọi quan hệ nhân quả trong văn xuôi; vẫn cần review biên tập trước khi đổi model chính.

## Thu thập và lọc tin bằng Python

Quy tắc nằm trong `config/news_rules.json`, xử lý tại `bttn/news.py`. Có tám nhóm:
liên ngân hàng, USD/VND, EUR/USD, Nhật Bản, Trung Quốc, cà phê, Brent và vàng.
Brent và vàng có trạng thái nguồn riêng dù cùng một mục trong bản tin.

1. Khám phá link từ RSS theo từng nhóm, RSS tài chính/hàng hóa và các trang chuyên mục.
   RSS chỉ cung cấp link, tiêu đề và ngày đăng để khám phá; không dùng phần mô tả RSS làm toàn văn.
   Ưu tiên link trực tiếp, sự kiện kinh tế trong tiêu đề rồi thời gian đăng, tối đa sáu ứng viên/nhóm.
2. Tải bài từ Trading Economics, VietnamBiz hoặc VIRA. Google News chỉ được dùng nếu redirect/link
   công khai dẫn tới nhà xuất bản; không giải được link thì ghi `PUBLISHER_LINK_UNRESOLVED`.
   Tối đa 36 URL ứng viên tin trong một lượt; bộ đọc giá cà phê kiểm tra riêng tối đa tám bài.
3. Lấy tiêu đề, toàn văn và ngày đăng từ HTML/JSON-LD; loại khối tin liên quan/quảng cáo.
   Bài cần ít nhất 80 đơn vị tách bằng khoảng trắng và 400 ký tự, tối đa 80.000 ký tự.
   Không lấy ngày chạy thay ngày đăng. Chỉ dùng ngày RSS nếu trang thiếu ngày; ngày địa phương
   không có offset chỉ được gắn múi giờ khi nhà xuất bản có cấu hình rõ ràng.
4. Chốt tin trong cửa sổ 36 giờ, mở rộng tới đầu ngày làm việc liền trước nếu cần.
   Ví dụ thứ Hai trưa vẫn nhận tin thứ Sáu; không nhận tin sau cutoff. Chưa có lịch nghỉ lễ.
5. Phân loại bằng từ/cụm có ranh giới trong tiêu đề và nội dung; URL không tham gia phân loại.
   USD/VND cần cả dấu hiệu thị trường Việt Nam và đồng USD; bảng euro trong nước không thay cho EUR/USD.
   Một lần nhắc Nhật Bản trong thân bài không đủ để coi là tin Nhật Bản. Đếm từ khóa khác nhau, không thưởng lặp từ.
   Xếp hạng theo mức liên quan, sự kiện kinh tế/chính sách, ngày đăng và độ đầy đủ.
6. Chuẩn hóa URL bỏ tracking, lọc bản sao theo URL/nội dung và ưu tiên bài đầy đủ hơn.
   Nhóm sự kiện bằng tiêu đề gần giống, cùng ngày và cùng bộ số liệu; tối đa hai bài/sự kiện,
   sáu bài/nhóm. Bài cập nhật số liệu mới vẫn được giữ. Đây là heuristic, cần review các trường hợp biên.
7. Lưu `news-decisions.json` (lý do chọn/loại), `news-coverage.json`, bản gốc HTTP và `snapshot.json`.
   Không có bài đủ dùng cho một nhóm thì ghi `price_data_only_or_missing`; bước biên tập chỉ mô tả
   số liệu và giới hạn nguồn, không tự viết nguyên nhân. Điều kiện phát hành về số liệu và độ dài vẫn giữ.

Bộ đọc giá cà phê chọn bài **giá cà phê hôm nay có ngày đăng mới nhất và đọc được giá**,
không chọn theo bài đầu trang. Lưu riêng ngày đăng bài và ngày giao dịch của giá Arabica/Robusta.
Một bài mới không làm giá của phiên cũ thành giá ngày hiện tại.

Kiểm tra riêng khâu thu thập, không gọi model và không gửi thư:

```bash
python -m bttn.news
# Giảm số URL tin để kiểm tra nhanh; bài giá cà phê có giới hạn riêng như trên.
python -m bttn.news --max-fetches 16
python -m bttn.news --as-of 2026-10-06T12:00:00+07:00 --output-dir output
```

Mỗi lượt tạo thư mục `news-review-*`. `reviewed_with_gaps` nghĩa là kiểm tra thu thập đã hoàn tất
nhưng còn nhóm thiếu bài; mã thoát 0 của lệnh review không xác nhận bản tin đủ điều kiện phát hành.
Muốn tái hiện nội dung tại cutoff cũ phải dùng snapshot/raw đã lưu, vì trang nguồn có thể được cập nhật.

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

Cấu hình ít nhất một key: `GEMINI_API_KEY` (hoặc `GOOGLE_API_KEY`), `ZENMUX_API_KEY`; OpenRouter là tùy chọn (`OPENROUTER_API_KEY`, tương thích secret cũ `Open_Router_API_Key`). Các tên model cấu hình qua `GEMINI_MODEL`, `ZENMUX_MODEL`, `OPENROUTER_MODEL`. Tên model phải tồn tại và tài khoản phải có quyền gọi. Chưa tự động nâng Ling Fin thành model chính khi chưa có đánh giá trên bản tin thực tế.

Pipeline dịch và biên tập theo từng mục: Python chọn nguồn còn hạn, lọc trùng và giới hạn tối đa sáu bài bổ sung/mục; giữ observation và provenance. Mỗi mục gọi AI riêng theo schema Section. Python đếm độ dài sau thay placeholder, bỏ câu trùng và chỉ rút gọn bằng câu hoàn chỉnh khi không mất câu chứa số liệu. Không cắt giữa câu, không chèn văn bản mẫu hay số liệu cố định. Nếu chưa đạt, chỉ mục đó được viết lại (Gemini tối đa ba vòng, ZenMux/OpenRouter hai vòng). Các mục đã đạt được giữ nguyên. Giới hạn từ và kiểm tra nguồn/số liệu vẫn là điều kiện phát hành.

Hỗ trợ `ZENMUX_API_KEY` và `ZENMUX_MODEL` trực tiếp tại endpoint ZenMux; không cần OpenRouter. Nếu provider hết số dư hoặc hết lượt thử mạng, các mục còn lại dùng provider dự phòng, không gọi lại provider lỗi trong cùng lượt chạy. Nhật ký `model-attempts.json` ghi từng mục, vòng biên tập, usage, thời gian và lỗi. `evidence-*.json`, `prompt-*.txt`, `response-*.txt` lưu bằng chứng và đầu ra; `content-progress.json` lưu các mục đã đạt để kiểm tra. Đây chưa phải cơ chế tự tiếp tục giữa hai lượt chạy. Mục chưa đạt còn dấu nháp và chặn gửi thư. Không lấy reasoning_content làm bản tin. Skills cải thiện chỉ dẫn, không tương đương fine-tuning trọng số.

Secrets gửi thư: `SENDER_EMAIL`, `SENDER_PASSWORD` (SMTP app password). Biến `RECIPIENTS` có thể cấu hình trong GitHub Repository Variables; mặc định giữ danh sách nhận cũ. Workflow schedule gọi `--send`. Chạy thủ công mặc định chỉ preview, bật `send_email` nếu muốn gửi; cùng kiểm tra dữ liệu và nội dung áp dụng. Workflow kiểm tra riêng chạy trên push/PR và không cần secrets.

`.state/` lưu trạng thái gửi theo ngày. Workflow dùng concurrency và Actions cache để lưu qua các lần chạy. Cache có thể bị xóa/evict và ledger ở máy khác không được đồng bộ; đây không phải bảo đảm exactly-once toàn cục. Nếu trạng thái `unknown`/`partial_or_unknown`, kiểm tra hộp thư người gửi và danh sách nhận trước khi xử lý ledger. Không xóa ledger để retry mù. Chạy nhiều môi trường sản xuất cần kho ledger bền vững dùng chung.

Không chạy workflow gửi thư chỉ để kiểm thử code. Xem `validation-data.json`, `validation-content.json` và `manifest.json` trong artifact khi workflow bị chặn.

## Bản dịch bài nguồn và thời hạn lưu

`bttn/translation_service.py` dịch riêng từng bài được chọn trước khi biên tập bản tin.
Mỗi bài lưu nội dung gốc đầy đủ theo phần đã thu thập, bản dịch theo đoạn, link, ngày xuất bản,
provider/model được yêu cầu và thời gian tạo/sửa. Không áp dụng giới hạn từ của bản tin lên bản dịch.
Bài tiếng Việt được giữ nguyên. Một link có cùng nội dung chỉ dịch một lần, dùng cho nhiều mục
và nhiều lượt chạy; sửa nội dung nguồn sẽ tạo bản dịch mới. Bài chưa dịch đạt kiểm tra không được
đưa nguyên văn tiếng nước ngoài vào bước tổng hợp. Các bài khác vẫn được xử lý.

Kho nằm tại `<state-dir>/translations/` (VPS: `/var/lib/bttn/state/translations`). Kiểm tra tự động
đối chiếu chữ số, ngày, ký hiệu và một số đơn vị theo từng đoạn, kiểm tra số đoạn và ngôn ngữ.
Kiểm tra này không chứng minh bản dịch đúng hoàn toàn về ngữ nghĩa hay chiều tăng/giảm;
vẫn cần đối chiếu nguồn khi duyệt. Có thể đọc/sửa bản dịch đã lưu, nhưng bản sửa thay đổi số liệu
sẽ không được tái sử dụng nếu không qua kiểm tra.

Bản dịch không sửa trong **bảy ngày** được xóa tại lần dọn tiếp theo. Thời hạn tính bằng thời gian
sửa file, gồm cả sửa thủ công; đọc/tái sử dụng không gia hạn. Mỗi lượt chạy dọn kho trước khi xử lý.
VPS có timer dọn hằng ngày lúc 03:00 giờ Việt Nam, độc lập lịch gửi bản tin. Chỉ xóa file bản dịch
trong kho; giữ nguyên báo cáo đã tạo, snapshot nguồn và sổ theo dõi gửi email. Diagnostics chỉ lưu
`translation_key`, trạng thái và lỗi; không tạo thêm bản sao toàn văn bản dịch ngoài kho có thời hạn.

```bash
python -m bttn.translation_service cleanup --cache-dir .state/translations
# segments.json có dạng {"segments": ["bản dịch đoạn một", "bản dịch đoạn hai"]},
# số đoạn tương ứng original_segments trong bản ghi. Sửa đạt kiểm tra sẽ bắt đầu lại hạn bảy ngày.
python -m bttn.translation_service edit --cache-dir .state/translations --key <translation_key> --text-file segments.json
```
