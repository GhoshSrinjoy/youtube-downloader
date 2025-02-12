import sys
import os
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                           QHBoxLayout, QLineEdit, QPushButton, QLabel, 
                           QProgressBar, QComboBox, QFileDialog, QCheckBox,
                           QGroupBox, QGridLayout)
from PyQt6.QtCore import QThread, pyqtSignal

import yt_dlp

class DownloadWorker(QThread):
    progress = pyqtSignal(float)
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, url, output_path, options):
        super().__init__()
        self.url = url
        self.output_path = output_path
        self.options = options

    def progress_hook(self, d):
        if d['status'] == 'downloading':
            p = d.get('_percent_str', '0%').replace('%','')
            try:
                self.progress.emit(float(p))
            except:
                pass

    def run(self):
        try:
            ydl_opts = {
                'format': self.options['format'],
                'progress_hooks': [self.progress_hook],
                'outtmpl': os.path.join(self.output_path, '%(title)s.%(ext)s'),
                'writesubtitles': self.options.get('subtitles', False),
                'subtitleslangs': ['en'] if self.options.get('subtitles', False) else [],
                'writedescription': self.options.get('description', False),
                'writethumbnail': self.options.get('thumbnail', False),
                'writeinfojson': self.options.get('info_json', False),
            }
            
            # Add resolution filter if specified
            if self.options.get('resolution'):
                if self.options['resolution'] != 'best':
                    ydl_opts['format'] += f'[height<={self.options["resolution"]}]'

            # Add video codec preference if specified
            if self.options.get('video_codec'):
                ydl_opts['format'] += f'[vcodec*={self.options["video_codec"]}]'

            # Add audio quality settings
            if self.options.get('audio_quality'):
                if self.options['audio_quality'] == 'best':
                    ydl_opts['format'] += '+bestaudio'
                else:
                    ydl_opts['format'] += f'+worstaudio[abr>={self.options["audio_quality"]}]'
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([self.url])
            
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))

class YouTubeDownloader(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Advanced YouTube Downloader")
        self.setGeometry(100, 100, 800, 400)
        
        # Create main widget and layout
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QVBoxLayout(main_widget)
        
        # URL input
        url_layout = QHBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Enter YouTube URL")
        url_layout.addWidget(self.url_input)
        layout.addLayout(url_layout)
        
        # Create options group
        options_group = QGroupBox("Download Options")
        options_layout = QGridLayout()
        
        # Format selection
        self.format_combo = QComboBox()
        self.format_combo.addItems([
            'Video + Audio',
            'Video Only',
            'Audio Only',
        ])
        options_layout.addWidget(QLabel("Format:"), 0, 0)
        options_layout.addWidget(self.format_combo, 0, 1)
        
        # Resolution selection
        self.resolution_combo = QComboBox()
        self.resolution_combo.addItems([
            'best',
            '2160',  # 4K
            '1440',  # 2K
            '1080',  # Full HD
            '720',   # HD
            '480',   # SD
            '360',
            '240',
            '144'
        ])
        options_layout.addWidget(QLabel("Max Resolution:"), 1, 0)
        options_layout.addWidget(self.resolution_combo, 1, 1)
        
        # Video codec selection
        self.codec_combo = QComboBox()
        self.codec_combo.addItems([
            'any',
            'avc1',  # H.264
            'vp9',   # WebM
            'av01'   # AV1
        ])
        options_layout.addWidget(QLabel("Video Codec:"), 2, 0)
        options_layout.addWidget(self.codec_combo, 2, 1)
        
        # Audio quality selection
        self.audio_combo = QComboBox()
        self.audio_combo.addItems([
            'best',
            '192',  # High
            '128',  # Medium
            '96',   # Low
            '64'    # Very Low
        ])
        options_layout.addWidget(QLabel("Audio Quality (kbps):"), 3, 0)
        options_layout.addWidget(self.audio_combo, 3, 1)
        
        # Additional options
        self.subtitle_check = QCheckBox("Download Subtitles")
        options_layout.addWidget(self.subtitle_check, 0, 2)
        
        self.thumbnail_check = QCheckBox("Download Thumbnail")
        options_layout.addWidget(self.thumbnail_check, 1, 2)
        
        self.description_check = QCheckBox("Save Description")
        options_layout.addWidget(self.description_check, 2, 2)
        
        self.info_json_check = QCheckBox("Save Video Info")
        options_layout.addWidget(self.info_json_check, 3, 2)
        
        options_group.setLayout(options_layout)
        layout.addWidget(options_group)
        
        # Output directory selection
        dir_layout = QHBoxLayout()
        self.dir_input = QLineEdit()
        self.dir_input.setPlaceholderText("Output directory")
        self.dir_input.setText(os.path.expanduser("~\Downloads"))
        dir_layout.addWidget(self.dir_input)
        
        browse_btn = QPushButton("Browse")
        browse_btn.clicked.connect(self.browse_directory)
        dir_layout.addWidget(browse_btn)
        
        layout.addLayout(dir_layout)
        
        # Download button
        self.download_btn = QPushButton("Download")
        self.download_btn.clicked.connect(self.start_download)
        layout.addWidget(self.download_btn)
        
        # Progress bar
        self.progress = QProgressBar()
        layout.addWidget(self.progress)
        
        # Status label
        self.status_label = QLabel("")
        layout.addWidget(self.status_label)
        
        self.download_worker = None

    def browse_directory(self):
        dir_path = QFileDialog.getExistingDirectory(
            self, "Select Output Directory",
            self.dir_input.text(),
            QFileDialog.Option.ShowDirsOnly
        )
        if dir_path:
            self.dir_input.setText(dir_path)

    def get_format_string(self):
        format_choice = self.format_combo.currentText()
        if format_choice == 'Video + Audio':
            return 'bestvideo+bestaudio/best'
        elif format_choice == 'Video Only':
            return 'bestvideo'
        else:  # Audio Only
            return 'bestaudio'

    def start_download(self):
        url = self.url_input.text().strip()
        if not url:
            self.status_label.setText("Please enter a valid URL")
            return
            
        self.download_btn.setEnabled(False)
        self.progress.setValue(0)
        self.status_label.setText("Downloading...")
        
        options = {
            'format': self.get_format_string(),
            'resolution': self.resolution_combo.currentText(),
            'video_codec': self.codec_combo.currentText() if self.codec_combo.currentText() != 'any' else None,
            'audio_quality': self.audio_combo.currentText(),
            'subtitles': self.subtitle_check.isChecked(),
            'thumbnail': self.thumbnail_check.isChecked(),
            'description': self.description_check.isChecked(),
            'info_json': self.info_json_check.isChecked()
        }
        
        self.download_worker = DownloadWorker(
            url,
            self.dir_input.text(),
            options
        )
        self.download_worker.progress.connect(self.update_progress)
        self.download_worker.finished.connect(self.download_finished)
        self.download_worker.error.connect(self.download_error)
        self.download_worker.start()

    def update_progress(self, percentage):
        self.progress.setValue(int(percentage))

    def download_finished(self):
        self.status_label.setText("Download completed!")
        self.download_btn.setEnabled(True)
        self.progress.setValue(100)

    def download_error(self, error_message):
        self.status_label.setText(f"Error: {error_message}")
        self.download_btn.setEnabled(True)

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = YouTubeDownloader()
    window.show()
    sys.exit(app.exec())