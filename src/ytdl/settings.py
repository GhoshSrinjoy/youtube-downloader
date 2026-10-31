"""Preferences of the desktop app, stored with QSettings (registry / plist / ini depending on the OS)."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass

from PyQt6.QtCore import QSettings, QStandardPaths

from . import core


@dataclass
class Prefs:
    """App behaviour that isn't about how a single item is downloaded."""

    theme: str = 'system'      # 'system', 'dark' or 'light'
    parallel: int = 3          # downloads running at the same time
    auto_start: bool = True    # start items as soon as their details are fetched
    check_updates: bool = True


def default_output_dir() -> str:
    path = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
    return path if path and os.path.isdir(path) else core.default_download_dir()


def _read(settings: QSettings, key: str) -> dict:
    try:
        return json.loads(settings.value(key, '{}') or '{}')
    except (TypeError, ValueError):
        return {}


def load() -> tuple[core.Options, Prefs]:
    settings = QSettings()
    options = core.Options.from_dict(_read(settings, 'options'))
    if not options.output_dir or not os.path.isdir(options.output_dir):
        options.output_dir = default_output_dir()
    return options, core.dataclass_from_dict(Prefs, _read(settings, 'prefs'))


def save(options: core.Options, prefs: Prefs):
    settings = QSettings()
    settings.setValue('options', json.dumps(options.to_dict()))
    settings.setValue('prefs', json.dumps(asdict(prefs)))
