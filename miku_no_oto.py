# -*- coding: utf-8 -*-
"""Small always-on-top slider that controls ONLY MikuMikuDance's per-app
audio volume via Windows' audio session API (pycaw) -- MMD itself has no
volume control, and other sounds (VOICEVOX playback, voice chat, etc.)
shouldn't be affected, so this is per-app rather than the system-wide
volume slider.

Usage: run (or the desktop shortcut). Drag the slider any time MMD is
running (it re-finds MMD's audio session each time you move it, so it's
fine to open this before or after MMD starts, and it survives MMD being
closed and reopened).
"""
import os
import sys
import tkinter as tk

from pycaw.pycaw import AudioUtilities


def _resource_path(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(__file__))
    return os.path.join(base, name)

TARGET_NAME_SUBSTRING = "mikumikudance"  # matches renamed exes like MikuMikuDance_v926x64.exe too


def find_mmd_volume_interface():
    for session in AudioUtilities.GetAllSessions():
        proc = session.Process
        if proc and TARGET_NAME_SUBSTRING in proc.name().lower():
            return session.SimpleAudioVolume
    return None


def main():
    root = tk.Tk()
    root.title("ミクの音")
    root.geometry("300x110")
    root.attributes("-topmost", True)
    try:
        root.iconbitmap(_resource_path("icon.ico"))
    except Exception:
        pass

    status = tk.Label(root, text="MMDの音声セッションを探しています...", font=("Meiryo", 9))
    status.pack(pady=(8, 0))

    def on_slide(value):
        vol_iface = find_mmd_volume_interface()
        if vol_iface is None:
            status.config(text="MMDの音声セッションが見つかりません（MMDで音を再生してから動かしてください）")
            return
        vol_iface.SetMasterVolume(int(value) / 100.0, None)
        status.config(text=f"MMDの音量: {value}%")

    slider = tk.Scale(
        root, from_=0, to=100, orient=tk.HORIZONTAL,
        length=260, command=on_slide,
    )
    slider.set(100)
    slider.pack(padx=20, pady=10)

    root.mainloop()


if __name__ == "__main__":
    main()
