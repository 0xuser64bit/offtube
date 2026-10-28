"""Stands in for tools/ytdlp_worker.py in tests: the same JSON events on
stdout, no network. What it does follows markers in the URL:

  block    works silently for a minute, as yt-dlp does while extracting or
           merging; only stopping its process ends that early
  chatty   reports progress every 50 ms for a minute
  fail     fails the way a private video does
  novideo  fails the way an image-only post does, so gallery-dl takes over
  (none)   reports progress and saves "Test Video [abc123].mp4"
"""
import json
import sys
import time
from pathlib import Path

request = json.load(sys.stdin)
url = request["url"]
folder = Path(request["opts"]["outtmpl"]).parent
NAME = "Test Video [abc123].mp4"
INFO = {"id": "abc123", "title": "Test Video"}


def emit(kind, data):
    print(json.dumps({"kind": kind, "data": data}), flush=True)


def progress(done):
    emit("progress", {"status": "downloading", "downloaded_bytes": done, "total_bytes": 10,
                      "speed": 1000, "eta": 1, "filename": str(folder / (NAME + ".part")), "info_dict": INFO})


if "block" in url:
    time.sleep(60)
if "chatty" in url:
    for _ in range(1200):
        progress(5)
        time.sleep(0.05)
if "fail" in url:
    emit("error", "ERROR: [youtube] abc123: Private video. Sign in if you've been granted access to this video")
    sys.exit(1)
if "novideo" in url:
    emit("error", "ERROR: [twitter] 1234567890: No video could be found in this tweet")
    sys.exit(1)
progress(5)
emit("progress", {"status": "finished", "filename": str(folder / NAME), "info_dict": INFO})
(folder / NAME).write_bytes(b"fake-video")
emit("file", str(folder / NAME))
