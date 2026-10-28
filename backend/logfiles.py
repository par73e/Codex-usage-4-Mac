"""Read plain and Zstandard rollouts without extracting or modifying sources."""
from contextlib import contextmanager
from pathlib import Path
import shutil
import subprocess


def zstd_executable():
    found = shutil.which('zstd')
    if found:
        return found
    for name in ('/opt/homebrew/bin/zstd', '/usr/local/bin/zstd'):
        if Path(name).is_file():
            return name
    return None


def log_paths(root):
    return sorted({path for folder in ('sessions', 'archived_sessions')
                   for pattern in ('*.jsonl', '*.jsonl.zst')
                   for path in (root / folder).rglob(pattern)})


@contextmanager
def compressed_lines(path):
    executable = zstd_executable()
    if not executable:
        raise OSError('读取 .jsonl.zst 压缩日志需要本机 zstd 工具')
    process = subprocess.Popen([executable, '-q', '-d', '-c', '--', str(path)],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        yield process.stdout
        if process.wait() != 0:
            raise OSError('压缩日志不完整或无法解码：' + path.name)
    finally:
        process.stdout.close()
        if process.poll() is None:
            process.terminate()
            process.wait()
