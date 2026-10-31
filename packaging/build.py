"""Build the desktop app for the current OS and package it into dist/release/.

    pip install -r requirements.txt -r requirements-build.txt
    python packaging/build.py

Windows  YTDL-Windows-x64-Setup.exe (when Inno Setup is installed) and YTDL-Windows-x64-Portable.zip
macOS    YTDL-macOS-AppleSilicon.dmg or YTDL-macOS-Intel.dmg
Linux    YTDL-Linux-x86_64.AppImage and YTDL-Linux-x86_64.tar.gz

The release workflow (.github/workflows/release.yml) runs this on each OS.
"""

import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / 'dist'
BUILD = ROOT / 'build'
RELEASE = DIST / 'release'
sys.path.insert(0, str(ROOT / 'src'))
from ytdl import APP_NAME, __version__  # noqa: E402

APPIMAGETOOL = 'https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-{arch}.AppImage'
DESKTOP_ENTRY = f"""[Desktop Entry]
Type=Application
Name={APP_NAME}
GenericName=Video Downloader
Comment=Download videos, audio and playlists from YouTube and 1000+ sites
Exec={APP_NAME} %U
Icon=ytdl
Terminal=false
Categories=AudioVideo;Network;
Keywords=youtube;download;video;audio;mp3;playlist;
"""


def run(*cmd, **kwargs):
    print('+', ' '.join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, **kwargs)


def build_env() -> dict:
    """PyInstaller looks up DLL dependencies on PATH. On Windows, keep that to Python and the system,
    so DLLs from other installs (e.g. Anaconda's old ICU and UCRT) can't sneak into the bundle."""
    env = dict(os.environ)
    if sys.platform == 'win32':
        system = os.environ.get('SystemRoot', r'C:\Windows')
        env['PATH'] = os.pathsep.join([
            os.path.dirname(sys.executable), sys.base_prefix, os.path.join(sys.base_prefix, 'DLLs'),
            os.path.join(system, 'System32'), system, os.path.join(system, 'System32', 'Wbem'),
        ])
    return env


def pyinstaller():
    run(sys.executable, '-m', 'PyInstaller', ROOT / 'packaging' / 'ytdl.spec', '--noconfirm', '--clean',
        '--distpath', DIST, '--workpath', BUILD / 'pyinstaller', env=build_env())


def smoke_test(executable: Path):
    """Start the packaged app headless; it checks its bundled ffmpeg, Deno and solver scripts and writes a report."""
    report = BUILD / 'smoke-test.json'
    report.unlink(missing_ok=True)
    env = {**os.environ, 'YTDL_SMOKE_REPORT': str(report), 'QT_QPA_PLATFORM': 'offscreen'}
    try:
        code = subprocess.run([str(executable), '--smoke-test'], env=env, timeout=120).returncode
    except subprocess.TimeoutExpired:
        sys.exit('The packaged app hung during its smoke test (a startup error dialog is the usual cause)')
    result = json.loads(report.read_text()) if report.exists() else None
    print(f'Smoke test: exit code {code}, {result}', flush=True)
    if code != 0 or not result:
        sys.exit('The packaged app failed its smoke test')


def archive(fmt: str, out: Path):
    """Zip / tar the app folder so it unpacks into a single YTDL/ directory."""
    base = str(out).removesuffix('.zip').removesuffix('.tar.gz')
    shutil.make_archive(base, fmt, root_dir=DIST, base_dir=APP_NAME)
    print('Created', out, flush=True)


def find_iscc():
    found = shutil.which('iscc')
    if found:
        return found
    for base in (os.environ.get('ProgramFiles(x86)'), os.environ.get('ProgramFiles'),
                 os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Programs')):
        for version in ('7', '6'):
            candidate = Path(base or '') / f'Inno Setup {version}' / 'ISCC.exe'
            if base and candidate.exists():
                return candidate
    return None


def windows():
    app_dir = DIST / APP_NAME
    smoke_test(app_dir / f'{APP_NAME}.exe')
    archive('zip', RELEASE / f'{APP_NAME}-Windows-x64-Portable.zip')
    iscc = find_iscc()
    if iscc:
        run(iscc, '/Q', f'/DAppVersion={__version__}', f'/DSourceDir={app_dir}', f'/DOutputDir={RELEASE}',
            ROOT / 'packaging' / 'windows' / 'installer.iss')
    else:
        print('Inno Setup not found, so no installer was built (get it from https://jrsoftware.org/isinfo.php)')


def macos():
    app = DIST / f'{APP_NAME}.app'
    smoke_test(app / 'Contents' / 'MacOS' / APP_NAME)
    out = RELEASE / f"{APP_NAME}-macOS-{'AppleSilicon' if platform.machine() == 'arm64' else 'Intel'}.dmg"
    staging = BUILD / 'dmg'
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    run('ditto', app, staging / app.name)  # keeps symlinks and code signatures intact
    (staging / 'Applications').symlink_to('/Applications')
    for attempt in range(3):  # hdiutil is known to fail now and then with "Resource busy"
        try:
            run('hdiutil', 'create', '-volname', APP_NAME, '-srcfolder', staging, '-fs', 'HFS+',
                '-format', 'UDZO', '-ov', out)
            break
        except subprocess.CalledProcessError:
            if attempt == 2:
                raise
            time.sleep(10)


def linux():
    app_dir = DIST / APP_NAME
    smoke_test(app_dir / APP_NAME)
    arch = platform.machine()
    archive('gztar', RELEASE / f'{APP_NAME}-Linux-{arch}.tar.gz')

    appdir = BUILD / 'AppDir'
    shutil.rmtree(appdir, ignore_errors=True)
    shutil.copytree(app_dir, appdir / 'usr' / 'bin', symlinks=True)
    shutil.copy2(ROOT / 'assets' / 'icon.png', appdir / 'ytdl.png')
    (appdir / '.DirIcon').symlink_to('ytdl.png')
    (appdir / 'ytdl.desktop').write_text(DESKTOP_ENTRY)
    apprun = appdir / 'AppRun'
    apprun.write_text(f'#!/bin/sh\nHERE="$(dirname "$(readlink -f "$0")")"\nexec "$HERE/usr/bin/{APP_NAME}" "$@"\n')
    apprun.chmod(0o755)

    tool = BUILD / f'appimagetool-{arch}.AppImage'
    if not tool.exists():
        print('Downloading appimagetool', flush=True)
        urllib.request.urlretrieve(APPIMAGETOOL.format(arch=arch), tool)
        tool.chmod(0o755)
    # Extract-and-run works without FUSE (CI runners, containers)
    env = {**os.environ, 'ARCH': arch, 'APPIMAGE_EXTRACT_AND_RUN': '1'}
    run(tool, '--no-appstream', appdir, RELEASE / f'{APP_NAME}-Linux-{arch}.AppImage', env=env)


def main():
    print(f'Building {APP_NAME} {__version__} on {platform.system()} {platform.machine()}', flush=True)
    shutil.rmtree(RELEASE, ignore_errors=True)
    RELEASE.mkdir(parents=True)
    pyinstaller()
    {'win32': windows, 'darwin': macos}.get(sys.platform, linux)()
    print('\nRelease files:')
    for path in sorted(RELEASE.iterdir()):
        print(f'  {path.name}  ({path.stat().st_size / 1e6:.1f} MB)')


if __name__ == '__main__':
    main()
