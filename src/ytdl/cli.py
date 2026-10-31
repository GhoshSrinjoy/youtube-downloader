"""Command-line interface.

    python src/ytdl_cli.py URL [URL ...] [options]
    python src/ytdl_cli.py -a links.txt -f mp3 -j 3
"""

from __future__ import annotations

import argparse
import shutil
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

from . import APP_NAME, __version__, core

# Values the original CLI accepted, still understood
_LEGACY_FORMATS = {'bestvideo+bestaudio/best': 'video', 'bestvideo': 'video_only', 'bestaudio': 'audio'}
_LEGACY_CODECS = {'any': 'auto', 'avc1': 'h264', 'av01': 'av1'}


class _Logger:
    """Keeps yt-dlp quiet; failures are reported once per item instead."""

    def __init__(self, verbose: bool):
        self.verbose = verbose

    def debug(self, msg):
        if self.verbose and not msg.startswith('[debug] '):
            print(msg, file=sys.stderr)

    info = debug

    def warning(self, msg):
        if self.verbose:
            print(f'WARNING: {msg}', file=sys.stderr)

    def error(self, msg):
        pass


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog='ytdl', description='Download videos, audio and whole playlists from YouTube and 1000+ other sites.',
        epilog='examples:\n'
               '  ytdl https://youtu.be/aqz-KE-bpKQ -q 1080\n'
               '  ytdl -f mp3 "https://www.youtube.com/playlist?list=..."\n'
               '  ytdl -a links.txt -j 3 -o ~/Videos',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('urls', nargs='*', metavar='URL', help='video, playlist or channel links')
    p.add_argument('-a', '--batch-file', metavar='FILE', help='read links from FILE, one per line ("-" for stdin)')
    p.add_argument('-o', '--output', metavar='DIR', default=core.default_download_dir(), help='download folder (default: %(default)s)')
    p.add_argument('-j', '--jobs', type=int, default=1, help='downloads to run in parallel (default: 1)')
    p.add_argument('-v', '--verbose', action='store_true', help="show yt-dlp's own messages")
    p.add_argument('--version', action='version', version=f'{APP_NAME} {__version__}')

    g = p.add_argument_group('format')
    g.add_argument('-x', '--audio', action='store_true', help='audio only (MP3 unless --format says otherwise)')
    g.add_argument('-f', '--format', metavar='EXT', help='mp4, mkv, webm or mp3, m4a, opus, flac, wav (audio formats imply --audio)')
    g.add_argument('-q', '--quality', '--resolution', dest='quality', default='best', choices=list(core.VIDEO_QUALITIES), help='maximum video height (default: best)')
    g.add_argument('--audio-quality', default='best', choices=list(core.AUDIO_QUALITIES), help='bitrate for lossy audio in kbps (default: best)')
    g.add_argument('--codec', default='auto', choices=[*core.CODECS, *_LEGACY_CODECS], help='preferred video codec (default: auto)')
    g.add_argument('--video-only', action='store_true', help='video without the audio track')

    g = p.add_argument_group('extras')
    g.add_argument('--subtitles', action='store_true', help='download subtitles and embed them in the video')
    g.add_argument('--sub-langs', default='en', metavar='LANGS', help='subtitle languages, e.g. "en,de" or "en.*" (default: en)')
    g.add_argument('--auto-subs', action='store_true', help='include auto-generated subtitles')
    g.add_argument('--subs-as-files', action='store_true', help='save subtitles as .srt files instead of embedding them')
    g.add_argument('--thumbnail', action='store_true', help='also save the thumbnail image')
    g.add_argument('--description', action='store_true', help='save the description to a .description file')
    g.add_argument('--info', action='store_true', help='save video metadata to a .info.json file')
    g.add_argument('--no-cover', action='store_true', help="don't embed the thumbnail as cover art")
    g.add_argument('--no-metadata', action='store_true', help="don't embed title, chapters and other metadata")
    g.add_argument('--flat', action='store_true', help="don't put playlists into their own numbered folder")

    g = p.add_argument_group('network')
    g.add_argument('-N', '--connections', type=int, default=4, help='connections per download (default: 4)')
    g.add_argument('--limit-rate', type=float, default=0, metavar='MBPS', help='speed limit per download in MB/s')
    g.add_argument('--cookies-from-browser', choices=[b for b in core.BROWSERS if b], metavar='BROWSER',
                   help=f"use your browser's cookies ({', '.join(b for b in core.BROWSERS if b)})")
    g.add_argument('--proxy', metavar='URL', help='e.g. socks5://127.0.0.1:1080')
    return p, p.parse_args(argv)


def build_options(p: argparse.ArgumentParser, args) -> core.Options:
    o = core.Options(
        mode='audio' if args.audio else 'video', quality=args.quality, audio_quality=args.audio_quality,
        codec=_LEGACY_CODECS.get(args.codec, args.codec), video_only=args.video_only, output_dir=args.output,
        playlist_folder=not args.flat, subtitles=args.subtitles or args.auto_subs, subtitle_langs=args.sub_langs,
        auto_subtitles=args.auto_subs, embed_subtitles=not args.subs_as_files, embed_thumbnail=not args.no_cover,
        embed_metadata=not args.no_metadata, save_thumbnail=args.thumbnail, save_description=args.description,
        save_info_json=args.info, fragments=max(1, args.connections), rate_limit=args.limit_rate,
        cookies_browser=args.cookies_from_browser or '', proxy=args.proxy or '',
    )
    fmt = (args.format or '').lower()
    if fmt in _LEGACY_FORMATS:
        legacy = _LEGACY_FORMATS[fmt]
        o.mode = 'audio' if legacy == 'audio' else o.mode
        o.video_only = o.video_only or legacy == 'video_only'
    elif fmt in core.AUDIO_FORMATS:
        o.mode, o.audio_format = 'audio', fmt
    elif fmt in core.VIDEO_FORMATS:
        if o.mode == 'audio':
            p.error(f'--format {fmt} is a video format, but --audio was given')
        o.video_format = fmt
    elif fmt:
        p.error(f'unknown --format {fmt!r}; use one of: {", ".join([*core.VIDEO_FORMATS, *core.AUDIO_FORMATS])}')
    return o


def read_batch(path: str | None) -> list[str]:
    if not path:
        return []
    text = sys.stdin.read() if path == '-' else open(path, encoding='utf-8').read()
    lines = (line.strip() for line in text.splitlines())
    return [line for line in lines if line and not line.startswith(('#', ';'))]


class Console:
    """Prints results line by line, plus one live status line when attached to a terminal."""

    def __init__(self, total: int):
        self.total = total
        self.done = 0
        self.failed = 0
        self.active = {}            # task index -> (title, progress dict)
        self.lock = threading.Lock()
        self.live = sys.stdout.isatty()

    def line(self, text: str):
        with self.lock:
            self._clear()
            print(text, flush=True)
            self._status()

    def progress(self, index: int, title: str, progress: dict):
        with self.lock:
            self.active[index] = (title, progress)
            self._status()

    def finish(self, index: int, ok: bool):
        with self.lock:
            self.active.pop(index, None)
            self.done += 1
            self.failed += not ok

    def _clear(self):
        if self.live:
            print('\r' + ' ' * (shutil.get_terminal_size().columns - 1) + '\r', end='')

    def _status(self):
        if not self.live or not self.active:
            return
        speed = sum(p.get('speed') or 0 for _, p in self.active.values())
        if len(self.active) == 1:
            title, p = next(iter(self.active.values()))
            pct = f"{p['fraction'] * 100:5.1f}%" if p.get('fraction') is not None else '  ...'
            parts = [pct, core.human_size(p.get('total')), f'{core.human_size(speed)}/s' if speed else '',
                     f"ETA {core.human_duration(p['eta'])}" if p.get('eta') else '', title]
        else:
            parts = [f'{len(self.active)} downloading', f'{core.human_size(speed)}/s' if speed else '']
        text = f'[{self.done}/{self.total}] ' + '  '.join(x for x in parts if x)
        width = shutil.get_terminal_size().columns - 1
        print('\r' + text[:width].ljust(width), end='', flush=True)


def resolve(urls: list[str], options: core.Options, jobs: int, verbose: bool) -> tuple[list[core.Task], int]:
    """Turn links into tasks (playlists expand into their videos). Returns (tasks, failures)."""
    print(f'Fetching {len(urls)} link{"s" * (len(urls) != 1)}...', flush=True)

    def fetch(url):
        try:
            return url, core.expand(url, options, _Logger(verbose)), None
        except Exception as e:
            return url, None, e

    tasks, failures = [], 0
    with ThreadPoolExecutor(max_workers=max(1, min(jobs, 4))) as pool:
        for url, result, error in pool.map(fetch, urls):
            if error:
                failures += 1
                print(f'  ✗ {url}: {core.friendly_error(error)}', file=sys.stderr)
                continue
            playlist, found = result
            if playlist:
                print(f'  + {playlist}: {len(found)} videos')
            tasks.extend(found)
    return tasks, failures


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):  # don't crash on ✓ when output is redirected
            stream.reconfigure(errors='replace')
    p, args = parse_args(argv)
    options = build_options(p, args)
    urls = [*args.urls, *read_batch(args.batch_file)]
    if not urls:
        p.error('give at least one URL (or --batch-file)')

    from yt_dlp.utils import DownloadCancelled

    tasks, failures = resolve(urls, options, args.jobs, args.verbose)
    console = Console(len(tasks))
    downloads = []

    def work(index: int, task: core.Task) -> bool:
        title = task.title or task.url
        download = core.Download(
            task, options, logger=_Logger(args.verbose),
            on_progress=lambda progress: console.progress(index, title, progress))
        downloads.append(download)
        try:
            path = download.run()
        except DownloadCancelled:
            console.finish(index, False)
            return False
        except Exception as e:
            console.finish(index, False)
            console.line(f'✗ {title}: {core.friendly_error(e)}')
            return False
        console.finish(index, True)
        console.line(f'✓ {title}' + (f'  →  {path}' if path else ''))
        return True

    print(f'Downloading {len(tasks)} item{"s" * (len(tasks) != 1)} as {options.describe()} to {options.output_dir}', flush=True)
    pool = ThreadPoolExecutor(max_workers=max(1, args.jobs))
    try:
        results = list(pool.map(work, range(1, len(tasks) + 1), tasks))
    except KeyboardInterrupt:
        # Keep the .part files so running the same command again resumes
        for download in downloads:
            download.stop(pause=True)
        pool.shutdown(wait=True, cancel_futures=True)
        print('\nStopped. Run the same command again to resume.', file=sys.stderr)
        return 130
    pool.shutdown()

    failed = failures + results.count(False)
    print(f'\nDone: {results.count(True)} downloaded' + (f', {failed} failed' if failed else '') + '.')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
