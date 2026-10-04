"""System file dialogs, opened only in response to a local UI request."""
from pathlib import Path
import subprocess
import sys
import threading

_lock = threading.Lock()

# Paths are passed as arguments, never interpolated into AppleScript source.
_SCRIPT = '''on run argv
    set startFolder to POSIX file (item 1 of argv)
    activate
    if item 2 of argv is "file" then
        set selectedItem to choose file with prompt "選擇 alosStack 使用的檔案" default location startFolder
    else
        set selectedItem to choose folder with prompt "選擇 alosStack 使用的資料夾" default location startFolder
    end if
    return POSIX path of selectedItem
end run'''

def choose_native(mode, start=None):
    if mode not in ('file', 'dir', 'newdir'):
        raise ValueError('無效的選擇類型。')
    if sys.platform != 'darwin':
        return {'supported': False}
    if not _lock.acquire(blocking=False):
        raise ValueError('已有檔案選擇視窗開啟，請先完成或取消。')
    try:
        p = Path(start or Path.home()).expanduser()
        if not p.is_dir():
            p = p.parent
        if not p.is_dir():
            p = Path.home()
        result = subprocess.run(
            ['/usr/bin/osascript', '-e', _SCRIPT, str(p), mode],
            capture_output=True, text=True, timeout=600,
        )
        if result.returncode:
            if '(-128)' in result.stderr:
                return {'supported': True, 'cancelled': True}
            raise RuntimeError('無法開啟 macOS 原生選擇視窗，請使用內建選擇器。')
        path = result.stdout.rstrip('\r\n')
        if not path:
            raise RuntimeError('原生選擇視窗沒有傳回路徑。')
        return {'supported': True, 'cancelled': False, 'path': str(Path(path))}
    except subprocess.TimeoutExpired:
        raise RuntimeError('檔案選擇視窗等待逾時，請重新選擇。') from None
    finally:
        _lock.release()
