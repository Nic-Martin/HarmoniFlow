# filepath: ensemble_arranger.py
"""Timing-preserving MIDI ensemble arrangements for HarmoniFlow."""

import os
import re
from collections import Counter, defaultdict

import mido


ENSEMBLE_STYLES = {
    "Drum and Bugle Corps": {
        "bpm": 138,
        "tracks": [
            ("Brass Line (Bugle/Trumpet)", 0, 56, 98, "lead"),
            ("Baritone / Euphonium Voice", 1, 59, 84, "harmony"),
            ("Battery / Snare & Tenor Cadence", 9, 38, 88, "drums"),
        ],
    },
    "Symphony Orchestra": {
        "bpm": 104,
        "tracks": [
            ("First Violins", 0, 40, 86, "lead"),
            ("Woodwind Counterpoint", 1, 73, 76, "harmony"),
            ("Cello & Contrabass", 2, 42, 80, "bass"),
            ("French Horn", 3, 60, 72, "pad"),
        ],
    },
    "String Quintet": {
        "bpm": 112,
        "tracks": [
            ("Violin I", 0, 40, 86, "lead"),
            ("Violin II", 1, 40, 76, "harmony"),
            ("Viola", 2, 41, 72, "pad"),
            ("Cello", 3, 42, 78, "bass"),
            ("Double Bass", 4, 43, 72, "bass"),
        ],
    },
    "Marching Band": {
        "bpm": 128,
        "tracks": [
            ("Trumpet / Brass Lead", 0, 56, 96, "lead"),
            ("Clarinet / Woodwind Harmony", 1, 71, 80, "harmony"),
            ("Tuba / Low Brass", 2, 58, 86, "bass"),
            ("Marching Percussion", 9, 38, 78, "drums"),
        ],
    },
}

_MAJOR = (0, 2, 4, 5, 7, 9, 11)
_MINOR = (0, 2, 3, 5, 7, 8, 10)


def _safe_stem(text, fallback="part"):
    cleaned = re.sub(r"[^A-Za-z0-9 _-]+", "", str(text or "")).strip()
    cleaned = "_".join(cleaned.split())
    return cleaned or fallback


def resolve_ensemble_style(style_name):
    if style_name in ENSEMBLE_STYLES:
        return style_name, ENSEMBLE_STYLES[style_name]
    lowered = (style_name or "").lower()
    if any(word in lowered for word in ("drum", "bugle", "corps")):
        name = "Drum and Bugle Corps"
    elif "symphony" in lowered or "orchestra" in lowered:
        name = "Symphony Orchestra"
    elif "quintet" in lowered or "string" in lowered:
        name = "String Quintet"
    else:
        name = "Marching Band"
    return name, ENSEMBLE_STYLES[name]


def _track_note_events(track, channel_filter=True):
    """Return notes with absolute ticks, pairing note-ons with note-offs."""
    active = defaultdict(list)
    events = []
    tick = 0
    for msg in track:
        tick += msg.time
        if not msg.is_meta and msg.type in ("note_on", "note_off"):
            if channel_filter and msg.channel == 9:
                continue
            key = (msg.channel, msg.note)
            if msg.type == "note_on" and msg.velocity > 0:
                active[key].append((tick, msg.velocity))
            elif active[key]:
                start, velocity = active[key].pop(0)
                if tick > start:
                    events.append({"pitch": msg.note, "start": start,
                                   "duration": tick - start, "velocity": velocity})
    for (channel, pitch), pending in active.items():
        for start, velocity in pending:
            if tick > start:
                events.append({"pitch": pitch, "start": start,
                               "duration": tick - start, "velocity": velocity})
    return events


def extract_note_events(source_midi_path, max_notes=4096):
    """Choose the most melody-like non-drum track and preserve its note timing."""
    if not source_midi_path or not os.path.exists(source_midi_path):
        return [], None, 480
    try:
        mid = mido.MidiFile(source_midi_path)
        candidates = []
        for track in mid.tracks:
            events = _track_note_events(track)
            if not events:
                continue
            pitches = [item["pitch"] for item in events]
            pitch_range = max(pitches) - min(pitches)
            distinct_ratio = len(set(pitches)) / max(1, len(pitches))
            melodic_register = sum(1 for pitch in pitches if 52 <= pitch <= 88) / len(pitches)
            score = len(events) * (0.5 + distinct_ratio) * (0.5 + melodic_register) * (1 + min(pitch_range, 36) / 36)
            candidates.append((score, events))
        chosen = max(candidates, key=lambda item: item[0])[1] if candidates else []
        return chosen[:max_notes], mid, mid.ticks_per_beat
    except Exception as err:
        print(f"[ENSEMBLE ARRANGER] MIDI extraction failed: {err}")
        return [], None, 480


def extract_notes_from_midi(source_midi_path, max_notes=256):
    """Compatibility helper returning pitches from the selected melody line."""
    events, _, _ = extract_note_events(source_midi_path, max_notes=max_notes)
    return [event["pitch"] for event in events]


def _key_and_scale(notes):
    histogram = Counter(note["pitch"] % 12 for note in notes)
    if not histogram:
        return 0, _MAJOR
    best = None
    for root in range(12):
        for scale, _ in ((_MAJOR, False), (_MINOR, True)):
            score = sum(histogram[(root + degree) % 12] * (2 if degree in (0, 2, 4) else 1) for degree in scale)
            score -= sum(histogram[(root + pitch) % 12] for pitch in range(12) if pitch not in scale)
            if best is None or score > best[0]:
                best = (score, root, scale)
    return best[1], best[2]


def _harmony_pitch(pitch, root, scale):
    pcs = {(root + degree) % 12: degree_index for degree_index, degree in enumerate(scale)}
    degree = pcs.get(pitch % 12)
    if degree is None:
        nearby = min(pcs, key=lambda pc: min((pc - pitch % 12) % 12, (pitch % 12 - pc) % 12))
        degree = pcs[nearby]
    target_degree = (degree + 2) % len(scale)
    octave = pitch // 12
    target = (octave * 12) + root + scale[target_degree]
    while target <= pitch:
        target += 12
    if target > 84:
        target -= 12
    return max(48, min(88, target))


def _make_track(name, channel, program, velocity, role, notes, root, scale, ticks_per_beat, total_ticks):
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name=name, time=0))
    if channel != 9:
        track.append(mido.Message("program_change", program=program, channel=channel, time=0))
    pending = []
    if role in ("lead", "harmony", "pad"):
        for event in notes:
            pitch = event["pitch"] if role == "lead" else _harmony_pitch(event["pitch"], root, scale)
            start = max(0, int(event["start"]))
            duration = max(1, int(event["duration"]))
            if role == "pad":
                duration = max(duration, ticks_per_beat)
            vel = max(1, min(127, int(event.get("velocity", velocity))))
            pending.extend(((start, 1, pitch, vel),
                            (start + duration, 0, pitch, 0)))
    elif role == "bass":
        bar = ticks_per_beat * 4
        bars = max(1, (total_ticks + bar - 1) // bar)
        for index in range(bars):
            start = index * bar
            notes_in_bar = [n for n in notes if start <= n["start"] < start + bar]
            if not notes_in_bar:
                continue
            source = notes_in_bar[0]["pitch"]
            pitch_class = min(((root + d) % 12 for d in scale),
                              key=lambda pc: min((pc - source % 12) % 12, (source % 12 - pc) % 12))
            pitch = max(36, min(55, 36 + pitch_class))
            duration = min(bar, total_ticks - start)
            pending.extend(((start, 1, pitch, velocity),
                            (start + duration, 0, pitch, 0)))
    elif role == "drums":
        beat = ticks_per_beat
        bar = beat * 4
        for start in range(0, total_ticks, beat):
            beat_in_bar = (start % bar) // beat
            if channel == 9:
                drum = 36 if beat_in_bar in (0, 2) else 38
                vel = velocity if beat_in_bar in (0, 2) else max(1, velocity - 8)
                pending.extend(((start, 1, drum, vel),
                                (start + max(1, beat // 3), 0, drum, 0)))
    pending.sort(key=lambda item: (item[0], item[1]))
    current = 0
    for tick, is_on, pitch, vel in pending:
        delta = max(0, tick - current)
        if is_on:
            track.append(mido.Message("note_on", note=pitch, velocity=vel, channel=channel, time=delta))
        else:
            track.append(mido.Message("note_off", note=pitch, velocity=0, channel=channel, time=delta))
        current = tick
    track.append(mido.MetaMessage("end_of_track", time=0))
    return track


def save_individual_track_midis(arranged_midi, output_dir, prefix="part"):
    """Writes each note-bearing instrument track in a MIDI file to its own .mid file."""
    os.makedirs(output_dir, exist_ok=True)
    mid = (
        mido.MidiFile(arranged_midi)
        if isinstance(arranged_midi, (str, os.PathLike))
        else arranged_midi
    )

    tempo_msg = None
    for track in mid.tracks:
        for msg in track:
            if msg.type == "set_tempo":
                tempo_msg = msg.copy(time=0)
                break
        if tempo_msg:
            break

    saved_paths = []
    part_index = 0
    safe_prefix = _safe_stem(prefix, fallback="part")

    for track in mid.tracks:
        track_name = None
        has_notes = False
        for msg in track:
            if msg.is_meta and msg.type == "track_name" and not track_name:
                track_name = msg.name
            if not msg.is_meta and msg.type == "note_on" and msg.velocity > 0:
                has_notes = True

        if not has_notes:
            continue

        part_index += 1
        single_mid = mido.MidiFile(type=1, ticks_per_beat=mid.ticks_per_beat)

        meta_track = mido.MidiTrack()
        meta_track.append(mido.MetaMessage("track_name", name=track_name or f"Part {part_index}", time=0))
        if tempo_msg:
            meta_track.append(tempo_msg.copy())
        meta_track.append(mido.MetaMessage("end_of_track", time=0))
        single_mid.tracks.append(meta_track)

        cloned_track = mido.MidiTrack()
        for msg in track:
            cloned_track.append(msg.copy())
        single_mid.tracks.append(cloned_track)

        part_stem = _safe_stem(track_name, fallback=f"part_{part_index}")
        out_path = os.path.join(output_dir, f"{part_index:02d}_{safe_prefix}_{part_stem}.mid")
        single_mid.save(out_path)
        saved_paths.append(out_path)

    return saved_paths


def split_midi_into_instrument_files(midi_path, output_dir=None):
    """Splits a MIDI file into individual per-instrument .mid files on disk."""
    if not midi_path or not os.path.exists(midi_path):
        return []
    stem = os.path.splitext(os.path.basename(midi_path))[0]
    target_dir = output_dir or os.path.join(os.path.dirname(os.path.abspath(midi_path)), f"{_safe_stem(stem)}_parts")
    return save_individual_track_midis(midi_path, output_dir=target_dir, prefix=stem)


def split_arranged_midi_to_xml_parts(midi_path, output_dir=None, target_instrument="All Instruments"):
    """Helper that delegates per-instrument MusicXML export to notation_generator if present."""
    try:
        from notation_generator import export_instrument_parts_from_midi
        return export_instrument_parts_from_midi(
            midi_path,
            output_dir=output_dir,
            target_instrument=target_instrument,
        )
    except Exception as err:
        print(f"[ENSEMBLE ARRANGER] XML part split skipped: {err}")
        return []


def arrange_notes_for_ensemble(notes, style_name="Marching Band", source_midi=None,
                               ticks_per_beat=None, tempo=None, split_midi_dir=None):
    """Create a playable ensemble score while retaining melody timing and lengths."""
    resolved_style, preset = resolve_ensemble_style(style_name)
    ticks_per_beat = int(ticks_per_beat or getattr(source_midi, "ticks_per_beat", 480))
    raw_notes = list(notes or [])
    if raw_notes and isinstance(raw_notes[0], int):
        raw_notes = [{"pitch": int(p), "start": i * ticks_per_beat // 2,
                      "duration": ticks_per_beat // 2, "velocity": 88} for i, p in enumerate(raw_notes)]
    normalized = sorted((n for n in raw_notes if 0 <= int(n["pitch"]) <= 127), key=lambda n: n["start"])
    if not normalized:
        raise ValueError("No notes could be read from this file. Choose a MIDI with a melodic part.")
    root, scale = _key_and_scale(normalized)
    total_ticks = max(n["start"] + n["duration"] for n in normalized)
    out_mid = mido.MidiFile(type=1, ticks_per_beat=ticks_per_beat)
    meta = mido.MidiTrack()
    out_mid.tracks.append(meta)
    meta.append(mido.MetaMessage("track_name", name=f"Arrangement: {resolved_style}", time=0))
    if source_midi:
        source_tempo = None
        for track in source_midi.tracks:
            for msg in track:
                if msg.type == "set_tempo":
                    source_tempo = msg.tempo
                    break
            if source_tempo is not None:
                break
        meta.append(mido.MetaMessage("set_tempo", tempo=source_tempo or mido.bpm2tempo(preset["bpm"]), time=0))
    else:
        meta.append(mido.MetaMessage("set_tempo", tempo=int(tempo or mido.bpm2tempo(preset["bpm"])), time=0))
    for name, channel, program, velocity, role in preset["tracks"]:
        out_mid.tracks.append(_make_track(name, channel, program, velocity, role, normalized,
                                          root, scale, ticks_per_beat, total_ticks))

    if split_midi_dir:
        save_individual_track_midis(out_mid, output_dir=split_midi_dir, prefix=_safe_stem(resolved_style))

    return resolved_style, out_mid