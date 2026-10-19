import email
import imaplib
import subprocess
import sys
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

import mail_fetch
import mail_ingest
import mail_ocr
import mail_parse

CDN = "https://cdn.kobo.com/book-images/abc-123/1200/1200/False/{}.jpg"
BAR = "https://shopjp.kobo.com/assets/x/20261015_dailydeal/images/bar{}.jpg"
TRACK = "https://shopjp.kobo.com/pub/cc?_ri_=SECRET"


def book_html(n: int, product: str, title: str, list_price: str, sell: str, desc: str = "") -> str:
    description = f'<p id="book{n}-description">{desc}</p>' if desc else ""
    return (
        f'<a id="book{n}-img-product-link" href="{TRACK}"><img id="book{n}-img-image-link" src="{CDN.format(product)}"></a>'
        f'<p><a id="book{n}-title" href="{TRACK}">{title}</a></p>'
        f'<p>優惠價 $ <del id="book{n}-list-price">{list_price}</del></p>'
        f'<p id="book{n}-sell-price">{sell}</p>{description}'
    )


def make_email(subject: str, html: str, date: str, sender: str = "樂天Kobo <news@shopjp.kobo.com>") -> bytes:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = "me@example.com"
    message["Subject"] = subject
    message["Date"] = date
    message.set_content(html, subtype="html")
    return message.as_bytes()


SELECT_HTML = (
    "<span>10 本夯書限時85折優惠起，輸入WKSelect1019每本再享85折</span>"
    + book_html(1, "HB7tVogQOzeycgEGzy4cgw", "不懂權謀的人", "255", "85折價格$ 217", "職場險惡")
    + book_html(2, "ai-627", "超級代理效應", "1,319", "85折價格$ 1,121")
)
SELECT_EMAIL = make_email("【本週精選】曉星", SELECT_HTML, "Mon, 19 Oct 2026 02:00:20 +0000")

DD99_HTML = (
    "<span>10/15-10/21 一週 99 元完整書單搶先看</span>"
    f'<img src="{BAR.format(1)}">' + book_html(1, "ai-146", "李飛飛", "$450", "特定價$ 99")
    + book_html(2, "zcPp", "超高齡", "$300", "特定價$ 99")
    + f'<img src="{BAR.format(2)}">' + book_html(3, "n1438", "霍金传", "$235", "特定價$ 99")
)
DD99_EMAIL = make_email("【今日 99】李飛飛", DD99_HTML, "Thu, 15 Oct 2026 02:00:22 +0000")


def fake_bars(texts):
    calls = []

    def read(urls):
        calls.append(urls)
        return texts

    return read, calls


def test_select_email_parses_rank_prices_promo_and_period():
    result = mail_parse.parse_email(SELECT_EMAIL, fake_bars([])[0])
    assert result["slug"] == "weekly-8585-2026-w43"
    assert (result["period_start"], result["period_end"]) == ("2026-10-19", "2026-10-25")
    assert result["promo_code"] == "WKSelect1019"
    first, second = result["books"]
    assert first == {
        "title": "不懂權謀的人",
        "description": "職場險惡",
        "kobo_url": "https://www.kobo.com/tw/zh/ebook/HB7tVogQOzeycgEGzy4cgw",
        "cover_url": "https://cdn.kobo.com/book-images/abc-123/300/300/False/HB7tVogQOzeycgEGzy4cgw.jpg",
        "list_price": 255,
        "sale_price": 217,
        "rank": 1,
    }
    assert (second["list_price"], second["sale_price"], second["rank"]) == (1319, 1121, 2)


def test_tracking_links_are_never_stored():
    result = mail_parse.parse_email(SELECT_EMAIL, fake_bars([])[0])
    assert "SECRET" not in repr(result)


def test_99_email_maps_banner_weekdays_to_dates_including_extra_day():
    read, calls = fake_bars(["-星期四限定- | - 星期五限定-", "- 星期五加碼-"])
    result = mail_parse.parse_email(DD99_EMAIL, read)
    assert calls == [[BAR.format(1), BAR.format(2)]]
    assert result["slug"] == "weekly-dd99-2026-w42"
    assert [b["sale_date"] for b in result["books"]] == ["2026-10-15", "2026-10-16", "2026-10-16"]
    assert all(b["sale_price"] == 99 for b in result["books"])
    assert result["books"][0]["list_price"] == 450


def test_99_period_across_new_year():
    html = DD99_HTML.replace("10/15-10/21", "12/31-1/6")
    raw = make_email("【今日 99】x", html, "Thu, 31 Dec 2026 02:00:00 +0000")
    result = mail_parse.parse_email(raw, fake_bars(["星期四 | 星期六", "星期三"])[0])
    assert [b["sale_date"] for b in result["books"]] == ["2026-12-31", "2027-01-02", "2027-01-06"]
    assert result["slug"] == "weekly-dd99-2026-w53"


@pytest.mark.parametrize(
    "texts, message",
    [
        (["星期四", "星期五"], "expected 2 weekdays"),
        (["?? | ??", "星期五"], "expected 2 weekdays"),
    ],
)
def test_99_unreadable_banner_fails_loudly(texts, message):
    with pytest.raises(mail_parse.MailParseError, match=message):
        mail_parse.parse_email(DD99_EMAIL, fake_bars(texts)[0])


def test_99_without_banners_or_period_fails():
    no_bars = make_email("【今日 99】x", DD99_HTML.replace("bar", "pic"), "Thu, 15 Oct 2026 02:00:22 +0000")
    with pytest.raises(mail_parse.MailParseError, match="banners"):
        mail_parse.parse_email(no_bars, fake_bars([])[0])
    no_period = make_email("【今日 99】x", DD99_HTML.replace("10/15-10/21", ""), "Thu, 15 Oct 2026 02:00:22 +0000")
    with pytest.raises(mail_parse.MailParseError, match="period"):
        mail_parse.parse_email(no_period, fake_bars([])[0])


def test_banner_on_unexpected_host_is_rejected():
    html = DD99_HTML.replace("https://shopjp.kobo.com/assets", "https://evil.example/assets")
    raw = make_email("【今日 99】x", html, "Thu, 15 Oct 2026 02:00:22 +0000")
    with pytest.raises(mail_parse.MailParseError, match="host"):
        mail_parse.parse_email(raw, fake_bars(["星期四 | 星期五", "星期五"])[0])


@pytest.mark.parametrize(
    "raw, message",
    [
        (make_email("【本週精選】x", SELECT_HTML, "x", sender="Phisher <news@kobo.com.evil.example>"), "Not a Kobo"),
        (make_email("Your receipt", SELECT_HTML, "Mon, 19 Oct 2026 02:00:20 +0000"), "Not a Kobo"),
        (make_email("【本週精選】x", "<p>no books</p>", "Mon, 19 Oct 2026 02:00:20 +0000"), "No books"),
        (make_email("【本週精選】x", SELECT_HTML.replace("cdn.kobo.com", "evil.example"), "Mon, 19 Oct 2026 02:00:20 +0000"),
         "cover"),
        (make_email("【本週精選】x", SELECT_HTML, "not a date"), "Date"),
    ],
)
def test_bad_emails_are_rejected(raw, message):
    with pytest.raises(mail_parse.MailParseError, match=message):
        mail_parse.parse_email(raw, fake_bars([])[0])


def test_latest_per_kind_keeps_newest_and_ignores_other_mail():
    older = make_email("【本週精選】old", SELECT_HTML, "Mon, 12 Oct 2026 02:00:20 +0000")
    other = make_email("Kobo 書展", SELECT_HTML, "Tue, 20 Oct 2026 02:00:20 +0000")
    latest = mail_ingest.latest_per_kind([SELECT_EMAIL, older, DD99_EMAIL, other])
    assert set(latest) == {"8585", "dd99"}
    assert latest["8585"] == SELECT_EMAIL


def test_mail_ingest_main_offline_writes_data(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(mail_ingest, "DATA_DIR", tmp_path)
    monkeypatch.setattr(mail_ingest, "read_bars", fake_bars(["星期四 | 星期五", "星期五"])[0])
    eml = tmp_path / "a.eml"
    eml.write_bytes(DD99_EMAIL)
    assert mail_ingest.main(["--eml", str(eml)]) == 0
    assert capsys.readouterr().out.strip() == "weekly-dd99-2026-w42"
    assert (tmp_path / "weekly-dd99-2026-w42.json").exists()


def test_mail_ingest_main_reports_parse_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(mail_ingest, "DATA_DIR", tmp_path)
    monkeypatch.setattr(mail_ingest, "read_bars", fake_bars(["?", "?"])[0])
    eml = tmp_path / "a.eml"
    eml.write_bytes(DD99_EMAIL)
    assert mail_ingest.main(["--eml", str(eml)]) == 1


def test_mail_ingest_main_requires_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("KOBO99_ENV_PATH", str(tmp_path / "missing.env"))
    monkeypatch.delenv("GMAIL_USER", raising=False)
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    assert mail_ingest.main([]) == 1


def test_mail_ingest_main_without_kobo_mail(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("GMAIL_USER=me@example.com\nGMAIL_APP_PASSWORD='abcd efgh'\n")
    monkeypatch.setenv("KOBO99_ENV_PATH", str(env))
    monkeypatch.delenv("GMAIL_USER", raising=False)
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    seen = {}

    def fake_fetch(user, password, days):
        seen.update(user=user, password=password, days=days)
        return []

    monkeypatch.setattr(mail_ingest, "fetch_kobo_emails", fake_fetch)
    assert mail_ingest.main([]) == 1
    assert seen == {"user": "me@example.com", "password": "abcdefgh", "days": mail_ingest.LOOKBACK_DAYS}


class FakeImap:
    def __init__(self, host, *, fail_login=False):
        self.fail_login = fail_login
        self.selected = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user, password):
        if self.fail_login:
            raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")

    def list(self):
        return "OK", [b'(\\HasNoChildren) "/" "INBOX"', b'(\\All \\HasNoChildren) "/" "[Gmail]/&YkBnCZCuTvY-"']

    def select(self, mailbox, readonly=False):
        self.selected = (mailbox, readonly)
        return "OK", [b"2"]

    def search(self, charset, *criteria):
        assert criteria[0] == "X-GM-RAW" and "news@shopjp.kobo.com" in criteria[1]
        return "OK", [b"1 2"]

    def fetch(self, message_id, parts):
        assert parts == "(BODY.PEEK[])"
        return "OK", [(b"1 (BODY[] {3}", b"raw" + message_id)]


def test_fetch_uses_all_mail_readonly_and_peek(monkeypatch):
    instances = []

    def factory(host):
        instances.append(FakeImap(host))
        return instances[-1]

    monkeypatch.setattr(mail_fetch.imaplib, "IMAP4_SSL", factory)
    assert mail_fetch.fetch_kobo_emails("u", "p", 8) == [b"raw1", b"raw2"]
    assert instances[0].selected == ('"[Gmail]/&YkBnCZCuTvY-"', True)


def test_fetch_wraps_login_errors(monkeypatch):
    monkeypatch.setattr(mail_fetch.imaplib, "IMAP4_SSL", lambda host: FakeImap(host, fail_login=True))
    with pytest.raises(mail_fetch.MailFetchError, match="AUTHENTICATIONFAILED"):
        mail_fetch.fetch_kobo_emails("u", "p", 8)


def test_read_bars_maps_ocr_output_to_inputs(monkeypatch):
    monkeypatch.setattr(mail_ocr, "_download", lambda url, target: target.write_bytes(b"img"))

    def fake_run(cmd, **kwargs):
        lines = [f"{path}\t星期{n}" for n, path in zip("四五", cmd[-2:])]
        return subprocess.CompletedProcess(cmd, 0, stdout="\n".join(reversed(lines)) + "\n", stderr="")

    monkeypatch.setattr(mail_ocr.subprocess, "run", fake_run)
    assert mail_ocr.read_bars(["https://a/bar1.jpg", "https://a/bar2.jpg"]) == ["星期四", "星期五"]


def test_read_bars_wraps_ocr_failure(monkeypatch):
    monkeypatch.setattr(mail_ocr, "_download", lambda url, target: target.write_bytes(b"img"))

    def boom(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(mail_ocr.subprocess, "run", boom)
    with pytest.raises(mail_ocr.OcrError):
        mail_ocr.read_bars(["https://a/bar1.jpg"])
