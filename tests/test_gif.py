"""GIF jobs: boundary validation, isolation, cancellation and real FFmpeg output.

The encoder tests use generated local video; no network or live server needed.
"""
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app


@pytest.mark.parametrize("field,value", [
    ("gif_size", 0), ("gif_size", "480"), ("gif_size", 480.0),
    ("gif_size", True), ("gif_size", 4096), ("gif_fps", 0),
    ("gif_fps", "15"), ("gif_fps", 60), ("gif_loop", "false"),
    ("gif_loop", 1), ("gif_loop", None),
])
def test_invalid_gif_settings(field, value):
    with pytest.raises(ValueError, match="GIF"):
        app.gif_settings({field: value})


def test_gif_requires_encoder_before_queuing(monkeypatch):
    monkeypatch.setattr(app.shutil, "which", lambda _: None)
    jid, error = app.enqueue_download({"url": "https://youtu.be/abc123abc12", "quality": "gif"})
    assert jid is None and error[1] == 400 and "ffmpeg" in error[0]


@pytest.mark.parametrize("value", ["nan", "inf", "1:nan", "1:inf", "1:", ":10", "1::2", "1e309", "9" * 400])
def test_time_must_be_finite_and_complete(value):
    assert app.parse_time_to_seconds(value) is None


@pytest.mark.parametrize("extra", [{"clip_to": "0"}, {"clip_from": "2", "clip_to": "1"}, {"clip_from": []}])
def test_invalid_range_rejected_before_queue(extra, monkeypatch):
    monkeypatch.setattr(app.shutil, "which", lambda _: "/ffmpeg")
    jid, error = app.enqueue_download({"url": "https://youtu.be/abc123abc12", "quality": "gif", **extra})
    assert jid is None and error[1] == 400


def test_gif_options_use_private_source_and_disable_subtitles(tmp_path, monkeypatch):
    monkeypatch.setattr(app.shutil, "which", lambda _: "/ffmpeg")
    opts = app.build_download_opts({"id": "gif", "gif_dir": str(tmp_path)},
                                   {"quality": "gif", "subtitles": True, "clip_from": "1.25", "clip_to": "2.5"})
    assert str(tmp_path) in opts["outtmpl"]
    assert opts["format"] == "bv/b[vcodec!=none]/b"
    assert not opts.get("writesubtitles")
    assert len(opts["post_hooks"]) == 1
    assert list(opts["download_ranges"]({}, None)) == [{"start_time": 1.25, "end_time": 2.5}]
    with pytest.raises(app.DownloadError, match="finished video"):
        opts["match_filter"]({"is_live": True}, incomplete=True)
    with pytest.raises(app.DownloadError, match="past the end"):
        opts["match_filter"]({"duration": 1})


@pytest.fixture
def gif_job(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DOWNLOAD_DIR", tmp_path)
    monkeypatch.setattr(app, "COOKIES_DIR", tmp_path)
    job = {"id": "gif-test", "quality": "gif", "status": "queued", "files": [], "log": []}
    app.jobs[job["id"]] = job
    yield job
    app.jobs.pop(job["id"], None)


def test_gif_job_cleanup_on_failure(gif_job, monkeypatch):
    folders = []
    def fail(job, *_):
        folder = Path(job["gif_dir"])
        folders.append(folder)
        (folder / "unfinished.mp4").write_bytes(b"source")
        raise app.DownloadError("source failed")
    monkeypatch.setattr(app, "run_gif_ytdlp", fail)
    app.run_download(gif_job["id"], {"url": "https://youtu.be/abc123abc12", "quality": "gif"})
    assert gif_job["status"] == "error"
    assert not folders[0].exists()
    assert "gif_dir" not in gif_job and not gif_job["files"]


def test_gif_cancellation_before_conversion(gif_job):
    gif_job["cancel_requested"] = True
    with pytest.raises(app.DownloadError, match="Cancelled"):
        app.check_gif_job(gif_job)


def test_gif_stops_on_low_disk(gif_job, monkeypatch):
    monkeypatch.setattr(app, "disk_low_locked", lambda _: True)
    with pytest.raises(app.DownloadError, match="disk is almost full"):
        app.check_gif_job(gif_job)
    assert gif_job["disk_full"]


def test_video_job_does_not_claim_gif_of_same_source(gif_job):
    video = app.DOWNLOAD_DIR / "Test [source-id].mp4"
    gif = app.DOWNLOAD_DIR / "Test [source-id] [GIF 320px 10fps gif-test-1].gif"
    video.write_bytes(b"video")
    gif.write_bytes(b"gif")
    gif_job["files"] = [gif.name]
    video_job = {"id": "video-test", "seen_ids": ["source-id"]}
    assert app.attribute_new_files(video_job, set()) == [video.name]


def test_startup_cleans_abandoned_gif_sources(gif_job):
    abandoned = app.DOWNLOAD_DIR / ".gif-012345abcdef-test"
    unrelated = app.DOWNLOAD_DIR / ".gif-personal"
    abandoned.mkdir()
    unrelated.mkdir()
    (abandoned / "source.mp4").write_bytes(b"private source")
    app.cleanup_stale_files()
    assert not abandoned.exists()
    assert unrelated.is_dir()


requires_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg and ffprobe needed")


def make_video(path, dimensions="640x360"):
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                    f"testsrc2=size={dimensions}:rate=20:duration=2", "-c:v", "mpeg4", "-y", str(path)],
                   check=True, capture_output=True)


@requires_ffmpeg
@pytest.mark.parametrize("dimensions,loop,clip,expected_size,expected_frames", [
    ("640x360", True, {}, (320, 180), 20),
    ("360x640", False, {"clip_from": "0.5", "clip_to": "1.5"}, (180, 320), 10),
    ("160x90", True, {"clip_from": "0.25", "clip_to": "1.25"}, (160, 90), 10),
])
def test_real_gif_job(gif_job, monkeypatch, dimensions, loop, clip, expected_size, expected_frames):
    # Exercise the complete job, using the gallery path so exact trimming is
    # performed on a local source and is measurable independently of yt-dlp.
    def source(job, payload, url):
        path = Path(job["gif_dir"]) / "Test video.mp4"
        make_video(path, dimensions)
        return [path.name]
    monkeypatch.setattr(app, "run_gallery", source)
    monkeypatch.setattr(app, "check_url", lambda _: ("https://example.com/video", {"engines": ["gallery-dl"]}))
    payload = {"url": "https://example.com/video", "quality": "gif", "gif_size": 320, "gif_fps": 10, "gif_loop": loop, **clip}
    app.run_download(gif_job["id"], payload)
    assert gif_job["status"] == "done", gif_job
    assert gif_job["progress"] == 100 and len(gif_job["files"]) == 1
    output = app.DOWNLOAD_DIR / gif_job["files"][0]
    data = output.read_bytes()
    assert data.startswith(b"GIF89a")
    assert (b"NETSCAPE2.0" in data) == loop
    probe = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-show_streams", "-of", "json", str(output)],
                           check=True, capture_output=True, text=True)
    stream = json.loads(probe.stdout)["streams"][0]
    assert (stream["width"], stream["height"]) == expected_size
    assert int(stream["nb_read_frames"]) == expected_frames
    assert float(stream["duration"]) == pytest.approx(expected_frames / 10, abs=0.02)
    assert app.file_kind(output.name) == "image"
    assert [p for p in app.DOWNLOAD_DIR.iterdir() if p.name != ".gitkeep"] == [output]


@requires_ffmpeg
def test_cancel_silent_encoder_kills_process(gif_job, tmp_path):
    gif_job["gif_dir"] = str(tmp_path)
    errors = []
    def encode():
        try:
            app.run_gif_ffmpeg(gif_job, ["-re", "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=1", "-f", "null", "-"],
                               "Encoding", 65, 99, 60)
        except app.DownloadError as exc:
            errors.append(str(exc))
    thread = threading.Thread(target=encode)
    thread.start()
    deadline = time.monotonic() + 5
    while "proc" not in gif_job and time.monotonic() < deadline:
        time.sleep(0.01)
    proc = gif_job.get("proc")
    gif_job["cancel_requested"] = True
    thread.join(timeout=3)
    assert not thread.is_alive() and proc is not None and proc.poll() is not None
    assert errors == ["Cancelled by user."] and "proc" not in gif_job


@requires_ffmpeg
def test_gif_preserves_anamorphic_display_ratio(gif_job, tmp_path):
    gif_job["gif_dir"] = str(tmp_path)
    source = tmp_path / ".Wide video.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=640x360:rate=10:duration=1", "-vf", "setsar=2/1",
                    "-c:v", "mpeg4", "-y", str(source)], check=True, capture_output=True)
    app.convert_gif(gif_job, source, {"gif_size": 320, "gif_fps": 10})
    output = app.DOWNLOAD_DIR / gif_job["files"][0]
    assert not output.name.startswith(".")
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(output)],
                           check=True, capture_output=True, text=True)
    stream = json.loads(probe.stdout)["streams"][0]
    assert (stream["width"], stream["height"]) == (320, 90)


def test_gif_playlist_progress_uses_the_whole_collection():
    app.jobs.clear()
    app.jobs["j"] = {"id": "j", "quality": "gif", "status": "queued", "progress": 0, "log": [], "files": []}
    try:
        app.progress_hook("j", {
            "status": "downloading", "downloaded_bytes": 50, "total_bytes": 100,
            "filename": "/tmp/V [a].mp4",
            "info_dict": {"playlist_index": 2, "playlist_count": 4, "id": "a"},
        })
        assert app.jobs["j"]["progress"] == 16.9
        assert "[2/4]" in app.jobs["j"]["detail"]
        assert "16.9%" in app.jobs["j"]["detail"]
        app.jobs["j"].pop("playlist_count")
        app.jobs["j"].pop("playlist_index")
        app.progress_hook("j", {
            "status": "downloading", "downloaded_bytes": 50, "total_bytes": 100,
            "filename": "/tmp/V [a].mp4", "info_dict": {"id": "a"},
        })
        assert app.jobs["j"]["progress"] == 22.5
    finally:
        app.jobs.clear()


def test_gif_encode_progress_advances_across_a_batch(gif_job, tmp_path, monkeypatch):
    monkeypatch.setattr(app.shutil, "which", lambda _: "/ffmpeg")
    spans = []

    def probe(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps({
            "streams": [{"codec_type": "video"}], "format": {"duration": "1"}}), stderr="")

    def encode(job, args, detail, start, end, duration):
        spans.append((start, end))
        if str(args[-1]).endswith("output.gif"):
            Path(args[-1]).write_bytes(b"GIF89a")

    monkeypatch.setattr(app.subprocess, "run", probe)
    monkeypatch.setattr(app, "run_gif_ffmpeg", encode)
    gif_job["gif_dir"] = str(tmp_path)
    payload = {"gif_size": 480, "gif_fps": 15, "gif_loop": True}
    (tmp_path / "clip.mp4").write_bytes(b"video")
    app.convert_gif(gif_job, tmp_path / "clip.mp4", payload, part=(1, 2))
    assert spans == [(72, 82), (82, 99)]
    spans.clear()
    (tmp_path / "one.mp4").write_bytes(b"video")
    app.convert_gif(gif_job, tmp_path / "one.mp4", payload)
    assert spans == [(45, 65), (65, 99)]


def test_gif_batch_keeps_successes_when_one_item_fails(gif_job, tmp_path, monkeypatch):
    parts = []

    def convert(job, source, payload, *, trimmed=False, part=(0, 1)):
        parts.append((trimmed, part))
        if source.name == "bad.mp4":
            raise app.DownloadError("no video")
        job["files"].append(source.stem + ".gif")

    monkeypatch.setattr(app, "convert_gif", convert)
    sources = [tmp_path / name for name in ("good.mp4", "bad.mp4", "later.mp4")]
    for source in sources:
        source.write_bytes(b"v")
    app.convert_gif_batch(gif_job, {}, sources, trimmed=True)
    assert gif_job["files"] == ["good.gif", "later.gif"]
    assert parts == [(True, (0, 3)), (True, (1, 3)), (True, (2, 3))]
    assert any("no video" in line for line in gif_job["log"])
    assert any("1 item could not be made into a GIF" in line for line in gif_job["log"])


def test_gif_batch_still_cancels(gif_job, tmp_path, monkeypatch):
    def convert(job, source, payload, *, trimmed=False, part=(0, 1)):
        job["cancel_requested"] = True
        raise app.DownloadError("Cancelled by user.")

    monkeypatch.setattr(app, "convert_gif", convert)
    with pytest.raises(app.DownloadError, match="Cancelled"):
        app.convert_gif_batch(gif_job, {}, [tmp_path / "clip.mp4"], trimmed=False)
    assert gif_job["files"] == []


def _source_proc(events, code):
    class Proc:
        def __init__(self, cmd, **kwargs):
            self.returncode = None
            self.pid = 0
            self.stdin = io.StringIO()
            self._out = kwargs["stdout"]

        def wait(self, timeout=None):
            if self.returncode is None:
                if events:
                    self._out.write(("\n".join(events) + "\n").encode())
                    self._out.flush()
                self.returncode = code
            return self.returncode

        def poll(self):
            return self.returncode

    return Proc


def test_gif_source_keeps_finished_gifs_when_the_worker_exits_early(gif_job, tmp_path, monkeypatch):
    folder = tmp_path / "src"
    folder.mkdir()
    gif_job["gif_dir"] = str(folder)
    video = folder / "clip.mp4"
    video.write_bytes(b"v")
    monkeypatch.setattr(app, "stop_gif_source", lambda proc: None)
    monkeypatch.setattr(app, "build_download_opts", lambda job, payload: {"outtmpl": str(folder / "x.mp4")})
    monkeypatch.setattr(app.subprocess, "Popen", _source_proc([
        json.dumps({"kind": "file", "data": str(video)}),
        json.dumps({"kind": "error", "data": "playlist item failed"}),
    ], 1))
    seen = []
    monkeypatch.setattr(app, "convert_gif_batch",
                        lambda job, payload, sources, *, trimmed: seen.append(sources) or job["files"].append("out.gif"))
    app.run_gif_ytdlp(gif_job, {"quality": "gif"}, "https://example.com/v", {"id": "other"})
    assert seen == [[video.resolve()]]
    assert any("playlist item failed" in line and "Finished GIFs were kept" in line for line in gif_job["log"])


def test_gif_source_error_is_kept_when_nothing_was_fetched(gif_job, tmp_path, monkeypatch):
    folder = tmp_path / "src"
    folder.mkdir()
    gif_job["gif_dir"] = str(folder)
    monkeypatch.setattr(app, "stop_gif_source", lambda proc: None)
    monkeypatch.setattr(app, "build_download_opts", lambda job, payload: {"outtmpl": str(folder / "x.mp4")})
    monkeypatch.setattr(app.subprocess, "Popen", _source_proc([
        json.dumps({"kind": "error", "data": "playlist item failed"}),
    ], 1))
    monkeypatch.setattr(app, "convert_gif_batch", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("converted without a source")))
    with pytest.raises(app.DownloadError, match="playlist item failed"):
        app.run_gif_ytdlp(gif_job, {"quality": "gif"}, "https://example.com/v", {"id": "other"})


def test_inspect_reports_upcoming_streams(monkeypatch):
    from types import SimpleNamespace

    class DL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download=False):
            return {"id": "x", "title": "Premiere", "is_live": False, "live_status": "is_upcoming",
                    "duration": None, "formats": [], "subtitles": {}, "automatic_captions": {},
                    "webpage_url": url}

    monkeypatch.setattr(app, "yt_dlp", SimpleNamespace(YoutubeDL=DL))
    info = app.fetch_info("https://www.youtube.com/watch?v=xxxxxxxxxxx", {"cookies_mode": "none"})
    assert info["is_live"] is False
    assert info["live_status"] == "is_upcoming"
