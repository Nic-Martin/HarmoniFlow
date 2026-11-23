# filepath: audio_converter.py

import base64
import glob
import json
import logging
import os
import shutil
import tempfile
import traceback
import urllib.error
import urllib.request

logging.getLogger("numba").setLevel(logging.WARNING)


def _configure_ffmpeg_path():
    """Ensure a standard ffmpeg.exe directory is present on PATH for stem and audio tools."""
    if shutil.which("ffmpeg"):
        return

    winget_matches = glob.glob(
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*\**\ffmpeg.exe"),
        recursive=True,
    )
    for exe_path in winget_matches:
        if os.path.isfile(exe_path):
            bin_dir = os.path.dirname(exe_path)
            os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
            return

    try:
        import imageio_ffmpeg

        src_exe = imageio_ffmpeg.get_ffmpeg_exe()
        if src_exe and os.path.isfile(src_exe):
            cache_dir = os.path.join(tempfile.gettempdir(), "harmoniflow_ffmpeg")
            os.makedirs(cache_dir, exist_ok=True)
            dst_exe = os.path.join(cache_dir, "ffmpeg.exe")
            if not os.path.exists(dst_exe):
                shutil.copy2(src_exe, dst_exe)
            os.environ["PATH"] = cache_dir + os.pathsep + os.environ.get("PATH", "")
    except Exception:
        pass


_configure_ffmpeg_path()

import mido
import numpy as np
from kivy.utils import platform

from ensemble_arranger import (
    ENSEMBLE_STYLES,
    arrange_notes_for_ensemble,
    extract_note_events,
    resolve_ensemble_style,
)

ARRANGEMENT_STYLES = ENSEMBLE_STYLES
_AUDIO_TEMPO = mido.bpm2tempo(120)
_AUDIO_TICKS_PER_BEAT = 480
DEFAULT_API_URL = os.environ.get("HARMONIFLOW_AI_API_URL", "http://127.0.0.1:8000")


def _request_api_arrangement(style_name, output_midi_path, notes=None, api_url=DEFAULT_API_URL, timeout=20.0):
    """Calls POST /api/arrange on the FastAPI server and writes the returned MIDI file."""
    if not api_url:
        return False
    endpoint = api_url.rstrip("/") + "/api/arrange"
    note_pitches = []
    for item in (notes or []):
        if isinstance(item, (list, tuple)) and item:
            note_pitches.append(int(item[0]))
        elif isinstance(item, (int, float)):
            note_pitches.append(int(item))

    payload = json.dumps({
        "style_name": resolve_ensemble_style(style_name),
        "notes": note_pitches[:256],
    }).encode("utf-8")

    req = urllib.request.Request(
        endpoint,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        if not isinstance(body, dict) or "midi_base64" not in body:
            raise ValueError("Invalid response payload from arrangement API.")
        midi_bytes = base64.b64decode(body["midi_base64"])
        parent_dir = os.path.dirname(os.path.abspath(output_midi_path))
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        with open(output_midi_path, "wb") as f:
            f.write(midi_bytes)
        engine = body.get("engine", "api")
        print(f"[API ARRANGER] Received '{body.get('style_name', style_name)}' ({engine}) -> {output_midi_path}")
        return True
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError, OSError) as err:
        print(f"[API ARRANGER] Fallback to local arranger ({endpoint}): {err}")
        return False


def _extract_pitches_via_basic_pitch(audio_path):
    """Return (pitch, start seconds, duration seconds, velocity) tuples."""
    try:
        from basic_pitch.inference import predict
        from basic_pitch import ICASSP_2022_MODEL_PATH

        _, midi_data, _ = predict(audio_path, ICASSP_2022_MODEL_PATH)
        result = []
        for instrument in getattr(midi_data, "instruments", []):
            if getattr(instrument, "is_drum", False):
                continue
            for note in getattr(instrument, "notes", []):
                pitch = int(note.pitch)
                if 36 <= pitch <= 96 and note.end > note.start:
                    result.append((
                        pitch,
                        float(note.start),
                        float(note.end - note.start),
                        int(getattr(note, "velocity", 88)),
                    ))
        result.sort(key=lambda event: event[1])
        return result[:4096]
    except ImportError:
        return []
    except Exception as err:
        print(f"[PITCH INFO] basic-pitch failed; trying librosa: {err}")
        traceback.print_exc()
        return []


def _extract_melody_events(audio_path):
    events = _extract_pitches_via_basic_pitch(audio_path)
    if events:
        return events
    try:
        import librosa

        audio, sample_rate = librosa.load(audio_path, sr=22050, mono=True)
        if audio is None or len(audio) == 0:
            return []
        hop = 256
        f0, voiced, _ = librosa.pyin(
            audio,
            fmin=librosa.note_to_hz("E2"),
            fmax=librosa.note_to_hz("C7"),
            sr=sample_rate,
            frame_length=2048,
            hop_length=hop,
        )
        times = librosa.times_like(f0, sr=sample_rate, hop_length=hop)
        pitches = []
        for hz, confidence in zip(f0, voiced):
            if hz is None or np.isnan(hz) or confidence is None or confidence < 0.55:
                pitches.append(None)
                continue
            pitch = int(round(librosa.hz_to_midi(float(hz))))
            pitches.append(pitch if 36 <= pitch <= 96 else None)

        result = []
        start = 0
        while start < len(pitches):
            pitch = pitches[start]
            end = start + 1
            while end < len(pitches) and pitches[end] == pitch:
                end += 1
            duration = (
                (float(times[min(end, len(times) - 1)]) - float(times[start]))
                if end < len(times)
                else hop / sample_rate
            )
            if pitch is not None and duration >= 0.08:
                result.append((pitch, float(times[start]), max(duration, hop / sample_rate), 76))
            start = end
        return result[:4096]
    except Exception as err:
        print(f"[PITCH ERROR] Could not transcribe audio: {err}")
        traceback.print_exc()
        return []


def _isolate_instrumental_if_desktop(audio_path):
    _configure_ffmpeg_path()
    if platform not in ("win", "linux", "macosx"):
        return audio_path, None
    try:
        from audio_separator.separator import Separator

        out_dir = os.path.dirname(os.path.abspath(audio_path))
        separator = Separator(output_dir=out_dir, output_format="WAV")
        separator.load_model(model_filename="Kim_Vocal_2.onnx")
        output_files = separator.separate(audio_path)
        for filename in output_files:
            candidate = os.path.join(out_dir, filename)
            lower_name = filename.lower()
            if ("instrumental" in lower_name or "no_vocal" in lower_name) and os.path.exists(candidate):
                return candidate, candidate
        return audio_path, None
    except Exception as err:
        print(f"[AUDIO INFO] Stem isolation skipped: {err}")
        return audio_path, None


def _write_events_midi(events, midi_path):
    midi = mido.MidiFile(type=1, ticks_per_beat=_AUDIO_TICKS_PER_BEAT)
    meta = mido.MidiTrack()
    midi.tracks.append(meta)
    meta.append(mido.MetaMessage("track_name", name="Audio Transcription", time=0))
    meta.append(mido.MetaMessage("set_tempo", tempo=_AUDIO_TEMPO, time=0))
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.append(mido.MetaMessage("track_name", name="Transcribed Melody", time=0))
    track.append(mido.Message("program_change", program=0, channel=0, time=0))
    pending = []
    for pitch, start_sec, duration_sec, velocity in events:
        start_tick = max(0, round(start_sec * 2 * _AUDIO_TICKS_PER_BEAT))
        end_tick = max(start_tick + 1, round((start_sec + duration_sec) * 2 * _AUDIO_TICKS_PER_BEAT))
        pending.extend((
            (start_tick, 1, pitch, max(1, min(127, velocity))),
            (end_tick, 0, pitch, 0),
        ))
    pending.sort(key=lambda item: (item[0], item[1]))
    current = 0
    for tick, on, pitch, velocity in pending:
        track.append(mido.Message(
            "note_on" if on else "note_off",
            note=pitch,
            velocity=velocity,
            channel=0,
            time=tick - current,
        ))
        current = tick
    track.append(mido.MetaMessage("end_of_track", time=0))
    parent_dir = os.path.dirname(os.path.abspath(midi_path))
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)
    midi.save(midi_path)


def transcribe_mp3_to_base_midi(audio_path, base_midi_path):
    audio_for_analysis, temporary_wav = _isolate_instrumental_if_desktop(audio_path)
    events = _extract_melody_events(audio_for_analysis)
    if temporary_wav and temporary_wav != audio_path and os.path.exists(temporary_wav):
        try:
            os.remove(temporary_wav)
        except OSError:
            pass
    if not events:
        print("[AUDIO ERROR] No melody notes were detected; no fallback tune was generated.")
        return False
    _write_events_midi(events, base_midi_path)
    return True


def arrange_midi_with_style(
    source_midi_path,
    arranged_midi_path,
    style_name="Marching Band",
    fallback_pitches=None,
    api_url=None,
):
    """Arrange via FastAPI /api/arrange first, falling back to local ensemble_arranger."""
    notes, source_midi, ticks_per_beat = extract_note_events(source_midi_path, max_notes=4096)
    if not notes and fallback_pitches:
        notes = list(fallback_pitches)
    if not notes:
        print("[ARRANGER ERROR] Source MIDI contains no usable melodic notes.")
        return False

    target_url = api_url or os.environ.get("HARMONIFLOW_AI_API_URL", DEFAULT_API_URL)
    if _request_api_arrangement(style_name, arranged_midi_path, notes=notes, api_url=target_url):
        return True

    try:
        resolved_style, result = arrange_notes_for_ensemble(
            notes,
            style_name=style_name,
            source_midi=source_midi,
            ticks_per_beat=ticks_per_beat,
        )
        parent_dir = os.path.dirname(os.path.abspath(arranged_midi_path))
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        result.save(arranged_midi_path)
        print(f"[ARRANGER] Saved {resolved_style} arrangement: {arranged_midi_path}")
        return True
    except Exception as err:
        print(f"[ARRANGER ERROR] Could not create arrangement: {err}")
        return False


def convert_mp3_to_midi(audio_path, output_midi_path, target_instrument="Marching Band", api_url=None):
    base_dir = os.path.dirname(os.path.abspath(output_midi_path))
    stem = os.path.splitext(os.path.basename(output_midi_path))[0]
    base_path = os.path.join(base_dir, f"_base_{stem}.mid")
    try:
        if not transcribe_mp3_to_base_midi(audio_path, base_path):
            return False
        return arrange_midi_with_style(
            base_path,
            output_midi_path,
            style_name=target_instrument,
            api_url=api_url,
        )
    finally:
        if os.path.exists(base_path):
            try:
                os.remove(base_path)
            except OSError:
                pass


if __name__ == "__main__":
    print("Ensemble styles:", ", ".join(ENSEMBLE_STYLES))