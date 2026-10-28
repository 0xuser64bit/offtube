"""Isolated yt-dlp worker, launched by the server with a JSON request on stdin.

It runs one download in its own process group and reports progress, files,
logs and errors as JSON lines on stdout, so the server can stop it - and any
ffmpeg under it - at once. See app.ytdlp_worker and app.run_ytdlp_worker.
"""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app

if __name__ == "__main__":
    app.ytdlp_worker(json.load(sys.stdin))
