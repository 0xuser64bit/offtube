"""X/Twitter compatibility: every kept video must be MP4/H.264/yuv420p/AAC
so it does not fail with "video not supported". Compatible files stay
untouched; incompatible ones are converted once with FFmpeg.
"""
import shutil
from pathlib import Path
import subprocess

import pytest

import app


requires_ffmpeg = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="ffmpeg and ffprobe needed to convert and measure videos")


def stream(codec_type, codec_name, **extra):
    return {"codec_type": codec_type, "codec_name": codec_name,
            "disposition": {"attached_pic": 0}, **extra}


def probe_info(video=None, audio=None):
    streams = []
    if video is not None:
        streams.append(stream("video", video[0], **video[1]))
    if audio is not None:
        streams.append(stream("audio", audio[0], **audio[1]))
    return {"streams": streams}


def test_x_ready_plan_accepts_the_target_shape():
    info = probe_info(
        ("h264", {"pix_fmt": "yuv420p", "avg_frame_rate": "30000/1001",
                  "sample_aspect_ratio": "1:1"}),
        ("aac", {"channels": 2}),
    )
    assert app.x_ready_plan(info, ".mp4") is None
    # yuvj420p and mono are accepted by X too.
    assert app.x_ready_plan(probe_info(
        ("h264", {"pix_fmt": "yuvj420p", "avg_frame_rate": "60/1"}),
        ("aac", {"channels": 1})), ".mp4") is None
    # No audio is fine.
    assert app.x_ready_plan(probe_info(
        ("h264", {"pix_fmt": "yuv420p", "avg_frame_rate": "25/1"})), ".mp4") is None


@pytest.mark.parametrize("info,suffix", [
    (probe_info(("vp9", {"pix_fmt": "yuv420p", "avg_frame_rate": "30/1"}),
                ("opus", {"channels": 2})), ".mp4"),
    (probe_info(("h264", {"pix_fmt": "yuv444p", "avg_frame_rate": "30/1"}),
                ("aac", {"channels": 2})), ".mp4"),
    (probe_info(("h264", {"pix_fmt": "yuv420p", "avg_frame_rate": "120/1"}),
                ("aac", {"channels": 2})), ".mp4"),
    (probe_info(("h264", {"pix_fmt": "yuv420p", "avg_frame_rate": "30/1",
                          "sample_aspect_ratio": "2:1"}),
                ("aac", {"channels": 2})), ".mp4"),
    (probe_info(("h264", {"pix_fmt": "yuv420p", "avg_frame_rate": "30/1"}),
                ("aac", {"channels": 6})), ".mp4"),
    # X wants AAC-LC, not HE-AAC.
    (probe_info(("h264", {"pix_fmt": "yuv420p", "avg_frame_rate": "30/1"}),
                ("aac", {"channels": 2, "profile": "HE-AAC"})), ".mp4"),
    # Right codecs, wrong container.
    (probe_info(("h264", {"pix_fmt": "yuv420p", "avg_frame_rate": "30/1"}),
                ("aac", {"channels": 2})), ".webm"),
])
def test_x_ready_plan_flags_what_x_rejects(info, suffix):
    assert app.x_ready_plan(info, suffix) is not None


def test_x_ready_plan_ignores_cover_art_and_non_video():
    assert app.x_ready_plan({"streams": [stream("audio", "aac", channels=2)]}, ".mp4") is None
    assert app.x_ready_plan(None, ".mp4") is None


def test_x_ready_plan_checks_every_audio_track():
    info = probe_info(
        ("h264", {"pix_fmt": "yuv420p", "avg_frame_rate": "30/1"}),
        ("aac", {"channels": 2}),
    )
    info["streams"].append(stream("audio", "opus", channels=2))
    plan = app.x_ready_plan(info, ".mp4")
    assert plan is not None and plan["audio"] is True and plan["video"] is False


@pytest.fixture
def x_job(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DOWNLOAD_DIR", tmp_path)
    job = {"id": "x-test", "status": "processing", "progress": 100,
           "detail": "", "log": [], "files": []}
    app.jobs[job["id"]] = job
    yield job
    app.jobs.pop(job["id"], None)


def make_video(path: Path, *, container_args=(), video_codec="mpeg4",
               audio_codec="mp3", size="161x91", rate=30) -> None:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
         f"testsrc2=size={size}:rate={rate}:duration=1",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-c:v", video_codec, "-c:a", audio_codec, *container_args, "-y", str(path)],
        check=True, capture_output=True)


def codecs(path: Path):
    info = app.probe_media(path)
    assert info is not None
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    audio = next(s for s in info["streams"] if s["codec_type"] == "audio")
    return video, audio


@requires_ffmpeg
def test_make_video_x_ready_converts_in_place(x_job, tmp_path):
    video = tmp_path / "clip.mp4"
    make_video(video)
    kept = app.make_video_x_ready(x_job, video)
    assert kept == video
    assert app.x_ready_plan(app.probe_media(kept), ".mp4") is None
    v, a = codecs(kept)
    assert (v["codec_name"], v["pix_fmt"]) == ("h264", "yuv420p")
    assert (int(v["width"]), int(v["height"])) == (160, 90)  # odd input made even
    assert a["codec_name"] == "aac"
    assert not list(tmp_path.glob("*.tmp")) and not list(tmp_path.glob(".*.tmp"))
    assert any("X-ready" in line for line in x_job["log"])


@requires_ffmpeg
def test_make_video_x_ready_preserves_anamorphic_display(x_job, tmp_path):
    video = tmp_path / "wide.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
         "testsrc2=size=160x90:rate=30:duration=1", "-f", "lavfi", "-i",
         "sine=frequency=440:duration=1", "-vf", "setsar=2/1",
         "-c:v", "mpeg4", "-c:a", "aac", "-y", str(video)],
        check=True, capture_output=True)
    kept = app.make_video_x_ready(x_job, video)
    v, _ = codecs(kept)
    assert (int(v["width"]), int(v["height"])) == (320, 90)
    assert v["sample_aspect_ratio"] == "1:1"


@requires_ffmpeg
def test_make_video_x_ready_renames_to_mp4(x_job, tmp_path):
    source = tmp_path / "clip.mkv"
    make_video(source, container_args=("-f", "matroska"))
    kept = app.make_video_x_ready(x_job, source)
    assert kept == tmp_path / "clip.mp4" and kept.is_file()
    assert not source.exists()
    assert app.x_ready_plan(app.probe_media(kept), ".mp4") is None


@requires_ffmpeg
def test_compatible_video_is_left_byte_for_byte(x_job, tmp_path):
    video = tmp_path / "ready.mp4"
    make_video(video, video_codec="libx264", audio_codec="aac",
               container_args=("-pix_fmt", "yuv420p", "-movflags", "+faststart"))
    before = video.read_bytes()
    kept = app.make_video_x_ready(x_job, video)
    assert kept == video and video.read_bytes() == before
    assert x_job["log"] == []


def test_accelerated_video_uses_one_x_ready_pass_with_matched_filters(x_job, tmp_path, monkeypatch):
    video = tmp_path / "ready.mp4"
    video.write_bytes(b"source")
    source_info = probe_info(
        ("vp9", {"pix_fmt": "yuv420p", "avg_frame_rate": "30/1", "sample_aspect_ratio": "1:1"}),
        ("opus", {"channels": 2}),
    )
    source_info["format"] = {"duration": "10", "format_name": "matroska,webm"}
    converted_info = probe_info(
        ("h264", {"pix_fmt": "yuv420p", "avg_frame_rate": "30/1", "sample_aspect_ratio": "1:1"}),
        ("aac", {"channels": 2}),
    )
    converted_info["format"] = {"duration": "8", "format_name": "mov,mp4,m4a,3gp,3g2,mj2"}
    calls = {}

    monkeypatch.setattr(app, "probe_media", lambda path: source_info if path == video else converted_info)
    monkeypatch.setattr(app.shutil, "which", lambda _: "/ffmpeg")

    def convert(job, args, detail, _log, *, duration, failure):
        calls.update(args=args, detail=detail, duration=duration, failure=failure)
        Path(args[-1]).write_bytes(b"accelerated")

    monkeypatch.setattr(app, "run_ffmpeg", convert)
    x_job["video_speed"] = 1.25
    kept = app.make_video_x_ready(x_job, video)

    assert kept == tmp_path / "ready [1.25x].mp4"
    assert calls["duration"] == 8
    assert calls["detail"] == "Speeding up video — 1/1 · 1.25x…"
    assert calls["args"][calls["args"].index("-vf") + 1].startswith("setpts=PTS/1.25,")
    assert calls["args"][calls["args"].index("-filter:a") + 1] == "atempo=1.25"
    assert app.x_ready_plan(converted_info, ".mp4") is None


@requires_ffmpeg
def test_accelerated_video_without_audio_is_a_valid_shorter_mp4(x_job, tmp_path):
    video = tmp_path / "silent.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
         "testsrc2=size=160x90:rate=30:duration=1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-an", "-movflags", "+faststart", "-y", str(video)],
        check=True, capture_output=True)
    x_job["video_speed"] = 2

    kept = app.make_video_x_ready(x_job, video)
    info = app.probe_media(kept)

    assert kept == tmp_path / "silent [2x].mp4"
    assert app.x_ready_plan(info, ".mp4") is None
    assert not any(s["codec_type"] == "audio" for s in info["streams"])
    assert float(info["format"]["duration"]) == pytest.approx(0.5, abs=0.08)


def test_one_failed_conversion_keeps_its_original(x_job, tmp_path, monkeypatch):
    broken, good = tmp_path / "broken.mp4", tmp_path / "good.mp4"
    broken.write_bytes(b"source")
    good.write_bytes(b"source")

    def convert(job, path, index=1, total=1):
        if path == broken:
            raise app.DownloadError("encoder refused")
        return path

    monkeypatch.setattr(app, "make_video_x_ready", convert)
    assert app.make_videos_x_ready(x_job, [broken, good]) == [broken, good]
    assert any("encoder refused" in line and "Kept the original" in line
               for line in x_job["log"])


def test_acceleration_failure_does_not_claim_to_keep_the_original(x_job, tmp_path, monkeypatch):
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"source")
    x_job["video_speed"] = 1.5

    monkeypatch.setattr(app, "make_video_x_ready",
                        lambda *args: (_ for _ in ()).throw(app.DownloadError("encoder refused")))

    with pytest.raises(app.DownloadError, match="encoder refused"):
        app.make_videos_x_ready(x_job, [broken])
    assert not x_job["log"]


def test_run_download_makes_videos_x_ready(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DOWNLOAD_DIR", tmp_path)
    monkeypatch.setattr(app, "COOKIES_DIR", tmp_path)
    (tmp_path / "cookies").mkdir()
    video = tmp_path / "Kept [abc].mp4"
    video.write_bytes(b"video")
    seen = []

    def download(job, payload, url, src):
        return [video]

    def convert(job, paths):
        seen.extend(paths)
        return paths

    monkeypatch.setattr(app, "run_ytdlp", download)
    monkeypatch.setattr(app, "make_videos_x_ready", convert)
    app.jobs["x-download"] = {"id": "x-download", "status": "processing", "progress": 100,
                              "detail": "", "log": [], "files": []}
    try:
        app.run_download("x-download", {"url": "https://youtu.be/abc123abc12"})
        assert seen == [video]
        assert app.jobs["x-download"]["files"] == [video.name]
        assert app.jobs["x-download"]["status"] == "done"
    finally:
        app.jobs.pop("x-download", None)


@requires_ffmpeg
def test_run_download_converts_a_real_video(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DOWNLOAD_DIR", tmp_path)
    monkeypatch.setattr(app, "COOKIES_DIR", tmp_path)
    (tmp_path / "cookies").mkdir()
    video = tmp_path / "Kept [abc].mp4"
    make_video(video)
    monkeypatch.setattr(app, "run_ytdlp", lambda job, payload, url, src: [video])
    app.jobs["x-real"] = {"id": "x-real", "status": "processing", "progress": 100,
                          "detail": "", "log": [], "files": []}
    try:
        app.run_download("x-real", {"url": "https://youtu.be/abc123abc12"})
        assert app.jobs["x-real"]["status"] == "done"
        assert app.jobs["x-real"]["files"] == [video.name]
        assert app.x_ready_plan(app.probe_media(video), ".mp4") is None
        assert app.jobs["x-real"]["progress"] == 100
    finally:
        app.jobs.pop("x-real", None)


def test_audio_jobs_are_not_converted(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DOWNLOAD_DIR", tmp_path)
    monkeypatch.setattr(app, "COOKIES_DIR", tmp_path)
    (tmp_path / "cookies").mkdir()
    track = tmp_path / "Track [abc].mp3"
    track.write_bytes(b"audio")
    seen = []

    def download(job, payload, url, src):
        return [track]

    def convert(job, paths):
        seen.extend(paths)
        return paths

    monkeypatch.setattr(app, "run_ytdlp", download)
    monkeypatch.setattr(app, "make_videos_x_ready", convert)
    app.jobs["x-audio"] = {"id": "x-audio", "status": "processing", "progress": 100,
                           "detail": "", "log": [], "files": []}
    try:
        app.run_download("x-audio", {"url": "https://youtu.be/abc123abc12",
                                     "quality": "audio_mp3"})
        assert seen == []
        assert app.jobs["x-audio"]["files"] == [track.name]
    finally:
        app.jobs.pop("x-audio", None)
