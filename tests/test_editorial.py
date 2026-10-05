import json
from datetime import timedelta
from pathlib import Path

from bttn.editorial import fit_section, generate_sections, prepare_evidence
from bttn.models import ReportContent, Section, Snapshot
from bttn.validation import validate_content

FIXTURES = Path(__file__).parent / "fixtures"


def fixtures():
    return (Snapshot.model_validate_json((FIXTURES / "snapshot.json").read_text(encoding="utf-8")),
            ReportContent.model_validate_json((FIXTURES / "content.json").read_text(encoding="utf-8")))


def test_preparation_filters_old_future_duplicate_and_unrelated_news():
    snapshot, _ = fixtures()
    base = snapshot.sources["news_a"]
    for sid, text, hours in [("fresh", "Japan yen strengthens after BOJ statement.", 2),
                             ("duplicate", "Japan yen strengthens after BOJ statement.", 3),
                             ("old", "Japan old report", 50), ("future", "Japan future report", -1),
                             ("irrelevant", "Coffee harvest in Brazil", 1)]:
        snapshot.sources[sid] = base.model_copy(update={"id": sid, "text": text,
                                                        "published_at": snapshot.as_of-timedelta(hours=hours)})
    evidence = prepare_evidence(snapshot, "japan")
    assert "fresh" in evidence["sources"]
    assert not {"duplicate", "old", "future", "irrelevant"} & evidence["sources"].keys()
    assert "USDJPY" in evidence["observations"]
    assert "ARABICA" not in evidence["observations"]
    assert all("series" not in obs for obs in evidence["observations"].values())


def test_fitting_preserves_numeric_sentence_and_does_not_pad():
    snapshot, _ = fixtures()
    section = Section(paragraphs=["Giá là {{GOLD}}. Một câu phụ ở cuối."], source_ids=["market"])
    fitted = fit_section(section, snapshot, 3, 5, "china")
    assert fitted.paragraphs == ["Giá là {{GOLD}}."]
    assert section.paragraphs != fitted.paragraphs
    short = Section(paragraphs=["Tin ngắn."], source_ids=["market"])
    assert fit_section(short, snapshot, 50, 70, "china") == short
    numeric = Section(paragraphs=["Một câu mở. Giá là {{GOLD}}."], source_ids=["market"])
    assert fit_section(numeric, snapshot, 2, 3, "china") == numeric


def test_completed_sections_survive_other_section_failure(tmp_path):
    snapshot, content = fixtures()
    evidence = prepare_evidence(snapshot, "interbank")
    accepted = content.interbank.model_copy(update={"source_ids": list(evidence["sources"])[:1]})
    calls = []
    def model(prompt, schema, model, key):
        name = prompt.split("SECTION: ")[1].splitlines()[0]
        calls.append(name)
        return (accepted.model_dump_json() if name == "interbank" else "{}"), {}
    result = generate_sections(snapshot, tmp_path, [("test", "test", "fake", model, 2)])
    assert result.interbank == accepted
    assert calls.count("interbank") == 1
    assert calls.count("china") == 2
    assert any(i.code == "INCOMPLETE_SECTION" for i in validate_content(result, snapshot))
    records = json.loads((tmp_path / "model-attempts.json").read_text(encoding="utf-8"))
    assert records[0]["section"] == "interbank"
    assert records[0]["status"] == "valid"


def test_outage_is_not_repeated_for_each_section(tmp_path):
    import pytest
    import requests
    snapshot, _ = fixtures()
    calls = []
    def unavailable(*args):
        calls.append(True)
        response = requests.Response()
        response.status_code = 402
        raise requests.HTTPError(response=response)
    with pytest.raises(RuntimeError, match="HTTP 402"):
        generate_sections(snapshot, tmp_path, [("test", "test", "fake", unavailable, 3)])
    assert len(calls) == 1
