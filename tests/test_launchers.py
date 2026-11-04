"""The launchers' upgrade path: a broken site needs newer engines, not the
same pinned versions reinstalled."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(os.name == "nt", reason="run.sh is a POSIX launcher")
def test_run_sh_upgrade_installs_the_latest_engines(tmp_path):
    (tmp_path / "requirements.txt").write_text("yt-dlp==0\ngallery-dl==0\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("print('app started')\n", encoding="utf-8")
    bin_dir = tmp_path / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    python = bin_dir / "python"
    python.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "-c" ]; then echo "' + str(tmp_path / ".venv") + '"; exit 0; fi\n'
        f'exec "{sys.executable}" "$@"\n',
        encoding="utf-8",
    )
    pip = bin_dir / "pip"
    calls = tmp_path / "pip-calls.txt"
    pip.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> "{calls}"\n',
        encoding="utf-8",
    )
    python.chmod(0o755)
    pip.chmod(0o755)
    shutil.copy2(ROOT / "run.sh", tmp_path / "run.sh")

    result = subprocess.run(["bash", "run.sh", "--upgrade"], cwd=tmp_path,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "app started" in result.stdout
    installed = calls.read_text(encoding="utf-8")
    assert "install -q -U yt-dlp gallery-dl" in installed
