# -*- coding: utf-8 -*-
"""ffmpeg-backed WAV trim/volume/timing editor, used by ミクの音's
"WAV編集してMMDに読み込む" panel. Never overwrites the source file --
always writes a fresh "<name>_edit_<timestamp>.wav" next to it (a distinct
name each time, not a fixed one -- see the comment in process_wav for why).
"""
import os
import subprocess
import sys
import time
import wave


def _resolve_ffmpeg():
    # PyInstaller onefile builds extract bundled `binaries=` entries into the
    # temp dir named by sys._MEIPASS at runtime, NOT next to sys.executable
    # (that would only be true for a onedir build) -- same place icon.ico's
    # _resource_path() already looks in miku_no_oto.py.
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bundled = os.path.join(meipass, "ffmpeg.exe")
        if os.path.isfile(bundled):
            return bundled
    if getattr(sys, "frozen", False):
        bundled = os.path.join(os.path.dirname(sys.executable), "ffmpeg.exe")
        if os.path.isfile(bundled):
            return bundled
    dev_copy = r"E:\AI用\RVC\ffmpeg.exe"
    if os.path.isfile(dev_copy):
        return dev_copy
    return "ffmpeg.exe"


FFMPEG = _resolve_ffmpeg()


def _get_samplerate(path):
    with wave.open(path, "rb") as wf:
        return wf.getframerate()


def _get_channel_layout(path):
    with wave.open(path, "rb") as wf:
        return "mono" if wf.getnchannels() == 1 else "stereo"


def process_wav(src_path, start=0.0, end=None, percent=100,
                 cut_ranges=None, silence_inserts=None,
                 append_path=None, append_mode="append", append_offset=0.0, out_path=None):
    """Applies, in order, up to four edits on top of each other -- cuts,
    then trim, then silence insert(s), then volume, then append -- so they
    compose instead of being exclusive alternatives (ocyacya asked for
    exactly this: cut section(s) out, THEN also trim the remaining
    start/end, in one go):

    1. cut_ranges, if given, is an iterable of (cut_start, cut_end) second
       pairs (cut_end > cut_start each); every one of those ranges is
       REMOVED from the middle and the remainder spliced together. Ranges
       may be given in any order and must not overlap.
    2. start/end then trims THAT result down to [start, end) (end=None
       means to the end) -- relative to the post-cut timeline, i.e. "0"
       here means the start of what's left after step 1, not of the
       original file.
    3. silence_inserts, if given, is an iterable of (position, duration)
       second pairs -- the reverse of a cut: *duration* seconds of silence
       is INSERTED at *position* (relative to the post-cut/trim timeline
       from steps 1-2, not the original file), pushing everything after it
       later. Positions are all measured against that same pre-insert
       timeline, so multiple inserts don't need to account for each other
       shifting things -- same model as cut_ranges. A position of 0 covers
       what used to be a separate "prepend silence to the head" option --
       there's no need for that as its own parameter once an insert can
       land anywhere, including the very start.
    4. percent (volume%) is applied to that.
    5. append_path, if given, is a second WAV file combined with the
       result of steps 1-4, resampled/channel-matched to src_path's own
       format only as needed for a clean join. How it combines depends on
       append_mode:
       - "append" (default): concatenated onto the end, one after the
         other, like everything above.
       - "mix": OVERLAID on top instead -- both play simultaneously,
         like a second take layered over the first (ocyacya asked for
         this literally: "二重演奏"). append_offset (seconds) delays when
         the overlaid track itself starts, relative to the same timeline
         as steps 1-4's result; 0 means both start together. If the
         overlaid track runs past the end of the main one, the output is
         extended to fit it rather than being cut off.

    Returns the output path written.
    """
    src_path = os.path.abspath(src_path)
    if out_path is None:
        directory, filename = os.path.split(src_path)
        name, _ = os.path.splitext(filename)
        # Timestamp suffix, not a fixed "<name>_edit.wav": MMD appears to
        # skip actually re-reading a file it thinks is already loaded at
        # that exact path, so re-editing and reloading with the same output
        # name silently kept playing the PREVIOUS edit's audio in MMD even
        # though the file on disk was correctly overwritten (ocyacya hit
        # this live, 2026-09-15 -- changing 無音追加 50->100 frames and
        # reloading still played back starting at the old 50-frame offset).
        # A fresh path each time forces MMD to treat it as a genuinely new
        # file.
        out_path = os.path.join(directory, f"{name}_edit_{int(time.time() * 1000)}.wav")

    steps = []
    cur = "0:a"
    counter = [0]

    def new_label():
        counter[0] += 1
        return f"s{counter[0]}"

    valid_cuts = sorted(
        (cs, ce) for cs, ce in (cut_ranges or []) if cs is not None and ce is not None and ce > cs
    )
    if valid_cuts:
        keep_labels = []
        prev_end = 0.0
        for cs, ce in valid_cuts:
            if cs > prev_end:  # keep the untouched stretch before this cut
                lbl = new_label()
                steps.append(f"[{cur}]atrim={prev_end}:{cs},asetpts=PTS-STARTPTS[{lbl}]")
                keep_labels.append(lbl)
            prev_end = max(prev_end, ce)
        lbl = new_label()  # the tail after the last cut
        steps.append(f"[{cur}]atrim=start={prev_end},asetpts=PTS-STARTPTS[{lbl}]")
        keep_labels.append(lbl)
        if len(keep_labels) > 1:
            lbl = new_label()
            steps.append("".join(f"[{k}]" for k in keep_labels) + f"concat=n={len(keep_labels)}:v=0:a=1[{lbl}]")
        cur = lbl

    if (start and start > 0) or end is not None:
        lbl = new_label()
        if end is not None:
            steps.append(f"[{cur}]atrim={start or 0.0}:{end},asetpts=PTS-STARTPTS[{lbl}]")
        else:
            steps.append(f"[{cur}]atrim=start={start or 0.0},asetpts=PTS-STARTPTS[{lbl}]")
        cur = lbl

    target_rate = None  # computed lazily -- only silence_inserts/append_path need these
    target_layout = None

    def get_target_rate():
        nonlocal target_rate
        if target_rate is None:
            try:
                target_rate = _get_samplerate(src_path)
            except Exception:
                target_rate = 44100
        return target_rate

    def get_target_layout():
        nonlocal target_layout
        if target_layout is None:
            try:
                target_layout = _get_channel_layout(src_path)
            except Exception:
                target_layout = "stereo"
        return target_layout

    valid_inserts = sorted(
        (p, d) for p, d in (silence_inserts or []) if p is not None and d is not None and d > 0 and p >= 0
    )
    if valid_inserts:
        rate = get_target_rate()
        layout = get_target_layout()
        n = len(valid_inserts)
        # cur might already be a processed filter label at this point (cut
        # and/or trim may have run first), and ffmpeg only allows a named
        # filter output pad to be consumed ONCE -- unlike a raw input
        # stream ("0:a"), which can feed multiple filters directly. This
        # block needs cur n+1 times (one "before" slice per insertion
        # point, plus the final tail), so asplit fans it out into that
        # many identical copies up front rather than referencing [cur]
        # itself more than once (confirmed live, 2026-09-15: combining a
        # cut + trim + silence-insert failed with "Invalid stream
        # specifier" because [cur] was already a processed label reused
        # twice).
        split_labels = [new_label() for _ in range(n + 1)]
        steps.append(f"[{cur}]asplit={n + 1}" + "".join(f"[{lb}]" for lb in split_labels))
        piece_labels = []
        prev = 0.0
        for (pos, dur), before_lbl in zip(valid_inserts, split_labels[:-1]):
            lbl = new_label()  # the untouched stretch before this insertion point (zero-length if pos == prev, which concat tolerates)
            steps.append(f"[{before_lbl}]atrim={prev}:{pos},asetpts=PTS-STARTPTS[{lbl}]")
            piece_labels.append(lbl)
            sil_lbl = new_label()
            # anullsrc's own `d=` duration option isn't available on every
            # ffmpeg build (confirmed missing on this project's bundled
            # one -- "Option 'd' not found") -- atrim-ing an otherwise
            # infinite anullsrc works everywhere. cl must match the
            # SOURCE's actual channel layout (mono vs stereo), not just be
            # assumed stereo, or concat below rejects the mismatch.
            steps.append(f"anullsrc=r={rate}:cl={layout},atrim=0:{dur}[{sil_lbl}]")
            piece_labels.append(sil_lbl)
            prev = pos
        lbl = new_label()  # the tail after the last insertion point
        steps.append(f"[{split_labels[-1]}]atrim=start={prev},asetpts=PTS-STARTPTS[{lbl}]")
        piece_labels.append(lbl)
        lbl = new_label()
        steps.append("".join(f"[{p}]" for p in piece_labels) + f"concat=n={len(piece_labels)}:v=0:a=1[{lbl}]")
        cur = lbl

    if percent is not None and percent != 100:
        lbl = new_label()
        steps.append(f"[{cur}]volume={percent / 100.0}[{lbl}]")
        cur = lbl

    extra_inputs = []
    if append_path:
        append_path = os.path.abspath(append_path)
        extra_inputs.append(append_path)
        rate = get_target_rate()
        layout = get_target_layout()
        a_lbl, b_lbl, joined_lbl = new_label(), new_label(), new_label()
        # Both concat and amix require both sides to already share
        # format/rate/layout; normalize each to src_path's own rate/layout
        # right before joining rather than assuming the two files already
        # match.
        steps.append(f"[{cur}]aformat=sample_rates={rate}:channel_layouts={layout}[{a_lbl}]")
        b_chain = f"aformat=sample_rates={rate}:channel_layouts={layout}"
        if append_mode == "mix" and append_offset and append_offset > 0:
            # shifts the OVERLAID track's own start later, so it doesn't
            # begin playing at position 0 alongside the main track
            b_chain += f",adelay={int(append_offset * 1000)}:all=1"
        steps.append(f"[1:a]{b_chain}[{b_lbl}]")
        if append_mode == "mix":
            # duration=longest (not the amix default of "first"): if the
            # overlaid track runs past the end of the main one, keep
            # playing it rather than cutting it off -- matches how "append"
            # never truncates either.
            steps.append(f"[{a_lbl}][{b_lbl}]amix=inputs=2:duration=longest[{joined_lbl}]")
        else:
            steps.append(f"[{a_lbl}][{b_lbl}]concat=n=2:v=0:a=1[{joined_lbl}]")
        cur = joined_lbl

    if steps:
        graph = ";".join(steps)
        cmd = [FFMPEG, "-y", "-i", src_path]
        for p in extra_inputs:
            cmd += ["-i", p]
        cmd += ["-filter_complex", graph, "-map", f"[{cur}]", out_path]
    else:
        cmd = [FFMPEG, "-y", "-i", src_path, out_path]

    # Without this, launching the console-subsystem ffmpeg.exe from this
    # windowed (console=False) app pops a visible cmd window that lingers
    # behind the GUI instead of stderr just going to the pipe (ocyacya saw
    # this live, 2026-09-15 -- a stray console full of ffmpeg's version
    # banner sitting behind the ミクの音 window). CREATE_NO_WINDOW alone
    # turned out not to be reliable from inside the frozen PyInstaller
    # onefile build (still popped a console there even though a plain
    # `python -c` dev-mode test never showed one) -- pairing it with a
    # STARTUPINFO that explicitly requests SW_HIDE is the combination that
    # actually suppresses it consistently for a windowed-subsystem parent.
    creationflags = 0
    startupinfo = None
    if os.name == "nt":
        creationflags = subprocess.CREATE_NO_WINDOW
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
    proc = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        creationflags=creationflags, startupinfo=startupinfo,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed (code {proc.returncode}):\n{proc.stdout}")
    return out_path
