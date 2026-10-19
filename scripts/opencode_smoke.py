"""Kiểm tra khóa OpenCode trên VPS: một lần dịch ngắn bằng DeepSeek V4.1 Flash miễn phí (và GLM dự phòng).

Đọc OPENCODE_API_KEY từ biến môi trường (.env). Không in khóa, header hay nội dung lỗi thô.
Chạy: python scripts/opencode_smoke.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from bttn.deepseek_translation import OPENCODE_BACKUP_MODEL, OPENCODE_MODEL, opencode_translator  # noqa: E402
from bttn.nvidia_translation import TranslationError  # noqa: E402

SAMPLE = "The Fed held its policy rate at 3.75%-4.00% on Wednesday, while the dollar index rose 0.2% to 101.95."


def main():
    report = {}
    for model in (OPENCODE_MODEL, OPENCODE_BACKUP_MODEL):
        client = opencode_translator(model)
        try:
            report[model] = {"status": "ok", "translation": client.translate(SAMPLE)}
        except TranslationError as exc:
            report[model] = {"status": "failed", "error": str(exc)}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report[OPENCODE_MODEL]["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
