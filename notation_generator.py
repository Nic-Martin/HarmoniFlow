# filepath: notation_generator.py

import os
import re
import music21 as m21

os.environ['MUSIC21_DONT_LOAD_CONFIG'] = '1'


def _clean_song_title(midi_path):
    stem = os.path.splitext(os.path.basename(midi_path))[0]
    cleaned = re.sub(
        r"^(arranged_(drum_and_bugle_corps|symphony_orchestra|string_quintet|marching_band)_|converted_)",
        "",
        stem,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"_(drum_and_bugle_corps|symphony_orchestra|string_quintet|marching_band)$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = cleaned.replace("_", " ").strip()
    return cleaned.title() if cleaned else "Arranged Score"


def _detect_ensemble_from_path(midi_path, fallback="Marching Band"):
    stem = os.path.basename(midi_path).lower()
    mapping = {
        "drum_and_bugle_corps": "Drum and Bugle Corps",
        "symphony_orchestra": "Symphony Orchestra",
        "string_quintet": "String Quintet",
        "marching_band": "Marching Band",
    }
    for key, label in mapping.items():
        if key in stem:
            return label
    return fallback if fallback and fallback != "All Instruments" else "Marching Band"


def _safe_filename_stem(name, fallback="Instrument"):
    cleaned = re.sub(r"[^A-Za-z0-9 _-]+", "", (name or "")).strip()
    cleaned = "_".join(cleaned.split())
    return cleaned or fallback


def _resolve_part_name(part, index, fallback_instrument="All Instruments"):
    if part.partName and part.partName.strip() and not part.partName.startswith("P"):
        return part.partName.strip()

    inst = part.getInstrument(returnDefault=False)
    if inst:
        for attr in ("partName", "instrumentName", "bestName"):
            val = getattr(inst, attr, None)
            if callable(val):
                val = val()
            if val and str(val).strip() and str(val).strip().lower() != "instrument":
                return str(val).strip()

    # Check MIDI program number if instrument object wasn't named
    for el in part.recurse():
        if isinstance(el, m21.instrument.Instrument):
            name = el.instrumentName or el.partName
            if name and name.strip() and name.strip().lower() != "instrument":
                return name.strip()

    if fallback_instrument and fallback_instrument != "All Instruments":
        return f"{fallback_instrument} {index}"
    return f"Band Part {index}"


def _extract_score_summary(score):
    """Extracts key signature, tempo BPM, and a clean sequence of chord symbols."""
    key_str = "C Major"
    keys = list(score.recurse().getElementsByClass(m21.key.KeySignature))
    if keys:
        try:
            key_str = str(keys[0].asKey())
        except Exception:
            key_str = f"{keys[0].sharps} sharps/flats"

    bpm_str = "120 BPM"
    tempos = list(score.recurse().getElementsByClass(m21.tempo.MetronomeMark))
    if tempos and tempos[0].number:
        bpm_str = f"{int(round(tempos[0].number))} BPM"

    chords_found = []
    try:
        chordified = score.chordify()
        for c in chordified.recurse().getElementsByClass(m21.chord.Chord):
            if len(c.pitches) >= 2:
                try:
                    cs = m21.harmony.chordSymbolFromChord(c)
                    fig = cs.figure
                    if fig and "Cannot" not in fig and (not chords_found or chords_found[-1] != fig):
                        chords_found.append(fig)
                except Exception:
                    pass
            if len(chords_found) >= 16:
                break
    except Exception:
        pass

    return {
        "key": key_str,
        "tempo": bpm_str,
        "chords": chords_found,
    }


def _prepare_score_from_midi(midi_path, target_instrument="All Instruments"):
    score = m21.converter.parse(midi_path)
    song_title = _clean_song_title(midi_path)
    ensemble_name = _detect_ensemble_from_path(midi_path, target_instrument)

    score.metadata = m21.metadata.Metadata()
    score.metadata.title = song_title
    score.metadata.movementName = f"{ensemble_name} Arrangement"
    score.metadata.composer = "HarmoniFlow AI Arranger"

    used_names = {}
    for idx, part in enumerate(score.parts, start=1):
        base_name = _resolve_part_name(part, idx, target_instrument)
        count = used_names.get(base_name, 0) + 1
        used_names[base_name] = count
        resolved_name = f"{base_name} {count}" if count > 1 else base_name

        part.partName = resolved_name
        part.partAbbreviation = resolved_name[:6]
        inst = part.getInstrument(returnDefault=True)
        inst.partName = resolved_name
        inst.instrumentName = resolved_name

        for n in part.recurse().notes:
            if n.isNote:
                n.addLyric(f"{n.name}{n.octave}")

    score.makeNotation(inPlace=True)
    return score


def split_score_by_instrument(score_or_path, output_dir, base_prefix=None):
    """Exports each Part in a music21 Score or score file as a separate MusicXML file."""
    os.makedirs(output_dir, exist_ok=True)
    score = (
        m21.converter.parse(score_or_path)
        if isinstance(score_or_path, (str, os.PathLike))
        else score_or_path
    )

    exported_paths = []
    title = (score.metadata.title if score.metadata and score.metadata.title else "") or ""
    prefix = _safe_filename_stem(base_prefix or title, fallback="Score")

    for idx, part in enumerate(score.parts, start=1):
        part_name = _resolve_part_name(part, idx)
        part.partName = part_name

        single_score = m21.stream.Score()
        single_score.metadata = m21.metadata.Metadata()
        single_score.metadata.title = f"{title or prefix} - {part_name}"
        if score.metadata and score.metadata.composer:
            single_score.metadata.composer = score.metadata.composer

        single_score.insert(0, part)

        inst_stem = _safe_filename_stem(part_name, fallback=f"Part_{idx}")
        out_path = os.path.join(output_dir, f"{prefix}_{idx:02d}_{inst_stem}.xml")
        single_score.write("musicxml", fp=out_path)
        exported_paths.append(out_path)

    return exported_paths


def export_instrument_parts_from_midi(midi_path, output_dir=None, target_instrument="All Instruments"):
    """Parses a MIDI file and writes one MusicXML file per instrument part."""
    try:
        score = _prepare_score_from_midi(midi_path, target_instrument=target_instrument)
        if not output_dir:
            stem = _safe_filename_stem(_clean_song_title(midi_path), fallback="Score")
            output_dir = os.path.join(os.path.dirname(os.path.abspath(midi_path)), f"{stem}_parts")
        paths = split_score_by_instrument(score, output_dir=output_dir, base_prefix=_clean_song_title(midi_path))
        print(f"[NOTATION SPLIT] Exported {len(paths)} instrument part(s) to: {output_dir}")
        return paths
    except Exception as e:
        print(f"[NOTATION ERROR] Failed to split instrument parts: {e}")
        return []


def convert_midi_to_sheet_xml(midi_path, output_xml_path, target_instrument="All Instruments", split_parts=True):
    """Parses the generated MIDI file and converts it into a MusicXML score."""
    print(f"[NOTATION ENGINE] Converting actual MIDI sequence: {os.path.basename(midi_path)}")

    try:
        current_project_dir = os.path.dirname(os.path.abspath(__file__))
        base_dir = os.path.join(current_project_dir, "songs")
        os.makedirs(base_dir, exist_ok=True)

        if not os.path.isabs(output_xml_path) and "songs" not in output_xml_path:
            output_xml_path = os.path.join(base_dir, os.path.basename(output_xml_path))

        score = _prepare_score_from_midi(midi_path, target_instrument=target_instrument)
        score.write('musicxml', fp=output_xml_path)
        print(f"[NOTATION SUCCESS] Real score generated successfully at: {output_xml_path}")

        if split_parts:
            parts_dir = os.path.splitext(output_xml_path)[0] + "_parts"
            split_score_by_instrument(score, output_dir=parts_dir, base_prefix=_clean_song_title(midi_path))

        return True

    except Exception as e:
        print(f"[NOTATION ERROR] Failed to parse actual MIDI: {e}")
        return False


if __name__ == '__main__':
    convert_midi_to_sheet_xml("songs/converted_Jet_lag.mid", "songs/converted_Jet_lag.xml", "All Instruments")