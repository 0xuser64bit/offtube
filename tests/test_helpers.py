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


# --- cookie export trimming ---------------------------------------------------
YOUTUBE_ROW = ".youtube.com\tTRUE\t/\tFALSE\t9999999999\tLOGIN_INFO\tabc"
GOOGLE_ROW = ".google.com\tTRUE\t/\tTRUE\t9999999999\tAPISID\tg"
HTTPONLY_ROW = "#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t9999999999\tSID\ts3cret"
FOREIGN_ROW = ".facebook.com\tTRUE\t/\tTRUE\t9999999999\tc_user\tfb"


def test_filter_cookies_keeps_youtube_drops_foreign():
    raw = "\n".join([
        "# Netscape HTTP Cookie File",
        HTTPONLY_ROW, YOUTUBE_ROW, GOOGLE_ROW, FOREIGN_ROW, "not-a-cookie-row",
    ])
    out = app.filter_cookies_text(raw)
    assert "SID" in out and "LOGIN_INFO" in out and "APISID" in out
    assert "facebook" not in out and "c_user" not in out
    assert "not-a-cookie-row" in out  # non-rows pass through untouched


def test_filter_cookies_adds_magic_header_when_missing():
    out = app.filter_cookies_text(YOUTUBE_ROW + "\n")
    assert out.startswith("# Netscape HTTP Cookie File")
    assert "LOGIN_INFO" in out


def test_filter_cookies_leaves_non_export_byte_identical():
    assert app.filter_cookies_text("AAA") == "AAA"
    assert app.filter_cookies_text("") == ""


def test_resolve_cookies_trims_export_to_youtube(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "COOKIES_DIR", tmp_path)
    monkeypatch.setattr(app, "SESSION_COOKIE_FILE", tmp_path / "session.txt")
    raw = "# Netscape HTTP Cookie File\n" + YOUTUBE_ROW + "\n" + FOREIGN_ROW + "\n"
    app.resolve_cookies({"cookies_mode": "upload", "cookies_text": raw}, job_id="trim1")
    written = (tmp_path / "job-trim1.cookies.txt").read_text()
    assert "LOGIN_INFO" in written and "facebook" not in written


def test_resolve_cookies_rejects_export_without_youtube(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "COOKIES_DIR", tmp_path)
    monkeypatch.setattr(app, "SESSION_COOKIE_FILE", tmp_path / "session.txt")
    raw = "# Netscape HTTP Cookie File\n" + FOREIGN_ROW + "\n"
    with pytest.raises(ValueError, match="No YouTube cookies"):
        app.resolve_cookies({"cookies_mode": "upload", "cookies_text": raw})


def test_base_ydl_opts_enables_installed_js_runtimes(monkeypatch):
    monkeypatch.setattr(app.shutil, "which",
                        lambda c: "/usr/bin/node" if c == "node" else None)
    opts = app.base_ydl_opts({"cookies_mode": "none"})
    assert opts["js_runtimes"] == {"node": {}}
    assert opts["remote_components"] == ["ejs:npm"]


def test_base_ydl_opts_no_runtime_no_keys(monkeypatch):
    monkeypatch.setattr(app.shutil, "which", lambda c: None)
    opts = app.base_ydl_opts({"cookies_mode": "none"})
    assert "js_runtimes" not in opts
    assert "remote_components" not in opts


# --- session fallback (alternate player clients) ------------------------------
def test_is_session_failure_markers():
    assert app._is_session_failure("Sign in to confirm you're not a bot")
    assert app._is_session_failure("ERROR: [youtube] x: The page needs to be reloaded.")
    assert not app._is_session_failure("Join this channel to get access")
    assert not app._is_session_failure(None)


def test_base_ydl_opts_player_clients():
    opts = app.base_ydl_opts({"cookies_mode": "none"}, player_clients=["tv"])
    assert opts["extractor_args"] == {"youtube": {"player_client": ["tv"]}}
    assert "extractor_args" not in app.base_ydl_opts({"cookies_mode": "none"})


def _stub_yt_dlp_fail_once(calls, exc_msg="The page needs to be reloaded"):
    from types import SimpleNamespace

    class DL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download=False):
            calls.append(self.opts.get("extractor_args"))
            if not self.opts.get("extractor_args"):
                raise Exception(exc_msg)
            return {"id": "x", "title": "T", "uploader": "u", "duration": 10,
                    "thumbnail": None, "webpage_url": url, "is_live": False,
                    "formats": [], "subtitles": {}, "automatic_captions": {}}

    return SimpleNamespace(YoutubeDL=DL)


def test_fetch_info_retries_session_failure_with_fallback(monkeypatch):
    calls = []
    monkeypatch.setattr(app, "yt_dlp", _stub_yt_dlp_fail_once(calls))
    info = app.fetch_info("https://www.youtube.com/watch?v=xxxxxxxxxxx",
                          {"cookies_mode": "none"})
    assert info["title"] == "T"
    assert calls[0] is None
    assert calls[1] == {"youtube": {"player_client": app.FALLBACK_PLAYER_CLIENTS}}


def test_fetch_info_no_retry_on_non_session_error(monkeypatch):
    calls = []
    monkeypatch.setattr(app, "yt_dlp", _stub_yt_dlp_fail_once(calls, "Private video"))
    with pytest.raises(Exception, match="Private video"):
        app.fetch_info("https://www.youtube.com/watch?v=xxxxxxxxxxx",
                       {"cookies_mode": "none"})
    assert len(calls) == 1


def test_run_download_retries_session_failure_with_fallback(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(app, "DOWNLOAD_DIR", tmp_path)
    monkeypatch.setattr(app, "COOKIES_DIR", tmp_path / "cookies")
    (tmp_path / "cookies").mkdir()
    seen = []

    class DL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def download(self, urls):
            seen.append(self.opts.get("extractor_args"))
            if not self.opts.get("extractor_args"):
                raise app.DownloadError("The page needs to be reloaded")
            (tmp_path / "V [x].mp4").write_bytes(b"v")

    monkeypatch.setattr(app, "yt_dlp", SimpleNamespace(YoutubeDL=DL))
    app.jobs.clear()
    jid = "fallback1"
    app.jobs[jid] = {"id": jid, "status": "queued", "progress": 0,
                     "detail": "", "log": [], "files": []}
    try:
        app.run_download(jid, {"url": "https://youtu.be/xxxxxxxxxxx", "quality": "best"})
        job = app.jobs[jid]
        assert job["status"] == "done"
        assert seen[1] == {"youtube": {"player_client": app.FALLBACK_PLAYER_CLIENTS}}
        assert any("alternate player" in ln for ln in job["log"])
    finally:
        app.jobs.clear()


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


# --- regression: cookie isolation -------------------------------------------
def test_validate_path_writes_no_cookie_file(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "COOKIES_DIR", tmp_path)
    monkeypatch.setattr(app, "SESSION_COOKIE_FILE", tmp_path / "session.txt")
    body = {"cookies_mode": "text", "cookies_text": "SECRET"}
    app.resolve_cookies(body, persist=False)
    app.build_download_opts({"id": "validate"}, body, persist=False)
    assert list(tmp_path.iterdir()) == []


def test_is_temp_file():
    assert app.is_temp_file("a.mp4.part")
    assert app.is_temp_file("a.temp")
    assert app.is_temp_file("a.ytdl")
    assert app.is_temp_file(".gitkeep")
    assert not app.is_temp_file("Video [abc123].mp4")
    assert not app.is_temp_file("Audio [x].mp3")


def test_attribute_prefers_stem_and_id(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DOWNLOAD_DIR", tmp_path)
    job = {
        "seen_files": ["Test Video [abc123].mp4.part", "Test Video [abc123].mp4", "Test Video [abc123]"],
        "seen_ids": ["abc123"],
    }
    before = set(tmp_path.iterdir())
    (tmp_path / "Test Video [abc123].mp3").write_bytes(b"x")  # mp4->mp3 rename
    (tmp_path / "Other [zzz].mp4").write_bytes(b"y")
    (tmp_path / "half.mp4.part").write_bytes(b"z")
    assert app.attribute_new_files(job, before) == ["Test Video [abc123].mp3"]


def test_playlist_progress_blends():
    app.jobs.clear()
    app.jobs["j"] = {"id": "j", "status": "queued", "progress": 0, "log": [], "files": []}
    try:
        app.progress_hook("j", {
            "status": "downloading", "downloaded_bytes": 50, "total_bytes": 100,
            "filename": "/tmp/V [a].mp4",
            "info_dict": {"playlist_index": 2, "playlist_count": 4, "id": "a"},
        })
        assert app.jobs["j"]["progress"] == 37.5
        assert "[2/4]" in app.jobs["j"]["detail"]
    finally:
        app.jobs.clear()


def test_progress_hook_cancel_raises():
    app.jobs.clear()
    app.jobs["j"] = {"id": "j", "status": "downloading", "progress": 0,
                     "log": [], "files": [], "cancel_requested": True}
    try:
        with pytest.raises(Exception, match="Cancelled"):
            app.progress_hook("j", {"status": "downloading", "filename": "x.mp4"})
    finally:
        app.jobs.clear()
