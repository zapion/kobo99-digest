import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

import kobo99
import publisher

TODAY = dt.date(2026, 10, 16)


class FakeGitHub:
    def __init__(self, listing):
        self.listing = listing
        self.puts: dict[str, dict] = {}

    def __call__(self, method, path, token, body=None):
        if method == "PUT":
            self.puts[path.rsplit("/", 1)[-1]] = body
            return {"content": {}}
        if path.endswith("/contents/"):
            return self.listing
        return {"message": "Not Found"}


@pytest.fixture
def output_dir(tmp_path, monkeypatch):
    payload = {"books": [{"title": "t", "sale_start": "2026-10-16", "sale_price": "NT$99", "kobo_price": "NT$1"}]}
    monkeypatch.setattr(kobo99, "fetch_latest", lambda: payload)
    kobo99.run(tmp_path, TODAY)
    monkeypatch.setattr(publisher, "taipei_today", lambda: TODAY)
    return tmp_path


def test_publish_writes_day_page_and_index_with_history(output_dir, monkeypatch):
    fake = FakeGitHub([{"name": "2026-10-15.html"}, {"name": "README.md"}])
    monkeypatch.setattr(publisher, "gh_request", fake)
    publisher.publish(output_dir, "o/r", "tok")
    assert set(fake.puts) == {"2026-10-16.html", "index.html"}


def test_publish_without_fetched_data_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(publisher, "taipei_today", lambda: TODAY)
    with pytest.raises(RuntimeError):
        publisher.publish(tmp_path, "o/r", "tok")


def test_main_skips_when_credentials_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("KOBO99_ENV_PATH", str(tmp_path / "none"))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPO", raising=False)
    assert publisher.main() == 1


def test_load_env_file_does_not_override_existing(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("# c\nA_KEY=from-file\nB_KEY='quoted'\n")
    monkeypatch.setenv("A_KEY", "preset")
    monkeypatch.delenv("B_KEY", raising=False)
    publisher.load_env_file(env)
    assert publisher.os.environ["A_KEY"] == "preset"
    assert publisher.os.environ["B_KEY"] == "quoted"
