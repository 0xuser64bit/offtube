"""Private yt-dlp source worker, launched by the server with a JSON stdin request."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app

if __name__ == "__main__":
    app.gif_source_worker(json.load(sys.stdin))
