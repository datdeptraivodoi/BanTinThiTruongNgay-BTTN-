"""Run the pipeline's DeepSeekTranslator on a few tricky sentences; write a JSON report (no key)."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bttn.deepseek_translation import DeepSeekTranslator, TranslationError  # noqa: E402

SAMPLES = [
    "The Federal Reserve kept rates unchanged at 4.25%, while the Japanese yen depreciated past 150 per dollar on Tuesday.",
    "Core inflation slowed to 2.8% in September, while headline inflation ticked up and Nonfarm Payrolls rose by 150,000.",
    "The offshore yuan strengthened to 7.12 per dollar as China announced $500 billion in expansionary fiscal policies.",
    "ECB Chief Economist Philip Lane said further tightening may be needed in the first quarter, sending the euro up 0.4% to $1.1650.",
    "Germany's current account surplus widened to €24.6 billion on Monday, and the offshore yuan traded flat around 7.1350 per dollar.",
    "Traders now price two further rate hikes by the Bank of Japan, while the yen weakened past 152.40 per dollar on Friday.",
]
client = DeepSeekTranslator(os.getenv("DEEPSEEK_API_KEY"))
results = []
for text in SAMPLES:
    try:
        results.append({"source": text, "status": "checked", "translation": client.translate(text)})
    except TranslationError as exc:
        results.append({"source": text, "status": "rejected", "error": str(exc)})
out = Path(sys.argv[1])
out.write_text(json.dumps({"model": client.model, "results": results, "attempts": client.attempts},
                          ensure_ascii=False, indent=2), encoding="utf-8")
print(out.read_text(encoding="utf-8"))
