import sys
import os
import yt_dlp
import argparse

def download_video(url, options):
    ydl_opts = {
        'format': options.format,
        'outtmpl': '%(title)s.%(ext)s',
        'writesubtitles': options.subtitles,
        'subtitleslangs': ['en'] if options.subtitles else [],
        'writethumbnail': options.thumbnail,
        'writedescription': options.description,
        'writeinfojson': options.info,
    }

    # Add resolution filter if specified
    if options.resolution and options.resolution != 'best':
        ydl_opts['format'] += f'[height<={options.resolution}]'

    # Add video codec preference if specified
    if options.codec and options.codec != 'any':
        ydl_opts['format'] += f'[vcodec*={options.codec}]'

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        print("Download completed successfully!")
    except Exception as e:
        print(f"Error: {str(e)}")
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description='YouTube Video Downloader')
    parser.add_argument('url', help='YouTube video URL')
    parser.add_argument('--format', default='bestvideo+bestaudio/best',
                      choices=['bestvideo+bestaudio/best', 'bestvideo', 'bestaudio'],
                      help='Download format')
    parser.add_argument('--resolution', default='best',
                      choices=['best', '2160', '1440', '1080', '720', '480', '360', '240', '144'],
                      help='Maximum resolution')
    parser.add_argument('--codec', default='any',
                      choices=['any', 'avc1', 'vp9', 'av01'],
                      help='Preferred video codec')
    parser.add_argument('--subtitles', action='store_true',
                      help='Download subtitles')
    parser.add_argument('--thumbnail', action='store_true',
                      help='Download thumbnail')
    parser.add_argument('--description', action='store_true',
                      help='Save description')
    parser.add_argument('--info', action='store_true',
                      help='Save video info JSON')

    args = parser.parse_args()
    download_video(args.url, args)

if __name__ == '__main__':
    main()