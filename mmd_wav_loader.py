# -*- coding: utf-8 -*-
"""Minimal win32 automation to load a WAV file directly into a running
MikuMikuDance instance, via its own File > 読み込み > 音声ファイル(WAV)
menu command (menu id 206) -- same effect as a human doing it by hand,
just driven programmatically so ミクの音 can hand off an edited WAV
without the user having to go through MMD's dialogs themselves.

Trimmed down from pmm_fixer's mmd_dialogs.py (same author's other MMD
automation tool) to just the WAV-loading path, so this tool doesn't need
to depend on pmm_fixer's whole codebase for one function.
"""
import time

import win32con
import win32gui
import win32process

MENU_ID_LOAD_WAV = 206
WM_COMMAND = win32con.WM_COMMAND
WM_SETTEXT = win32con.WM_SETTEXT
EDIT_FILENAME_ID = 1148
OPEN_BUTTON_ID = 1


def find_mmd_window(timeout=3):
    deadline = time.time() + timeout
    while time.time() < deadline:
        found = []

        def cb(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if title.startswith("MikuMikuDance"):
                    found.append(hwnd)
            return True

        win32gui.EnumWindows(cb, None)
        if found:
            return found[0]
        time.sleep(0.3)
    return None


def _ensure_restored(mmd_hwnd):
    if win32gui.IsIconic(mmd_hwnd):
        win32gui.ShowWindow(mmd_hwnd, win32con.SW_RESTORE)
        time.sleep(0.3)


def _pid_of(hwnd):
    _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
    return pid


def _list_owned_dialogs(mmd_hwnd):
    target_pid = _pid_of(mmd_hwnd)
    dialogs = []

    def cb(hwnd, _):
        if hwnd == mmd_hwnd or not win32gui.IsWindowVisible(hwnd):
            return True
        if _pid_of(hwnd) == target_pid:
            dialogs.append(hwnd)
        return True

    win32gui.EnumWindows(cb, None)
    return dialogs


def _find_control(hwnd, control_id):
    found = []

    def cb(child, _):
        try:
            if win32gui.GetDlgCtrlID(child) == control_id:
                found.append(child)
                return False
        except Exception:
            pass
        return True

    try:
        win32gui.EnumChildWindows(hwnd, cb, None)
    except Exception:
        pass
    return found[0] if found else None


def _has_control(hwnd, control_id):
    return _find_control(hwnd, control_id) is not None


def _find_control_retry(hwnd, control_id, timeout=2.0):
    deadline = time.time() + timeout
    while True:
        ctrl = _find_control(hwnd, control_id)
        if ctrl is not None or time.time() >= deadline:
            return ctrl
        time.sleep(0.1)


def _wait_for_close(hwnd, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not win32gui.IsWindow(hwnd):
            return True
        time.sleep(0.1)
    return False


def _wait_for_dialog_with_control(mmd_hwnd, control_id, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        for hwnd in _list_owned_dialogs(mmd_hwnd):
            if _has_control(hwnd, control_id):
                return hwnd
        time.sleep(0.3)
    return None


def _wait_for_dialog(mmd_hwnd, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        dialogs = _list_owned_dialogs(mmd_hwnd)
        if dialogs:
            return dialogs[0]
        time.sleep(0.3)
    return None


def _get_dialog_text(hwnd):
    text = win32gui.GetWindowText(hwnd)
    static = ""

    def cb(child, _):
        nonlocal static
        try:
            if win32gui.GetClassName(child) == "Static":
                t = win32gui.GetWindowText(child)
                if t:
                    static += t + "\n"
        except Exception:
            pass
        return True

    try:
        win32gui.EnumChildWindows(hwnd, cb, None)
    except Exception:
        pass
    return text, static.strip()


def _fill_and_open_file(hwnd, path):
    import os
    edit = _find_control_retry(hwnd, EDIT_FILENAME_ID)
    win32gui.SendMessage(edit, WM_SETTEXT, 0, os.path.normpath(path))
    time.sleep(0.2)
    open_btn = _find_control_retry(hwnd, OPEN_BUTTON_ID)
    win32gui.PostMessage(hwnd, WM_COMMAND, (0 << 16) | OPEN_BUTTON_ID, open_btn)
    return _wait_for_close(hwnd)


def load_wav_into_mmd(path, log=print, step_timeout=15):
    """Find the running MMD window and load `path` as its WAV via the
    File > WAVE読込 menu command. Returns (ok, message)."""
    mmd_hwnd = find_mmd_window(timeout=2)
    if mmd_hwnd is None:
        return False, "MMDのウィンドウが見つかりません（MMDを起動してから試してください）"

    _ensure_restored(mmd_hwnd)
    win32gui.PostMessage(mmd_hwnd, WM_COMMAND, (0 << 16) | MENU_ID_LOAD_WAV, 0)
    time.sleep(0.5)

    dialog = _wait_for_dialog_with_control(mmd_hwnd, EDIT_FILENAME_ID, step_timeout)
    if dialog is None:
        return False, "MMDの「ファイルを開く」ダイアログが出ませんでした"

    _fill_and_open_file(dialog, path)

    extra = _wait_for_dialog(mmd_hwnd, 3)
    if extra is not None:
        title, static_text = _get_dialog_text(extra)
        return False, f"読み込み後に想定外のダイアログが出ました: {title!r} {static_text!r}"

    return True, "MMDにWAVを読み込みました"
