"""System file dialogs, opened only in response to a local UI request."""
from pathlib import Path
import json
import os
import plistlib
import re
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import time

_lock = threading.Lock()
_cache = None
_helper = None
RESOURCES = Path(__file__).parent / 'resources'
STARTUP_TIMEOUT = 20
SELECTION_TIMEOUT = 600


def _native_environment():
    """Do not inject scientific-environment libraries into a macOS GUI tool."""
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('DYLD_', 'PYTHON', 'CONDA_', '_CE_'))
           and key not in ('LD_LIBRARY_PATH', 'LD_PRELOAD')}
    env['PATH'] = '/usr/bin:/bin:/usr/sbin:/sbin'
    return env


def _failure(stderr):
    # Keep enough OS detail to diagnose a real failure, without flooding the UI.
    detail = re.sub(r'\s+', ' ', stderr or '').strip()[-240:]
    message = 'macOS 原生選擇視窗未能啟動，已改用網頁選擇器。請確認工作台在已登入的桌面中執行。'
    return message + (' 系統訊息：' + detail if detail else '')


def _stop_process(process):
    if process.poll() is not None:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=1)
            return
        except subprocess.TimeoutExpired:
            continue


def _bounded_command(args, timeout=120, env=None):
    # A descendant may inherit stdout; wait on the process, never on pipe EOF.
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT,
                                   env=env or _native_environment(), start_new_session=True)
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _stop_process(process)
            raise RuntimeError('原生選擇工具準備逾時，請改用網頁選擇器。') from None
        log.seek(0, 2)
        end = log.tell()
        log.seek(max(0, end - 8192))
        text = log.read().decode('utf-8', errors='replace')
        if code:
            raise RuntimeError(_failure(text))
        return text


def build_native_helper(app, target=None, compiler_cache=None):
    """Build-time helper; source installs may use existing Xcode tools once."""
    app = Path(app)
    if app.exists():
        raise ValueError('原生選擇工具的建置位置必須全新。')
    # xcode-select -p only checks; it never asks macOS to install developer tools.
    try:
        developer = _bounded_command(['/usr/bin/xcode-select', '-p'], timeout=10).strip()
    except RuntimeError:
        raise RuntimeError('原始碼版需要既有的 Xcode Command Line Tools 才能建立原生選擇器。可改用下載版 Mac App 或網頁選擇器；工作台不會自動安裝開發工具。') from None
    if not Path(developer).is_dir():
        raise RuntimeError('找不到可用的 Xcode 工具，請使用 Mac App 或網頁選擇器。')
    binary = app / 'Contents/MacOS/SARFilePicker'
    binary.parent.mkdir(parents=True)
    cache = Path(compiler_cache or app.parent / 'compiler-cache')
    cache.mkdir(parents=True, exist_ok=True)
    info = dict(CFBundleName='SARFilePicker', CFBundleDisplayName='SAR 選擇檔案',
                CFBundleIdentifier='io.github.chris20011026.sar-workbench.file-picker',
                CFBundleExecutable='SARFilePicker', CFBundlePackageType='APPL',
                CFBundleVersion='1', CFBundleShortVersionString='1.0',
                NSHighResolutionCapable=True, LSMinimumSystemVersion='14.5',
                NSPrincipalClass='NSApplication', CFBundleDevelopmentRegion='zh_TW')
    (app / 'Contents/Info.plist').write_bytes(plistlib.dumps(info))
    command = ['/usr/bin/xcrun', 'swiftc', '-swift-version', '5', '-O',
               '-module-cache-path', str(cache)]
    if target:
        command += ['-target', target]
    command += [str(RESOURCES / 'NativePicker.swift'), '-framework', 'AppKit', '-o', str(binary)]
    env = _native_environment()
    env['CLANG_MODULE_CACHE_PATH'] = str(cache)
    _bounded_command(command, env=env)
    _bounded_command(['/usr/bin/codesign', '--force', '--sign', '-', str(app)], timeout=30)
    _bounded_command(['/usr/bin/codesign', '--verify', '--strict', str(app)], timeout=30)
    return app


def _ensure_helper():
    global _cache, _helper
    bundled = RESOURCES / 'SARFilePicker.app'
    if (bundled / 'Contents/MacOS/SARFilePicker').is_file():
        return bundled
    if _helper and (_helper / 'Contents/MacOS/SARFilePicker').is_file():
        return _helper
    _cache = tempfile.TemporaryDirectory(prefix='sar-native-picker-')
    try:
        _helper = build_native_helper(Path(_cache.name) / 'SARFilePicker.app')
        return _helper
    except Exception:
        _cache.cleanup()
        _cache = _helper = None
        raise


def _read_reply(path, token):
    try:
        if path.stat().st_size > 65536:
            raise RuntimeError('原生選擇視窗傳回的資料過大。')
        value = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (ValueError, UnicodeError):
        raise RuntimeError('原生選擇視窗傳回的資料無法解析。') from None
    if not isinstance(value, dict) or value.get('token') != token or value.get('parent_pid') != os.getpid():
        raise RuntimeError('原生選擇視窗回覆驗證失敗。')
    return value


def _show_helper(app, mode, start):
    with tempfile.TemporaryDirectory(prefix='sar-picker-request-') as directory:
        root = Path(directory)
        token = secrets.token_hex(32)
        request = root / 'request.json'
        request.write_text(json.dumps(dict(mode=mode, start=str(start), token=token, parent_pid=os.getpid())))
        ready = None
        with (root / 'launch.log').open('wb') as log:
            process = subprocess.Popen(['/usr/bin/open', '-n', '-W', str(app), '--args', '--request', str(request)],
                                       stdout=log, stderr=subprocess.STDOUT, env=_native_environment(), start_new_session=True)
            began = time.monotonic()
            try:
                while True:
                    selected = _read_reply(root / 'response.json', token)
                    if selected:
                        if selected.get('error'):
                            raise RuntimeError(str(selected['error']) + ' 已改用網頁選擇器。')
                        return selected
                    ready = _read_reply(root / 'ready.json', token) or ready
                    elapsed = time.monotonic() - began
                    if elapsed > SELECTION_TIMEOUT or (elapsed > STARTUP_TIMEOUT and (ready or {}).get('phase') != 'visible'):
                        raise RuntimeError('原生選擇視窗未能顯示或等待逾時，已改用網頁選擇器。請確認目前桌面可使用原生應用程式。')
                    code = process.poll()
                    if code is not None:
                        raise RuntimeError(_failure((root / 'launch.log').read_text(errors='replace') or f'原生應用程式提前結束（{code}）。'))
                    time.sleep(0.1)
            finally:
                # A native watchdog watches this marker on its own queue,
                # independently of AppKit. Never signal a PID read from JSON.
                (root / 'cancel').touch()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    _stop_process(process)

def choose_native(mode, start=None):
    if mode not in ('file', 'dir', 'newdir'):
        raise ValueError('無效的選擇類型。')
    if sys.platform != 'darwin':
        return {'supported': False}
    if not _lock.acquire(blocking=False):
        raise ValueError('已有檔案選擇視窗開啟，請先完成或取消。')
    try:
        p = Path(start or Path.home()).expanduser().resolve()
        if not p.is_dir():
            p = p.parent
        if not p.is_dir():
            p = Path.home()
        selected = _show_helper(_ensure_helper(), mode, p)
        if not isinstance(selected, dict) or not isinstance(selected.get('cancelled'), bool):
            raise RuntimeError('原生選擇視窗沒有傳回有效結果，已改用網頁選擇器。')
        if selected['cancelled']:
            return {'supported': True, 'cancelled': True}
        path = selected.get('path')
        if not isinstance(path, str) or not path or not Path(path).is_absolute():
            raise RuntimeError('原生選擇視窗沒有傳回有效的絕對路徑，已改用網頁選擇器。')
        return {'supported': True, 'cancelled': False, 'path': str(Path(path))}
    finally:
        _lock.release()
