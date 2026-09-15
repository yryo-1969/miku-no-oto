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
import tempfile
import time
import tkinter as tk
import winsound
from tkinter import filedialog, ttk

from pycaw.pycaw import AudioUtilities

import wav_editor
import mmd_wav_loader
import wav_waveform
import mci_player


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


def find_own_volume_interface():
    """Same pycaw technique as find_mmd_volume_interface() above, but for
    THIS process's own audio session -- controls 元を再生/プレビュー再生
    (both winsound and MCI open their wave-out handle inside this same
    process), independent of MMD's own volume slider. ocyacya asked for
    this, 2026-09-15: 「ミクの音自体の音量調整が無いですね」. Like MMD's
    session, Windows only creates this process's audio session lazily,
    after it has actually played something at least once."""
    my_pid = os.getpid()
    for session in AudioUtilities.GetAllSessions():
        proc = session.Process
        if proc and proc.pid == my_pid:
            return session.SimpleAudioVolume
    return None


def main():
    root = tk.Tk()
    root.title("ミクの音")
    root.geometry("400x1180")
    # 横方向だけリサイズ可 -- 波形欄が横幅に追従して広がる(下の<Configure>
    # ハンドラ参照, ocyacya 要望 2026-09-15:「横に広げたら同じように広がっ
    # たら見やすいかな」)。縦方向は各セクションが固定の縦積みレイアウトで
    # スクロールも無いため、縦だけ伸ばすと単に下に空白が増えるだけになる。
    root.resizable(True, False)
    root.minsize(380, 1)
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

    # MMD音量スライダーは(上のon_slide参照)MMD自身の音声セッションだけを
    # 動かすもので、元を再生/プレビュー再生(このツール自体がwinsound/
    # MCIで鳴らす音)は別のプロセス(このツール自身)の音声セッションなので、
    # このスライダーでは一切変わらない。ここにも独立した音量スライダーが
    # ない、とocyacyaに指摘されたので(2026-09-15:「ミクの音自体の音量
    # 調整が無いですね」)、同じpycawの仕組みをこのプロセス自身(PID)に
    # 向けて追加した。最初は再生ボタンのすぐ下に置いたが、「WAV編集」欄の
    # 「音量(%)」(保存されるWAVファイル自体の音量)と混同しそうとの指摘で
    # (2026-09-15:「下にあるとWAVファイルのボリュウム弄れると勘違いしそう
    # なので」)、紛らわしくないよう上のMMD音量スライダーのすぐ下に移動。
    def on_preview_volume_slide(value):
        vol_iface = find_own_volume_interface()
        if vol_iface is None:
            preview_volume_label.config(text="プレビュー音量: (まだ未再生。一度再生すると反映されます)")
            return
        vol_iface.SetMasterVolume(int(value) / 100.0, None)
        preview_volume_label.config(text=f"プレビュー音量: {value}%")

    preview_volume_label = tk.Label(root, text="プレビュー音量: 100%", font=("Meiryo", 8), fg="#555555")
    preview_volume_label.pack()
    preview_volume_slider = tk.Scale(
        root, from_=0, to=100, orient=tk.HORIZONTAL,
        showvalue=False, length=200, command=on_preview_volume_slide,
    )
    preview_volume_slider.set(100)
    preview_volume_slider.pack(pady=(0, 8))

    tk.Frame(root, height=2, bd=1, relief=tk.SUNKEN).pack(fill=tk.X, padx=10, pady=8)

    tk.Label(root, text="WAV編集してMMDに読み込む", font=("Meiryo", 10, "bold")).pack(pady=(0, 4))

    selected_path = {"path": None}

    # 編集対象: which of the two files (main / the WAV picked in "WAVを追加")
    # the start/end/音量/カット/無音挿入 controls below currently apply to.
    # サブ starts disabled since there's no second file yet -- enabled once
    # one is picked in the WAVを追加 section further down (ocyacya asked
    # for this, 2026-09-15: being able to edit either track, not just
    # offset the sub one when mixing).
    edit_target_var = tk.StringVar(value="main")
    # 最初どこにあるか分からなかった、と言われたので(ocyacya, 2026-09-15)、
    # 枠線+背景色+大きめフォントで目立たせている。地味な1行ラベルのままだと
    # 他の設定行に埋もれて見つけにくかった。
    target_outer = tk.Frame(root, bd=2, relief=tk.GROOVE, bg="#fff3cd")
    target_outer.pack(padx=10, pady=(2, 4), fill=tk.X)
    target_row = tk.Frame(target_outer, bg="#fff3cd")
    target_row.pack(padx=8, pady=6, fill=tk.X)
    tk.Label(target_row, text="編集対象:", font=("Meiryo", 11, "bold"), bg="#fff3cd", anchor="w").pack(side=tk.LEFT)
    tk.Radiobutton(target_row, text="メイン", variable=edit_target_var, value="main", font=("Meiryo", 11), bg="#fff3cd", activebackground="#fff3cd").pack(side=tk.LEFT, padx=(6, 0))
    target_sub_radio = tk.Radiobutton(target_row, text="サブ(追加WAV)", variable=edit_target_var, value="sub", font=("Meiryo", 11), bg="#fff3cd", activebackground="#fff3cd", state=tk.DISABLED)
    target_sub_radio.pack(side=tk.LEFT)

    file_row = tk.Frame(root)
    file_row.pack(padx=10, pady=2, fill=tk.X)
    file_label = tk.Label(file_row, text="(ファイル未選択)", font=("Meiryo", 8), anchor="w")
    file_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

    WAVE_W, WAVE_H = 330, 70
    SUB_WAVE_H = 40  # the sub waveform is a secondary reference, so a bit shorter

    def canvas_width(canvas):
        """The canvas's actual on-screen width, for waveform/marker/playhead
        x-math -- falls back to WAVE_W before the widget is first laid out
        (winfo_width() returns 1 for an unrealized widget)."""
        w = canvas.winfo_width()
        return w if w > 1 else WAVE_W
    # 「実行」を押すと、上のメイン波形の枠に(サブも含めた)結合後の
    # 最終結果が緑色で表示される -- が、これがずっと「メインの波形」だった
    # 枠なので、編集対象をサブにしてから実行すると、ぱっと見「メインが
    # 編集された」ように見えてしまう(ocyacya がスクリーンショット付きで
    # 報告、2026-09-15:「サブに切り替えたけどメインが編集されたよ」。
    # 実際の処理は数値検証済みで正しかった -- メイン区間は無音化されず、
    # 挿入した無音はちょうどメイン/サブの境目に現れていた)。紛らわしさを
    # 減らすため、結果表示中だけこのラベルを出す。
    result_label = tk.Label(root, text="", font=("Meiryo", 8, "bold"), fg="#2ecc71")
    result_label.pack(padx=10, anchor="w")
    # width=WAVE_W is just the INITIAL size -- fill=tk.X makes the canvas
    # itself track the window's actual width when the user drags it wider
    # (ocyacya asked for this, 2026-09-15: 「横に広げたら同じように広がったら
    # 見やすいかな」). The waveform DRAWING also has to follow along --
    # render_peaks()/refresh_markers() below read the canvas's live
    # winfo_width() at draw time instead of the fixed WAVE_W constant, and
    # <Configure> is bound further down to redraw when the window resizes.
    wave_canvas = tk.Canvas(root, width=WAVE_W, height=WAVE_H, bg="#1e1e1e", highlightthickness=1, highlightbackground="#888")
    wave_canvas.pack(padx=10, pady=(2, 4), fill=tk.X)

    # サブの波形+マーカーは、タブ分け(2026-09-15)より前は「WAVを追加」の
    # 節に常時表示されていたので、編集対象をサブに切り替えてカット/
    # 無音挿入タブで数値を打っても、その場でマーカーが見えていた。タブに
    # 分けたことで sub_wave_canvas が「WAV追加/ミックス」タブの中に
    # 隠れてしまい、他のタブを見ながらサブを編集するとマーカーどころか
    # 波形そのものが見えなくなっていた(ocyacya がスクリーンショット付きで
    # 報告、2026-09-15:「サブ編集の時、波形表示が見えなくなるね」)。
    # タブをまたいで常に見える場所(メイン波形のすぐ下)に置き直して解決。
    sub_wave_label = tk.Label(root, text="追加WAVの波形:", font=("Meiryo", 8), fg="gray", anchor="w")
    sub_wave_label.pack(padx=10, anchor="w")
    sub_wave_canvas = tk.Canvas(root, width=WAVE_W, height=SUB_WAVE_H, bg="#1e1e1e", highlightthickness=1, highlightbackground="#888")
    sub_wave_canvas.pack(padx=10, pady=(0, 4), fill=tk.X)

    # main/sub each get their own peaks/duration/max_val -- see
    # active_wave_state() below, which picks the one matching 編集対象.
    wave_states = {
        "main": {"peaks": None, "duration": 0.0, "max_val": 32768},
        "sub": {"peaks": None, "duration": 0.0, "max_val": 32768},
    }

    def active_wave_state():
        return wave_states[edit_target_var.get()]

    # 「▶ プレビュー再生」(編集結果)と紛らわしい、とocyacyaに指摘されたので
    # (2026-09-15:「メイン音源を再生にしましょうか、プレビュー再生と間違える
    # 人いてそうなので」)、常にメインファイルの元の音を再生するボタンだと
    # わかる名前に変更。
    PLAY_ORIG_LABEL = "▶ メイン音源を再生"
    play_state = {"path": None, "after_id": None, "button": None, "label": None}
    last_edit_path = {"path": None}
    preview_state = {"temp_path": None}
    mci_state = {"path": None, "duration": None}
    playhead_state = {"after_id": None}

    def clear_playhead():
        if playhead_state["after_id"] is not None:
            try:
                root.after_cancel(playhead_state["after_id"])
            except Exception:
                pass
            playhead_state["after_id"] = None
        wave_canvas.delete("playhead")
        clear_time_label()

    def draw_playhead(fraction):
        w = canvas_width(wave_canvas)
        x = min(w, max(0, fraction * w))
        wave_canvas.delete("playhead")
        wave_canvas.create_line(x, 0, x, WAVE_H, fill="white", width=1, tags="playhead")

    def format_time_value(sec):
        # Mirrors the rest of the panel's 単位 setting (秒/フレーム) --
        # ocyacya specifically wanted frame numbers here, not just seconds,
        # since MMD's own timeline is frame-based (2026-09-15).
        if unit_var.get() == "フレーム":
            try:
                fps_val = float(fps_entry.get().strip() or 30)
            except ValueError:
                fps_val = 30.0
            if fps_val <= 0:
                fps_val = 30.0
            return f"{sec * fps_val:.0f}"
        return f"{sec:.1f}"

    def update_time_label(cur_sec, total_sec):
        unit_suffix = "フレーム" if unit_var.get() == "フレーム" else "秒"
        cur_sec = max(0.0, min(cur_sec, total_sec))
        time_label.config(text=f"再生位置: {format_time_value(cur_sec)} / {format_time_value(total_sec)} {unit_suffix}")

    def clear_time_label():
        time_label.config(text="")

    def poll_winsound_playhead():
        # winsound has no position query, so this is a real-time-elapsed
        # estimate -- accurate here since winsound playback (元を再生) is
        # strictly play-from-start-or-stop, never paused/seeked.
        if play_state["path"] is None:
            clear_playhead()
            return
        duration = play_state.get("duration")
        if duration:
            elapsed = time.time() - play_state["start_time"]
            draw_playhead(min(1.0, elapsed / duration))
            update_time_label(elapsed, duration)
        playhead_state["after_id"] = root.after(100, poll_winsound_playhead)

    def poll_mci_playhead():
        if mci_state["path"] is None:
            clear_playhead()
            return
        st = mci_player.status()
        if st not in ("playing", "paused"):
            clear_playhead()
            return
        duration = mci_state["duration"]
        pos_ms = mci_player.position_ms()
        if duration and pos_ms is not None:
            draw_playhead(min(1.0, (pos_ms / 1000.0) / duration))
            update_time_label(pos_ms / 1000.0, duration)
        playhead_state["after_id"] = root.after(150, poll_mci_playhead)

    def cleanup_preview_temp():
        old = preview_state["temp_path"]
        if old and os.path.isfile(old) and old != play_state["path"] and old != mci_state["path"]:
            try:
                os.remove(old)
            except Exception:
                pass  # MMD may still have it open, or it's already gone -- not worth bothering the user about
        preview_state["temp_path"] = None

    def stop_playback():
        """Stops the simple winsound-based playback (元を再生 only)."""
        if play_state["after_id"] is not None:
            try:
                root.after_cancel(play_state["after_id"])
            except Exception:
                pass
            play_state["after_id"] = None
        try:
            winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception:
            pass
        if play_state["button"] is not None:
            play_state["button"].config(text=play_state["label"])
        play_state["path"] = None
        play_state["button"] = None
        play_state["label"] = None
        clear_playhead()

    def start_playback(path, button, label):
        stop_playback()
        try:
            duration = wav_waveform.get_duration(path)
        except Exception:
            duration = None
        try:
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception as e:
            edit_status.config(text=f"再生に失敗しました: {e}", fg="red")
            return
        button.config(text="■ 停止")
        play_state["path"] = path
        play_state["button"] = button
        play_state["label"] = label
        play_state["duration"] = duration
        play_state["start_time"] = time.time()
        if duration:
            play_state["after_id"] = root.after(int(duration * 1000) + 300, stop_playback)
        draw_waveform()  # this plays the ORIGINAL file, so make sure that's what's shown
        poll_winsound_playhead()

    def toggle_play(get_path, button, label):
        path = get_path()
        if not path or not os.path.isfile(path):
            edit_status.config(text="再生するファイルがありません。", fg="red")
            return
        if play_state["path"] == path:
            stop_playback()
            return
        start_playback(path, button, label)

    play_row = tk.Frame(root)
    play_row.pack(padx=10, pady=(0, 4), fill=tk.X)
    play_orig_btn = tk.Button(play_row, text=PLAY_ORIG_LABEL, width=17)
    play_orig_btn.config(command=lambda: toggle_play(lambda: selected_path["path"], play_orig_btn, PLAY_ORIG_LABEL))
    play_orig_btn.pack(side=tk.LEFT)

    def preview_transport_play():
        path = last_edit_path["path"]
        if not path or not os.path.isfile(path):
            edit_status.config(text="再生する編集結果がありません。先に「実行」をしてください。", fg="red")
            return
        already_loaded = mci_state["path"] == path
        current_status = mci_player.status() if already_loaded else ""
        was_paused = current_status == "paused"
        if not already_loaded:
            if not mci_player.load(path):
                edit_status.config(text="再生の準備に失敗しました。", fg="red")
                return
            mci_state["path"] = path
            try:
                mci_state["duration"] = wav_waveform.get_duration(path)
            except Exception:
                mci_state["duration"] = None
        elif current_status == "stopped":
            # MCIの"stopped"は「最後まで自然に再生し終わった」場合と、
            # 「早戻しなどでseekした直後」の場合とで同じ文字列になり、
            # 区別がつかない(pause中でもseekすると"paused"ではなく
            # "stopped"になる -- 早戻し実装時に確認済み)。位置が末尾
            # 付近(自然終了)の時だけ先頭へ巻き戻す。そうでなければ
            # (早戻しで手前に戻した直後など)その位置からそのまま続きを
            # 再生する -- 最初、末尾かどうかを見ずに毎回先頭へ戻して
            # いたため、ocyacyaが早戻しで戻した位置がプレビュー再生の
            # たびに毎回先頭に巻き戻されてしまっていた(2026-09-15:
            # 「巻き戻したいちから再生出来たらな」「プレビュー再生押すと
            # 最初からの再生になりますからね」)。
            pos_ms = mci_player.position_ms()
            duration_ms = (mci_state["duration"] or 0) * 1000
            if pos_ms is not None and duration_ms and pos_ms >= duration_ms - 300:
                mci_player.seek_to_start()
        if was_paused:
            mci_player.resume()
        else:
            mci_player.play()
        render_result_waveform(path)  # show what's actually playing, not whatever the canvas happened to have
        edit_status.config(text="再生中...", fg="blue")
        clear_playhead()
        poll_mci_playhead()

    def preview_transport_pause():
        if mci_player.status() == "playing":
            mci_player.pause()
            edit_status.config(text="一時停止しました。", fg="blue")

    def preview_transport_stop():
        mci_player.stop()
        clear_playhead()
        edit_status.config(text="停止しました。", fg="blue")

    # 早戻し: 押している間、数百ms おきに少しずつ手前へシークし続ける
    # (ocyacya 要望、2026-09-15:「再生中巻き戻しボタン」)。MCIのwaveaudio
    # デバイスは本当の逆再生(音そのものを逆に鳴らす)には対応していない
    # ため、押している間は一旦一時停止して位置だけどんどん戻し、離したら
    # (元が再生中だった場合)そこから普通に再生を再開する、という擬似的な
    # 早戻しにしている。
    REWIND_STEP_MS = 400
    REWIND_INTERVAL_MS = 120
    rewind_state = {"active": False, "after_id": None, "was_playing": False}

    def rewind_step():
        if not rewind_state["active"] or mci_state["path"] is None:
            rewind_state["active"] = False
            return
        pos_ms = mci_player.position_ms()
        if pos_ms is None:
            rewind_state["active"] = False
            return
        new_pos_ms = max(0, pos_ms - REWIND_STEP_MS)
        mci_player.seek_to_ms(new_pos_ms)
        if mci_state["duration"]:
            draw_playhead(min(1.0, (new_pos_ms / 1000.0) / mci_state["duration"]))
            update_time_label(new_pos_ms / 1000.0, mci_state["duration"])
        if new_pos_ms <= 0:
            rewind_state["active"] = False
            return
        rewind_state["after_id"] = root.after(REWIND_INTERVAL_MS, rewind_step)

    def start_rewind(_event=None):
        if mci_state["path"] is None:
            return
        rewind_state["was_playing"] = mci_player.status() == "playing"
        if rewind_state["was_playing"]:
            mci_player.pause()
        rewind_state["active"] = True
        rewind_step()

    def stop_rewind(_event=None):
        rewind_state["active"] = False
        if rewind_state["after_id"] is not None:
            root.after_cancel(rewind_state["after_id"])
            rewind_state["after_id"] = None
        if rewind_state["was_playing"]:
            # MCIのseekは(pause中に呼んでも)デバイスを"paused"ではなく
            # "stopped"にしてしまうため、resume()では何も起きない(確認済み
            # -- resumeはFalseを返し、位置も動かない)。play()なら現在位置
            # (早戻しで動かした先)からそのまま続きが再生される。
            mci_player.play()
            edit_status.config(text="再生中...", fg="blue")
            poll_mci_playhead()

    transport_row = tk.Frame(root)
    transport_row.pack(padx=10, pady=(0, 4), fill=tk.X)
    preview_play_btn = tk.Button(transport_row, text="▶ プレビュー再生", command=preview_transport_play, width=13, state=tk.DISABLED)
    preview_play_btn.pack(side=tk.LEFT, padx=(0, 4))
    preview_pause_btn = tk.Button(transport_row, text="一時停止", command=preview_transport_pause, width=8, state=tk.DISABLED)
    preview_pause_btn.pack(side=tk.LEFT, padx=(0, 4))
    preview_stop_btn = tk.Button(transport_row, text="■ 停止", command=preview_transport_stop, width=8, state=tk.DISABLED)
    preview_stop_btn.pack(side=tk.LEFT, padx=(0, 4))
    rewind_btn = tk.Button(transport_row, text="⏪ 早戻し", width=9)
    rewind_btn.bind("<ButtonPress-1>", start_rewind)
    rewind_btn.bind("<ButtonRelease-1>", stop_rewind)
    rewind_btn.pack(side=tk.LEFT)
    tk.Label(root, text="※「⏪ 早戻し」は押している間、少しずつ手前に戻ります(本物の逆再生の音にはなりません)。離すと元の状態(再生中/一時停止)に戻ります", font=("Meiryo", 8), fg="gray", wraplength=340, justify=tk.LEFT).pack(padx=10, anchor="w")

    time_label = tk.Label(root, text="", font=("Meiryo", 8), fg="#555555")
    time_label.pack(padx=10, anchor="w")

    def render_peaks(canvas, height, peaks, max_val, color="#5dade2"):
        canvas.delete("wave")
        if not peaks:
            return
        max_val = max_val or 1
        mid = height / 2
        n = len(peaks)
        width = canvas_width(canvas)
        for i, (mn, mx) in enumerate(peaks):
            x = i * width / n
            y1 = mid - (mx / max_val) * mid
            y2 = mid - (mn / max_val) * mid
            canvas.create_line(x, y1, x, max(y2, y1 + 1), fill=color, tags="wave")

    def draw_waveform():
        # Always redraws the MAIN canvas from wave_states["main"] (the main
        # file's own waveform), never from a result preview --
        # render_result_waveform() below draws a result's waveform directly
        # without touching wave_states, so calling this always gets you
        # back to the source view (used by refresh_markers so any settings
        # edit after an "実行" reverts the canvas back to the
        # source+markers view automatically).
        render_peaks(wave_canvas, WAVE_H, wave_states["main"]["peaks"], wave_states["main"]["max_val"])
        result_label.config(text="")

    def draw_sub_waveform():
        render_peaks(sub_wave_canvas, SUB_WAVE_H, wave_states["sub"]["peaks"], wave_states["sub"]["max_val"], color="#e67e22")
        sub_wave_label.config(text="追加WAVの波形:", fg="gray")

    def render_result_waveform(path):
        """Show path's own waveform (a processed/edited FINAL MIXED result,
        always on the main/top canvas regardless of 編集対象 -- there's
        only ever one combined output) without touching wave_states, so
        durations stay correct for marker math -- see draw_waveform's
        comment."""
        try:
            peaks, _duration, max_val = wav_waveform.read_peaks(path, target_columns=WAVE_W)
        except Exception:
            return False
        render_peaks(wave_canvas, WAVE_H, peaks, max_val, color="#2ecc71")
        wave_canvas.delete("marker")
        result_label.config(text="▼ 実行結果(メイン+サブ結合後の全体)")
        return True

    def load_waveform(path, target="main"):
        canvas = wave_canvas if target == "main" else sub_wave_canvas
        canvas.delete("wave")
        canvas.delete("marker")
        st = wave_states[target]
        try:
            peaks, duration, max_val = wav_waveform.read_peaks(path, target_columns=WAVE_W)
        except wav_waveform.UnsupportedWavError as e:
            st["peaks"] = None
            st["duration"] = 0.0
            edit_status.config(text=str(e), fg="red")
            return
        except Exception as e:
            st["peaks"] = None
            st["duration"] = 0.0
            edit_status.config(text=f"波形の読み込みに失敗しました: {e}", fg="red")
            return
        st["peaks"] = peaks
        st["duration"] = duration
        st["max_val"] = max_val
        # Show the file's actual length right next to its name -- without
        # this, a cut/trim value beyond the file's real duration silently
        # gets flagged invalid with no easy way to tell why (ocyacya hit
        # this live, 2026-09-15: entered 開始=60/終了=70, correctly ordered
        # but the file itself was only 40秒, and there was nowhere on
        # screen to see that at a glance).
        if target == "main":
            file_label.config(text=f"{os.path.basename(path)}  (長さ: {duration:.1f}秒)")
            draw_waveform()
        else:
            append_label.config(text=f"{os.path.basename(path)}  (長さ: {duration:.1f}秒)")
            draw_sub_waveform()
            target_sub_radio.config(state=tk.NORMAL)
        if target == edit_target_var.get():
            # only re-validate/re-mark the canvas that's actually being
            # edited right now -- reloading the INACTIVE file (e.g.
            # re-picking the sub WAV while still editing メイン) shouldn't
            # touch the currently-shown markers/placeholder at all.
            refresh_end_placeholder()
            refresh_markers()

    def select_file(path):
        stop_playback()
        mci_player.stop()
        mci_player.close()
        mci_state["path"] = None
        clear_playhead()
        cleanup_preview_temp()
        last_edit_path["path"] = None
        preview_play_btn.config(state=tk.DISABLED)
        preview_pause_btn.config(state=tk.DISABLED)
        preview_stop_btn.config(state=tk.DISABLED)
        clear_append_file()
        selected_path["path"] = path
        file_label.config(text=os.path.basename(path))
        load_waveform(path)

    def pick_file():
        path = filedialog.askopenfilename(
            title="編集するWAVファイルを選択",
            filetypes=[("WAVファイル", "*.wav"), ("すべてのファイル", "*.*")],
        )
        if path:
            select_file(path)

    tk.Button(file_row, text="選択...", command=pick_file, width=8).pack(side=tk.RIGHT)

    def labeled_entry(parent, label_text, default):
        row = tk.Frame(parent)
        row.pack(padx=10, pady=2, fill=tk.X)
        tk.Label(row, text=label_text, font=("Meiryo", 9), width=12, anchor="w").pack(side=tk.LEFT)
        entry = tk.Entry(row, width=10)
        entry.insert(0, default)
        entry.pack(side=tk.LEFT)
        return entry

    unit_row = tk.Frame(root)
    unit_row.pack(padx=10, pady=(2, 0), fill=tk.X)
    tk.Label(unit_row, text="単位:", font=("Meiryo", 9), width=12, anchor="w").pack(side=tk.LEFT)
    unit_var = tk.StringVar(value="秒")
    tk.Radiobutton(unit_row, text="秒", variable=unit_var, value="秒", font=("Meiryo", 9)).pack(side=tk.LEFT)
    tk.Radiobutton(unit_row, text="フレーム", variable=unit_var, value="フレーム", font=("Meiryo", 9)).pack(side=tk.LEFT)
    tk.Label(unit_row, text="fps:", font=("Meiryo", 9)).pack(side=tk.LEFT, padx=(8, 2))
    fps_entry = tk.Entry(unit_row, width=4)
    fps_entry.insert(0, "30")
    fps_entry.pack(side=tk.LEFT)

    # 機能が増えて縦にごちゃごちゃになってきた分をタブで整理(ocyacya,
    # 2026-09-15)。単位/fps・波形・再生系・編集対象は全タブ共通なので
    # タブの外(上)に置いたまま、開始/終了/音量、カット/無音挿入、
    # WAV追加/ミックスの3つだけをタブで分ける。
    #
    # Windows既定の ttk テーマ("vista")はネイティブ描画のため、
    # Notebook.Tab の背景色を style.map で指定しても無視されてしまい、
    # 選択中タブと非選択タブがほぼ同じ見た目になる(ocyacya が
    # スクリーンショット付きで報告、2026-09-15:「選択タブがわかりずらい」)。
    # "clam" テーマはPython側で描画するため指定した色がちゃんと反映される。
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("TNotebook.Tab", font=("Meiryo", 9), padding=(10, 6))
    style.map(
        "TNotebook.Tab",
        background=[("selected", "#3a7bd5"), ("!selected", "#d9d9d9")],
        foreground=[("selected", "white"), ("!selected", "#333333")],
        font=[("selected", ("Meiryo", 9, "bold"))],
    )
    notebook = ttk.Notebook(root)
    notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=(6, 0))
    tab_trim = tk.Frame(notebook)
    tab_cut = tk.Frame(notebook)
    tab_append = tk.Frame(notebook)
    notebook.add(tab_trim, text="トリム/音量")
    notebook.add(tab_cut, text="カット/無音挿入")
    notebook.add(tab_append, text="WAV追加/ミックス")

    start_entry = labeled_entry(tab_trim, "開始:", "0")
    end_entry = labeled_entry(tab_trim, "終了:", "")
    volume_entry = labeled_entry(tab_trim, "音量(%):", "100")
    # 無音追加(先頭に無音を足すだけの専用欄)は削除した -- 下の「無音を挿入」の
    # 位置=0の行が完全に同じことをできる上、任意の位置にも挿入できて上位互換
    # なので、別々の欄として持つ意味がなくなった(ocyacya, 2026-09-15)。
    tk.Label(tab_trim, text="※終了は空欄で最後まで再生。開始/終了は単位に従います", font=("Meiryo", 8), fg="gray", wraplength=340, justify=tk.LEFT).pack(padx=10, anchor="w")

    # 終了 stays functionally blank ("play to the end") until the user
    # actually types something -- but showing the file's own length there
    # in gray, as a pure visual reference, saves a trip up to the filename
    # label to see it (ocyacya asked for exactly this, 2026-09-15). Not a
    # real value: parse_float below treats a placeholder-showing entry as
    # blank regardless of its displayed text.
    end_entry._placeholder_active = False

    def end_placeholder_text():
        duration = active_wave_state()["duration"]
        if not duration:
            return ""
        if unit_var.get() == "フレーム":
            try:
                fps_val = float(fps_entry.get().strip() or 30)
            except ValueError:
                fps_val = 30.0
            if fps_val <= 0:
                fps_val = 30.0
            return f"{duration * fps_val:.0f}"
        return f"{duration:.1f}"

    def show_end_placeholder():
        text = end_placeholder_text()
        if not text or end_entry.get().strip():
            return
        end_entry.insert(0, text)
        end_entry.config(fg="gray")
        end_entry._placeholder_active = True

    def clear_end_placeholder():
        if end_entry._placeholder_active:
            end_entry.delete(0, tk.END)
            end_entry.config(fg="black")
            end_entry._placeholder_active = False

    def refresh_end_placeholder():
        clear_end_placeholder()
        if root.focus_get() is not end_entry:
            show_end_placeholder()

    end_entry.bind("<FocusIn>", lambda _e: clear_end_placeholder())
    end_entry.bind("<FocusOut>", lambda _e: show_end_placeholder())

    tk.Label(tab_cut, text="カット(中抜き)区間", font=("Meiryo", 9, "bold")).pack(padx=10, pady=(6, 0), anchor="w")

    cuts_container = tk.Frame(tab_cut)
    cuts_container.pack(fill=tk.X)
    cut_rows = []

    def add_cut_row(default_start="", default_end=""):
        row_info = {}
        row = tk.Frame(cuts_container)
        row.pack(padx=10, pady=1, fill=tk.X)
        row_info["frame"] = row
        tk.Label(row, text="開始:", font=("Meiryo", 9), width=5, anchor="w").pack(side=tk.LEFT)
        s_entry = tk.Entry(row, width=7)
        s_entry.insert(0, default_start)
        s_entry.pack(side=tk.LEFT, padx=(0, 8))
        row_info["start_entry"] = s_entry
        tk.Label(row, text="終了:", font=("Meiryo", 9), width=5, anchor="w").pack(side=tk.LEFT)
        e_entry = tk.Entry(row, width=7)
        e_entry.insert(0, default_end)
        e_entry.pack(side=tk.LEFT, padx=(0, 8))
        row_info["end_entry"] = e_entry
        remove_btn = tk.Button(row, text="－", width=2, command=lambda: remove_cut_row(row_info))
        remove_btn.pack(side=tk.LEFT)
        s_entry.bind("<KeyRelease>", refresh_markers)
        e_entry.bind("<KeyRelease>", refresh_markers)
        cut_rows.append(row_info)
        return row_info

    def remove_cut_row(row_info):
        row_info["frame"].destroy()
        cut_rows.remove(row_info)
        refresh_markers()

    tk.Button(tab_cut, text="＋ カットを追加", command=lambda: add_cut_row(), width=14).pack(pady=(2, 0))
    tk.Label(tab_cut, text="※各行の区間を削除して前後をつなげます(開始・終了の両方が入った行のみ有効)。複数行を追加すれば何か所でもカットできます。開始/終了トリムと重ねて使えます(カット後の音声に対して開始/終了が適用されます)", font=("Meiryo", 8), fg="gray", wraplength=340, justify=tk.LEFT).pack(padx=10, pady=(2, 0), anchor="w")

    tk.Frame(tab_cut, height=1, bd=1, relief=tk.SUNKEN).pack(fill=tk.X, padx=10, pady=4)
    tk.Label(tab_cut, text="無音を挿入", font=("Meiryo", 9, "bold")).pack(padx=10, anchor="w")

    inserts_container = tk.Frame(tab_cut)
    inserts_container.pack(fill=tk.X)
    insert_rows = []

    def add_insert_row(default_pos="", default_dur=""):
        row_info = {}
        row = tk.Frame(inserts_container)
        row.pack(padx=10, pady=1, fill=tk.X)
        row_info["frame"] = row
        tk.Label(row, text="位置:", font=("Meiryo", 9), width=5, anchor="w").pack(side=tk.LEFT)
        p_entry = tk.Entry(row, width=7)
        p_entry.insert(0, default_pos)
        p_entry.pack(side=tk.LEFT, padx=(0, 8))
        row_info["pos_entry"] = p_entry
        tk.Label(row, text="長さ:", font=("Meiryo", 9), width=5, anchor="w").pack(side=tk.LEFT)
        d_entry = tk.Entry(row, width=7)
        d_entry.insert(0, default_dur)
        d_entry.pack(side=tk.LEFT, padx=(0, 8))
        row_info["dur_entry"] = d_entry
        remove_btn = tk.Button(row, text="－", width=2, command=lambda: remove_insert_row(row_info))
        remove_btn.pack(side=tk.LEFT)
        p_entry.bind("<KeyRelease>", refresh_markers)
        d_entry.bind("<KeyRelease>", refresh_markers)
        insert_rows.append(row_info)
        return row_info

    def remove_insert_row(row_info):
        row_info["frame"].destroy()
        insert_rows.remove(row_info)
        refresh_markers()

    tk.Button(tab_cut, text="＋ 無音挿入を追加", command=lambda: add_insert_row(), width=16).pack(pady=(2, 0))
    tk.Label(tab_cut, text="※指定した「位置」に、指定した「長さ」の無音を挿入します(カットの逆で、範囲を消すのではなく差し込みます。位置・長さの両方が入った行のみ有効)。カット/開始・終了トリムの後の音声に対して挿入されます", font=("Meiryo", 8), fg="gray", wraplength=340, justify=tk.LEFT).pack(padx=10, pady=(2, 0), anchor="w")

    tk.Label(tab_append, text="WAVを追加", font=("Meiryo", 9, "bold")).pack(padx=10, pady=(6, 0), anchor="w")

    append_state = {"path": None}
    append_row = tk.Frame(tab_append)
    append_row.pack(padx=10, pady=2, fill=tk.X)
    append_label = tk.Label(append_row, text="(追加なし)", font=("Meiryo", 8), anchor="w")
    append_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
    # サブの波形は「メインの波形」のすぐ下(タブの外、常時表示エリア)に
    # あるので、ここには波形は置かない -- see sub_wave_canvas above.

    def clear_append_file():
        append_state["path"] = None
        append_label.config(text="(追加なし)")
        wave_states["sub"]["peaks"] = None
        wave_states["sub"]["duration"] = 0.0
        sub_wave_canvas.delete("wave")
        sub_wave_canvas.delete("marker")
        target_sub_radio.config(state=tk.DISABLED)
        if edit_target_var.get() == "sub":
            edit_target_var.set("main")  # no sub file left to edit -- switch back (triggers on_target_change)

    def pick_append_file():
        path = filedialog.askopenfilename(
            title="追加するWAVファイルを選択",
            filetypes=[("WAVファイル", "*.wav"), ("すべてのファイル", "*.*")],
        )
        if path:
            append_state["path"] = path
            load_waveform(path, "sub")

    tk.Button(append_row, text="解除", command=clear_append_file, width=6).pack(side=tk.RIGHT)
    tk.Button(append_row, text="選択...", command=pick_append_file, width=8).pack(side=tk.RIGHT, padx=(0, 4))

    append_mode_var = tk.StringVar(value="append")
    append_mode_row = tk.Frame(tab_append)
    append_mode_row.pack(padx=10, pady=(0, 2), fill=tk.X)
    tk.Radiobutton(append_mode_row, text="末尾に結合", variable=append_mode_var, value="append", font=("Meiryo", 9)).pack(side=tk.LEFT)
    tk.Radiobutton(append_mode_row, text="重ねる(ミックス)", variable=append_mode_var, value="mix", font=("Meiryo", 9)).pack(side=tk.LEFT)

    append_offset_entry = labeled_entry(tab_append, "重ねる開始位置:", "0")
    tk.Label(tab_append, text="※「末尾に結合」は選んだWAVをそのまま後ろにつなげます。「重ねる(ミックス)」は同時に鳴らして二重演奏のように重ねます(上の「重ねる開始位置」で、追加WAV側の再生開始タイミングをずらせます。0なら両方同時スタート)。どちらもカット/トリム/無音挿入/音量を適用した後の音声が対象です", font=("Meiryo", 8), fg="gray", wraplength=340, justify=tk.LEFT).pack(padx=10, anchor="w")

    # 編集対象 switching keeps a separate "snapshot" of 開始/終了/音量/
    # カット行/無音挿入行 for whichever target ISN'T currently live in the
    # widgets, so flipping between メイン/サブ doesn't lose either side's
    # settings (ocyacya asked for exactly this, 2026-09-15).
    target_params = {"main": None, "sub": None}
    last_edit_target = {"value": "main"}

    def default_params():
        return {"start": "0", "end": "", "volume": "100", "cuts": [], "inserts": []}

    def snapshot_current_params():
        return {
            "start": start_entry.get(),
            "end": "" if getattr(end_entry, "_placeholder_active", False) else end_entry.get(),
            "volume": volume_entry.get(),
            "cuts": [(r["start_entry"].get(), r["end_entry"].get()) for r in cut_rows],
            "inserts": [(r["pos_entry"].get(), r["dur_entry"].get()) for r in insert_rows],
        }

    def restore_params(snap):
        start_entry.delete(0, tk.END)
        start_entry.insert(0, snap["start"])
        clear_end_placeholder()
        end_entry.delete(0, tk.END)
        if snap["end"]:
            end_entry.insert(0, snap["end"])
        volume_entry.delete(0, tk.END)
        volume_entry.insert(0, snap["volume"])
        for row_info in list(cut_rows):
            row_info["frame"].destroy()
        cut_rows.clear()
        if snap["cuts"]:
            for s, e in snap["cuts"]:
                add_cut_row(s, e)
        else:
            add_cut_row()  # カット(中抜き)区間 always shows at least one empty row by default
        for row_info in list(insert_rows):
            row_info["frame"].destroy()
        insert_rows.clear()
        for p, d in snap["inserts"]:
            add_insert_row(p, d)
        # 無音を挿入 has no default empty row (matches its original
        # "starts with zero rows" behavior), so nothing to add when empty.

    def get_params_for(target):
        if target == edit_target_var.get():
            return snapshot_current_params()
        return target_params.get(target) or default_params()

    def on_target_change(*_a):
        new_target = edit_target_var.get()
        old_target = last_edit_target["value"]
        if new_target == old_target:
            return
        if new_target == "sub" and not append_state["path"]:
            # shouldn't be reachable (the radio is disabled with no sub
            # file), but guard anyway rather than switch to an empty view
            edit_target_var.set("main")
            return
        target_params[old_target] = snapshot_current_params()
        restore_params(target_params[new_target] or default_params())
        last_edit_target["value"] = new_target
        refresh_end_placeholder()
        refresh_markers()
        edit_status.config(text=f"編集対象を「{'メイン' if new_target == 'main' else 'サブ(追加WAV)'}」に切り替えました。", fg="blue")

    edit_target_var.trace_add("write", on_target_change)

    def reset_settings():
        clear_append_file()  # also forces 編集対象 back to メイン if it was サブ (no sub file left to edit)
        target_params["main"] = None
        target_params["sub"] = None
        restore_params(default_params())  # resets whatever's live now -- guaranteed メイン after clear_append_file above
        append_mode_var.set("append")
        append_offset_entry.delete(0, tk.END)
        append_offset_entry.insert(0, "0")
        refresh_markers()  # also reverts the canvas to the source waveform if "実行" had swapped it
        refresh_end_placeholder()
        edit_status.config(text="設定を元に戻しました(選択中のファイルはそのままです)。", fg="blue")

    # 「実行の1つ前に戻す」: 「設定を元に戻す」(完全に初期状態へ)とは別に、
    # 直前に「実行」した時の設定にだけ戻せるボタン(ocyacya要望、
    # 2026-09-15:「編集の欄に実行の一個前に戻る」)。note_run_snapshot()が
    # 「実行」のたびに呼ばれ、それまでのlatestをprevに繰り上げてから
    # 今回の内容をlatestとして記録する1段階のundo(2回連続で押しても
    # それ以上は戻らない)。
    run_history = {"prev": None, "latest": None}

    def capture_full_snapshot():
        return {
            "main": get_params_for("main"),
            "sub": get_params_for("sub"),
            "append_mode": append_mode_var.get(),
            "append_offset": append_offset_entry.get(),
        }

    def note_run_snapshot():
        current = capture_full_snapshot()
        if run_history["latest"] is not None:
            run_history["prev"] = run_history["latest"]
            undo_run_btn.config(state=tk.NORMAL)
        run_history["latest"] = current

    def restore_full_snapshot(snap):
        target_params["main"] = snap["main"]
        target_params["sub"] = snap["sub"]
        restore_params(target_params[edit_target_var.get()] or default_params())
        append_mode_var.set(snap["append_mode"])
        append_offset_entry.delete(0, tk.END)
        append_offset_entry.insert(0, snap["append_offset"])
        refresh_end_placeholder()
        refresh_markers()

    def undo_to_previous_run():
        if run_history["prev"] is None:
            edit_status.config(text="戻れる前回実行時の設定がありません。", fg="red")
            return
        restore_full_snapshot(run_history["prev"])
        # 1段階だけのundoなので、戻した後はそこが新しいlatestになり、
        # それより前へはもう戻れない(ボタンを再度無効化)。
        run_history["latest"] = run_history["prev"]
        run_history["prev"] = None
        undo_run_btn.config(state=tk.DISABLED)
        edit_status.config(text="1つ前に実行した時の設定に戻しました。", fg="blue")

    reset_row = tk.Frame(root)
    reset_row.pack(pady=(4, 0))
    tk.Button(reset_row, text="設定を元に戻す", command=reset_settings, width=16).pack(side=tk.LEFT, padx=2)
    undo_run_btn = tk.Button(reset_row, text="実行の1つ前に戻す", command=undo_to_previous_run, width=18, state=tk.DISABLED)
    undo_run_btn.pack(side=tk.LEFT, padx=2)

    edit_status = tk.Label(root, text="", font=("Meiryo", 8), fg="blue", wraplength=330, justify=tk.LEFT)
    edit_status.pack(padx=10, pady=(4, 0), fill=tk.X)

    def parse_raw_float(text, allow_blank=False, default=0.0):
        text = (text or "").strip()
        if not text:
            if allow_blank:
                return None
            return default
        return float(text)

    def parse_float(entry, allow_blank=False, default=0.0):
        # A placeholder-showing entry (currently only 終了) displays gray
        # hint text but isn't a real value -- treat it as blank either way.
        text = "" if getattr(entry, "_placeholder_active", False) else entry.get()
        return parse_raw_float(text, allow_blank=allow_blank, default=default)

    def to_seconds(value, fps):
        if value is None:
            return None
        return value / fps if unit_var.get() == "フレーム" else value

    def parse_cut_pairs(pairs, fps, duration):
        """pairs: list of (start_text, end_text). Returns (ranges,
        invalid_row_numbers) -- ranges is the list of valid (start_sec,
        end_sec) pairs where both fields parse, end > start, AND start is
        within duration (a pair left entirely blank is just not a cut, not
        an error). invalid_row_numbers lists the 1-based indices where
        both fields ARE filled but don't form a usable range -- either
        end <= start, or start is at/past the end of the file (ocyacya hit
        both live, 2026-09-15: first 開始=50/終了=45 with no feedback that
        it was silently dropped, then fixed it to 開始=50/終了=70 on a
        file only 40s long -- still silently a no-op, since ffmpeg's atrim
        just clamps a past-the-end start to nothing, and the marker
        rectangle clamped to the canvas edge made it invisible instead of
        obviously wrong)."""
        ranges = []
        invalid_row_numbers = []
        for i, (s_text, e_text) in enumerate(pairs, start=1):
            s_text = (s_text or "").strip()
            e_text = (e_text or "").strip()
            if not s_text or not e_text:
                continue
            try:
                s_val = to_seconds(float(s_text), fps)
                e_val = to_seconds(float(e_text), fps)
            except ValueError:
                invalid_row_numbers.append(i)
                continue
            if e_val > s_val and not (duration and s_val >= duration):
                ranges.append((s_val, e_val))
            else:
                invalid_row_numbers.append(i)
        return ranges, invalid_row_numbers

    def get_cut_ranges_sec(fps):
        pairs = [(r["start_entry"].get(), r["end_entry"].get()) for r in cut_rows]
        return parse_cut_pairs(pairs, fps, active_wave_state()["duration"])

    def apply_cut_row_highlighting(invalid_rows):
        for i, row_info in enumerate(cut_rows, start=1):
            bg = "#ffdddd" if i in invalid_rows else "white"
            row_info["start_entry"].config(bg=bg)
            row_info["end_entry"].config(bg=bg)

    def parse_insert_pairs(pairs, fps, duration):
        """Same shape as parse_cut_pairs, but for 無音を挿入 rows: a pair
        is valid when both 位置/長さ are filled, 長さ > 0, and 位置 doesn't
        fall past the end of the (post-cut/trim) audio."""
        inserts = []
        invalid_row_numbers = []
        for i, (p_text, d_text) in enumerate(pairs, start=1):
            p_text = (p_text or "").strip()
            d_text = (d_text or "").strip()
            if not p_text or not d_text:
                continue
            try:
                p_val = to_seconds(float(p_text), fps)
                d_val = to_seconds(float(d_text), fps)
            except ValueError:
                invalid_row_numbers.append(i)
                continue
            if d_val > 0 and p_val >= 0 and not (duration and p_val > duration):
                inserts.append((p_val, d_val))
            else:
                invalid_row_numbers.append(i)
        return inserts, invalid_row_numbers

    def get_silence_inserts_sec(fps):
        pairs = [(r["pos_entry"].get(), r["dur_entry"].get()) for r in insert_rows]
        return parse_insert_pairs(pairs, fps, active_wave_state()["duration"])

    def apply_insert_row_highlighting(invalid_rows):
        for i, row_info in enumerate(insert_rows, start=1):
            bg = "#ffdddd" if i in invalid_rows else "white"
            row_info["pos_entry"].config(bg=bg)
            row_info["dur_entry"].config(bg=bg)

    def refresh_markers(*_args):
        draw_waveform()  # revert to the source waveform in case "実行" swapped it for a result view
        draw_sub_waveform()
        target = edit_target_var.get()
        canvas = wave_canvas if target == "main" else sub_wave_canvas
        height = WAVE_H if target == "main" else SUB_WAVE_H
        other_canvas = sub_wave_canvas if target == "main" else wave_canvas
        other_canvas.delete("marker")  # only the ACTIVE target's canvas shows markers
        canvas.delete("marker")
        duration = active_wave_state()["duration"]
        if not duration:
            return
        try:
            fps = parse_float(fps_entry, default=30.0)
            if fps <= 0:
                return
            start_sec = to_seconds(parse_float(start_entry, default=0.0), fps)
            end_sec = to_seconds(parse_float(end_entry, allow_blank=True), fps)
        except ValueError:
            return
        cut_ranges, invalid_cut_rows = get_cut_ranges_sec(fps)
        apply_cut_row_highlighting(invalid_cut_rows)  # updates live on every keystroke, so a fixed row un-highlights immediately
        _insert_ranges, invalid_insert_rows = get_silence_inserts_sec(fps)
        apply_insert_row_highlighting(invalid_insert_rows)  # same live-highlighting treatment as cut rows

        w = canvas_width(canvas)
        if cut_ranges:
            for cs, ce in cut_ranges:
                x1 = min(w, max(0, cs / duration * w))
                x2 = min(w, max(0, ce / duration * w))
                canvas.create_rectangle(x1, 0, x2, height, fill="#8e44ad", stipple="gray50", outline="#8e44ad", tags="marker")
        else:
            # start/end trim's position would be relative to the POST-cut
            # timeline once a cut is active, so drawing it at face value
            # here (on the pre-cut waveform) would land in the wrong
            # place -- left off this preview overlay whenever a cut is
            # active. Use "実行" to see the actual combined result instead.
            if start_sec is not None:
                x = min(w, max(0, start_sec / duration * w))
                canvas.create_line(x, 0, x, height, fill="#e74c3c", width=2, tags="marker")
            if end_sec is not None:
                x = min(w, max(0, end_sec / duration * w))
                canvas.create_line(x, 0, x, height, fill="#f39c12", width=2, tags="marker")

        # 無音挿入 markers, unlike start/end trim above, are always drawn
        # even while a cut is active -- ocyacya hit this live, 2026-09-15,
        # typing a perfectly valid 位置/長さ (e.g. 15/18 on a cut-active
        # file) produced no marker at all, because the whole block used to
        # bail out early whenever cut_ranges was non-empty. The position is
        # still only approximate once a cut precedes it (same caveat as
        # start/end above), but showing SOMETHING beats showing nothing.
        for pos, dur in _insert_ranges:
            x1 = min(w, max(0, pos / duration * w))
            x2 = min(w, max(0, (pos + dur) / duration * w))
            canvas.create_rectangle(x1, 0, max(x2, x1 + 1), height, fill="#16a085", stipple="gray50", outline="#16a085", tags="marker")

    prev_unit_value = {"unit": unit_var.get()}

    def convert_entry_value(entry, factor, new_unit):
        """Rewrites entry's displayed number by *factor* (fps for 秒→フレーム,
        1/fps for フレーム→秒) so it still points at the same real moment in
        the audio, not just the same digits meaning something else now."""
        if getattr(entry, "_placeholder_active", False):
            return  # a display hint, not a real value -- nothing to convert
        text = entry.get().strip()
        if not text:
            return
        try:
            val = float(text)
        except ValueError:
            return
        new_val = val * factor
        entry.delete(0, tk.END)
        entry.insert(0, f"{new_val:.0f}" if new_unit == "フレーム" else f"{new_val:g}")

    def convert_all_time_entries(old_unit, new_unit):
        if old_unit == new_unit:
            return
        try:
            fps_val = float(fps_entry.get().strip() or 30)
        except ValueError:
            fps_val = 30.0
        if fps_val <= 0:
            fps_val = 30.0
        factor = fps_val if new_unit == "フレーム" else (1.0 / fps_val)
        for entry in (start_entry, end_entry):
            convert_entry_value(entry, factor, new_unit)
        for row_info in cut_rows:
            convert_entry_value(row_info["start_entry"], factor, new_unit)
            convert_entry_value(row_info["end_entry"], factor, new_unit)
        for row_info in insert_rows:
            convert_entry_value(row_info["pos_entry"], factor, new_unit)
            convert_entry_value(row_info["dur_entry"], factor, new_unit)
        convert_entry_value(append_offset_entry, factor, new_unit)

        # The INACTIVE 編集対象's stored snapshot (if any) isn't live in
        # any widget right now, so convert_entry_value can't touch it --
        # without this, switching unit while editing メイン would silently
        # leave a previously-saved サブ snapshot's numbers in the OLD
        # unit, misinterpreted the next time you switch to サブ.
        def convert_raw(text):
            text = (text or "").strip()
            if not text:
                return text
            try:
                val = float(text)
            except ValueError:
                return text
            new_val = val * factor
            return f"{new_val:.0f}" if new_unit == "フレーム" else f"{new_val:g}"

        inactive = "sub" if edit_target_var.get() == "main" else "main"
        snap = target_params.get(inactive)
        if snap:
            snap["start"] = convert_raw(snap["start"])
            snap["end"] = convert_raw(snap["end"])
            snap["cuts"] = [(convert_raw(s), convert_raw(e)) for s, e in snap["cuts"]]
            snap["inserts"] = [(convert_raw(p), convert_raw(d)) for p, d in snap["inserts"]]

    def on_unit_or_fps_change(*_a):
        # ocyacya hit this live, 2026-09-15: switching 秒→フレーム changed
        # how every number was INTERPRETED but left the digits on screen
        # untouched, so e.g. a カット start of "5" (5 seconds) silently
        # became "5" meaning 5 frames instead -- same text, wildly
        # different actual cut point, with no visual sign anything moved.
        new_unit = unit_var.get()
        if new_unit != prev_unit_value["unit"]:
            convert_all_time_entries(prev_unit_value["unit"], new_unit)
            prev_unit_value["unit"] = new_unit
        refresh_markers()
        refresh_end_placeholder()  # the placeholder number is shown in whichever unit is currently selected

    for entry in (start_entry, end_entry):
        entry.bind("<KeyRelease>", refresh_markers)
    fps_entry.bind("<KeyRelease>", on_unit_or_fps_change)
    unit_var.trace_add("write", on_unit_or_fps_change)
    add_cut_row()  # start with one empty row, now that refresh_markers exists for its bindings

    def do_process(mode):
        """mode: 'preview' (temp file, nothing saved to the real folder --
        redraws the waveform canvas in green with the RESULT's actual
        waveform so a cut/trim/insert's effect is visible; reverts to the
        source view as soon as any setting is touched again, see
        refresh_markers; listening to it is a separate, explicit step via
        the ▶プレビュー再生 transport controls, not automatic here -- see
        the "no auto-play" comment below for why), 'save' (writes next to
        the source file), or 'save_mmd' (save, then also load into MMD).

        編集対象(メイン/サブ) 切り替えに対応: メインとサブそれぞれの
        設定(開始/終了/カット/無音挿入)を、今どちらが画面に出ていても
        両方まとめて取得・処理する(ocyacya, 2026-09-15の「メインとサブ
        どちらか選んで編集できるように」要望)。サブ側に何か編集が
        入っていれば、先にサブ単体をffmpegで処理してから、その結果を
        メインの結合/ミックス材料として渡す。"""
        main_path = selected_path["path"]
        if not main_path:
            edit_status.config(text="先にWAVファイルを選択してください。", fg="red")
            return
        try:
            fps = parse_float(fps_entry, default=30.0)
            if fps <= 0:
                raise ValueError("fps must be positive")
            append_offset = to_seconds(parse_float(append_offset_entry, default=0.0), fps)
        except ValueError:
            edit_status.config(text="数値の入力が正しくありません。", fg="red")
            return

        try:
            main_duration = wav_waveform.get_duration(main_path)
        except Exception:
            main_duration = None

        sub_path = append_state["path"]
        sub_duration = None
        if sub_path:
            try:
                sub_duration = wav_waveform.get_duration(sub_path)
            except Exception:
                sub_duration = None

        def parse_snap(snap, duration):
            try:
                start = to_seconds(parse_raw_float(snap["start"], default=0.0), fps)
                end = to_seconds(parse_raw_float(snap["end"], allow_blank=True), fps)
                percent = int(parse_raw_float(snap["volume"], default=100.0))
            except ValueError:
                return None
            cut_ranges, invalid_cuts = parse_cut_pairs(snap["cuts"], fps, duration)
            silence_inserts, invalid_inserts = parse_insert_pairs(snap["inserts"], fps, duration)
            return {
                "start": start, "end": end, "percent": percent,
                "cut_ranges": cut_ranges, "silence_inserts": silence_inserts,
                "invalid_cuts": invalid_cuts, "invalid_inserts": invalid_inserts,
            }

        main_snap = get_params_for("main")
        main_parsed = parse_snap(main_snap, main_duration)
        if main_parsed is None:
            edit_status.config(text="数値の入力が正しくありません(メイン)。", fg="red")
            return

        sub_parsed = None
        if sub_path:
            sub_snap = get_params_for("sub")
            sub_parsed = parse_snap(sub_snap, sub_duration)
            if sub_parsed is None:
                edit_status.config(text="数値の入力が正しくありません(サブ)。", fg="red")
                return

        # ハイライトは今画面に出ている側(編集対象)だけに反映する --
        # 見えていない側の行を勝手にピンクにしても分かりにくいだけなので。
        active = edit_target_var.get()
        active_parsed = main_parsed if active == "main" else sub_parsed
        apply_cut_row_highlighting(active_parsed["invalid_cuts"] if active_parsed else [])
        apply_insert_row_highlighting(active_parsed["invalid_inserts"] if active_parsed else [])

        cut_warning = ""

        def describe_invalid(label, parsed):
            nonlocal cut_warning
            if not parsed:
                return
            if parsed["invalid_cuts"]:
                rows_text = "、".join(f"{i}行目" for i in parsed["invalid_cuts"])
                cut_warning += f" ※{label}カット{rows_text}は無効(終了が開始より後でないか、開始がファイルの長さを超えている)なので無視されました。"
            if parsed["invalid_inserts"]:
                rows_text = "、".join(f"{i}行目" for i in parsed["invalid_inserts"])
                cut_warning += f" ※{label}無音挿入{rows_text}は無効(長さが0以下か、位置がファイルの長さを超えている)なので無視されました。"

        describe_invalid("メイン", main_parsed)
        describe_invalid("サブ", sub_parsed)

        if mode == "preview":
            note_run_snapshot()  # 「実行の1つ前に戻す」用に、この実行が使う設定を記録しておく
            fd, out_path_arg = tempfile.mkstemp(suffix=".wav", prefix="miku_no_oto_preview_")
            os.close(fd)
            edit_status.config(text="実行中...", fg="blue")
        else:
            out_path_arg = None  # process_wav defaults to next to the source file
            edit_status.config(text="保存中...", fg="blue")
        root.update()

        # サブに何かしら編集(トリム/カット/無音挿入/音量変更)が入っていれば、
        # 結合/ミックスする前にサブ単体を先に処理しておく(そうしないと
        # サブ側の設定がどこにも反映されない)。何も編集がなければ元の
        # ファイルをそのまま渡す(余計な一時ファイルを作らない)。
        sub_processed_path = sub_path
        sub_temp_to_cleanup = None
        # サブ単体の処理結果の波形データは、実行(プレビュー)の時だけ
        # サブの波形欄(オレンジ)を緑に切り替えて見せるために、一時
        # ファイルが消される前にここで読んでおく(ocyacya がスクリーン
        # ショット付きで報告、2026-09-15:「メイン弄ってからサブ編集して
        # 実行押したけどサブの波形変わらないね」-- 実行結果はメインの
        # 枠にしか反映されていなかった)。
        sub_result_peaks = None
        if sub_parsed and (
            sub_parsed["cut_ranges"] or sub_parsed["silence_inserts"]
            or sub_parsed["start"] or sub_parsed["end"] is not None
            or sub_parsed["percent"] != 100
        ):
            fd, sub_tmp = tempfile.mkstemp(suffix=".wav", prefix="miku_no_oto_sub_")
            os.close(fd)
            try:
                sub_processed_path = wav_editor.process_wav(
                    sub_path, start=sub_parsed["start"], end=sub_parsed["end"],
                    percent=sub_parsed["percent"], cut_ranges=sub_parsed["cut_ranges"],
                    silence_inserts=sub_parsed["silence_inserts"], out_path=sub_tmp,
                )
                sub_temp_to_cleanup = sub_processed_path
                try:
                    sub_result_peaks = wav_waveform.read_peaks(sub_processed_path, target_columns=WAVE_W)
                except Exception:
                    sub_result_peaks = None
            except Exception as e:
                edit_status.config(text=f"追加WAVの編集に失敗しました: {e}", fg="red")
                return

        try:
            out_path = wav_editor.process_wav(
                main_path, start=main_parsed["start"], end=main_parsed["end"],
                percent=main_parsed["percent"], cut_ranges=main_parsed["cut_ranges"],
                silence_inserts=main_parsed["silence_inserts"],
                append_path=sub_processed_path, append_mode=append_mode_var.get(),
                append_offset=append_offset, out_path=out_path_arg,
            )
        except Exception as e:
            edit_status.config(text=f"{'実行' if mode == 'preview' else '変換'}に失敗しました: {e}", fg="red")
            return
        finally:
            if sub_temp_to_cleanup:
                try:
                    os.remove(sub_temp_to_cleanup)
                except Exception:
                    pass

        last_edit_path["path"] = out_path
        preview_play_btn.config(state=tk.NORMAL)
        preview_pause_btn.config(state=tk.NORMAL)
        preview_stop_btn.config(state=tk.NORMAL)

        if mode == "preview":
            cleanup_preview_temp()  # drops the PREVIOUS preview temp file, if it's safe to (not currently loaded/playing)
            preview_state["temp_path"] = out_path
            render_result_waveform(out_path)
            if sub_result_peaks:
                peaks, _sub_dur, max_val = sub_result_peaks
                render_peaks(sub_wave_canvas, SUB_WAVE_H, peaks, max_val, color="#2ecc71")
                sub_wave_canvas.delete("marker")
                sub_wave_label.config(text="追加WAVの波形: (実行結果)", fg="#2ecc71")
            else:
                draw_sub_waveform()  # サブに今回の編集がなければ、通常の元波形+マーカー表示のまま
            # No auto-play here -- ocyacya pointed out live, 2026-09-15,
            # that this button auto-playing AND the separate "▶プレビュー
            # 再生" transport button right above it were doing overlapping
            # jobs. Now this button has exactly one job (build + show the
            # result waveform, matching what used to be 実行-only
            # behavior), and プレビュー再生 is the one and only way to
            # actually hear it.
            edit_status.config(text="実行完了(緑の波形が編集結果です。▶プレビュー再生で音を確認できます。設定を変更すると元の波形表示に戻ります)" + cut_warning, fg="blue")
            return

        cleanup_preview_temp()

        if mode == "save":
            edit_status.config(text=f"保存完了: {os.path.basename(out_path)}" + cut_warning, fg="blue")
            return

        edit_status.config(text="MMDに読み込んでいます...", fg="blue")
        root.update()
        ok, message = mmd_wav_loader.load_wav_into_mmd(out_path)
        edit_status.config(text=(f"保存完了、{message}" if ok else message) + cut_warning, fg="blue" if ok else "red")

    preview_row = tk.Frame(root)
    preview_row.pack(pady=(8, 0))
    tk.Button(preview_row, text="実行", command=lambda: do_process("preview"), width=10).pack()
    tk.Label(root, text="※保存はせず、波形(緑)で結果を確認します。音を聞きたいときは上の「▶ プレビュー再生」を押してください", font=("Meiryo", 8), fg="gray", wraplength=340, justify=tk.LEFT).pack(padx=10, pady=(2, 6))

    btn_row = tk.Frame(root)
    btn_row.pack(pady=(0, 8))
    tk.Button(btn_row, text="保存", command=lambda: do_process("save"), width=14).pack(side=tk.LEFT, padx=4)
    tk.Button(btn_row, text="保存してMMDに読み込む", command=lambda: do_process("save_mmd"), width=18).pack(side=tk.LEFT, padx=4)

    # WAVファイルをこの exe / ショートカットのアイコンにドラッグ&ドロップして
    # 起動した場合、Windowsがそのファイルパスを起動引数として渡してくる --
    # それを拾ってWAV編集パネルに自動でセットする。
    startup_args = [a for a in sys.argv[1:] if os.path.isfile(a)]
    if startup_args:
        select_file(startup_args[0])

    # ウィンドウ幅が変わったら波形欄も追従させる。<Configure> は移動時や
    # 子ウィジェットの再配置でも連発するので、実際に幅が変わった時だけ、
    # かつドラッグ中の連打を間引いて(120ms後にまとめて)refresh_markersで
    # 再描画する(refresh_markersは緑の実行結果表示も元の波形表示に戻す
    # ので、リサイズも他の設定変更と同じ「表示がリセットされる」扱いに
    # なる -- 既存の「設定を変更すると元の波形表示に戻ります」と同じ挙動)。
    resize_state = {"after_id": None, "width": None}

    def redraw_after_resize():
        resize_state["after_id"] = None
        refresh_markers()

    def on_root_configure(event):
        if event.widget is not root or event.width == resize_state["width"]:
            return
        resize_state["width"] = event.width
        if resize_state["after_id"] is not None:
            root.after_cancel(resize_state["after_id"])
        resize_state["after_id"] = root.after(120, redraw_after_resize)

    root.bind("<Configure>", on_root_configure)

    def on_close():
        stop_playback()
        mci_player.stop()
        mci_player.close()
        cleanup_preview_temp()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
