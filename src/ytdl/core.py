"""Download engine shared by the desktop app and the command line.

Everything that talks to yt-dlp lives here: turning the user's choices into
yt-dlp options, finding the bundled ffmpeg and JavaScript runtime, fetching
metadata, and folding yt-dlp's per-stream progress callbacks into one value.

yt-dlp is imported lazily so the app window can open before it has loaded.
"""

from __future__ import annotations

import copy
import functools
import glob
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field, fields

# Choices offered by the app and the CLI. Keys are what gets stored and passed around.
VIDEO_QUALITIES = {
    'best': 'Best available',
    '2160': '2160p · 4K',
    '1440': '1440p · 2K',
    '1080': '1080p · Full HD',
    '720': '720p · HD',
    '480': '480p',
    '360': '360p',
    '240': '240p',
    '144': '144p',
}
VIDEO_FORMATS = {'mp4': 'MP4', 'mkv': 'MKV', 'webm': 'WebM'}
AUDIO_FORMATS = {'mp3': 'MP3', 'm4a': 'M4A · AAC', 'opus': 'Opus', 'flac': 'FLAC · lossless', 'wav': 'WAV · lossless'}
LOSSLESS_AUDIO = {'flac', 'wav'}
AUDIO_QUALITIES = {'best': 'Best quality', '320': '320 kbps', '256': '256 kbps', '192': '192 kbps', '128': '128 kbps', '96': '96 kbps'}
CODECS = {'auto': 'Auto (best quality)', 'h264': 'H.264 (most compatible)', 'vp9': 'VP9', 'av1': 'AV1 (smallest files)'}
FILENAME_TEMPLATES = {
    'title': ('Title', '%(title)s.%(ext)s'),
    'title_id': ('Title [id]', '%(title)s [%(id)s].%(ext)s'),
    'channel_title': ('Channel - Title', '%(uploader,channel|Unknown)s - %(title)s.%(ext)s'),
}
BROWSERS = {
    '': 'None', 'firefox': 'Firefox', 'chrome': 'Chrome', 'edge': 'Edge', 'brave': 'Brave',
    'opera': 'Opera', 'vivaldi': 'Vivaldi', 'chromium': 'Chromium', 'safari': 'Safari',
}

_CODEC_SORT = {'h264': 'vcodec:h264', 'vp9': 'vcodec:vp9', 'av1': 'vcodec:av01'}
# Containers that EmbedThumbnail can write cover art into.
_THUMBNAIL_CONTAINERS = {'mp4', 'mkv', 'm4a', 'mp3', 'opus', 'flac'}
_STAGES = {
    'Merger': 'Merging video and audio',
    'ExtractAudio': 'Converting audio',
    'SubtitlesConvertor': 'Converting subtitles',
    'EmbedSubtitle': 'Embedding subtitles',
    'Metadata': 'Writing metadata',
    'ThumbnailsConvertor': 'Preparing cover art',
    'EmbedThumbnail': 'Embedding cover art',
    'MoveFiles': 'Finishing up',
}


def default_download_dir() -> str:
    downloads = os.path.join(os.path.expanduser('~'), 'Downloads')
    return downloads if os.path.isdir(downloads) else os.path.expanduser('~')


def dataclass_from_dict(cls, data: dict | None):
    """Build a dataclass from stored values, coercing types and skipping unknown or unreadable keys."""
    defaults = cls()
    kwargs = {}
    for f in fields(cls):
        if not data or f.name not in data:
            continue
        value, kind = data[f.name], type(getattr(defaults, f.name))
        if kind is bool and isinstance(value, str):
            value = value.lower() in ('1', 'true', 'yes')
        try:
            kwargs[f.name] = kind(value)
        except (TypeError, ValueError):
            pass  # keep the default
    return cls(**kwargs)


@dataclass
class Options:
    """Everything that controls how an item is downloaded."""

    mode: str = 'video'                  # 'video' or 'audio'
    quality: str = 'best'                # key of VIDEO_QUALITIES (max height)
    video_format: str = 'mp4'
    audio_format: str = 'mp3'
    audio_quality: str = 'best'          # key of AUDIO_QUALITIES
    codec: str = 'auto'                  # key of CODECS
    video_only: bool = False             # drop the audio track from video downloads
    output_dir: str = ''
    filename: str = 'title'              # key of FILENAME_TEMPLATES
    playlist_folder: bool = True         # playlists go into their own numbered folder
    subtitles: bool = False
    subtitle_langs: str = 'en'
    auto_subtitles: bool = False
    embed_subtitles: bool = True
    embed_thumbnail: bool = True
    embed_metadata: bool = True
    save_thumbnail: bool = False
    save_description: bool = False
    save_info_json: bool = False
    fragments: int = 4                   # parallel connections per download
    rate_limit: float = 0.0              # MB/s, 0 = unlimited
    cookies_browser: str = ''            # key of BROWSERS
    proxy: str = ''

    @classmethod
    def from_dict(cls, data: dict | None) -> Options:
        return dataclass_from_dict(cls, data)

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def target_ext(self) -> str:
        return self.audio_format if self.mode == 'audio' else self.video_format

    def describe(self) -> str:
        """Short summary such as '1080p · MP4' or 'MP3 · 320 kbps'."""
        if self.mode == 'audio':
            fmt = self.audio_format.upper()
            if self.audio_format in LOSSLESS_AUDIO or self.audio_quality == 'best':
                return fmt
            return f'{fmt} · {self.audio_quality} kbps'
        quality = 'Best' if self.quality == 'best' else f'{self.quality}p'
        return f"{quality} · {self.video_format.upper()}{' · no audio' if self.video_only else ''}"


# ---------------------------------------------------------------------------
# Helper tools (ffmpeg for merging/converting, a JS runtime for YouTube)

EXE_SUFFIX = '.exe' if os.name == 'nt' else ''
# Keeps helper processes from flashing a console window on Windows
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def _bundled(name: str) -> str | None:
    """Path of a helper binary shipped inside a frozen (PyInstaller) build."""
    base = getattr(sys, '_MEIPASS', None)
    if base:
        path = os.path.join(base, 'bin', name + EXE_SUFFIX)
        if os.path.isfile(path):
            return path
    return None


@functools.cache
def find_ffmpeg() -> str | None:
    """Bundled ffmpeg, else one on PATH, else the imageio-ffmpeg wheel's copy."""
    path = _bundled('ffmpeg') or shutil.which('ffmpeg')
    if path:
        return path
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # not installed, or no binary for this platform
        return None


@functools.cache
def find_deno() -> str | None:
    """Bundled Deno, else the one installed by the `deno` pip package."""
    path = _bundled('deno')
    if path:
        return path
    try:
        import deno
        return deno.find_deno_bin()
    except Exception:
        return None


def js_runtimes() -> dict:
    """yt-dlp needs a JavaScript runtime to solve YouTube's challenges."""
    deno = find_deno()
    if deno:
        return {'deno': {'path': deno}}
    # No Deno of our own: let yt-dlp use whichever supported runtime is installed.
    return {'deno': {}, 'node': {}, 'bun': {}, 'quickjs': {}}


def _run_version(path: str, *args: str) -> str:
    try:
        return subprocess.run(
            [path, *args], capture_output=True, text=True, timeout=20, creationflags=NO_WINDOW,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ''


def _js_runtime_name() -> str | None:
    deno = find_deno()
    if deno:
        m = re.match(r'deno (\S+)', _run_version(deno, '--version'))
        return f'deno {m.group(1)}' if m else None
    try:  # yt-dlp's own detection; private API, so only used for display
        from yt_dlp.utils import _jsruntime as jsr
        runtimes = (jsr.DenoJsRuntime(), jsr.NodeJsRuntime(), jsr.BunJsRuntime(), jsr.QuickJsRuntime())
    except Exception:
        return None
    for runtime in runtimes:
        info = runtime.info
        if info and info.supported:
            return f'{info.name} {info.version}'
    return None


_warm_lock = threading.Lock()
_warm = False


def warm_up():
    """Import yt-dlp and run its one-time setup once, before worker threads use it concurrently."""
    global _warm
    with _warm_lock:
        if not _warm:
            from yt_dlp import YoutubeDL
            with YoutubeDL({'quiet': True, 'no_warnings': True}):
                pass
            _warm = True


def environment() -> dict:
    """Versions of yt-dlp and its helpers; runs a few subprocesses, so call it off the UI thread."""
    import yt_dlp

    warm_up()
    ffmpeg = find_ffmpeg()
    ffmpeg_version = None
    if ffmpeg:
        m = re.search(r'ffmpeg version n?(\d+(?:\.\d+)*)', _run_version(ffmpeg, '-version'))
        ffmpeg_version = m.group(1) if m else 'found'
    return {'yt-dlp': yt_dlp.version.__version__, 'ffmpeg': ffmpeg_version, 'js': _js_runtime_name()}


# ---------------------------------------------------------------------------
# Options -> yt-dlp parameters

def split_langs(text: str) -> list[str]:
    return [lang.strip() for lang in re.split(r'[,\s]+', text or '') if lang.strip()] or ['en']


def format_selection(o: Options) -> tuple[str, list[str], str | None]:
    """Return yt-dlp's (format, format_sort, merge_output_format) for the options.

    Preferences go into format_sort rather than hard format filters, so yt-dlp
    falls back to the closest match instead of failing when e.g. 1080p does not exist.
    """
    if o.mode == 'audio':
        # Pick a source that needs no re-encoding when the target codec allows it
        sort = {'m4a': ['acodec:aac'], 'opus': ['acodec:opus']}.get(o.audio_format, [])
        return 'ba/b', sort, None
    sort = []
    if o.codec in _CODEC_SORT:
        sort.append(_CODEC_SORT[o.codec])
    sort.append('res' if o.quality == 'best' else f'res:{o.quality}')
    if o.video_format == 'mp4':
        sort.append('ext:mp4:m4a')
    elif o.video_format == 'webm':
        sort.append('ext:webm:webm')
    return ('bv/b' if o.video_only else 'bv*+ba/b'), sort, o.video_format


def filename_template(o: Options) -> str:
    return FILENAME_TEMPLATES.get(o.filename, FILENAME_TEMPLATES['title'])[1]


def network_params(o: Options) -> dict:
    params = {
        'js_runtimes': js_runtimes(),
        'retries': 10,
        'fragment_retries': 10,
        'extractor_retries': 3,
        'socket_timeout': 30,
    }
    if o.cookies_browser:
        params['cookiesfrombrowser'] = (o.cookies_browser,)
    if o.proxy:
        params['proxy'] = o.proxy
    return params


def build_params(o: Options, outtmpl: str | None = None) -> dict:
    """yt-dlp parameters for downloading with the given options."""
    fmt, sort, merge = format_selection(o)
    audio = o.mode == 'audio'
    # Without a merge the container is whatever the stream was, which may not take cover art
    embed_thumbnail = o.embed_thumbnail and o.target_ext in _THUMBNAIL_CONTAINERS and not (o.video_only and not audio)
    subtitles = o.subtitles and not audio
    embed_subtitles = subtitles and o.embed_subtitles

    postprocessors = []
    if audio:
        quality = '0' if o.audio_quality == 'best' or o.audio_format in LOSSLESS_AUDIO else o.audio_quality
        postprocessors.append({'key': 'FFmpegExtractAudio', 'preferredcodec': o.audio_format, 'preferredquality': quality})
    if subtitles and not embed_subtitles:
        postprocessors.append({'key': 'FFmpegSubtitlesConvertor', 'format': 'srt', 'when': 'before_dl'})
    if embed_subtitles:
        postprocessors.append({'key': 'FFmpegEmbedSubtitle', 'already_have_subtitle': False})
    if o.embed_metadata:
        postprocessors.append({'key': 'FFmpegMetadata', 'add_metadata': True, 'add_chapters': True, 'add_infojson': False})
    if embed_thumbnail:
        postprocessors.append({'key': 'EmbedThumbnail', 'already_have_thumbnail': o.save_thumbnail})

    params = {
        **network_params(o),
        'format': fmt,
        'format_sort': sort,
        'paths': {'home': o.output_dir or default_download_dir()},
        'outtmpl': {'default': outtmpl or filename_template(o)},
        'noplaylist': True,
        'quiet': True,
        'noprogress': True,
        'color': 'no_color',
        # yt-dlp applies the rate limit to each fragment connection, so a limit means one connection
        'concurrent_fragment_downloads': 1 if o.rate_limit else max(1, int(o.fragments)),
        'writesubtitles': subtitles,
        'writeautomaticsub': subtitles and o.auto_subtitles,
        'writethumbnail': o.save_thumbnail or embed_thumbnail,
        'writedescription': o.save_description,
        'writeinfojson': o.save_info_json,
        'postprocessors': postprocessors,
    }
    if subtitles:
        params['subtitleslangs'] = split_langs(o.subtitle_langs)
    if merge:
        params['merge_output_format'] = merge
    if audio:
        # Lets yt-dlp recognise an already converted file and skip it
        params['final_ext'] = o.audio_format
    if o.rate_limit:
        params['ratelimit'] = int(float(o.rate_limit) * 1024 * 1024)
    ffmpeg = find_ffmpeg()
    if ffmpeg:
        params['ffmpeg_location'] = ffmpeg
    return params


# ---------------------------------------------------------------------------
# Fetching metadata

# Stream links in fetched info expire after some hours; older info is extracted again instead.
REUSE_INFO_FOR = 3 * 3600


@dataclass
class Task:
    """One downloadable video, as resolved from a link."""

    url: str
    title: str = ''
    meta: dict = field(default_factory=dict)    # title, duration, uploader, thumbnails...
    info: dict | None = None                    # raw extractor result the download can reuse
    outtmpl: str | None = None                  # per-item output template (playlist entries)
    fetched_at: float = 0.0


def extract(url: str, options: Options, logger=None) -> tuple[dict, bool]:
    """Fetch metadata for *url* without downloading.

    Returns (info, reusable). A single video comes back as yt-dlp's raw extractor
    result, which the download step can reuse so the page is not extracted twice;
    playlists and channels come back processed, with flat entries.
    """
    from yt_dlp import YoutubeDL

    warm_up()
    params = {
        **network_params(options), 'quiet': True, 'noprogress': True, 'color': 'no_color',
        'noplaylist': True, 'skip_download': True, 'extract_flat': 'in_playlist',
    }
    if logger:
        params['logger'] = logger
    with YoutubeDL(params) as ydl:
        info = ydl.extract_info(url, download=False, process=False)
        if info.get('_type', 'video') == 'video':
            return info, bool(info.get('formats'))
        return ydl.process_ie_result(info, download=False), False


def _is_nested_playlist(entry: dict) -> bool:
    return entry.get('_type') == 'playlist' or entry.get('ie_key') == 'YoutubeTab'


def expand(url: str, options: Options, logger=None, _depth: int = 0) -> tuple[str | None, list[Task]]:
    """Resolve a link into tasks. Returns (playlist title or None, tasks)."""
    from yt_dlp.utils import sanitize_filename

    info, reusable = extract(url, options, logger)
    if info.get('_type', 'video') == 'video':
        return None, [Task(
            url=info.get('webpage_url') or url, title=info.get('title') or url, meta=info,
            info=info if reusable else None, fetched_at=time.time(),
        )]

    entries = []
    for entry in info.get('entries') or []:
        if not entry:
            continue
        if _is_nested_playlist(entry) and entry.get('url') and _depth < 2:
            # e.g. a channel link lists its Videos / Shorts / Live tabs
            entries.extend(t.meta for t in expand(entry['url'], options, logger, _depth + 1)[1])
        elif entry.get('url') or entry.get('webpage_url'):
            entries.append(entry)

    title = info.get('title') or info.get('id') or 'Playlist'
    folder = sanitize_filename(title).replace('%', '%%') if options.playlist_folder else None
    width = max(2, len(str(len(entries))))
    tasks = []
    for index, entry in enumerate(entries, 1):
        outtmpl = f'{folder}/{index:0{width}d} - {filename_template(options)}' if folder else None
        tasks.append(Task(
            url=entry.get('webpage_url') or entry['url'], title=entry.get('title') or entry.get('id') or '',
            meta=entry, outtmpl=outtmpl,
        ))
    return title, tasks


def thumbnail_url(meta: dict) -> str | None:
    """A small (~320px wide) thumbnail for the queue."""
    video_id = meta.get('id')
    extractor = meta.get('ie_key') or meta.get('extractor_key') or ''
    if extractor == 'Youtube' and video_id:
        return f'https://i.ytimg.com/vi/{video_id}/mqdefault.jpg'
    thumbs = [t for t in meta.get('thumbnails') or [] if t.get('url')]
    sized = [t for t in thumbs if t.get('width')]
    if sized:
        return min(sized, key=lambda t: abs(t['width'] - 320))['url']
    return thumbs[-1]['url'] if thumbs else meta.get('thumbnail')


def fetch_bytes(url: str, options: Options, timeout: float = 15) -> bytes:
    """Download a small resource (e.g. a thumbnail) through yt-dlp's networking stack."""
    from yt_dlp import YoutubeDL
    from yt_dlp.networking import Request

    warm_up()
    params = {'quiet': True, 'no_warnings': True, 'socket_timeout': timeout}
    if options.proxy:
        params['proxy'] = options.proxy
    with YoutubeDL(params) as ydl:
        return ydl.urlopen(Request(url)).read()


# ---------------------------------------------------------------------------
# Downloading

class ProgressTracker:
    """Folds yt-dlp's per-stream progress hooks into one overall progress value.

    "Video + audio" downloads are two separate transfers that yt-dlp merges
    afterwards, so the raw hooks would make a progress bar run to 100% twice.
    """

    def __init__(self):
        self._expected = {}   # format_id -> expected size of each selected stream
        self._streams = {}    # format_id -> (downloaded, total) as reported so far

    def set_formats(self, formats: list[dict]):
        self._expected = {
            str(f.get('format_id')): f.get('filesize') or f.get('filesize_approx') or 0 for f in formats
        }

    def update(self, d: dict) -> dict:
        stream = str((d.get('info_dict') or {}).get('format_id'))
        done = d.get('downloaded_bytes') or 0
        total = d.get('total_bytes') or d.get('total_bytes_estimate') or self._expected.get(stream) or 0
        if d.get('status') == 'finished':
            done = total = total or done
        self._streams[stream] = (done, total)

        ids = self._expected.keys() | self._streams.keys()
        downloaded = sum(self._streams.get(i, (0, 0))[0] for i in ids)
        size = sum(self._streams.get(i, (0, 0))[1] or self._expected.get(i, 0) for i in ids)
        fraction = downloaded / size if size else None
        if fraction is None and d.get('fragment_count'):
            fraction = (d.get('fragment_index') or 0) / d['fragment_count']
        speed = d.get('speed') if d.get('status') == 'downloading' else None
        eta = (size - downloaded) / speed if speed and size > downloaded else d.get('eta')
        return {
            'fraction': None if fraction is None else min(max(fraction, 0.0), 1.0),
            'downloaded': downloaded, 'total': size or None, 'speed': speed, 'eta': eta,
        }


def _callback_pp(callback):
    """A yt-dlp post-processor that just hands the info dict to *callback*."""
    from yt_dlp.postprocessor.common import PostProcessor

    class CallbackPP(PostProcessor):
        def run(self, info):
            callback(info)
            return [], info

    return CallbackPP()


class Download:
    """One download, run synchronously on the calling thread.

    The optional callbacks are called on that same thread:
      on_progress(dict) - throttled overall progress (fraction, downloaded, total, speed, eta)
      on_stage(str)     - post-processing step, e.g. "Merging video and audio"
      on_format(dict)   - what yt-dlp picked: ext, height, size
    """

    def __init__(self, task: Task, options: Options, *, logger=None, on_progress=None, on_stage=None, on_format=None):
        self.task = task
        self.options = options
        self.logger = logger
        self.on_progress = on_progress
        self.on_stage = on_stage
        self.on_format = on_format
        self.filepath = None
        self._stop = None              # None, 'pause' or 'cancel'
        self._downloading = False      # True until post-processing starts
        self._partials = set()         # .part files this run wrote
        self._streams = set()          # stream files this run downloaded
        self._side_files = set()       # thumbnails / subtitles written for this item
        self._tracker = ProgressTracker()
        self._last_emit = 0.0

    def stop(self, pause: bool = False):
        """Ask the download to stop. Only takes effect while data is transferring."""
        self._stop = 'pause' if pause else 'cancel'

    @property
    def stop_reason(self) -> str | None:
        return self._stop

    def run(self) -> str | None:
        """Download and post-process; returns the final file path.

        Raises yt_dlp.utils.DownloadCancelled when stopped, DownloadError on failure.
        """
        from yt_dlp import YoutubeDL
        from yt_dlp.utils import DownloadCancelled, DownloadError

        warm_up()
        params = build_params(self.options, self.task.outtmpl)
        params['progress_hooks'] = [self._on_download_progress]
        params['postprocessor_hooks'] = [self._on_postprocess]
        if self.logger:
            params['logger'] = self.logger
        self._downloading = True
        try:
            with YoutubeDL(params) as ydl:
                ydl.add_post_processor(_callback_pp(self._before_download), when='before_dl')
                ydl.add_post_processor(_callback_pp(self._after_move), when='after_move')
                if self.task.info is not None and time.time() - self.task.fetched_at < REUSE_INFO_FOR:
                    try:
                        ydl.process_ie_result(copy.deepcopy(self.task.info), download=True)
                    except DownloadError:
                        if not self._downloading:
                            raise  # failed while post-processing; extracting again won't help
                        # The stream links from the earlier fetch can expire; start over from the page.
                        self._tracker = ProgressTracker()
                        ydl.download([self.task.url])
                else:
                    ydl.download([self.task.url])
        except DownloadCancelled:
            if self._stop == 'cancel':
                self.cleanup()
            raise
        finally:
            self._downloading = False
        return self.filepath

    def cleanup(self):
        """Delete partial and intermediate files written by this download (never finished output)."""
        paths = set(self._side_files)
        for part in self._partials:
            paths.update((part, part + '.ytdl'))
            paths.update(glob.glob(glob.escape(part) + '-Frag*'))
        for stream in self._streams:
            paths.add(stream + '.ytdl')
            if re.search(r'\.f[\w-]+\.\w+$', stream):  # e.g. "Title.f137.mp4", merged later
                paths.add(stream)
        for path in paths:
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except OSError:
                pass

    # yt-dlp callbacks -----------------------------------------------------

    def _on_download_progress(self, d: dict):
        if self._stop:
            from yt_dlp.utils import DownloadCancelled
            raise DownloadCancelled('Paused' if self._stop == 'pause' else 'Cancelled')
        if d.get('status') == 'downloading':
            if d.get('tmpfilename'):
                self._partials.add(d['tmpfilename'])
            if d.get('filename'):
                self._streams.add(d['filename'])
        progress = self._tracker.update(d)
        now = time.monotonic()
        if self.on_progress and (d.get('status') != 'downloading' or now - self._last_emit >= 0.2):
            self._last_emit = now
            self.on_progress(progress)

    def _on_postprocess(self, d: dict):
        name = d.get('postprocessor')
        if d.get('status') != 'started' or name == 'Callback':
            return
        self._downloading = False
        if self.on_stage:
            self.on_stage(_STAGES.get(name, 'Processing'))

    def _before_download(self, info: dict):
        formats = info.get('requested_formats') or [info]
        self._tracker.set_formats(formats)
        self._side_files.update(path for path in info.get('__files_to_move') or {} if path)
        if self.on_format:
            self.on_format({
                'ext': info.get('ext'),
                'quality': quality_label(info.get('width'), info.get('height'), info.get('fps')),
                'size': sum(f.get('filesize') or f.get('filesize_approx') or 0 for f in formats) or None,
            })

    def _after_move(self, info: dict):
        self.filepath = info.get('filepath')


# ---------------------------------------------------------------------------
# Formatting

def human_size(n: float | None) -> str:
    if not n:
        return ''
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024:
            return f'{n:.0f} {unit}' if unit in ('B', 'KB') else f'{n:.1f} {unit}'
        n /= 1024
    return f'{n:.2f} TB'


_LADDER = ((7680, 4320), (3840, 2160), (2560, 1440), (1920, 1080), (1280, 720), (854, 480), (640, 360), (426, 240), (256, 144))


def quality_label(width: int | None, height: int | None, fps: float | None = None) -> str:
    """'1080p60' style label. Wide (1920x800) and vertical (1080x1920) videos get their usual class."""
    if not height:
        return ''
    long_side, short_side = max(width or 0, height), min(width or height, height)
    label = next((h for w, h in _LADDER if long_side >= w * 0.95 or short_side >= h * 0.95), short_side)
    return f'{label}p{round(fps) if fps and fps > 30 else ""}'


def human_duration(seconds: float | None) -> str:
    if not seconds:
        return ''
    seconds = int(round(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return f'{hours}:{minutes:02d}:{seconds:02d}' if hours else f'{minutes}:{seconds:02d}'


_ERROR_HINTS = [
    (r"confirm you.re not a bot|sign in to confirm", 'YouTube wants a signed-in session. Choose your browser under Settings → Network → Cookies.'),
    (r'confirm your age|age.restricted|inappropriate for some users', 'Age-restricted video. Use cookies from a browser where you are signed in (Settings → Network).'),
    (r'members.only|join this channel', 'Members-only video. Use cookies from a browser signed in to a member account.'),
    (r'requested format is not available', 'That quality isn\'t available for this video. Try "Best available".'),
    (r'ffmpeg.*not (found|installed)|ffprobe.*not found', 'FFmpeg is missing, so video and audio can\'t be merged or converted.'),
    (r'private video', 'This video is private.'),
    (r'video unavailable|this video is not available', 'This video is unavailable.'),
    (r'unsupported url', 'This link isn\'t supported.'),
    (r'could not (copy|find).*cookie|failed to decrypt|dpapi', 'Couldn\'t read browser cookies. Close the browser and retry, or pick Firefox.'),
    (r'no space left|disk full|errno 28', 'The disk is full.'),
    (r'getaddrinfo|name resolution|network is unreachable|timed out', 'Network problem. Check your connection and retry.'),
]


def clean_error(message) -> str:
    text = re.sub(r'\x1b\[[0-9;]*m', '', str(message)).strip()
    return re.sub(r'^(ERROR:\s*)+', '', text)


def friendly_error(message) -> str:
    """One short line for an error from yt-dlp, with a hint when it's a known problem."""
    text = clean_error(message)
    for pattern, hint in _ERROR_HINTS:
        if re.search(pattern, text, re.IGNORECASE):
            return hint
    text = re.sub(r'^\[[\w:]+\]\s*[\w-]+:\s*', '', text)  # "[youtube] abc123: " prefix
    return (text.splitlines() or ['Download failed'])[0]


def find_urls(text: str) -> list[str]:
    """All http(s) links in a blob of text, de-duplicated, in order."""
    urls = re.findall(r'https?://[^\s<>"\']+', text or '')
    return list(dict.fromkeys(u.rstrip('.,;)]}') for u in urls))
