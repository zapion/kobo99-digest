import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

import ingest

KOBO = "https://www.kobo.com/tw/zh/ebook/"


def item(heading: str, title: str, **extra) -> dict:
    return {
        "heading": heading,
        "description": "desc",
        "title": title,
        "author": "由 李飛飛（Fei-Fei Li）＠著",
        "publisher": "天下文化",
        "kobo_url": KOBO + "x?utm_source=twblog",
        "kobo_hk_url": "https://www.kobo.com/hk/zh/ebook/x",
        "cover_url": "https://kobo-blog.transforms.svdcdn.com/c.jpeg",
        **extra,
    }


def payload(slug: str, title: str, items: list[dict], intro: str = "") -> dict:
    return {
        "source": "kobo-blog-bookmarklet",
        "version": 1,
        "url": f"https://www.kobo.com/zh/blog/{slug}",
        "title": title,
        "intro": intro,
        "items": items,
    }


DD99 = payload(
    "weekly-dd99-2026-w42",
    "【一週99書單】柳林中的風聲（10/15-10/21）",
    [
        item("10/16 週五 Kobo99選書:《B》", "《B》"),
        item("10/15 週四 Kobo99選書:《A》", "《A》"),
    ],
)
P8585 = payload(
    "weekly-8585-2026-w43",
    "【本週精選書單】內在小孩。（10/19-10/25）",
    [item("#2《B》", "《B》"), item("#1《A》", "《A》")],
    intro="本週折扣代碼：WKSelect1019（10/19-10/25)",
)


def test_dd99_books_get_sale_dates_and_clean_author():
    result = ingest.parse_payload(DD99)
    assert result["kind"] == "dd99"
    assert (result["period_start"], result["period_end"]) == ("2026-10-15", "2026-10-21")
    assert [b["sale_date"] for b in result["books"]] == ["2026-10-16", "2026-10-15"]
    assert result["books"][0]["author"] == "李飛飛（Fei-Fei Li）"
    assert result["books"][0]["title"] == "B"


def test_8585_has_rank_and_promo_code():
    result = ingest.parse_payload(P8585)
    assert result["label"] == "本週精選書單"
    assert result["promo_code"] == "WKSelect1019"
    assert [b["rank"] for b in result["books"]] == [2, 1]
    assert "sale_date" not in result["books"][0]


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("《柳林風聲：年度暢銷書《蛤蟆先生去看心理師》故事原型》", "柳林風聲：年度暢銷書《蛤蟆先生去看心理師》故事原型"),
        ("《阿爾卑斯看臺的母親》【本屋大賞TOP2】", "阿爾卑斯看臺的母親"),
        ("沒有書名號", "沒有書名號"),
    ],
)
def test_clean_title_handles_nested_and_trailing_text(raw, expected):
    assert ingest._clean_title(raw) == expected


def test_period_crossing_new_year_in_iso_week_1():
    start, end = ingest._parse_period("（12/31-1/6）", 2027, 1)
    assert (start.isoformat(), end.isoformat()) == ("2026-12-31", "2027-01-06")


def test_sale_date_across_year_boundary_lands_in_period():
    data = payload(
        "weekly-dd99-2027-w1",
        "【一週99書單】（12/31-1/6）",
        [item("1/2 週六 Kobo99選書:", "《A》"), item("12/31 週四 Kobo99選書:", "《B》")],
    )
    dates = [b["sale_date"] for b in ingest.parse_payload(data)["books"]]
    assert dates == ["2027-01-02", "2026-12-31"]


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda p: {**p, "source": "other"}, "bookmarklet output"),
        (lambda p: {**p, "items": []}, "no books"),
        (lambda p: {**p, "url": "https://www.kobo.com/zh/blog/booklist_enough"}, "Not a Kobo weekly"),
        (lambda p: {**p, "url": "https://evil.example/zh/blog/weekly-dd99-2026-w42"}, "Not a Kobo weekly"),
        (lambda p: {**p, "title": "沒有期間"}, "period"),
        (lambda p: {**p, "items": [item("10/30 週五", "《A》")]}, "outside"),
        (lambda p: {**p, "items": [item("沒有日期", "《A》")]}, "No sale date"),
        (lambda p: {**p, "items": [item("10/15", "《A》", kobo_url="javascript:alert(1)")]}, "Kobo link"),
    ],
)
def test_invalid_payloads_are_rejected(mutate, message):
    with pytest.raises(ingest.IngestError, match=message):
        ingest.parse_payload(mutate(DD99))


def test_non_https_cover_and_hk_links_are_dropped():
    data = {**DD99, "items": [item("10/15", "《A》", cover_url="javascript:x", kobo_hk_url="http://x")]}
    book = ingest.parse_payload(data)["books"][0]
    assert book["cover_url"] == "" and book["kobo_hk_url"] == ""


def test_manifest_replaces_same_slug_and_sorts_newest_first():
    older = ingest.parse_payload(DD99)
    newer = ingest.parse_payload(P8585)
    manifest = ingest.update_manifest(ingest.update_manifest({}, older), newer)
    manifest = ingest.update_manifest(manifest, older)
    assert [e["slug"] for e in manifest["lists"]] == ["weekly-8585-2026-w43", "weekly-dd99-2026-w42"]


def test_ingest_file_writes_data_and_archives_raw(tmp_path):
    source = tmp_path / "weekly-dd99-2026-w42.json"
    source.write_text(json.dumps(DD99, ensure_ascii=False), encoding="utf-8")
    data_dir = tmp_path / "data"
    ingest.ingest_file(source, data_dir)
    assert not source.exists()
    assert (data_dir / "raw" / "weekly-dd99-2026-w42.json").exists()
    saved = json.loads((data_dir / "weekly-dd99-2026-w42.json").read_text(encoding="utf-8"))
    assert len(saved["books"]) == 2
    manifest = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["lists"][0]["slug"] == "weekly-dd99-2026-w42"


def test_ingest_file_rejects_bad_json_and_keeps_file(tmp_path):
    source = tmp_path / "weekly-dd99-2026-w42.json"
    source.write_text("not json", encoding="utf-8")
    with pytest.raises(ingest.IngestError):
        ingest.ingest_file(source, tmp_path / "data")
    assert source.exists()


def test_main_reports_failure_but_processes_other_files(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ingest, "DATA_DIR", tmp_path / "data")
    good = tmp_path / "weekly-8585-2026-w43.json"
    good.write_text(json.dumps(P8585, ensure_ascii=False), encoding="utf-8")
    bad = tmp_path / "weekly-dd99-2026-w42.json"
    bad.write_text("{}", encoding="utf-8")
    assert ingest.main([str(bad), str(good)]) == 1
    assert capsys.readouterr().out.strip() == "weekly-8585-2026-w43"


def test_main_without_inputs_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(ingest.Path, "home", lambda: tmp_path)
    assert ingest.main([]) == 1
