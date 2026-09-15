# -*- coding: utf-8 -*-
"""Lightweight WAV waveform peak extraction for display in a Tk Canvas.
Uses only the stdlib `wave`/`struct` modules (no numpy/matplotlib) to keep
the PyInstaller build small. Supports 8-bit and 16-bit PCM, which covers
ffmpeg's default WAV output and typical MMD background-audio files;
other sample widths (24/32-bit, float) are reported as unsupported rather
than misdecoded.
"""
import struct
import wave


class UnsupportedWavError(Exception):
    pass


def get_duration(path):
    """Cheap duration read (header only, no sample decoding) -- used to
    auto-revert a playback button's label once winsound's async playback
    naturally finishes (winsound has no completion callback of its own)."""
    with wave.open(path, "rb") as wf:
        framerate = wf.getframerate()
        n_frames = wf.getnframes()
    return (n_frames / framerate) if framerate else 0.0


def read_peaks(path, target_columns=330):
    """Returns (peaks, duration_sec, max_val). peaks is a list of
    (min, max) sample-value pairs, one per display column, already
    downmixed to mono."""
    with wave.open(path, "rb") as wf:
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)

    duration = (n_frames / framerate) if framerate else 0.0

    if sampwidth == 1:
        samples = [b - 128 for b in raw]
        max_val = 128
    elif sampwidth == 2:
        count = len(raw) // 2
        samples = list(struct.unpack("<%dh" % count, raw[: count * 2]))
        max_val = 32768
    else:
        raise UnsupportedWavError(f"{sampwidth * 8}bit PCMの波形表示には対応していません")

    if n_channels > 1 and samples:
        mono = []
        for i in range(0, len(samples) - n_channels + 1, n_channels):
            mono.append(sum(samples[i:i + n_channels]) // n_channels)
        samples = mono

    n = len(samples)
    if n == 0 or target_columns <= 0:
        return [], duration, max_val

    block = max(1, n // target_columns)
    peaks = []
    for i in range(0, n, block):
        chunk = samples[i:i + block]
        peaks.append((min(chunk), max(chunk)))
    return peaks, duration, max_val
