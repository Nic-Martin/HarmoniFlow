# filepath: score_builder.py

import os
import music21 as m21

os.environ.setdefault("MUSIC21_DONT_LOAD_CONFIG", "1")

INSTRUMENT_MAP = {
    "Trumpet Section": m21.instrument.Trumpet,
    "Clarinet Section": m21.instrument.Clarinet,
    "Flute": m21.instrument.Flute,
    "Violin": m21.instrument.Violin,
    "Piano": m21.instrument.Piano,
}

KEY_FIFTHS_MAP = {
    "C Major / A Minor": 0,
    "G Major (1#)": 1,
    "D Major (2#)": 2,
    "A Major (3#)": 3,
    "E Major (4#)": 4,
    "F Major (1b)": -1,
    "Bb Major (2b)": -2,
    "Eb Major (3b)": -3,
    "Ab Major (4b)": -4,
}


def _safe_filename(title: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in (" ", "_", "-") else "_" for ch in (title or ""))
    cleaned = "_".join(cleaned.strip().split())
    return cleaned or "new_Composition"


def build_blank_score(title, composer, instruments, time_sig_str, bpm, key_sig_str, measure_count, has_pickup=False):
    """Generates a blank multi-staff score template with metadata, tempo, key, and measures."""
    try:
        score = m21.stream.Score()
        score.metadata = m21.metadata.Metadata()
        score.metadata.title = title.strip() if title and title.strip() else "Untitled Composition"
        score.metadata.composer = composer.strip() if composer and composer.strip() else "Unknown Composer"

        valid_instruments = [
            inst for inst in (instruments or [])
            if inst and str(inst).strip() and inst != "Select Instrument"
        ]
        if not valid_instruments:
            valid_instruments = ["Piano"]

        try:
            bpm_val = max(20, min(300, int(bpm)))
        except (ValueError, TypeError):
            bpm_val = 120

        try:
            total_m = max(1, min(512, int(measure_count)))
        except (ValueError, TypeError):
            total_m = 16

        ts_text = (time_sig_str or "4/4").strip()
        fifths = KEY_FIFTHS_MAP.get(key_sig_str, 0)

        for inst_name in valid_instruments:
            part = m21.stream.Part()
            inst_class = INSTRUMENT_MAP.get(inst_name, m21.instrument.Instrument)
            part.insert(0, inst_class())
            part.partName = inst_name

            m1 = m21.stream.Measure(number=1)
            ts = m21.meter.TimeSignature(ts_text)
            m1.insert(0, ts)
            m1.insert(0, m21.tempo.MetronomeMark(number=bpm_val))
            m1.insert(0, m21.key.KeySignature(fifths))

            bar_ql = float(ts.barDuration.quarterLength or 4.0)
            if has_pickup:
                pickup_ql = max(0.5, bar_ql / 2.0)
                m1.append(m21.note.Rest(quarterLength=pickup_ql))
            else:
                m1.append(m21.note.Rest(quarterLength=bar_ql))
            part.append(m1)

            for m_num in range(2, total_m + 1):
                m = m21.stream.Measure(number=m_num)
                m.append(m21.note.Rest(quarterLength=bar_ql))
                part.append(m)

            part.makeNotation(inPlace=True)
            score.append(part)

        os.makedirs("songs", exist_ok=True)
        filename = _safe_filename(title)
        output_filepath = os.path.join("songs", f"{filename}.xml")

        score.write("musicxml", fp=output_filepath)
        print(f"[SCORE BUILDER] Blank composition score created: {output_filepath}")
        return output_filepath

    except Exception as e:
        print(f"[SCORE BUILDER] Score generation error: {e}")
        return None


def append_note_to_score(xml_filepath, pitch_str, duration_val):
    """Appends a new musical note into measure 1, replacing placeholder rests as needed."""
    try:
        if not xml_filepath or not os.path.exists(xml_filepath):
            print(f"[SCORE BUILDER] File not found: {xml_filepath}")
            return False

        score = m21.converter.parse(xml_filepath)
        if not score.parts:
            print("[SCORE BUILDER] Error: Score has no parts.")
            return False

        part = score.parts[0]
        target_measure = part.measure(1)
        if target_measure is None:
            measures = list(part.getElementsByClass(m21.stream.Measure))
            target_measure = measures[0] if measures else None

        if target_measure is None:
            print("[SCORE BUILDER] Error: Could not locate measure 1.")
            return False

        for element in list(target_measure.notesAndRests):
            if element.isRest and float(element.quarterLength or 0) >= 2.0:
                target_measure.remove(element)

        new_note = m21.note.Note(pitch_str)
        new_note.quarterLength = float(duration_val)
        target_measure.append(new_note)

        part.makeNotation(inPlace=True)
        score.write("musicxml", fp=xml_filepath)
        print(f"[SCORE BUILDER] Successfully appended Note {pitch_str} ({duration_val}) to measure 1.")
        return True
    except Exception as e:
        print(f"[SCORE BUILDER] Error appending note: {e}")
        return False


def append_rest_to_score(xml_filepath, duration_val):
    """Appends a rest into measure 1."""
    try:
        if not xml_filepath or not os.path.exists(xml_filepath):
            print(f"[SCORE BUILDER] File not found: {xml_filepath}")
            return False

        score = m21.converter.parse(xml_filepath)
        if not score.parts:
            print("[SCORE BUILDER] Error: Score has no parts.")
            return False

        part = score.parts[0]
        target_measure = part.measure(1)
        if target_measure is None:
            measures = list(part.getElementsByClass(m21.stream.Measure))
            target_measure = measures[0] if measures else None

        if target_measure is None:
            print("[SCORE BUILDER] Error: Could not locate measure 1.")
            return False

        new_rest = m21.note.Rest()
        new_rest.quarterLength = float(duration_val)
        target_measure.append(new_rest)

        part.makeNotation(inPlace=True)
        score.write("musicxml", fp=xml_filepath)
        print(f"[SCORE BUILDER] Successfully appended Rest ({duration_val}) to measure 1.")
        return True
    except Exception as e:
        print(f"[SCORE BUILDER] Error appending rest: {e}")
        return False