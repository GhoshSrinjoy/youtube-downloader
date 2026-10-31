# PyInstaller spec for the desktop app. Use packaging/build.py rather than calling this directly.
#
# One-folder build (fast start-up, no unpacking on every launch) that also ships
# ffmpeg (from the imageio-ffmpeg wheel) and Deno (from the deno wheel) in bin/,
# so the app works without anything else installed.

import shutil
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
sys.path.insert(0, str(ROOT / 'src'))
from ytdl import APP_NAME, REPO_URL, __version__  # noqa: E402

EXE_SUFFIX = '.exe' if sys.platform == 'win32' else ''
ICON = ROOT / 'assets' / ('icon.icns' if sys.platform == 'darwin' else 'icon.ico')


def helper_binaries():
    """Copy ffmpeg and deno under their plain names; the app looks for them in bin/."""
    import deno
    import imageio_ffmpeg

    staging = ROOT / 'build' / 'helpers'
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    binaries = []
    for name, source in (('ffmpeg', imageio_ffmpeg.get_ffmpeg_exe()), ('deno', deno.find_deno_bin())):
        target = staging / f'{name}{EXE_SUFFIX}'
        shutil.copy2(source, target)
        binaries.append((str(target), 'bin'))
    return binaries


def windows_version_info():
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo,
    )

    numbers = tuple(int(n) for n in __version__.split('.')[:3]) + (0,)
    strings = {
        'CompanyName': 'Srinjoy Ghosh',
        'FileDescription': f'{APP_NAME} - YouTube Downloader',
        'FileVersion': __version__,
        'InternalName': APP_NAME,
        'LegalCopyright': f'Apache License 2.0 - {REPO_URL}',
        'OriginalFilename': f'{APP_NAME}.exe',
        'ProductName': APP_NAME,
        'ProductVersion': __version__,
    }
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=numbers, prodvers=numbers),
        kids=[
            StringFileInfo([StringTable('040904B0', [StringStruct(k, v) for k, v in strings.items()])]),
            VarFileInfo([VarStruct('Translation', [1033, 1200])]),
        ],
    )


a = Analysis(
    [str(ROOT / 'src' / 'ytdl_gui.py')],
    pathex=[str(ROOT / 'src')],
    binaries=helper_binaries(),
    datas=[(str(ROOT / 'assets' / 'icon.png'), 'assets')],
    # imageio_ffmpeg and deno are only used to locate the binaries copied above
    excludes=['tkinter', 'imageio_ffmpeg', 'deno', 'PyQt5', 'PySide2', 'PySide6', 'numpy', 'IPython', 'pytest'],
)

# Parts of Qt the app never touches: QML/Quick, PDF, the software OpenGL fallback, virtual keyboard
UNUSED = ('opengl32sw', 'qt6pdf', 'qpdf', 'qt6quick', 'qt6qml', 'qt6virtualkeyboard', 'qtvirtualkeyboard')
a.binaries = [b for b in a.binaries if not any(u in Path(b[0]).name.lower() for u in UNUSED)]
if sys.platform == 'win32':
    # Windows 10+ always has the Universal CRT; a stray old copy in the bundle breaks Qt
    a.binaries = [b for b in a.binaries
                  if not Path(b[0]).name.lower().startswith(('api-ms-win-', 'ucrtbase'))]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    console=False,
    icon=str(ICON),
    version=windows_version_info() if sys.platform == 'win32' else None,
    upx=False,
)

coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=APP_NAME)

if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name=f'{APP_NAME}.app',
        icon=str(ICON),
        bundle_identifier='io.github.ghoshsrinjoy.ytdl',
        version=__version__,
        info_plist={
            'CFBundleDisplayName': APP_NAME,
            'CFBundleShortVersionString': __version__,
            'NSHighResolutionCapable': True,
            'NSRequiresAquaSystemAppearance': False,  # follow light/dark mode
            'LSApplicationCategoryType': 'public.app-category.utilities',
            'LSMinimumSystemVersion': '14.0',  # PyQt6 6.12 wheels need macOS 14+
        },
    )
