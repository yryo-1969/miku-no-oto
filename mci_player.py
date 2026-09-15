# -*- coding: utf-8 -*-
"""Minimal MCI (winmm.dll) wrapper giving real play/pause/resume/stop
transport control over one WAV file at a time.

winsound (used elsewhere in this app for the quick one-shot "元を再生"
button) can only play-from-start or stop -- it has no concept of pausing
and resuming from the same position, which a real transport control needs.
MCI's "waveaudio" device type supports that natively, and mciSendStringW
is stdlib-reachable via ctypes, so this needs no extra dependency.
"""
import ctypes

_winmm = ctypes.windll.winmm
_ALIAS = "miku_no_oto_preview"


def _send(command):
    buf = ctypes.create_unicode_buffer(128)
    err = _winmm.mciSendStringW(command, buf, len(buf), 0)
    return err == 0, buf.value


def close():
    _send(f"close {_ALIAS}")


def load(path):
    """Closes whatever was previously loaded (a no-op if nothing was) and
    opens path fresh, stopped at position 0."""
    close()
    ok, _ = _send(f'open "{path}" type waveaudio alias {_ALIAS}')
    return ok


def play():
    ok, _ = _send(f"play {_ALIAS}")
    return ok


def pause():
    ok, _ = _send(f"pause {_ALIAS}")
    return ok


def resume():
    ok, _ = _send(f"resume {_ALIAS}")
    return ok


def stop():
    ok, _ = _send(f"stop {_ALIAS}")
    _send(f"seek {_ALIAS} to start")
    return ok


def seek_to_start():
    ok, _ = _send(f"seek {_ALIAS} to start")
    return ok


def seek_to_ms(ms):
    """Seeks to an absolute position (milliseconds -- waveaudio's default
    MCI time format, matching position_ms()). Note: MCI's "seek" command
    itself stops playback at the target position; call play()/resume()
    afterward to keep audio going from there."""
    ok, _ = _send(f"seek {_ALIAS} to {int(max(0, ms))}")
    return ok


def status():
    """Returns 'playing', 'paused', 'stopped', or '' if nothing is loaded."""
    ok, val = _send(f"status {_ALIAS} mode")
    return val if ok else ""


def position_ms():
    """Current playback position in milliseconds (waveaudio's default MCI
    time format), or None if nothing is loaded / the query failed."""
    ok, val = _send(f"status {_ALIAS} position")
    if not ok or not val:
        return None
    try:
        return int(val)
    except ValueError:
        return None
