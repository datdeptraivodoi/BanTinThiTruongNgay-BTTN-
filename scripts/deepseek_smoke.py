"""DeepSeek smoke test: list model ids and make one minimal chat completion.

Reads DEEPSEEK_API_KEY from the environment. Never prints or writes the key,
headers or raw error bodies. Writes a small JSON report for review.
"""
import json
import os
import sys
import time
from pathlib import Path

import requests

BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "deepseek/smoke/result.json")


def main():
    key = (os.getenv("DEEPSEEK_API_KEY") or "").strip()
    report = {"base_url": BASE_URL, "key_present": bool(key), "models": [], "chat": None}
    if not key:
        report["error"] = "DEEPSEEK_API_KEY_MISSING"
        return finish(report, 2)
    headers = {"Authorization": "Bearer " + key}

    r = requests.get(BASE_URL + "/models", headers=headers, timeout=30)
    report["models_http_status"] = r.status_code
    if r.status_code == 200:
        report["models"] = sorted(m.get("id") for m in r.json().get("data", []) if m.get("id"))

    wanted = (os.getenv("DEEPSEEK_MODEL") or "").strip()
    if not wanted:
        flash = [m for m in report["models"] if "flash" in m.lower()]
        wanted = flash[0] if len(flash) == 1 else ("deepseek-chat" if "deepseek-chat" in report["models"] else "")
    report["chosen_model"] = wanted or None
    if not wanted:
        report["error"] = "MODEL_NOT_DETERMINED"
        return finish(report, 2)

    body = {"model": wanted, "messages": [
        {"role": "system", "content": "Translate the user's English text into Vietnamese. Output only the translation."},
        {"role": "user", "content": "The Federal Reserve kept rates unchanged at 4.25%, while the Japanese yen "
                                    "depreciated past 150 per dollar on Tuesday."},
    ]}
    started = time.monotonic()
    r = requests.post(BASE_URL + "/chat/completions", headers=headers, json=body, timeout=120)
    chat = {"http_status": r.status_code, "elapsed_seconds": round(time.monotonic() - started, 2)}
    if r.status_code == 200:
        data = r.json()
        choice = data["choices"][0]
        chat.update({"returned_model": data.get("model"), "finish_reason": choice.get("finish_reason"),
                     "content": choice["message"].get("content"),
                     "has_reasoning_content": bool(choice["message"].get("reasoning_content")),
                     "usage": data.get("usage")})
    else:
        try:
            err = r.json().get("error", {})
            chat["error_type"] = err.get("type") or err.get("code")
            chat["error_message"] = (err.get("message") or "")[:200]
        except ValueError:
            chat["error_type"] = "non_json_error"
    report["chat"] = chat
    return finish(report, 0 if r.status_code == 200 else 1)


def finish(report, code):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
