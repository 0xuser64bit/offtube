"""offtube's real server with a stand-in yt-dlp, for the browser tests.

Usage: python stub_server.py <repo root> <data dir>  (PORT comes from the env)

Source resolution still uses the real extractor tables; only extraction and
downloading are faked: a playlist for any `list=` link, otherwise one video
whose download reports progress for about two seconds and keeps a real
clip (ffmpeg is needed, as for the rest of the gate).
"""
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, sys.argv[1])
import app  # noqa: E402

VIDEO = "A test video [abc123abc12].mp4"


def write_clip(path: Path) -> None:
    """A real one-second clip, as a download would leave. Browsers and axe
    read the metadata of every video thumbnail, and junk never loads: axe's
    autoplay check waits 10 s on each one."""
    made = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=64x36:rate=5:duration=1",
                           "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-y", str(path)], capture_output=True)
    if made.returncode:
        raise RuntimeError(made.stderr.decode(errors="replace"))


class YoutubeDL:
    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        if "list=" in url:
            return {"_type": "playlist", "title": "A playlist", "uploader": "Someone", "playlist_count": 3,
                    "entries": [{"id": f"v{i}", "title": f"Clip {i}", "duration": 30 + i,
                                 "url": f"https://www.youtube.com/watch?v=v{i}"} for i in range(1, 4)]}
        return {"id": "abc123abc12", "title": "A test video", "uploader": "Tester", "duration": 95,
                "webpage_url": url, "is_live": False, "subtitles": {"en": []}, "automatic_captions": {},
                "formats": [{"vcodec": "avc1", "acodec": "none", "height": 720, "ext": "mp4",
                             "filesize": 5_000_000, "format_id": "22"},
                            {"vcodec": "none", "acodec": "opus", "ext": "webm", "format_id": "251"}]}

    def download(self, urls):
        for step in range(1, 11):
            for hook in self.opts["progress_hooks"]:
                hook({"status": "downloading", "downloaded_bytes": step * 10, "total_bytes": 100,
                      "filename": str(app.DOWNLOAD_DIR / (VIDEO + ".part")),
                      "info_dict": {"id": "abc123abc12", "title": "A test video"}})
            time.sleep(0.2)
        write_clip(app.DOWNLOAD_DIR / VIDEO)


class Stub:
    YoutubeDL = YoutubeDL

    class version:
        __version__ = "stub"


app.yt_dlp = Stub()
data = Path(sys.argv[2])
app.DOWNLOAD_DIR = data / "downloads"
app.COOKIES_DIR = data / "cookies"
app.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.COOKIES_DIR.mkdir(parents=True, exist_ok=True)
app.main()
