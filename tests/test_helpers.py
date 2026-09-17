"""Unit tests for app.py pure helpers. No network, no yt-dlp needed."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app  # noqa: E402


# --- validate_url -----------------------------------------------------------
@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "http://youtube.com/watch?v=x",
    "https://m.youtube.com/watch?v=x",
    "https://music.youtube.com/watch?v=x",
    "https://youtu.be/dQw4w9WgXcQ",
    "youtu.be/dQw4w9WgXcQ",  # scheme auto-added
    "https://www.youtube-nocookie.com/embed/x",
    "https://WWW.YOUTUBE.COM/watch?v=x",
])
def test_validate_url_accepts_youtube(url):
    assert app.validate_url(url).startswith("http")


@pytest.mark.parametrize("url", [
    "https://vimeo.com/123",
    "https://evilyoutube.com/watch?v=x",
    "https://youtube.com.evil.com/x",
    "https://notyoutu.be/x",
    "file:///etc/passwd",
    "ftp://youtube.com/x",
    "",
    "   ",
])
def test_validate_url_rejects_non_youtube(url):
    with pytest.raises(ValueError):
        app.validate_url(url)


def test_validate_url_rejects_oversize():
    with pytest.raises(ValueError, match="too long"):
        app.validate_url("https://youtube.com/" + "x" * 2000)


# --- safe_child -------------------------------------------------------------
def test_safe_child_allows_inside():
    assert app.safe_child(app.WEB_DIR, "index.html") is not None


@pytest.mark.parametrize("name", ["../app.py", "..", "/etc/passwd", ".gitkeep", "sub/../../x"])
def test_safe_child_blocks_escape_and_dotfiles(name):
    assert app.safe_child(app.DOWNLOAD_DIR, name) is None


# --- time parsing -----------------------------------------------------------
@pytest.mark.parametrize("raw, want", [
    ("90", 90.0), ("1:30", 90.0), ("01:30", 90.0), ("1:02:03", 3723.0),
    ("1:02:03.5", 3723.5), ("", None), (None, None), ("abc", None),
    ("1:2:3:4", None), ("-5", None), ("1:-30", None),
])
def test_parse_time_to_seconds(raw, want):
    assert app.parse_time_to_seconds(raw) == want


def test_format_seconds():
    assert app.format_seconds(None) == "-"
    assert app.format_seconds(90) == "1:30"
    assert app.format_seconds(3723) == "1:02:03"


def test_format_bytes():
    assert app.format_bytes(None) is None
    assert app.format_bytes(999) == "999 B"
    assert app.format_bytes(250_000_000) == "250.0 MB"


# --- quality mapping --------------------------------------------------------
def test_quality_to_format_known_and_numeric():
    assert app.quality_to_format("best") == app.QUALITY_FORMATS["best"]
    assert "height<=720" in app.quality_to_format("720")
    assert "height<=1080" in app.quality_to_format("1080")
    assert app.quality_to_format("bogus") == app.QUALITY_FORMATS["best"]


# --- clean_text --------------------------------------------------------------
def test_clean_text_strips_ansi_and_controls():
    out = app.clean_text("\x1b[31mhello\x1b[0m\r\nwor\x00ld")
    assert out == "hello\nworld"


def test_clean_text_truncates_tail():
    assert app.clean_text("x" * 3000, limit=100) == "x" * 100


# --- summarize_formats -------------------------------------------------------
def test_summarize_formats_ladder():
    info = {"formats": [
        {"vcodec": "avc1", "acodec": "none", "height": 1080, "ext": "mp4",
         "fps": 30, "filesize": 250_000_000, "format_id": "248"},
        {"vcodec": "avc1", "acodec": "none", "height": 720, "ext": "mp4",
         "fps": 30, "filesize_approx": 90_000_000, "format_id": "22"},
        {"vcodec": "none", "acodec": "opus", "height": None, "ext": "webm",
         "format_id": "251"},
    ]}
    ladder = app.summarize_formats(info)
    ids = [q["id"] for q in ladder]
    assert ids[0] == "best" and "1080" in ids and "720" in ids
    q1080 = next(q for q in ladder if q["id"] == "1080")
    assert q1080["filesize"] == 250_000_000
    assert q1080["filesize_str"] == "250.0 MB"
    assert "audio_mp3" in ids and "audio_m4a" in ids


# --- cookies -----------------------------------------------------------------
def test_resolve_cookies_modes(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "SESSION_COOKIE_FILE", tmp_path / "session.txt")
    assert app.resolve_cookies({"cookies_mode": "none"}) == {}
    with pytest.raises(ValueError, match="Unsupported browser"):
        app.resolve_cookies({"cookies_mode": "browser", "cookies_browser": "netscape"})
    opts = app.resolve_cookies({"cookies_mode": "browser", "cookies_browser": "firefox"})
    assert opts["cookiesfrombrowser"][0] == "firefox"
    with pytest.raises(ValueError, match="No cookies"):
        app.resolve_cookies({"cookies_mode": "upload"})


def test_resolve_cookies_per_job_isolation(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "COOKIES_DIR", tmp_path)
    monkeypatch.setattr(app, "SESSION_COOKIE_FILE", tmp_path / "session.txt")
    app.resolve_cookies({"cookies_mode": "text", "cookies_text": "AAA"}, job_id="job1")
    app.resolve_cookies({"cookies_mode": "text", "cookies_text": "BBB"}, job_id="job2")
    assert (tmp_path / "job-job1.cookies.txt").read_text() == "AAA"
    assert (tmp_path / "job-job2.cookies.txt").read_text() == "BBB"
    assert not (tmp_path / "session.txt").exists()


def test_resolve_cookies_persist_false_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "COOKIES_DIR", tmp_path)
    monkeypatch.setattr(app, "SESSION_COOKIE_FILE", tmp_path / "session.txt")
    app.resolve_cookies({"cookies_mode": "text", "cookies_text": "AAA"}, persist=False)
    assert list(tmp_path.iterdir()) == []


# --- build_download_opts validation ------------------------------------------
def test_build_download_opts_rejects_bad_ranges():
    job = {"id": "validate"}
    with pytest.raises(ValueError, match="after 'From'"):
        app.build_download_opts(job, {"clip_from": "02:00", "clip_to": "01:00"})
    with pytest.raises(ValueError, match="numbers"):
        app.build_download_opts(job, {"playlist_mode": "playlist",
                                      "playlist_start": "abc"})
    with pytest.raises(ValueError, match="invalid"):
        app.build_download_opts(job, {"playlist_mode": "playlist",
                                      "playlist_items": "1-5; DROP TABLE"})
    with pytest.raises(ValueError, match="language"):
        app.build_download_opts(job, {"subtitles": True, "sub_lang": "en; rm -rf"})


def test_build_download_opts_audio_and_subs():
    job = {"id": "validate"}
    opts = app.build_download_opts(job, {"quality": "audio_mp3"})
    assert opts["postprocessors"][0]["preferredcodec"] == "mp3"
    opts = app.build_download_opts(job, {"subtitles": True, "sub_lang": "en,de"})
    assert opts["subtitleslangs"] == ["en", "de"]
