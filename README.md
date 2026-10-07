# Bản tin thị trường ngày BTTN

Pipeline Python tạo bản tin từ dữ liệu có nguồn, Riva dịch từng bài và Python chọn câu và template Word. Workflow bắt đầu **12:00 giờ Việt Nam, thứ Hai–thứ Sáu** (`05:00 UTC`). Đây là giờ trigger; GitHub Actions có thể xếp hàng nên không bảo đảm email đến đúng 12:00.

Trong **Actions → BTTN midday → Run workflow**, chỉ có một menu:

1. **Gửi file Word đến các email**: gửi một file `.docx` tới danh sách `RECIPIENTS` sau kiểm tra tự động.
2. **Chỉ tạo file Word**: lưu `.docx` trong Artifacts, không gửi email.

Mặc định chọn gửi Word; lịch 12:00 cũng gửi Word. Không còn ô gửi email thử,
địa chỉ nhận thử hoặc nhánh gửi báo cáo mẫu SMTP. **Tạm ngừng tạo và đính kèm PDF.**
Kiểm thử mã nguồn nằm trong workflow `BTTN checks`, không chạy lại trước mỗi lượt xuất bản.

## Các thay đổi

- `bttn/sources.py`, `vira.py`: thu thập, lưu bản gốc và hash; mỗi số có nguồn, thời điểm, đơn vị, ngày giao dịch và cách tính.
- VIRA Market Watch: chọn ấn bản theo thời gian xuất bản, bỏ bài cũ ghim đầu trang và bài sau cutoff. Bảng ảnh được OCR cục bộ ở bốn cấu hình; một ô chỉ được dùng khi ít nhất hai lần đọc đồng ý và không có kết quả trái nhau. OCR đồng thuận vẫn không thay thế kiểm tra nghiệp vụ; thay đổi bố cục nguồn có thể khiến bản tin bị chặn.
- VNIBOR VND/USD, SOFR và lợi suất trái phiếu lấy từ VIRA. Lợi suất trái phiếu hiện là chuẩn **10 năm theo quốc gia**, không tự tạo đường cong nhiều kỳ hạn.
- Swap tham khảo = **lãi suất VND − lãi suất USD**, cùng kỳ hạn và cùng ấn bản, đơn vị **điểm phần trăm**. VND có ngày fixing riêng; USD ghi Last trong ấn bản, không khẳng định hai fixing cùng thời điểm. Đây không phải báo giá FX swap mua/bán.
- Python chọn câu nguyên vẹn từ nguồn tiếng Việt hoặc bản dịch NVIDIA đã kiểm tra. Mỗi câu có `sentence_refs` đối chiếu đúng bài/bản dịch. Số liệu thị trường dùng `{{OBSERVATION_ID}}`; số trong tin chỉ được phép khi nằm trong câu nguyên vẹn có nguồn. Giới hạn từ và cấu trúc đoạn được kiểm tra trước khi dựng file.
- Mọi ô dữ liệu, bảng và biểu đồ động cũ trong template bị xóa trước khi dựng lại. Không dùng số mẫu làm fallback. Nội dung biên tập thực sự xuất hiện trong Word.
- Biến động ngày so với phiên hoàn tất liền trước, không dùng `chartPreviousClose` của toàn khoảng tải. Biến động năm ghi rõ **YoY**.
- Phần dự báo và ý tưởng sản phẩm để trống. Thiếu nguồn cho mục tùy chọn thì hiện `—`; thiếu dữ liệu bắt buộc thì dừng và không gửi email.
- Word được kiểm tra cấu trúc ba bảng trang, không còn placeholder, Times New Roman 11. Không gọi LibreOffice hoặc chuyển đổi PDF trong luồng xuất bản. Gửi thất bại trả mã lỗi; ledger ngăn tự gửi lại khi SMTP có kết quả không chắc chắn.

## Nguồn và giới hạn hiện tại

| Nhóm | Nguồn | Khi thiếu |
|---|---|---|
| VNIBOR, SOFR, trái phiếu | [VIRA Market Watch](https://vira.org.vn/tin/Market-Watch.html) | Chặn nếu thiếu kỳ hạn bắt buộc hoặc chưa có ấn bản ngày phát hành |
| Tỷ giá trung tâm | [NHNN](https://sbv.gov.vn/vi/tỷ-giá) | Chặn; trang có thể trả Request Rejected |
| Tỷ giá chuyển khoản USD | [MBBank](https://www.mbbank.com.vn/ExchangeRate) | Chặn nếu thiếu ngày hiện tại; ngày trước thiếu thì để — |
| FX, chỉ số, một số hợp đồng hàng hóa | Yahoo Finance chart API | FX bắt buộc thiếu thì chặn; chỉ tiêu tùy chọn để — |
| Tin quốc tế | Trading Economics: United States, Euro Area, Japan, China | API hoặc nhập bài có nội dung; tối đa ba bài/quốc gia trong 36 giờ |
| Tin nội địa, cà phê | VietnamBiz, VIRA và báo kinh tế | Chỉ chọn câu từ thân bài có ngày xuất bản; RSS không đủ để biên tập |

CRB Spot, LME Index, cao su, RON92 và kim loại LME chưa có adapter được xác minh nên để `—`. Không thay bằng chỉ số/hợp đồng khác chỉ vì tên gần giống. Dữ liệu futures Yahoo không phải báo giá spot và có thể có hiệu ứng đổi hợp đồng. Mỗi lần chạy lưu ngày/nguồn cụ thể trong `snapshot.json`; không coi tất cả là giá realtime.

Lịch ngày làm việc hiện bỏ thứ Bảy/Chủ nhật, chưa tích hợp lịch nghỉ lễ từng thị trường. Yêu cầu VIRA, NHNN và MB đúng ngày sẽ chặn phát hành khi chưa có số mới, kể cả ngày nghỉ. Không phát hành bản tin nếu nguồn bị lỗi; đây là hành vi chủ ý. Kiểm tra JSON/nguồn/số không chứng minh hoàn toàn mọi quan hệ nhân quả trong văn xuôi; vẫn cần review biên tập trước khi đổi model chính.

## Cài đặt và chạy

Python 3.12 và font **Times New Roman thực**. Không chấp nhận font thay thế Liberation Serif.
Word có ba khối trang theo template, cỡ chữ 11; biểu đồ dựng tại kích thước vật lý trong Word.
Kiểm tra cấu trúc Word không chứng minh số trang khi Word phân trang trên máy người nhận;
việc kiểm chứng bố cục qua PDF tạm ngừng theo quy trình hiện tại. Không cần LibreOffice để tạo/gửi Word.

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python build_test_report.py
```

`build_test_report.py` chỉ dùng fixtures có nhãn KIỂM THỬ, qua cùng pipeline sản xuất, không gọi model/nguồn mạng và không gửi email. Fixtures nằm trong `tests/fixtures`, hoàn toàn tách dữ liệu sản xuất. `--send` từ chối snapshot fixture.

```powershell
# Kiểm tra nguồn, không gọi AI và không gửi
python market_report.py --collect-only
# Chỉ tạo Word (mặc định), dịch bằng NVIDIA và chọn câu bằng Python
python market_report.py --create-only
# Gửi Word sau kiểm tra tự động; không yêu cầu gửi email thử hoặc duyệt dịch thủ công
python market_report.py --send
# Tái hiện bản đã lưu, không gọi nguồn hoặc AI
python market_report.py --snapshot path/to/snapshot.json --content path/to/content.json --create-only
```

`--as-of` yêu cầu ISO timestamp có múi giờ. Muốn tái hiện chính xác giá intraday cũ phải dùng snapshot đã lưu, không lấy nến ngày hiện tại để suy ngược intraday. Mỗi lần chạy có thư mục riêng dưới `output/`, gồm raw sources, OCR, snapshot, validation, bản dịch, dấu vết chọn câu, biểu đồ, Word và manifest hash/thời gian. `word_created` nghĩa là chỉ tạo Word; chỉ `sent` xác nhận SMTP đã gửi. `--dry-run` vẫn là tên tương thích của `--create-only`. Lỗi trả mã khác không; `--collect-only` trả 2 khi dữ liệu chưa đạt. Không lưu API key vào các log này.

## Model và GitHub Actions

### Dịch riêng từng bài bằng NVIDIA Riva

`NVIDIA_API_KEY` chỉ dùng tại `https://integrate.api.nvidia.com/v1` với model
`nvidia/riva-translate-4b-instruct-v2`. [Chat template của NVIDIA](https://huggingface.co/nvidia/Riva-Translate-4B-Instruct-v2)
nhận system message **`en-vi`**; không dùng một system prompt dài để thay mã ngôn ngữ.
Python truyền ví dụ thuật ngữ tài chính, dịch tiêu đề riêng và chia đoạn để giữ toàn văn.
Chỉ truyền ví dụ liên quan đến bài. Khi bản dịch bị từ chối, thử lại tối đa một lần
bằng chỉ dẫn ngôn ngữ tối giản cùng NVIDIA; không thêm yêu cầu sửa vào thân bài.
“Current account” và “consumer confidence” được khóa bằng marker kiểm tra để Python
khôi phục thành “cán cân vãng lai” và “niềm tin người tiêu dùng”. Tên thứ trong tuần
cũng được khóa theo chữ trong nguồn, không suy từ ngày đăng bài. Marker thiếu hoặc
trùng đều bị từ chối. Các sửa thuật ngữ/quy tắc đều ghi `python_postprocessed`;
đây là tiền/hậu xử lý Python, không phải model tự tuân thủ hoàn hảo.
Không yêu cầu Riva trả JSON, viết nhận định, rút gọn hoặc giới hạn từ.

```powershell
# Chỉ dịch tin bằng NVIDIA; không dựng báo cáo hoặc gửi email.
python market_report.py --translate-news-only --news-file path/to/te-news.json
# Hoặc dịch các nguồn TE có trong snapshot đã lưu.
python market_report.py --translate-news-only --snapshot path/to/snapshot.json
```

Trading Economics hiện trả HTTP 403 khi Python truy cập website từ môi trường đã kiểm tra.
Không dùng tiêu đề Google News thay toàn văn. Bộ thu thập mới dùng
[API tin theo quốc gia](https://docs.tradingeconomics.com/news/news-by-country-and-date/)
khi có `TRADINGECONOMICS_API_KEY`, hoặc file nhập do người dùng cung cấp qua
`TE_NEWS_IMPORT_PATH`. Key NVIDIA không cấp quyền đọc API Trading Economics.
File nhập có cùng cấu trúc API: danh sách các bản ghi `id`, `title`, `date`,
`description` (nội dung bài), `country`, `url`; country là Euro Area/United States/Japan/China.
Date của API là UTC; file nhập nên ghi rõ múi giờ. Chọn tối đa ba bài mới nhất, khác nội dung, từng quốc gia
trước giờ chốt, tối đa 36 giờ; bài thiếu nội dung bị loại. Bộ thu thập nguồn này
chưa được kiểm thử trực tiếp qua API TE có trả phí/quyền truy cập.

Mỗi bài có bản gốc, bản dịch từng đoạn, nguồn, ngày, model, phiên bản quy tắc và hash.
Kiểm tra giá trị số (chấp nhận dấu thập phân Anh/Việt), thứ trong tuần theo đúng thứ tự, tháng Anh/Việt tương ứng, đơn vị %, tiền tệ,
quý I–IV/Q1–Q4 và số lần tăng lãi suất viết bằng chữ/chữ số (cùng đơn vị),
thuật ngữ, thẻ `dnt`, ngôn ngữ và phản hồi bị cắt. Giữ thuật ngữ hawkish/cứng rắn,
dovish/mềm mỏng, tâm lý tiêu dùng, Bảng lương phi nông nghiệp, lạm phát cơ bản,
lạm phát danh nghĩa; không dùng “nhích nhẹ”. Lỗi API/kiểm tra được ghi mã an toàn,
không có key hoặc response body; không chuyển sang nhà cung cấp dịch khác.

**Kiểm tra số không chứng minh bản dịch đúng nghĩa.** Trong thử nghiệm thực tế,
Riva từng dịch sai chiều yết giá JPY, tên thứ trong tuần và một số thuật ngữ/vai trò nhân vật.
Đã bổ sung ví dụ yết giá và kiểm tra lỗi quan sát được; bản dịch mới vẫn mang trạng thái
`needs_review`. Trạng thái này ghi nhận chưa có người review, không tự đánh dấu model đã được duyệt.
Theo chế độ gửi trực tiếp, review thủ công là tùy chọn; các kiểm tra nguồn, thời điểm,
số/đơn vị, thuật ngữ và nội dung vẫn bắt buộc. Lượt gửi không dừng chỉ vì `needs_review`.
Riêng cách diễn đạt “yen depreciated past X per dollar”, Python chuẩn hóa câu mở đầu
khi con số và cấu trúc nguồn khớp thành “USD-JPY vượt mức X cho thấy JPY đang suy yếu”.
Giữ nguyên phần tin theo sau và số gốc, không đảo thành “dưới X”. Nhật ký ghi `python_postprocessed`; đây là sửa bằng quy tắc,
không phải bằng chứng model tự dịch đúng mọi chiều tỷ giá. Giảm nhiệt độ về 0,
thử lại đầu ra bị từ chối tối đa một lần, cùng nhà cung cấp.
Toàn bộ pipeline dùng **NVIDIA Riva để dịch, Python để chọn câu và dựng báo cáo**.
Không có model viết/tóm tắt lại nội dung. Bộ chọn câu ưu tiên chủ đề, giữ câu nguyên vẹn,
loại dự báo và tin ngoài giờ chốt, khử câu trùng, áp trần từ. Những câu mở đầu phụ thuộc
ngữ cảnh như “Kết quả này…” được loại khỏi phần chọn câu để tránh mất đối tượng tham chiếu;
bản dịch toàn văn vẫn giữ nguyên để review. Mục EUR cần cả tin Mỹ và
Euro Area. Nếu không đủ câu để đạt sàn từ, giữ bản nháp có cảnh báo; không cắt giữa câu
hoặc thêm nhận định để đủ chữ. `selection.json` lưu câu được chọn và lý do loại;
`editorial-validation.json` lưu kết quả kiểm tra.

Xem riêng phần chọn tin (không gọi API, không gửi email):
```powershell
python -m bttn.editorial_preview --snapshot output/translation-review/snapshot.json --translations output/translation-review/translations.json --output-dir output/news-selection
```
Preview này không có bảng giá FX nên chưa phải báo cáo đủ điều kiện phát hành.
Chỉ thêm `--allow-stale-review` để thử lịch sử; bài cũ luôn được ghi rõ và không
được luồng phát hành chấp nhận.

Mẫu mở đầu EUR/JPY dùng chung tại `bttn/fx_editorial.py`: EUR bắt đầu bằng
“Trong phiên giao dịch hôm qua, tính đến ngày…”, đoạn hai “Về phía Châu Âu,”;
JPY bắt đầu bằng “Trong phiên hôm qua ngày…”. Ngày phiên trước đi cùng dữ liệu
đóng cửa, ngày hôm nay theo múi giờ Việt Nam. Câu giá hiện tại dùng “giao dịch quanh mức”,
không mặc định thị trường đi ngang hay gọi phiên trưa là “chiều nay”. Python chọn câu,
bản nháp và kiểm định dùng cùng mẫu; đầu vào JSON sai mẫu hoặc ngày bị chặn.
Giới hạn EUR vẫn 150–200 từ trong hai đoạn, JPY 100–130 từ trong một đoạn.
Phiên bản quy tắc dịch mới tạo cache mới; bản cũ không được tự coi là đã áp dụng quy tắc mới.

```powershell
# Sau khi sửa file JSON có trường translated_segments (danh sách đoạn):
python -m bttn.translation_service edit --cache-dir .state/translations --key HASH --text-file reviewed.json
# Đánh dấu đã review nghĩa và văn phong; lệnh này không gửi email.
python -m bttn.translation_service approve --cache-dir .state/translations --key HASH
python -m bttn.translation_service cleanup --cache-dir .state/translations
```

Cache nằm trong `.state/translations`, không commit. Sửa bản gốc/model/quy tắc sẽ
tạo cache khác; đọc lại không kéo dài thời gian lưu. Xóa bản dịch không chỉnh sửa
ít nhất **bảy ngày**, chỉ xóa đúng file bản dịch trực tiếp, không đụng báo cáo hay ledger.
Cleanup chạy trước lượt dịch; trên VPS có timer trong `deploy/` để xóa cả khi không chạy bản tin.
`--allow-stale-review` của CLI dịch chỉ dành cho thử lịch sử, không được dùng để biên tập phát hành.

Để nhập key mới trên VPS: chạy `/usr/local/sbin/configure-bttn-nvidia.py` qua SSH tương tác.
Helper chỉ thêm `NVIDIA_API_KEY` vào `/etc/bttn/bttn.env`, giữ các cấu hình khác,
nhập ẩn và không gửi thư. `deploy/translate-news-preview.sh` chạy từ checkout riêng
`/opt/bttn-nvidia-preview`, dùng môi trường hiện có; không tự cập nhật checkout phát hành.
GitHub Actions đọc `secrets.NVIDIA_API_KEY` và `secrets.TRADINGECONOMICS_API_KEY` nếu được cấu hình.

Chỉ cần `NVIDIA_API_KEY` cho dịch thuật; không còn dependency hoặc cấu hình nhà cung cấp model khác. Quy tắc Python và ví dụ thuật ngữ không phải fine-tuning trọng số của model hosted.

Secrets gửi thư: `SENDER_EMAIL`, `SENDER_PASSWORD` (SMTP app password). Biến `RECIPIENTS` có thể cấu hình trong GitHub Repository Variables; mặc định giữ danh sách nhận cũ. Workflow schedule gọi `--send`. Chạy thủ công mặc định chỉ preview, bật `send_email` nếu muốn gửi; cùng giới hạn giờ và dữ liệu áp dụng. Workflow kiểm tra riêng chạy trên push/PR và không cần secrets.

`.state/` lưu trạng thái gửi theo ngày. Workflow dùng concurrency và Actions cache để lưu qua các lần chạy. Cache có thể bị xóa/evict và ledger ở máy khác không được đồng bộ; đây không phải bảo đảm exactly-once toàn cục. Nếu trạng thái `unknown`/`partial_or_unknown`, kiểm tra hộp thư người gửi và danh sách nhận trước khi xử lý ledger. Không xóa ledger để retry mù. Chạy nhiều môi trường sản xuất cần kho ledger bền vững dùng chung.

Không chạy workflow gửi thư chỉ để kiểm thử code. Xem `validation-data.json`, `validation-content.json` và `manifest.json` trong artifact khi workflow bị chặn.

## Quy tắc bố cục và báo giá trader

Tin nổi bật thứ nhất và thứ hai **35–45 từ**, tin thứ ba **12–45 từ**; liên ngân hàng **88–95 từ**, USD-VND **75–80 từ**. Đếm theo khoảng trắng sau khi thay placeholder; không tính số thứ tự, tiêu đề, nguồn và nhãn dự báo. Các giới hạn nằm trong `config/editorial_rules.json` và được dùng chung ở bộ chọn câu Python và validator. Renderer dùng nguyên nội dung biên tập; không ghi đè bằng tin mẫu, toàn văn VIRA hay dự báo cố định.

Bảng NHNN là 4 hàng × 3 cột, tỷ lệ 44/28/28, màu xanh `4F81BD`. Bảng liên ngân hàng có hai cột Hôm qua/Hôm nay, số hôm nay màu đỏ. Cặp mua/bán lấy từ trader, không lấy bảng niêm yết MBBank. “Hôm qua” là phiên làm việc liền trước (thứ Hai dùng thứ Sáu); ngày thực tế ghi dưới bảng. Lịch này chưa nhận diện nghỉ lễ.

Để nhập báo giá từ room **firm ALM**, tạo file riêng ngoài repo, ví dụ `/etc/bttn/trader_quotes.json`, theo cấu trúc `config/trader_quotes.example.json`. Thay `purpose` bằng `live`, ghi giá thực bằng số thập phân dùng dấu chấm, `quoted_at` có múi giờ và `source_url` là link tin nhắn Teams. Giữ cả báo giá phiên trước và hiện tại. Đặt `TRADER_QUOTES_PATH` trong môi trường chạy BTTN trỏ tới file này. Đây là đầu vào nhập thủ công; code chưa tự đọc room Teams và không xác thực nội dung tin nhắn.

Chỉ báo giá trong ngày, trước giờ chốt mới xuất hiện ở cột Hôm nay và bảng SWAP. Mỗi kỳ hạn chọn báo giá cuối cùng phù hợp. Giá mua lớn hơn giá bán, thiếu một vế hoặc thiếu múi giờ bị từ chối. File ví dụ mang nhãn fixture và không được dùng trong báo cáo live. Khi chưa nhập dữ liệu, hiển thị `—` và ghi cảnh báo, không lấy `config/swap_rates.json` làm giá thật. Chênh lệch VND − USD vẫn nằm trong snapshot dưới các mã `SWAP_*` để tham khảo; báo giá trader dùng mã `ALM_SWAP_*`.

Biểu đồ nội địa cũng chỉ dùng snapshot của kỳ báo cáo: lãi suất theo kỳ hạn VIRA, lịch sử báo giá trader USD-VND và so sánh lợi suất trái phiếu mười năm theo quốc gia. Không dựng chuỗi lịch sử bằng các file giá mẫu cũ. Times New Roman trên Linux có thể cài qua `ttf-mscorefonts-installer` theo điều khoản Microsoft; workflow kiểm tra font trước khi chạy.
