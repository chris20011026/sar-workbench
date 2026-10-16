#!/usr/bin/env python3
"""Build the Apple Silicon launcher. Runtime downloads happen on first use."""
import argparse
import hashlib
import os
from pathlib import Path
import plistlib
import runpy
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
VERSIONS = runpy.run_path(ROOT / 'src/alosstack_helper/__init__.py')
VERSION = VERSIONS['DISPLAY_VERSION']


def build(output):
    output.mkdir(parents=True, exist_ok=True)
    app = output / 'SAR Workbench.app'
    if app.exists():
        raise RuntimeError('Use a new build directory; existing apps are not overwritten.')
    contents = app / 'Contents'
    executable = contents / 'MacOS'
    payload = contents / 'Resources/workbench'
    executable.mkdir(parents=True)
    payload.mkdir(parents=True)
    shutil.copytree(ROOT / 'src', payload / 'src', ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.egg-info'))
    for name in ('README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md'):
        shutil.copyfile(ROOT / name, payload / name)
    shutil.copytree(ROOT / 'docs', payload / 'docs')
    # Ship a real LaunchServices application for file dialogs. End users need
    # neither AppleScript automation permission nor an installed Swift compiler.
    picker_builder = runpy.run_path(ROOT / 'src/alosstack_helper/native_picker.py')['build_native_helper']
    picker_builder(payload / 'src/alosstack_helper/resources/SARFilePicker.app',
                   target='arm64-apple-macos14.5', compiler_cache=output / 'compiler-cache')
    info = dict(CFBundleName='SAR Workbench', CFBundleDisplayName='SAR 研究工作台',
                CFBundleIdentifier='io.github.chris20011026.sar-workbench',
                CFBundleExecutable='SARWorkbench', CFBundlePackageType='APPL',
                CFBundleShortVersionString=VERSION.split('-')[0], CFBundleVersion=VERSIONS['MAC_BUNDLE_VERSION'],
                LSMinimumSystemVersion='14.5', NSHighResolutionCapable=True,
                CFBundleDevelopmentRegion='zh_TW',
                NSHumanReadableCopyright='SAR Workbench contributors; third-party licenses apply.')
    (contents / 'Info.plist').write_bytes(plistlib.dumps(info))
    env = os.environ.copy()
    env['CLANG_MODULE_CACHE_PATH'] = str(output / 'compiler-cache')
    subprocess.run(['xcrun', 'swiftc', '-swift-version', '5', '-O', '-target', 'arm64-apple-macos14.5',
                    '-module-cache-path', str(output / 'compiler-cache'),
                    str(ROOT / 'desktop/Bootstrap.swift'), str(ROOT / 'desktop/Uninstall.swift'), str(ROOT / 'desktop/main.swift'),
                    '-framework', 'AppKit', '-framework', 'CryptoKit', '-o', str(executable / 'SARWorkbench')],
                   env=env, check=True)
    # Ad-hoc signing enables local ARM execution; it is NOT Developer ID signing
    # or Apple notarization. Published beta notes explicitly state that limit.
    subprocess.run(['codesign', '--force', '--sign', '-', str(app)], check=True)
    subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True)
    zip_path = output / f'SAR-Workbench-{VERSION}-macOS-arm64.zip'
    subprocess.run(['ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', str(app), str(zip_path)], check=True)
    checksum = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    (output / 'SHA256SUMS').write_text(f'{checksum}  {zip_path.name}\n')
    print(f'App: {app}\nDownload: {zip_path}\nSHA256: {checksum}')
    return app


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    build(parser.parse_args().output.resolve())
