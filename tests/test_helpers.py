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
    "file:///etc/passwd",
    "ftp://youtube.com/x",
    "http://127.0.0.1:8000/api/jobs",
    "http://192.168.1.5/Mediasite/Play/x",
    "http://[::1]/x",
    "http://localhost/x",
    "http://nas.local/x",
    "",
    "   ",
])
def test_validate_url_rejects_bad_and_local(url):
    with pytest.raises(ValueError):
        app.validate_url(url)


@pytest.mark.parametrize("url, sid, label", [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "youtube", "YouTube"),
    ("https://youtu.be/dQw4w9WgXcQ", "youtube", "YouTube"),
    ("https://music.youtube.com/watch?v=dQw4w9WgXcQ", "youtube", "YouTube"),
    ("https://www.tiktok.com/@scout2015/video/6718335390845095173", "tiktok", "TikTok"),
    ("https://www.instagram.com/reel/CxyzABC123/", "instagram", "Instagram"),
    ("https://twitter.com/user/status/1234567890", "x", "X"),
    ("https://x.com/user/status/1234567890", "x", "X"),
    ("https://www.reddit.com/r/pics/comments/abc123/title/", "reddit", "Reddit"),
    ("https://www.facebook.com/watch/?v=123456", "facebook", "Facebook"),
    ("https://vimeo.com/76979871", "vimeo", "Vimeo"),
    ("https://www.twitch.tv/videos/123456", "twitch", "Twitch"),
    ("https://soundcloud.com/artist/track", "soundcloud", "SoundCloud"),
    ("https://artist.bandcamp.com/track/song", "bandcamp", "Bandcamp"),
    ("https://www.dailymotion.com/video/x7tgad0", "dailymotion", "Dailymotion"),
    ("https://www.bilibili.com/video/BV1xx411c7mD", "bilibili", "Bilibili"),
    ("https://www.pinterest.com/pin/664281013778109217/", "pinterest", "Pinterest"),
    ("https://www.bbc.co.uk/news/av/world-123", "bbc", "bbc.co.uk"),
])
def test_resolve_source_known_sites(url, sid, label):
    src = app.resolve_source(url)
    assert (src["id"], src["label"]) == (sid, label)
    assert "yt-dlp" in src["engines"]


@pytest.mark.parametrize("url, match", [
    ("https://evilyoutube.com/watch?v=dQw4w9WgXcQ", "doesn't support"),
    ("https://youtube.com.evil.com/watch?v=dQw4w9WgXcQ", "doesn't support"),
    ("https://notyoutu.be/dQw4w9WgXcQ", "doesn't support"),
    ("https://example.com/page", "doesn't support"),
    ("https://open.spotify.com/track/abc", "DRM"),
])
def test_resolve_source_rejects_unknown_and_drm(url, match):
    with pytest.raises(ValueError, match=match):
        app.resolve_source(url)


@pytest.mark.parametrize("host, domain", [
    ("www.youtube.com", "youtube.com"),
    ("m.youtu.be", "youtube.com"),
    ("pin.it", "pinterest.com"),
    ("www.pinterest.co.uk", "pinterest.co.uk"),
    ("twitter.com", "x.com"),
    ("www.nicovideo.jp", "nicovideo.jp"),
])
def test_site_domain(host, domain):
    assert app.site_domain(host) == domain


def test_validate_url_rejects_oversize():
    with pytest.raises(ValueError, match="too long"):
        app.validate_url("https://youtube.com/" + "x" * 2000)


@pytest.mark.parametrize("url, kind", [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "video"),
    ("https://www.youtube.com/watch?v=x&list=RDxxxx", "video"),
    ("https://youtu.be/dQw4w9WgXcQ", "video"),
    ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "video"),
    ("https://www.youtube.com/playlist?list=PLxxxx", "playlist"),
    ("https://www.youtube.com/channel/UCxxxx", "channel"),
    ("https://www.youtube.com/@someone/videos", "channel"),
    ("https://www.youtube.com/", "other"),
    ("https://www.youtube.com/results?search_query=x", "other"),
])
def test_classify_youtube_url(url, kind):
    assert app.classify_youtube_url(url) == kind


def test_check_url_blocks_youtube_channels():
    with pytest.raises(ValueError, match="Channel pages"):
        app.check_url("https://www.youtube.com/@someone")
    with pytest.raises(ValueError, match="isn't a video"):
        app.check_url("https://www.youtube.com/results?search_query=x")
    url, src = app.check_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=RDxxxx")
    assert url.startswith("http") and src["id"] == "youtube"


def test_inspect_opts_watch_list_is_single_video():
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=RDxxxx"
    opts = app._inspect_opts({"cookies_mode": "none"}, "info-x", url)
    assert opts["noplaylist"] is True


def test_inspect_opts_playlist_is_flat():
    url = "https://www.youtube.com/playlist?list=PLxxxx"
    opts = app._inspect_opts({"cookies_mode": "none"}, "info-x", url)
    assert opts["extract_flat"] == "in_playlist"
    assert opts["playlistend"] == 50
    assert opts["noplaylist"] is False


def test_inspect_opts_other_sites_flat_and_single():
    opts = app._inspect_opts({"cookies_mode": "none"}, "info-x",
                             "https://www.tiktok.com/@scout2015")
    assert opts["extract_flat"] == "in_playlist" and opts["playlistend"] == 50
    assert opts["noplaylist"] is True


# --- CORS / origin trust ----------------------------------------------------
@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8000",
    "http://localhost:8000",
    "http://[::1]:8000",
    "https://127.0.0.1",
    "chrome-extension://abcdefghijklmnopqrstuvwxyz123456",
])
def test_origin_is_trusted(url):
    assert app.origin_is_trusted(url)


@pytest.mark.parametrize("url", [
    "https://evil.com",
    "http://127.0.0.1.evil.com",
    "https://example.com",
    "chrome-extension://",
    "file:///etc/passwd",
    "ftp://127.0.0.1",
    "",
])
def test_origin_is_untrusted(url):
    assert not app.origin_is_trusted(url)


def test_cors_allow_origin_reflects_only_trusted():
    assert app.cors_allow_origin(None) is None
    assert app.cors_allow_origin("https://evil.com") is None
    assert app.cors_allow_origin("http://127.0.0.1:8000") == "http://127.0.0.1:8000"
    ext = "chrome-extension://abcdefghijklmnopqrstuvwxyz123456"
    assert app.cors_allow_origin(ext) == ext


def test_public_job_strips_payload():
    job = {"id": "abc", "status": "done", "log": ["a", "b", "c"],
           "payload": {"cookies_mode": "upload", "url": "https://youtu.be/x"}}
    out = app.public_job(job, log_limit=2)
    assert "payload" not in out
    assert out["log"] == ["b", "c"]
    assert job["payload"]["cookies_mode"] == "upload"  # original untouched


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


HTTPONLY_FOREIGN = "#HttpOnly_.facebook.com\tTRUE\t/\tTRUE\t9999999999\tc_user\tfb"


def test_filter_cookies_keeps_youtube_drops_foreign():
    raw = "\n".join([
        "# Netscape HTTP Cookie File",
        HTTPONLY_ROW, YOUTUBE_ROW, GOOGLE_ROW, FOREIGN_ROW, HTTPONLY_FOREIGN,
        "not-a-cookie-row",
    ])
    out = app.filter_cookies_text(raw, "youtube")
    assert "SID" in out and "LOGIN_INFO" in out and "APISID" in out
    assert "facebook" not in out and "c_user" not in out
    assert "not-a-cookie-row" in out  # non-rows pass through untouched


def test_filter_cookies_drops_httponly_foreign_only_export():
    raw = "\n".join(["# Netscape HTTP Cookie File", HTTPONLY_FOREIGN, HTTPONLY_ROW])
    out = app.filter_cookies_text(raw, "youtube")
    assert "SID" in out
    assert "facebook" not in out and "c_user" not in out


def test_filter_cookies_adds_magic_header_when_missing():
    out = app.filter_cookies_text(YOUTUBE_ROW + "\n", "youtube")
    assert out.startswith("# Netscape HTTP Cookie File")
    assert "LOGIN_INFO" in out


def test_filter_cookies_leaves_non_export_byte_identical():
    assert app.filter_cookies_text("AAA", "youtube") == "AAA"
    assert app.filter_cookies_text("", "youtube") == ""


def test_resolve_cookies_trims_export_to_youtube(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "COOKIES_DIR", tmp_path)
    monkeypatch.setattr(app, "SESSION_COOKIE_FILE", tmp_path / "session.txt")
    raw = "# Netscape HTTP Cookie File\n" + YOUTUBE_ROW + "\n" + FOREIGN_ROW + "\n"
    app.resolve_cookies({"cookies_mode": "upload", "cookies_text": raw,
                         "url": "https://youtu.be/dQw4w9WgXcQ"}, job_id="trim1")
    written = (tmp_path / "job-trim1.cookies.txt").read_text()
    assert "LOGIN_INFO" in written and "facebook" not in written


X_ROW = ".x.com\tTRUE\t/\tTRUE\t9999999999\tauth_token\tt"
TWITTER_ROW = ".twitter.com\tTRUE\t/\tTRUE\t9999999999\tct0\tc"


def test_filter_cookies_trims_to_target_site():
    raw = "\n".join(["# Netscape HTTP Cookie File", X_ROW, TWITTER_ROW, YOUTUBE_ROW, GOOGLE_ROW])
    out = app.filter_cookies_text(raw, "x")
    assert "auth_token" in out and "ct0" in out  # twitter.com folds into x
    assert "LOGIN_INFO" not in out and "APISID" not in out  # google is YouTube's sibling only


def test_resolve_cookies_names_the_site_when_export_misses_it(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "COOKIES_DIR", tmp_path)
    raw = "# Netscape HTTP Cookie File\n" + YOUTUBE_ROW + "\n"
    with pytest.raises(ValueError, match="No TikTok cookies"):
        app.resolve_cookies({"cookies_mode": "upload", "cookies_text": raw,
                             "url": "https://www.tiktok.com/@a/video/6718335390845095173"})


def test_resolve_cookies_rejects_export_without_youtube(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "COOKIES_DIR", tmp_path)
    monkeypatch.setattr(app, "SESSION_COOKIE_FILE", tmp_path / "session.txt")
    raw = "# Netscape HTTP Cookie File\n" + FOREIGN_ROW + "\n"
    with pytest.raises(ValueError, match="No YouTube cookies"):
        app.resolve_cookies({"cookies_mode": "upload", "cookies_text": raw,
                             "url": "https://youtu.be/dQw4w9WgXcQ"})


def _clear_runtime_cache():
    app._js_runtime_cache.update(at=0.0, runtimes=None, node_version=None)
    app._health_cache.update(at=0.0, checks={})


def test_base_ydl_opts_enables_installed_js_runtimes(monkeypatch):
    _clear_runtime_cache()
    monkeypatch.setattr(app.shutil, "which",
                        lambda c: "/usr/bin/node" if c == "node" else None)
    monkeypatch.setattr(app, "_cmd_version", lambda argv: "v23.5.0")
    opts = app.base_ydl_opts({"cookies_mode": "none"})
    assert opts["js_runtimes"] == {"node": {}}
    assert opts["remote_components"] == ["ejs:npm"]


def test_base_ydl_opts_ignores_unsupported_node(monkeypatch):
    _clear_runtime_cache()
    monkeypatch.setattr(app.shutil, "which",
                        lambda c: "/usr/bin/node" if c == "node" else None)
    monkeypatch.setattr(app, "_cmd_version", lambda argv: "v18.19.0")
    opts = app.base_ydl_opts({"cookies_mode": "none"})
    assert "js_runtimes" not in opts
    assert "remote_components" not in opts


def test_base_ydl_opts_no_runtime_no_keys(monkeypatch):
    _clear_runtime_cache()
    monkeypatch.setattr(app.shutil, "which", lambda c: None)
    opts = app.base_ydl_opts({"cookies_mode": "none"})
    assert "js_runtimes" not in opts
    assert "remote_components" not in opts


def test_node_meets_yt_dlp():
    assert app.node_meets_yt_dlp("v23.5.0")
    assert app.node_meets_yt_dlp("v24.1.0")
    assert not app.node_meets_yt_dlp("v18.19.0")
    assert not app.node_meets_yt_dlp("v23.4.0")
    assert not app.node_meets_yt_dlp(None)


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


def test_cleanup_job_temps_removes_part_files(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DOWNLOAD_DIR", tmp_path)
    (tmp_path / "Test Video [abc123].mp4.part").write_bytes(b"partial")
    (tmp_path / "Test Video [abc123].mp4").write_bytes(b"keep")
    (tmp_path / "Other [zzz].mp4.part").write_bytes(b"other")
    job = {
        "seen_files": ["Test Video [abc123].mp4.part", "Test Video [abc123].mp4",
                       "Test Video [abc123]"],
        "seen_ids": ["abc123"],
    }
    app.cleanup_job_temps(job)
    names = {p.name for p in tmp_path.iterdir()}
    assert "Test Video [abc123].mp4" in names
    assert "Test Video [abc123].mp4.part" not in names
    assert "Other [zzz].mp4.part" in names


def test_progress_hook_cancel_raises():
    app.jobs.clear()
    app.jobs["j"] = {"id": "j", "status": "downloading", "progress": 0,
                     "log": [], "files": [], "cancel_requested": True}
    try:
        with pytest.raises(Exception, match="Cancelled"):
            app.progress_hook("j", {"status": "downloading", "filename": "x.mp4"})
    finally:
        app.jobs.clear()


# --- public_job whitelist ----------------------------------------------------
def test_public_job_whitelists_internal_fields():
    job = {
        "id": "abc", "url": "https://youtu.be/x", "quality": "720",
        "status": "downloading", "progress": 42, "detail": "Downloading…",
        "log": ["a"], "files": [], "created_at": "t", "finished_at": None,
        "downloaded_bytes": 1, "total_bytes": 2, "speed": 3, "eta": 4,
        "playlist_index": 1, "playlist_count": 2,
        # internals that must never reach a client:
        "filename": "/srv/offtube/downloads/Secret [x].mp4",
        "seen_files": ["Secret [x].mp4"], "seen_ids": ["x"],
        "cancel_requested": False, "payload": {"cookies_text": "SECRET"},
    }
    out = app.public_job(job)
    for leak in ("filename", "seen_files", "seen_ids", "cancel_requested", "payload"):
        assert leak not in out
    # public fields survive
    for k in ("id", "url", "quality", "status", "progress", "detail", "log",
              "files", "created_at", "downloaded_bytes", "total_bytes",
              "speed", "eta", "playlist_index", "playlist_count"):
        assert k in out
    # original untouched
    assert job["payload"]["cookies_text"] == "SECRET"


def test_public_job_log_limit():
    job = {"id": "a", "log": [str(i) for i in range(50)]}
    assert app.public_job(job, log_limit=5)["log"] == ["45", "46", "47", "48", "49"]


# --- cross-job temp protection -------------------------------------------------
def test_cleanup_job_temps_skips_file_another_live_job_writes(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DOWNLOAD_DIR", tmp_path)
    (tmp_path / "Video [abc].f137.mp4.part").write_bytes(b"partial")
    (tmp_path / "Video [abc].mp4").write_bytes(b"done")
    (tmp_path / "Other [zzz].mp4.part").write_bytes(b"keep")
    app.jobs.clear()
    app.jobs["a"] = {"id": "a", "status": "downloading",
                     "seen_files": ["Video [abc].f137.mp4.part"], "seen_ids": ["abc"]}
    app.jobs["b"] = {"id": "b", "status": "downloading",
                     "seen_files": ["Video [abc].f137.mp4.part"], "seen_ids": ["abc"]}
    try:
        app.cleanup_job_temps(app.jobs["a"])
        names = {p.name for p in tmp_path.iterdir()}
        # b is still writing it — a must not delete it
        assert "Video [abc].f137.mp4.part" in names
        assert "Video [abc].mp4" in names
        assert "Other [zzz].mp4.part" in names
    finally:
        app.jobs.clear()


def test_cleanup_job_temps_deletes_own_orphan(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DOWNLOAD_DIR", tmp_path)
    (tmp_path / "Video [abc].f137.mp4.part").write_bytes(b"partial")
    (tmp_path / "Video [abc].mp4").write_bytes(b"done")
    app.jobs.clear()
    app.jobs["a"] = {"id": "a", "status": "downloading",
                     "seen_files": ["Video [abc].f137.mp4.part"], "seen_ids": ["abc"]}
    try:
        app.cleanup_job_temps(app.jobs["a"])
        names = {p.name for p in tmp_path.iterdir()}
        assert "Video [abc].f137.mp4.part" not in names
        assert "Video [abc].mp4" in names
    finally:
        app.jobs.clear()


def test_job_claims_name():
    job = {"seen_files": ["Video [abc].mp4", "Clip [abc]"], "seen_ids": ["abc"]}
    assert app._job_claims_name(job, "Video [abc].f137.mp4.part")
    assert app._job_claims_name(job, "Clip [abc].webm.part")
    assert app._job_claims_name(job, "Other [abc].mp4.part")  # id match
    assert not app._job_claims_name(job, "Other [xyz].mp4.part")


# --- cookie_file_for sanitization ---------------------------------------------
def test_cookie_file_for_sanitizes_job_id(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "COOKIES_DIR", tmp_path)
    p = app.cookie_file_for("../../etc/passwd")
    assert p.name == "job-etcpasswd.cookies.txt"
    assert p.parent == tmp_path


# --- is_terminal / active count -----------------------------------------------
def test_is_terminal():
    assert app.is_terminal({"status": "done"})
    assert app.is_terminal({"status": "error"})
    assert app.is_terminal({"status": "cancelled"})
    assert not app.is_terminal({"status": "downloading"})
    assert not app.is_terminal({"status": "queued"})


def test_active_job_count_locked():
    app.jobs.clear()
    app.jobs["a"] = {"id": "a", "status": "queued"}
    app.jobs["b"] = {"id": "b", "status": "downloading"}
    app.jobs["c"] = {"id": "c", "status": "done"}
    try:
        assert app.active_job_count_locked() == 2
    finally:
        app.jobs.clear()


def test_evict_jobs_locked_drops_oldest_terminal():
    app.jobs.clear()
    app.jobs["old"] = {"id": "old", "status": "done", "finished_at": "2020-01-01T00:00:00"}
    app.jobs["new"] = {"id": "new", "status": "done", "finished_at": "2021-01-01T00:00:00"}
    app.futures["old"] = object()
    try:
        app.MAX_JOBS = 1
        app.evict_jobs_locked()
        assert "old" not in app.jobs and "new" in app.jobs
        assert "old" not in app.futures
    finally:
        app.MAX_JOBS = 50
        app.jobs.clear()
        app.futures.clear()


# --- resolve_cookies "file" mode ----------------------------------------------
def test_resolve_cookies_file_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "COOKIES_DIR", tmp_path)
    monkeypatch.setattr(app, "SESSION_COOKIE_FILE", tmp_path / "session.txt")
    opts = app.resolve_cookies({"cookies_mode": "file", "cookies_text": "AAA"}, job_id="f1")
    assert opts["cookiefile"].endswith("job-f1.cookies.txt")


# --- detect_js_runtimes cache ---------------------------------------------------
def test_detect_js_runtimes_caches(monkeypatch):
    app._js_runtime_cache.update(at=0.0, runtimes=None, node_version=None)
    calls = []
    monkeypatch.setattr(app.shutil, "which",
                        lambda c: calls.append(c) or ("/usr/bin/deno" if c == "deno" else None))
    first = app.detect_js_runtimes()
    second = app.detect_js_runtimes()
    assert first == second == {"deno": {}}
    # deno + node probes ran once total, not once per call (cached)
    assert calls == ["deno", "node"]


# --- summarize_formats without audio -------------------------------------------
def test_summarize_formats_no_audio():
    info = {"formats": [
        {"vcodec": "avc1", "acodec": "none", "height": 1080, "ext": "mp4",
         "format_id": "248"},
    ]}
    ids = [q["id"] for q in app.summarize_formats(info)]
    assert ids == ["best", "1080"]  # no audio entries


# --- quality_to_format numeric --------------------------------------------------
def test_quality_to_format_numeric_height():
    assert "height<=480" in app.quality_to_format("480")
    assert "height<=1080" in app.quality_to_format("1080")
    assert "height<=9999" in app.quality_to_format("9999")


# --- filter_cookies_text foreign-only export ------------------------------------
def test_filter_cookies_text_foreign_only_returns_rows():
    raw = "# Netscape HTTP Cookie File\n" + FOREIGN_ROW + "\n"
    out = app.filter_cookies_text(raw, "youtube")
    assert "facebook" not in out and "c_user" not in out
    assert out.startswith("# Netscape HTTP Cookie File")


# --- postprocessor_hook ----------------------------------------------------------
def test_postprocessor_hook_sets_processing():
    app.jobs.clear()
    app.jobs["j"] = {"id": "j", "status": "downloading", "progress": 0,
                     "log": [], "files": []}
    try:
        app.postprocessor_hook("j", {"status": "started", "postprocessor_name": "Merger"})
        assert app.jobs["j"]["status"] == "processing"
        app.postprocessor_hook("j", {"status": "finished"})
        assert app.jobs["j"]["detail"] == "Post-processing done."
    finally:
        app.jobs.clear()


def test_postprocessor_hook_cancel_raises():
    app.jobs.clear()
    app.jobs["j"] = {"id": "j", "status": "processing", "progress": 0,
                     "log": [], "files": [], "cancel_requested": True}
    try:
        with pytest.raises(Exception, match="Cancelled"):
            app.postprocessor_hook("j", {"status": "started"})
    finally:
        app.jobs.clear()


# --- JobLogger -------------------------------------------------------------------
def test_job_logger_appends_cleaned():
    app.jobs.clear()
    app.jobs["j"] = {"id": "j", "status": "downloading", "progress": 0,
                     "log": [], "files": []}
    try:
        log = app.JobLogger("j")
        log.info("hello\x1b[31m world\r")
        log.debug("[debug] skipped")
        log.warning("careful")
        assert app.jobs["j"]["log"] == ["hello world", "WARNING: careful"]
    finally:
        app.jobs.clear()


# --- attribute_new_files fallback (no hooks saw anything) ------------------------
def test_attribute_new_files_raw_diff_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DOWNLOAD_DIR", tmp_path)
    before = set(tmp_path.iterdir())
    (tmp_path / "Brand New [qqq].mp4").write_bytes(b"x")
    (tmp_path / "half.mp4.part").write_bytes(b"y")
    job = {"seen_files": [], "seen_ids": []}
    assert app.attribute_new_files(job, before) == ["Brand New [qqq].mp4"]


# --- cleanup_stale_files -----------------------------------------------------------
def test_cleanup_stale_files_removes_orphans(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DOWNLOAD_DIR", tmp_path / "dl")
    monkeypatch.setattr(app, "COOKIES_DIR", tmp_path / "ck")
    (tmp_path / "dl").mkdir()
    (tmp_path / "ck").mkdir()
    (tmp_path / "dl" / "half.mp4.part").write_bytes(b"x")
    (tmp_path / "dl" / "keep.mp4").write_bytes(b"y")
    (tmp_path / "ck" / "job-abc.cookies.txt").write_bytes(b"z")
    (tmp_path / "ck" / ".gitkeep").write_bytes(b"")
    app.cleanup_stale_files()
    assert not (tmp_path / "dl" / "half.mp4.part").exists()
    assert (tmp_path / "dl" / "keep.mp4").exists()
    assert not (tmp_path / "ck" / "job-abc.cookies.txt").exists()
    assert (tmp_path / "ck" / ".gitkeep").exists()


# --- environment_checks ------------------------------------------------------------
def test_environment_checks_reports_missing(monkeypatch):
    app._health_cache.update(at=0.0, checks={})
    app._js_runtime_cache.update(at=0.0, runtimes=None, node_version=None)
    monkeypatch.setattr(app.shutil, "which", lambda c: None)
    monkeypatch.setattr(app, "_cmd_version", lambda argv: None)
    monkeypatch.setattr(app, "yt_dlp", None)
    checks = app.environment_checks()
    assert checks["ffmpeg"] is False
    assert checks["yt_dlp"] == "missing"
    assert checks["js_runtime"] == "none"
    assert checks["js_runtime_ok"] is False


def test_output_name_caps_long_captions_and_keeps_id():
    import yt_dlp
    opts = app.build_download_opts({"id": "validate"}, {}, persist=False)
    name = yt_dlp.YoutubeDL({"outtmpl": opts["outtmpl"]}).prepare_filename(
        {"title": "🔥" * 200 + " caption", "id": "abc", "ext": "mp4"})
    base = Path(name).name
    assert len(base.encode()) <= 255 and base.endswith("[abc].mp4")


@pytest.mark.parametrize("msg, needle", [
    ("ERROR: [youtube] x: Join this channel to get access", "members-only"),
    ("Sign in to confirm you're not a bot", "bot-check"),
    ("The page needs to be reloaded.", "Re-export fresh cookies"),
    ("ERROR: [Instagram] x: Requested content is not available, rate-limit "
     "reached or login required. Use --cookies", "Instagram only shows this"),
    ("ERROR: [TikTok] x: This video is private", "Instagram only shows this"),
    ("The uploader has not made this video available in your country", "region-locks"),
    ("ffmpeg not found", "ffmpeg is required"),
    ("HTTP Error 404: Not Found", ""),
])
def test_failure_hint(msg, needle):
    hint = app.failure_hint(msg, "Instagram")
    assert (needle in hint) if needle else hint == ""


# --- gallery-dl engine (fake CLI, no network) -----------------------------------
FAKE_GALLERY = r'''
import json, pathlib, sys, time
args = sys.argv[1:]
url = args[-1]
if "-j" in args:
    if "missing" in url:
        print(json.dumps([[-1, {"error": "NotFoundError", "message": "Requested pin could not be found"}]]))
    else:
        meta = {"category": "pinterest", "subcategory": "board", "extension": "jpg",
                "board": {"name": "Moodboard"}, "pinner": {"full_name": "Ann"}}
        print(json.dumps([
            [2, meta],
            [3, "https://i.pinimg.com/originals/a.jpg", dict(meta, id="1", title="A")],
            [3, "ytdl:https://v1.pinimg.com/b.m3u8", dict(meta, id="2", extension="mp4",
                                                       image_medium_url="https://i.pinimg.com/b.jpg")],
            [3, "text:follow now", dict(meta, id="2", extension="txt")],
        ]))
    sys.exit(0)
d = pathlib.Path(args[args.index("-D") + 1])
if "slow" in url:
    time.sleep(30)
if "fail" in url:
    print("[pinterest][error] Requested pin could not be found", flush=True)
    sys.exit(8)
(d / "pinterest_1.jpg").write_bytes(b"img")
print(d / "pinterest_1.jpg", flush=True)
print("# " + str(d / "pinterest_0.jpg"), flush=True)
if "partial" in url:
    print("[pinterest][error] HttpError: 403 Forbidden", flush=True)
    sys.exit(4)
'''


@pytest.fixture()
def fake_gallery(monkeypatch):
    monkeypatch.setattr(app, "gallery_cmd", lambda payload, cookie_id: [sys.executable, "-c", FAKE_GALLERY])


@pytest.mark.parametrize("url, engines", [
    ("https://www.pinterest.com/pin/664281013778109217/", ["gallery-dl", "yt-dlp"]),
    ("https://pin.it/1Q2YHZ5Hb", ["gallery-dl"]),
    ("https://www.pinterest.ca/mashal0407/cool-diys/", ["gallery-dl", "yt-dlp"]),
    ("https://x.com/user/status/1234567890", ["yt-dlp", "gallery-dl"]),
    ("https://www.deviantart.com/artist/art/Title-123456", ["gallery-dl"]),
    ("https://www.artstation.com/artwork/abc", ["gallery-dl"]),
    ("https://vimeo.com/76979871", ["yt-dlp"]),
])
def test_resolve_source_engine_order(url, engines):
    assert app.resolve_source(url)["engines"] == engines


def test_resolve_source_ignores_gallery_catch_alls():
    with pytest.raises(ValueError, match="doesn't support"):
        app.resolve_source("https://i.pinimg.com/originals/aa/bb/cc.jpg")


def test_gallery_cmd_cookie_args(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "COOKIES_DIR", tmp_path)
    cmd = app.gallery_cmd({"cookies_mode": "browser", "cookies_browser": "firefox"}, "g1")
    assert cmd[cmd.index("--cookies-from-browser") + 1] == "firefox"
    assert "--config-ignore" in cmd and "--no-input" in cmd
    cmd = app.gallery_cmd({"cookies_mode": "upload", "cookies_text": "AAA"}, "g2")
    assert cmd[cmd.index("--cookies") + 1] == str(tmp_path / "job-g2.cookies.txt")
    assert "--cookies" not in app.gallery_cmd({"cookies_mode": "none"}, "g3")


@pytest.mark.parametrize("payload, args", [
    ({"playlist_mode": "single", "playlist_items": "1-3"}, []),
    ({"playlist_mode": "playlist"}, []),
    ({"playlist_mode": "playlist", "playlist_items": "1-5;7"}, ["--range", "1-5,7"]),
    ({"playlist_mode": "playlist", "playlist_start": "3"}, ["--range", "3-"]),
    ({"playlist_mode": "playlist", "playlist_end": "10"}, ["--range", "1-10"]),
])
def test_gallery_range_args(payload, args):
    assert app.gallery_range_args(payload) == args


def test_inspect_gallery_lists_files_skips_text(fake_gallery):
    src = {"id": "pinterest", "label": "Pinterest"}
    info = app.inspect_gallery("https://www.pinterest.com/ann/moodboard/", {}, src, "i1")
    assert info["type"] == "gallery" and info["count"] == 2
    assert info["media"] == "mixed"
    assert info["title"] == "Moodboard" and info["uploader"] == "Ann"
    assert info["entries"][0]["thumbnail"] == "https://i.pinimg.com/originals/a.jpg"
    assert info["entries"][1]["thumbnail"] == "https://i.pinimg.com/b.jpg"  # video: poster, not m3u8


def test_inspect_gallery_surfaces_error(fake_gallery):
    with pytest.raises(RuntimeError, match="could not be found"):
        app.inspect_gallery("https://www.pinterest.com/pin/1/?missing", {}, {"label": "Pinterest"}, "i2")


def _gallery_job(jid):
    app.jobs[jid] = {"id": jid, "status": "starting", "progress": 0,
                     "detail": "", "log": [], "files": []}
    return app.jobs[jid]


def test_run_gallery_collects_saved_and_existing(tmp_path, monkeypatch, fake_gallery):
    monkeypatch.setattr(app, "DOWNLOAD_DIR", tmp_path)
    job = _gallery_job("g-ok")
    try:
        files = app.run_gallery(job, {}, "https://www.pinterest.com/pin/1/")
        assert files == ["pinterest_1.jpg", "pinterest_0.jpg"]
        assert "proc" not in job
    finally:
        app.jobs.clear()


def test_run_gallery_partial_keeps_files_and_warns(tmp_path, monkeypatch, fake_gallery):
    monkeypatch.setattr(app, "DOWNLOAD_DIR", tmp_path)
    job = _gallery_job("g-partial")
    try:
        assert app.run_gallery(job, {}, "https://www.pinterest.com/pin/1/?partial")
        assert any("1 item(s) failed" in ln for ln in job["log"])
    finally:
        app.jobs.clear()


def test_run_gallery_nothing_saved_raises_last_error(tmp_path, monkeypatch, fake_gallery):
    monkeypatch.setattr(app, "DOWNLOAD_DIR", tmp_path)
    job = _gallery_job("g-fail")
    try:
        with pytest.raises(app.DownloadError, match="could not be found"):
            app.run_gallery(job, {}, "https://www.pinterest.com/pin/1/?fail")
    finally:
        app.jobs.clear()


def test_run_download_falls_back_to_gallery_for_photo_posts(tmp_path, monkeypatch, fake_gallery):
    from types import SimpleNamespace
    monkeypatch.setattr(app, "DOWNLOAD_DIR", tmp_path)
    monkeypatch.setattr(app, "COOKIES_DIR", tmp_path / "cookies")
    (tmp_path / "cookies").mkdir()

    class DL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def download(self, urls):
            raise app.DownloadError("ERROR: [twitter] 1: No video could be found in this tweet")

    monkeypatch.setattr(app, "yt_dlp", SimpleNamespace(YoutubeDL=DL))
    job = _gallery_job("x-photos")
    try:
        app.run_download("x-photos", {"url": "https://x.com/user/status/1234567890"})
        assert job["status"] == "done", job["detail"]
        assert job["files"] == ["pinterest_1.jpg", "pinterest_0.jpg"]
        assert any("trying gallery-dl" in ln for ln in job["log"])
    finally:
        app.jobs.clear()


# --- in-flight disk guard ----------------------------------------------------------
def _low_disk(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(app.shutil, "disk_usage",
                        lambda *a, **k: SimpleNamespace(free=app.MIN_DISK_FREE - 1))


def test_progress_hook_stops_when_disk_runs_low(monkeypatch):
    _low_disk(monkeypatch)
    app.jobs["d1"] = {"id": "d1", "status": "downloading", "log": []}
    try:
        with pytest.raises(app.DownloadError, match="disk is almost full"):
            app.progress_hook("d1", {"status": "downloading", "downloaded_bytes": 1})
        assert app.jobs["d1"]["disk_full"] is True
        # throttled: the next hook inside DISK_CHECK_EVERY doesn't re-probe
        assert app.disk_low_locked(app.jobs["d1"]) is False
    finally:
        app.jobs.clear()


def test_run_download_disk_full_is_error_not_engine_fallback(tmp_path, monkeypatch, fake_gallery):
    _low_disk(monkeypatch)
    monkeypatch.setattr(app, "DOWNLOAD_DIR", tmp_path)
    job = _gallery_job("d2")
    try:
        app.run_download("d2", {"url": "https://www.pinterest.com/pin/664281013778109217/"})
        assert job["status"] == "error"
        assert "disk is almost full" in job["detail"]
        assert not any("trying yt-dlp" in ln for ln in job["log"])
    finally:
        app.jobs.clear()


# --- the web UI's "works with" strip only names sites that resolve -------------------
FEATURED = {
    "YouTube": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "TikTok": "https://www.tiktok.com/@scout2015/video/6718335390845095173",
    "Instagram": "https://www.instagram.com/p/CxyzABC123/",
    "Pinterest": "https://www.pinterest.com/pin/664281013778109217/",
    "X": "https://x.com/user/status/1234567890",
    "Reddit": "https://www.reddit.com/r/pics/comments/abc123/title/",
    "Facebook": "https://www.facebook.com/watch/?v=123456",
    "Bluesky": "https://bsky.app/profile/user.bsky.social/post/3kabc",
    "Tumblr": "https://user.tumblr.com/post/123/slug",
    "Vimeo": "https://vimeo.com/76979871",
    "Twitch": "https://www.twitch.tv/videos/123456",
    "Dailymotion": "https://www.dailymotion.com/video/x7tgad0",
    "Bilibili": "https://www.bilibili.com/video/BV1xx411c7mD",
    "SoundCloud": "https://soundcloud.com/artist/track",
    "Bandcamp": "https://artist.bandcamp.com/track/song",
    "Mixcloud": "https://www.mixcloud.com/user/mix/",
    "Flickr": "https://www.flickr.com/photos/user/123456/",
    "DeviantArt": "https://www.deviantart.com/artist/art/Title-123456",
    "ArtStation": "https://www.artstation.com/artwork/abc",
    "Imgur": "https://imgur.com/gallery/abcDEF",
}


def test_featured_sources_in_web_ui_all_resolve():
    import re
    html = (Path(app.__file__).parent / "web" / "index.html").read_text()
    strip = re.search(r'class="sources"[^>]*>(.*?)<span>', html).group(1)
    assert {n.strip() for n in strip.split("·") if n.strip()} == set(FEATURED)
    for label, url in FEATURED.items():
        assert app.resolve_source(url)["label"] == label, url


# --- display fields --------------------------------------------------------------------
def test_display_thumbnail_only_http():
    assert app.display_thumbnail("https://i.ytimg.com/vi/x/hq.jpg") == "https://i.ytimg.com/vi/x/hq.jpg"
    assert app.display_thumbnail("javascript:alert(1)") is None
    assert app.display_thumbnail("data:image/png;base64,AAA") is None
    assert app.display_thumbnail(None) is None


@pytest.mark.parametrize("name, kind", [
    ("Clip [abc].mp4", "video"), ("Song [x].mp3", "audio"), ("Track [x].m4a", "audio"),
    ("pinterest_1.jpg", "image"), ("pin.png", "image"), ("subs.en.srt", "other"),
])
def test_file_kind(name, kind):
    assert app.file_kind(name) == kind


def test_progress_hook_fills_title_and_thumbnail_once():
    app.jobs["t1"] = {"id": "t1", "status": "starting", "log": []}
    try:
        app.progress_hook("t1", {"status": "downloading", "info_dict": {
            "id": "x", "title": "Me at the zoo", "thumbnail": "https://i.ytimg.com/x.jpg"}})
        app.progress_hook("t1", {"status": "downloading", "info_dict": {
            "id": "y", "title": "Second", "thumbnail": "https://i.ytimg.com/y.jpg"}})
        assert app.jobs["t1"]["title"] == "Me at the zoo"
        assert app.jobs["t1"]["thumbnail"] == "https://i.ytimg.com/x.jpg"
    finally:
        app.jobs.clear()


def test_entry_thumbnail_prefers_single_then_largest_listed():
    assert app.entry_thumbnail({"thumbnail": "https://a/1.jpg"}) == "https://a/1.jpg"
    assert app.entry_thumbnail({"thumbnails": [{"url": "https://a/s.jpg"}, {"url": "https://a/l.jpg"}]}) == "https://a/l.jpg"
    assert app.entry_thumbnail({"thumbnails": []}) is None
