# filepath: native_score_editor.py

import os
import shutil
import threading
import webbrowser
import xml.etree.ElementTree as ET
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlencode

from kivy.app import App
from kivy.clock import Clock
from kivy.graphics import Color, Ellipse, Line, Rectangle
from kivy.graphics.stencil_instructions import StencilPop, StencilPush, StencilUse
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.filechooser import FileChooserListView
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.spinner import Spinner
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget
from kivy.utils import platform as kivy_platform
import music21

PITCH_MAP = {
    -25: ("D", 4),
    -15: ("E", 4),
    -5: ("F", 4),
    5: ("G", 4),
    15: ("A", 4),
    25: ("B", 4),
    35: ("C", 5),
    45: ("D", 5),
    55: ("E", 5),
    65: ("F", 5),
    75: ("G", 5),
    85: ("A", 5),
}

# Dimensions in inches (width, height) for Portrait
PAPER_DIMENSIONS_INCHES = {
    "Short Bond": (8.5, 11.0),
    "A4": (8.27, 11.69),
    "Legal": (8.5, 14.0),
}

PAGE_GAP = 36.0
PAGE_MARGIN_Y = 24.0


def get_page_aspect_ratio(paper_size="Short Bond", page_orientation="Portrait"):
    """Returns width / height aspect ratio for the chosen paper size and page orientation."""
    w_in, h_in = PAPER_DIMENSIONS_INCHES.get(paper_size, PAPER_DIMENSIONS_INCHES["Short Bond"])
    if page_orientation == "Landscape":
        w_in, h_in = h_in, w_in
    return w_in / h_in


class _QuietFileHandler(SimpleHTTPRequestHandler):
    """Serve only exported score files without noisy request logs."""

    def log_message(self, format_string, *args):
        pass


class PlacedSymbol(Widget):
    """Renders notes, rests, individual accidentals, lyrics, and chords inside staff boundaries."""

    def __init__(self, measure_idx, rel_x_ratio, vertical_offset, symbol_type="Note", duration=1.0,
                 accidental="None", lyric="", chord="", staff_y=240, line_spacing=14, canvas_ref=None, **kwargs):
        super().__init__(**kwargs)
        self.measure_idx = measure_idx
        self.rel_x_ratio = max(0.08, min(0.82, float(rel_x_ratio)))
        self.vertical_offset = vertical_offset
        self.symbol_type = symbol_type
        self.duration = duration
        self.accidental = accidental
        self.lyric = lyric
        self.chord = chord
        self.canvas_ref = canvas_ref
        self.sub_labels = []
        self.size = (40, 50)
        self.update_position(staff_y)

    def update_position(self, staff_y):
        y = staff_y + self.vertical_offset
        self.pos = (self.x, y - 6)

        self.canvas.clear()
        with self.canvas:
            rel_offset = self.vertical_offset
            if self.symbol_type == "Note" and abs(rel_offset) > 30:
                Color(0.25, 0.25, 0.25, 1)
                steps = int(round((abs(rel_offset) - 30) / 14))
                direction = 1 if rel_offset > 0 else -1
                for s in range(1, steps + 1):
                    ledger_y = staff_y + (direction * (30 + (s * 14)))
                    Line(points=[self.pos[0] - 4, ledger_y, self.pos[0] + 20, ledger_y], width=1.5)

            if self.symbol_type == "Note":
                Color(0.1, 0.1, 0.1, 1)
                if self.accidental == "Flat":
                    ax, ay = self.pos[0] - 12, self.pos[1]
                    Line(points=[ax, ay - 6, ax, ay + 16], width=1.5)
                    Line(points=[ax, ay, ax + 6, ay - 2, ax + 6, ay + 4, ax, ay + 6], width=1.2)
                elif self.accidental == "Sharp":
                    ax, ay = self.pos[0] - 12, self.pos[1] + 2
                    Line(points=[ax, ay - 4, ax, ay + 12], width=1.3)
                    Line(points=[ax + 6, ay - 6, ax + 6, ay + 10], width=1.3)
                    Line(points=[ax - 3, ay + 1, ax + 9, ay - 3], width=1.2)
                    Line(points=[ax - 3, ay + 7, ax + 9, ay + 3], width=1.2)
                elif self.accidental == "Natural":
                    ax, ay = self.pos[0] - 12, self.pos[1]
                    Line(points=[ax, ay - 6, ax, ay + 10], width=1.3)
                    Line(points=[ax + 5, ay - 2, ax + 5, ay + 14], width=1.3)
                    Line(points=[ax, ay + 2, ax + 5, ay - 2], width=1.2)
                    Line(points=[ax, ay + 6, ax + 5, ay + 2], width=1.2)

                Color(0.1, 0.1, 0.1, 1)
                Ellipse(pos=(self.pos[0], self.pos[1]), size=(15, 11))

                if self.duration >= 2.0:
                    Color(1, 1, 1, 1)
                    Ellipse(pos=(self.pos[0] + 3, self.pos[1] + 2), size=(9, 7))

                if self.duration < 4.0:
                    Color(0.1, 0.1, 0.1, 1)
                    Line(points=[self.pos[0] + 13, self.pos[1] + 5, self.pos[0] + 13, self.pos[1] + 34], width=1.8)

                if self.duration <= 0.5:
                    Line(points=[self.pos[0] + 13, self.pos[1] + 34, self.pos[0] + 21, self.pos[1] + 24], width=1.8)

            elif self.symbol_type == "Rest":
                Color(0.1, 0.1, 0.1, 1)
                if self.duration == 4.0:
                    Rectangle(pos=(self.pos[0], staff_y + 14 - 2), size=(13, 4))
                elif self.duration == 2.0:
                    Rectangle(pos=(self.pos[0], staff_y - 2), size=(13, 4))
                elif self.duration == 1.0:
                    Line(points=[self.pos[0] + 4, self.pos[1] + 14, self.pos[0] + 9, self.pos[1] + 7, self.pos[0] + 4, self.pos[1] + 2], width=2.0)
                else:
                    Line(points=[self.pos[0] + 7, self.pos[1] + 12, self.pos[0] + 2, self.pos[1] + 2], width=1.8)
                    Ellipse(pos=(self.pos[0] + 5, self.pos[1] + 8), size=(5, 5))

            elif self.symbol_type == "KeyAccidental":
                Color(0.1, 0.1, 0.1, 1)
                ax, ay = self.pos[0], y
                if self.accidental == "Flat":
                    Line(points=[ax, ay - 8, ax, ay + 14], width=1.5)
                    Line(points=[ax, ay - 2, ax + 5, ay - 4, ax + 5, ay + 2, ax, ay + 4], width=1.2)
                elif self.accidental == "Sharp":
                    Line(points=[ax, ay - 6, ax, ay + 10], width=1.3)
                    Line(points=[ax + 5, ay - 8, ax + 5, ay + 8], width=1.3)
                    Line(points=[ax - 2, ay, ax + 7, ay - 3], width=1.2)
                    Line(points=[ax - 2, ay + 4, ax + 7, ay + 1], width=1.2)

        for lbl in self.sub_labels:
            if lbl in self.children:
                self.remove_widget(lbl)
        self.sub_labels.clear()

        if self.lyric and self.symbol_type == "Note":
            l_lbl = Label(text=self.lyric, pos=(self.pos[0] - 10, staff_y - 44),
                          font_size='10sp', color=(0.1, 0.1, 0.1, 1), bold=True, size_hint=(None, None), size=(44, 18))
            self.add_widget(l_lbl)
            self.sub_labels.append(l_lbl)

        if self.chord and self.symbol_type == "Note":
            c_lbl = Label(text=self.chord, pos=(self.pos[0] - 10, staff_y + 32),
                          font_size='10sp', color=(0.1, 0.1, 0.1, 1), bold=True, size_hint=(None, None), size=(44, 18))
            self.add_widget(c_lbl)
            self.sub_labels.append(c_lbl)

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos) and self.canvas_ref:
            if self.canvas_ref.editor.is_read_only:
                return False
            if self.canvas_ref.editor.active_symbol_type == "Eraser":
                self.canvas_ref.remove_symbol(self)
                return True
        return super().on_touch_down(touch)


class ScoreStaffCanvas(Widget):
    """Multi-page printable sheet layout with distinct page sheets and gaps between pages."""

    _renderer_server = None
    _renderer_thread = None

    def __init__(self, editor_reference, song_title="", composer="", time_sig="4/4",
                 total_measures=16, paper_size="Short Bond", page_orientation="Portrait", **kwargs):
        super().__init__(**kwargs)
        self.editor = editor_reference
        self.song_title = song_title
        self.composer = composer
        self.time_sig = time_sig
        self.total_measures = int(total_measures)
        self.paper_size = paper_size
        self.page_orientation = page_orientation
        self.part_names = []
        self.source_xml_path = ""
        self.extracted_chords = []
        self.extracted_key = ""
        self.extracted_tempo = ""
        self.symbols = []
        self.redo_stack = []
        self.ui_labels = []
        self.line_spacing = 14
        self.system_spacing = 150
        self._redraw_event = None
        self._wizard_seeded = False

        self.bind(pos=self._schedule_redraw, size=self._schedule_redraw)

    def get_layout_metrics(self):
        """Computes sheet width, height, measures per page, and total pages."""
        margin_x = max(20, min(40, int(self.width * 0.05)))
        avail_w = max(240, self.width - (margin_x * 2))
        ratio = get_page_aspect_ratio(self.paper_size, self.page_orientation)

        measures_per_sys = 4 if self.page_orientation == "Portrait" else 6
        systems_per_page = 4 if self.page_orientation == "Portrait" else 3
        measures_per_page = measures_per_sys * systems_per_page

        if self.page_orientation == "Landscape":
            page_w = min(avail_w, 980)
        else:
            page_w = min(avail_w, 760)

        sheet_h = max(page_w / ratio, 145 + (systems_per_page * self.system_spacing))
        num_pages = max(1, (max(1, self.total_measures) + measures_per_page - 1) // measures_per_page)
        total_h = (num_pages * sheet_h) + ((num_pages - 1) * PAGE_GAP) + (PAGE_MARGIN_Y * 2)
        page_x = self.x + (self.width - page_w) * 0.5
        return page_x, page_w, sheet_h, total_h, measures_per_sys, measures_per_page, num_pages

    def _extract_xml_harmony_and_meta(self, xml_path):
        """Reads key, tempo, part names, and chord symbols from a MusicXML file."""
        chords = []
        key_name = ""
        tempo_str = ""
        part_names = []
        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()
            for sp in root.findall(".//part-list/score-part"):
                pname = sp.findtext("part-name") or ""
                iname = sp.findtext("score-instrument/instrument-name") or ""
                chosen = pname.strip() if pname.strip() and not pname.strip().startswith("P") else iname.strip()
                if chosen:
                    part_names.append(chosen)

            fifths_txt = root.findtext(".//attributes/key/fifths")
            if fifths_txt is not None:
                fifths_map = {
                    "-7": "Cb Major", "-6": "Gb Major", "-5": "Db Major", "-4": "Ab Major",
                    "-3": "Eb Major", "-2": "Bb Major", "-1": "F Major", "0": "C Major",
                    "1": "G Major", "2": "D Major", "3": "A Major", "4": "E Major",
                    "5": "B Major", "6": "F# Major", "7": "C# Major",
                }
                key_name = fifths_map.get(fifths_txt.strip(), "")

            sound_el = root.find(".//sound[@tempo]")
            bpm_raw = sound_el.attrib.get("tempo") if sound_el is not None else root.findtext(".//metronome/per-minute")
            if bpm_raw:
                try:
                    tempo_str = f"{int(round(float(bpm_raw)))} BPM"
                except ValueError:
                    pass

            for h in root.findall(".//harmony"):
                step = (h.findtext("root/root-step") or "").strip()
                alter = (h.findtext("root/root-alter") or "0").strip()
                kind_el = h.find("kind")
                kind = (kind_el.attrib.get("text") if kind_el is not None else "") or (h.findtext("kind") or "").strip()
                if step:
                    acc = "#" if alter == "1" else ("b" if alter == "-1" else "")
                    clean_kind = "" if kind in ("major", "none", "") else ("m" if kind == "minor" else kind)
                    label = f"{step}{acc}{clean_kind}"
                    if not chords or chords[-1] != label:
                        chords.append(label)
        except Exception as err:
            print(f"[VIEWER] XML metadata scan notice: {err}")

        return part_names, key_name, tempo_str, chords

    def load_from_musicxml(self, xml_path):
        """Parses a MusicXML file and distributes notes and rests across all measures."""
        if not xml_path or not os.path.exists(xml_path):
            return
        self.source_xml_path = os.path.abspath(xml_path)
        parsed_parts, parsed_key, parsed_tempo, parsed_chords = self._extract_xml_harmony_and_meta(self.source_xml_path)
        self.extracted_key = parsed_key
        self.extracted_tempo = parsed_tempo
        self.extracted_chords = parsed_chords

        try:
            score = music21.converter.parse(xml_path)
            if score.metadata:
                if score.metadata.title and not self._wizard_seeded:
                    self.song_title = str(score.metadata.title)
                    if self.editor:
                        self.editor.song_title = self.song_title
                if score.metadata.composer and not self._wizard_seeded:
                    self.composer = str(score.metadata.composer)
                    if self.editor:
                        self.editor.composer = self.composer

            for sym in list(self.symbols):
                if sym in self.children:
                    self.remove_widget(sym)
            self.symbols.clear()
            self.redo_stack.clear()

            parts = list(score.parts)
            self.part_names = parsed_parts or [p.partName for p in parts if getattr(p, "partName", None)]
            if not parts:
                self._schedule_redraw()
                return

            ts_list = list(parts[0].recurse().getElementsByClass(music21.meter.TimeSignature))
            beats_per_bar = 4.0
            if ts_list:
                self.time_sig = f"{ts_list[0].numerator}/{ts_list[0].denominator}"
                beats_per_bar = float(ts_list[0].barDuration.quarterLength) or 4.0
                if self.editor:
                    self.editor.time_sig = self.time_sig

            midi_to_offset = {
                60: -35, 62: -25, 64: -15, 65: -5, 67: 5, 69: 15,
                71: 25, 72: 35, 74: 45, 76: 55, 77: 65, 79: 75, 81: 85,
            }

            max_measure = 1
            current_beat = 0.0
            max_symbols = 512

            for elem in parts[0].recurse().notesAndRests:
                if len(self.symbols) >= max_symbols:
                    break
                dur = float(elem.quarterLength) if elem.quarterLength else 1.0
                snapped_dur = min([4.0, 2.0, 1.0, 0.5], key=lambda d: abs(d - dur))

                m_idx = int(current_beat // beats_per_bar)
                beat_in_bar = current_beat % beats_per_bar
                rel_ratio = 0.10 + (beat_in_bar / max(beats_per_bar, 1.0)) * 0.68
                max_measure = max(max_measure, m_idx + 1)

                if elem.isNote:
                    midi_val = int(elem.pitch.midi)
                    closest_midi = min(midi_to_offset.keys(), key=lambda m: abs(m - midi_val))
                    v_off = midi_to_offset[closest_midi]
                    acc = "None"
                    if elem.pitch.accidental:
                        acc_map = {"sharp": "Sharp", "flat": "Flat", "natural": "Natural"}
                        acc = acc_map.get(elem.pitch.accidental.name, "None")
                    lyric_txt = elem.lyrics[0].text if elem.lyrics else ""
                    sym = PlacedSymbol(
                        measure_idx=m_idx,
                        rel_x_ratio=rel_ratio,
                        vertical_offset=v_off,
                        symbol_type="Note",
                        duration=snapped_dur,
                        accidental=acc,
                        lyric=lyric_txt,
                        staff_y=240,
                        line_spacing=self.line_spacing,
                        canvas_ref=self,
                    )
                    self.symbols.append(sym)
                elif elem.isRest:
                    sym = PlacedSymbol(
                        measure_idx=m_idx,
                        rel_x_ratio=rel_ratio,
                        vertical_offset=0,
                        symbol_type="Rest",
                        duration=snapped_dur,
                        staff_y=240,
                        line_spacing=self.line_spacing,
                        canvas_ref=self,
                    )
                    self.symbols.append(sym)

                current_beat += snapped_dur

            needed_measures = max(16, ((max_measure + 3) // 4) * 4)
            self.total_measures = needed_measures
            if self.editor:
                self.editor.total_measures = needed_measures
                self.editor._resize_score_canvas(self.editor.score_scroll, self.editor.score_scroll.size)

            self._schedule_redraw()
        except Exception as err:
            print(f"[VIEWER] Could not parse XML notes into canvas: {err}")
            self._schedule_redraw()

    def _schedule_redraw(self, *args):
        if self._redraw_event is not None:
            self._redraw_event.cancel()
        self._redraw_event = Clock.schedule_once(self._draw_document_page, 0.02)

    def _clear_ui_labels(self):
        for child in list(self.children):
            if not isinstance(child, PlacedSymbol):
                self.remove_widget(child)
        self.ui_labels.clear()

    def _draw_document_page(self, *args):
        self._redraw_event = None

        self.canvas.before.clear()
        self.canvas.after.clear()
        self._clear_ui_labels()

        if self.width < 180 or self.height < 180:
            return

        app = App.get_running_app()
        if not self._wizard_seeded and app and hasattr(app, 'wizard_data') and app.wizard_data:
            self.song_title = app.wizard_data.get('title', self.song_title)
            self.composer = app.wizard_data.get('composer', self.composer)
            self.time_sig = app.wizard_data.get('time_sig', self.time_sig)
            if 'measures' in app.wizard_data:
                try:
                    self.total_measures = int(app.wizard_data['measures'])
                    if self.editor:
                        self.editor.total_measures = self.total_measures
                except (ValueError, TypeError):
                    pass
            self._wizard_seeded = True
        elif self.editor:
            if self.editor.song_title:
                self.song_title = self.editor.song_title
            if self.editor.composer:
                self.composer = self.editor.composer
            if self.editor.time_sig:
                self.time_sig = self.editor.time_sig
            if self.editor.total_measures:
                self.total_measures = int(self.editor.total_measures)
            self.paper_size = self.editor.paper_size
            self.page_orientation = self.editor.page_orientation

        display_title = self.song_title.strip() if self.song_title else "NEW SCORE"
        display_composer = self.composer.strip() if self.composer else "COMPOSER"

        self.system_spacing = 150
        page_x, page_w, sheet_h, _, measures_per_sys, measures_per_page, num_pages = self.get_layout_metrics()
        self.page_x = page_x
        self.page_w = page_w
        self.sheet_h = sheet_h
        self._measures_per_sys = measures_per_sys
        self._measures_per_page = measures_per_page

        staff_left = page_x + 35
        staff_right = page_x + page_w - 30
        bars_start_x = page_x + 88
        measure_width = max(42.0, (staff_right - bars_start_x) / measures_per_sys)
        clef_type = self.editor.active_clef if self.editor else "Treble"
        top_canvas_y = self.y + self.height - PAGE_MARGIN_Y

        sys_y_by_measure = {}

        with self.canvas.before:
            Color(0.18, 0.19, 0.23, 1)
            Rectangle(pos=self.pos, size=self.size)

            for p_idx in range(num_pages):
                p_top = top_canvas_y - (p_idx * (sheet_h + PAGE_GAP))
                p_bottom = p_top - sheet_h

                # Drop shadow + distinct white page sheet with gap between pages
                Color(0.08, 0.08, 0.1, 0.55)
                Rectangle(pos=(page_x + 4, p_bottom - 4), size=(page_w, sheet_h))

                Color(1.0, 1.0, 1.0, 1)
                Rectangle(pos=(page_x, p_bottom), size=(page_w, sheet_h))

                Color(0.68, 0.68, 0.68, 1)
                Line(rectangle=(page_x, p_bottom, page_w, sheet_h), width=1.4)

                # Clip page contents inside the white sheet bounds so nothing leaks onto dark background
                StencilPush()
                Rectangle(pos=(page_x, p_bottom), size=(page_w, sheet_h))
                StencilUse()

                title_lbl = Label(
                    text=display_title.upper() if p_idx == 0 else f"{display_title.upper()} (Cont.)",
                    font_size='18sp' if p_idx == 0 else '13sp',
                    bold=True,
                    color=(0.1, 0.1, 0.1, 1),
                    halign='center',
                    valign='middle',
                    size_hint=(None, None),
                    size=(page_w - 20, 28),
                )
                title_lbl.text_size = title_lbl.size
                title_lbl.pos = (page_x + 10, p_top - 42)
                self.add_widget(title_lbl)
                self.ui_labels.append(title_lbl)

                meta_text = (
                    f"Composer: {display_composer}   |   Time: {self.time_sig}   |   {self.paper_size} ({self.page_orientation})"
                    if p_idx == 0
                    else f"{display_composer}   |   Time: {self.time_sig}"
                )
                composer_lbl = Label(
                    text=meta_text,
                    font_size='11sp',
                    color=(0.28, 0.28, 0.28, 1),
                    halign='left',
                    valign='middle',
                    size_hint=(None, None),
                    size=(page_w - 70, 20),
                )
                composer_lbl.text_size = composer_lbl.size
                composer_lbl.pos = (page_x + 35, p_top - 66)
                self.add_widget(composer_lbl)
                self.ui_labels.append(composer_lbl)

                page_lbl = Label(
                    text=f"Page {p_idx + 1} of {num_pages}",
                    font_size='10sp',
                    color=(0.42, 0.42, 0.42, 1),
                    halign='right',
                    valign='middle',
                    size_hint=(None, None),
                    size=(120, 18),
                )
                page_lbl.text_size = page_lbl.size
                page_lbl.pos = (page_x + page_w - 150, p_bottom + 8)
                self.add_widget(page_lbl)
                self.ui_labels.append(page_lbl)

                page_start_m = p_idx * measures_per_page
                page_end_m = min(self.total_measures, page_start_m + measures_per_page)
                page_measures = max(0, page_end_m - page_start_m)
                page_systems = max(1, (page_measures + measures_per_sys - 1) // measures_per_sys)
                first_sys_y = p_top - 122

                for local_sys in range(page_systems):
                    sys_y = first_sys_y - (local_sys * self.system_spacing)
                    sys_start_m = page_start_m + (local_sys * measures_per_sys)
                    bars_in_this_sys = min(measures_per_sys, page_end_m - sys_start_m)
                    if bars_in_this_sys <= 0:
                        continue

                    for b_offset in range(bars_in_this_sys):
                        sys_y_by_measure[sys_start_m + b_offset] = (sys_y, b_offset)

                    row_end_x = bars_start_x + (bars_in_this_sys * measure_width)
                    bottom_staff = sys_y - (2 * self.line_spacing)
                    top_staff = sys_y + (2 * self.line_spacing)

                    Color(0.15, 0.15, 0.15, 1)
                    for i in range(5):
                        line_y = sys_y + ((i - 2) * self.line_spacing)
                        Line(points=[staff_left, line_y, row_end_x, line_y], width=1.6)

                    cx = staff_left + 16
                    cy = sys_y
                    if clef_type == "Treble":
                        Line(points=[cx, cy - 22, cx, cy + 26], width=2.0)
                        Line(points=[cx - 5, cy - 12, cx + 7, cy - 4, cx - 3, cy + 8, cx - 7, cy - 2], width=1.5)
                        Ellipse(pos=(cx - 5, cy - 14), size=(6, 6))
                    elif clef_type == "Bass":
                        Ellipse(pos=(cx - 4, cy + 4), size=(5, 5))
                        Ellipse(pos=(cx + 14, cy + 8), size=(4, 4))
                        Ellipse(pos=(cx + 14, cy - 2), size=(4, 4))
                        Line(points=[cx - 4, cy + 6, cx + 8, cy + 12, cx + 14, cy + 4, cx + 6, cy - 8, cx - 2, cy - 12], width=1.8)
                    elif clef_type == "Alto":
                        Line(points=[cx - 4, cy - 16, cx - 4, cy + 16], width=1.8)
                        Line(points=[cx - 4, cy, cx + 6, cy + 8], width=1.4)
                        Line(points=[cx - 4, cy, cx + 6, cy - 8], width=1.4)

                    for m in range(bars_in_this_sys + 1):
                        current_m_num = sys_start_m + m + 1
                        bar_x = bars_start_x + (m * measure_width)
                        Color(0.15, 0.15, 0.15, 1)
                        Line(points=[bar_x, bottom_staff, bar_x, top_staff], width=1.8)

                        if current_m_num == self.total_measures + 1:
                            Line(points=[bar_x + 5, bottom_staff, bar_x + 5, top_staff], width=2.2)

                        if m < bars_in_this_sys:
                            num_x = min(bar_x + 4, page_x + page_w - 34)
                            num_x = max(page_x + 4, num_x)
                            m_lbl = Label(
                                text=str(current_m_num),
                                font_size='10sp',
                                color=(0.25, 0.25, 0.25, 1),
                                halign='left',
                                valign='bottom',
                                size_hint=(None, None),
                                size=(28, 16),
                            )
                            m_lbl.text_size = m_lbl.size
                            m_lbl.pos = (num_x, top_staff + 4)
                            self.add_widget(m_lbl)
                            self.ui_labels.append(m_lbl)

                StencilPop()

        self._sys_y_by_measure = sys_y_by_measure
        self._bar_x_by_measure = {
            m_idx: bars_start_x + (b_off * measure_width)
            for m_idx, (_, b_off) in sys_y_by_measure.items()
        }
        self._measure_width = measure_width

        for sym in self.symbols:
            if sym.measure_idx not in sys_y_by_measure:
                continue
            sys_y, b_off = sys_y_by_measure[sym.measure_idx]
            bar_x = bars_start_x + (b_off * measure_width)
            usable_w = max(18.0, measure_width - 26.0)
            sym.x = bar_x + 10.0 + (sym.rel_x_ratio * usable_w)
            sym.update_position(sys_y)
            if sym not in self.children:
                self.add_widget(sym)

        if self.editor and self.editor.is_read_only:
            active_user = getattr(app, 'active_user', 'MEMBER') if app else 'MEMBER'
            wm_text = (
                f"ARRANGED SCORE (READ-ONLY) - {active_user.upper()}"
                if getattr(self.editor, "read_only_conversion", False)
                else f"CONFIDENTIAL - ASSIGNED TO: {active_user.upper()}"
            )
            wm_lbl = Label(
                text=wm_text,
                font_size='18sp',
                bold=True,
                color=(0.85, 0.15, 0.15, 0.18),
                halign='center',
                valign='middle',
                size_hint=(None, None),
                size=(page_w - 20, 40),
            )
            wm_lbl.text_size = wm_lbl.size
            wm_lbl.pos = (page_x + 10, top_canvas_y - (sheet_h * 0.5))
            self.add_widget(wm_lbl)
            self.ui_labels.append(wm_lbl)

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            if self.editor and self.editor.is_read_only:
                return super().on_touch_down(touch)

            sys_map = getattr(self, "_sys_y_by_measure", {})
            bar_map = getattr(self, "_bar_x_by_measure", {})
            measure_width = getattr(self, "_measure_width", 120.0)
            if not sys_map:
                return super().on_touch_down(touch)

            if self.editor.active_symbol_type == "Eraser":
                return super().on_touch_down(touch)

            local_x, local_y = touch.x, touch.y
            best_m = None
            best_dist = float("inf")
            for m_idx, (sy, b_off) in sys_map.items():
                bx = bar_map[m_idx]
                if bx <= local_x <= bx + measure_width:
                    dist = abs(sy - local_y)
                    if dist < best_dist:
                        best_dist = dist
                        best_m = m_idx

            if best_m is None:
                best_m = min(sys_map.keys(), key=lambda m: abs(sys_map[m][0] - local_y))

            closest_sys_y, b_off = sys_map[best_m]
            bar_x = bar_map[best_m]
            usable_w = max(18.0, measure_width - 26.0)
            rel_x_ratio = (local_x - (bar_x + 10.0)) / usable_w

            symbol_type = self.editor.active_symbol_type
            duration = self.editor.active_duration
            accidental = self.editor.active_accidental
            lyric = self.editor.lyric_input.text.strip()
            chord = self.editor.chord_input.text.strip()

            self.add_symbol_at(
                measure_idx=best_m,
                rel_x_ratio=rel_x_ratio,
                local_y=local_y,
                symbol_type=symbol_type,
                duration=duration,
                accidental=accidental,
                lyric=lyric,
                chord=chord,
                closest_sys_y=closest_sys_y,
            )

            self.redo_stack.clear()
            return True
        return super().on_touch_down(touch)

    def add_symbol_at(self, measure_idx, rel_x_ratio, local_y, symbol_type="Note", duration=1.0,
                      accidental="None", lyric="", chord="", closest_sys_y=240):
        if symbol_type == "Note":
            rel_y = local_y - closest_sys_y
            half_step = self.line_spacing / 2
            closest_offset = round(rel_y / half_step) * half_step
            closest_offset = max(-40, min(70, closest_offset))
            vertical_offset = closest_offset
        else:
            vertical_offset = 0

        symbol = PlacedSymbol(
            measure_idx=measure_idx,
            rel_x_ratio=rel_x_ratio,
            vertical_offset=vertical_offset,
            symbol_type=symbol_type,
            duration=duration,
            accidental=accidental,
            lyric=lyric,
            chord=chord,
            staff_y=closest_sys_y,
            line_spacing=self.line_spacing,
            canvas_ref=self,
        )
        self.add_widget(symbol)
        self.symbols.append(symbol)
        self._schedule_redraw()

    def add_measure(self):
        if self.editor and self.editor.is_read_only:
            return
        self.total_measures += 4
        if self.editor:
            self.editor.total_measures = self.total_measures
            self.editor._resize_score_canvas(self.editor.score_scroll, self.editor.score_scroll.size)
        app = App.get_running_app()
        if app and hasattr(app, 'wizard_data') and app.wizard_data:
            app.wizard_data['measures'] = str(self.total_measures)
        self._draw_document_page()

    def undo(self):
        if self.editor and self.editor.is_read_only:
            return
        if self.symbols:
            sym = self.symbols.pop()
            self.remove_widget(sym)
            self.redo_stack.append(sym)

    def redo(self):
        if self.editor and self.editor.is_read_only:
            return
        if self.redo_stack:
            sym = self.redo_stack.pop()
            self.add_widget(sym)
            self.symbols.append(sym)

    def remove_symbol(self, symbol):
        if self.editor and self.editor.is_read_only:
            return
        if symbol in self.symbols:
            self.symbols.remove(symbol)
            self.remove_widget(symbol)

    def _can_export_score(self):
        """Allow export for editable scores or for Maestro (Admin) users."""
        if not self.editor:
            return True
        if not self.editor.is_read_only:
            return True
        app = App.get_running_app()
        return getattr(app, "active_role", "") == "Admin"

    def _build_music21_score(self):
        if not self._can_export_score():
            print("[SECURITY] Export disabled in read-only/protected viewer.")
            return None

        sorted_symbols = sorted(self.symbols, key=lambda s: (s.measure_idx, s.rel_x_ratio))
        score = music21.stream.Score()
        score.metadata = music21.metadata.Metadata()
        score.metadata.title = self.song_title or "NEW SCORE"
        score.metadata.composer = self.composer or "COMPOSER"

        part = music21.stream.Part()
        if self.editor.active_clef == "Bass":
            part.append(music21.clef.BassClef())
        elif self.editor.active_clef == "Alto":
            part.append(music21.clef.AltoClef())
        else:
            part.append(music21.clef.TrebleClef())

        part.append(music21.meter.TimeSignature(self.editor.time_sig))

        for sym in sorted_symbols:
            if sym.symbol_type == "Note":
                closest_offset = sym.vertical_offset
                if closest_offset in PITCH_MAP:
                    step, octave = PITCH_MAP[closest_offset]
                    pitch_str = f"{step}{octave}"
                else:
                    pitch_str = "G4"

                if sym.accidental == "Flat":
                    pitch_str = pitch_str[0] + "-" + pitch_str[1]
                elif sym.accidental == "Sharp":
                    pitch_str = pitch_str[0] + "#" + pitch_str[1]
                element = music21.note.Note(pitch_str)
                element.quarterLength = sym.duration
                if sym.lyric:
                    element.addLyric(sym.lyric)
                part.append(element)
            elif sym.symbol_type == "Rest":
                element = music21.note.Rest()
                element.quarterLength = sym.duration
                part.append(element)

        score.append(part)
        return score

    def _infer_ensemble_name(self):
        app = App.get_running_app()
        wizard_data = getattr(app, "wizard_data", {}) if app else {}
        if wizard_data and wizard_data.get("ensemble"):
            return str(wizard_data["ensemble"])
        combined_hint = f"{self.source_xml_path} {self.song_title}".lower()
        if "drum_and_bugle" in combined_hint or "drum and bugle" in combined_hint:
            return "Drum and Bugle Corps"
        if "symphony_orchestra" in combined_hint or "symphony orchestra" in combined_hint:
            return "Symphony Orchestra"
        if "string_quintet" in combined_hint or "string quintet" in combined_hint:
            return "String Quintet"
        if "marching_band" in combined_hint or "marching band" in combined_hint:
            return "Marching Band"
        if len(self.part_names) > 1:
            return f"Band Ensemble ({len(self.part_names)} Parts)"
        return "Marching Band"

    def _collect_chords_str(self):
        typed_chords = [
            s.chord.strip() for s in self.symbols
            if getattr(s, "chord", "") and s.chord.strip()
        ]
        dedup_typed = []
        for c in typed_chords:
            if not dedup_typed or dedup_typed[-1] != c:
                dedup_typed.append(c)
        chords = dedup_typed or list(self.extracted_chords)
        return ",".join(chords[:24])

    def show_export_options(self, *_args):
        if not self._can_export_score():
            print("[SECURITY] Export disabled in read-only/protected viewer.")
            return
        box = BoxLayout(orientation="vertical", spacing=10, padding=12)
        box.add_widget(Label(text="Choose how to save your score", size_hint_y=None, height=36))
        options = BoxLayout(spacing=8, size_hint_y=None, height=48)
        pdf_btn = Button(text="PDF via Browser")
        document_btn = Button(text="Editable Score Document (.musicxml)")
        options.add_widget(pdf_btn)
        options.add_widget(document_btn)
        box.add_widget(options)
        popup = Popup(title="Save Score", content=box, size_hint=(0.72, 0.34))
        pdf_btn.bind(on_press=lambda *_: (popup.dismiss(), self._open_renderer_export()))
        document_btn.bind(on_press=lambda *_: (popup.dismiss(), self._show_save_picker("musicxml")))
        popup.open()

    def _open_renderer_export(self):
        """Write the current score as MusicXML and open the browser PDF renderer."""
        export_dir = os.path.abspath(os.path.join("songs", "renderer_exports"))
        renderer_source = os.path.join(os.path.dirname(os.path.abspath(__file__)), "renderer.html")
        if not os.path.isfile(renderer_source):
            self._show_export_error("renderer.html is missing from the HarmoniFlow project folder.")
            return
        try:
            os.makedirs(export_dir, exist_ok=True)
            title = (self.song_title or "my_composition").strip()
            safe_title = "".join(char if char.isalnum() or char in " _-" else "_" for char in title).strip()
            base = safe_title or "my_composition"
            name = base + ".musicxml"
            suffix = 2
            while os.path.exists(os.path.join(export_dir, name)):
                name = f"{base}_{suffix}.musicxml"
                suffix += 1
            xml_path = os.path.join(export_dir, name)

            # Prefer copying existing multi-part XML when available so instrument toggles have all parts
            if self.source_xml_path and os.path.isfile(self.source_xml_path):
                shutil.copy2(self.source_xml_path, xml_path)
            else:
                score = self._build_music21_score()
                if score is None:
                    return
                score.write("musicxml", fp=xml_path)

            shutil.copy2(renderer_source, os.path.join(export_dir, "renderer.html"))
            meta_params = {
                "title": title,
                "ensemble": self._infer_ensemble_name(),
                "composer": (self.composer or "HarmoniFlow AI Arranger").strip(),
                "key": self.extracted_key or (self.editor.key_sig if self.editor else "C Major"),
                "tempo": self.extracted_tempo or "120 BPM",
                "chords": self._collect_chords_str(),
            }
            url = self._renderer_url(export_dir, name, meta_params=meta_params)
            self._open_external_browser(url)
            print(f"[EXPORT SUCCESS] Browser renderer opened for {xml_path}")
        except Exception as err:
            self._show_export_error(f"Could not open the score renderer: {err}")
            print(f"[EXPORT ERROR] {err}")

    @classmethod
    def _renderer_url(cls, export_dir, xml_name, meta_params=None):
        if cls._renderer_server is None:
            handler = partial(_QuietFileHandler, directory=export_dir)
            cls._renderer_server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            cls._renderer_server.daemon_threads = True
            cls._renderer_thread = threading.Thread(target=cls._renderer_server.serve_forever, daemon=True)
            cls._renderer_thread.start()
        port = cls._renderer_server.server_address[1]
        query = {"file": xml_name}
        for k, v in (meta_params or {}).items():
            if v:
                query[k] = v
        return f"http://127.0.0.1:{port}/renderer.html?{urlencode(query)}"

    @staticmethod
    def _open_external_browser(url):
        if kivy_platform == "android":
            from jnius import autoclass
            Intent = autoclass("android.content.Intent")
            Uri = autoclass("android.net.Uri")
            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            intent = Intent(Intent.ACTION_VIEW, Uri.parse(url))
            PythonActivity.mActivity.startActivity(intent)
        else:
            webbrowser.open(url)

    @staticmethod
    def _find_musescore_executable():
        for cmd in ("musescore3", "musescore", "MuseScore3", "MuseScore4"):
            found = shutil.which(cmd)
            if found:
                return found
        return None

    def _show_pdf_requirement(self):
        self._show_export_error("Use 'PDF via Browser' to render and save PDFs directly in your browser.")

    @staticmethod
    def _show_export_error(message):
        box = BoxLayout(orientation="vertical", spacing=10, padding=12)
        box.add_widget(Label(text=message, halign="center", valign="middle"))
        close = Button(text="Close", size_hint_y=None, height=42)
        box.add_widget(close)
        popup = Popup(title="Score Export", content=box, size_hint=(0.76, 0.32))
        close.bind(on_press=popup.dismiss)
        popup.open()

    def _show_save_picker(self, file_format):
        if file_format == "pdf" and not self._find_musescore_executable():
            self._show_pdf_requirement()
            return
        extension = ".pdf" if file_format == "pdf" else ".musicxml"
        title = (self.song_title or "my_composition").strip()
        safe_title = "".join(char if char.isalnum() or char in " _-" else "_" for char in title).strip()
        os.makedirs("songs", exist_ok=True)
        chooser = FileChooserListView(path=os.path.abspath("songs"), dirselect=False)
        filename = TextInput(text=(safe_title or "my_composition") + extension,
                             multiline=False, size_hint_y=None, height=38)
        message = Label(text="Choose a folder and file name.", size_hint_y=None, height=26)
        controls = BoxLayout(size_hint_y=None, height=40, spacing=8)
        cancel_btn = Button(text="Cancel")
        save_btn = Button(text="Save")
        controls.add_widget(cancel_btn)
        controls.add_widget(save_btn)
        content = BoxLayout(orientation="vertical", spacing=6, padding=8)
        content.add_widget(Label(text="PDF file" if file_format == "pdf" else "Editable score document (.musicxml)",
                                 size_hint_y=None, height=28))
        content.add_widget(chooser)
        content.add_widget(filename)
        content.add_widget(message)
        content.add_widget(controls)
        popup = Popup(title="Choose Save Location", content=content,
                      size_hint=(0.82, 0.86), auto_dismiss=False)
        cancel_btn.bind(on_press=popup.dismiss)

        def save_file(*_args):
            name = os.path.basename(filename.text.strip())
            if not name:
                message.text = "Enter a file name."
                return
            if not name.lower().endswith(extension):
                name += extension
            stem, ext = os.path.splitext(name)
            output_path = os.path.join(chooser.path, name)
            suffix = 2
            while os.path.exists(output_path):
                output_path = os.path.join(chooser.path, f"{stem}_{suffix}{ext}")
                suffix += 1
            score = self._build_music21_score()
            if score is None:
                message.text = "This score is read-only and cannot be exported."
                return
            try:
                os.makedirs(chooser.path, exist_ok=True)
                score.write("musicxml.pdf" if file_format == "pdf" else "musicxml", fp=output_path)
                print(f"[EXPORT SUCCESS] Saved score: {os.path.abspath(output_path)}")
                popup.dismiss()
            except Exception as err:
                message.text = f"Could not export: {err}"
                print(f"[EXPORT ERROR] {err}")

        save_btn.bind(on_press=save_file)
        popup.open()

    def export_to_pdf_and_xml(self, output_base_path="songs/my_composition"):
        """Compatibility wrapper for older callers; opens the format picker."""
        self.show_export_options()
        return None


class PaletteButton(Button):
    def __init__(self, symbol_type, duration, text, bg_color, editor_ref, **kwargs):
        super().__init__(**kwargs)
        self.symbol_type = symbol_type
        self.duration = duration
        self.text = text
        self.editor = editor_ref
        self.background_color = bg_color
        self.size_hint_y = None
        self.height = 30

    def on_press(self):
        self.editor.select_tool(self.symbol_type, self.duration, self)


class InteractiveSheetEditor(BoxLayout):
    """Complete sidebar layout with Paper Size, Page Orientation, Clefs, Accidentals, and Read-Only Mode."""

    def __init__(self, song_title="", composer="", time_sig="4/4", key_sig="C Major", total_measures=16, **kwargs):
        super().__init__(orientation='horizontal', **kwargs)

        app = App.get_running_app()
        if app and hasattr(app, 'wizard_data') and app.wizard_data:
            self.song_title = app.wizard_data.get('title', song_title)
            self.composer = app.wizard_data.get('composer', composer)
            self.time_sig = app.wizard_data.get('time_sig', time_sig)
            self.key_sig = app.wizard_data.get('key_sig', key_sig)
            try:
                self.total_measures = int(app.wizard_data.get('measures', total_measures))
            except (ValueError, TypeError):
                self.total_measures = int(total_measures)
        else:
            self.song_title = song_title
            self.composer = composer
            self.time_sig = time_sig
            self.key_sig = key_sig
            self.total_measures = int(total_measures)

        self.is_read_only = False
        self.read_only_conversion = False
        self.paper_size = "Short Bond"
        self.page_orientation = "Portrait"
        self.active_symbol_type = "Note"
        self.active_duration = 1.0
        self.active_accidental = "None"
        self.active_clef = "Treble"
        self.tool_buttons = []
        self.acc_buttons = []
        self.clef_buttons = []
        self.orient_buttons = []

        self.sidebar = BoxLayout(orientation='vertical', size_hint_x=0.28, padding=6, spacing=4)
        with self.sidebar.canvas.before:
            Color(0.15, 0.15, 0.18, 1)
            self.sidebar_bg = Rectangle(pos=self.sidebar.pos, size=self.sidebar.size)

        self.sidebar.bind(
            pos=lambda s, pos: setattr(self.sidebar_bg, 'pos', pos),
            size=lambda s, size: setattr(self.sidebar_bg, 'size', size),
        )

        self.sidebar.add_widget(Label(text="Paper & Orientation", bold=True, size_hint_y=None, height=20))
        self.paper_spinner = Spinner(
            text="Short Bond",
            values=["Short Bond", "A4", "Legal"],
            size_hint_y=None,
            height=28,
            background_color=(0.2, 0.45, 0.7, 1),
        )
        self.paper_spinner.bind(text=lambda spinner, val: self.set_paper_size(val))
        self.sidebar.add_widget(self.paper_spinner)

        orient_box = BoxLayout(size_hint_y=None, height=28, spacing=4)
        self.btn_portrait = Button(text="Portrait", background_color=(0.15, 0.7, 0.4, 1))
        self.btn_landscape = Button(text="Landscape", background_color=(0.3, 0.3, 0.35, 1))
        self.btn_portrait.bind(on_press=lambda x: self.set_orientation("Portrait", self.btn_portrait))
        self.btn_landscape.bind(on_press=lambda x: self.set_orientation("Landscape", self.btn_landscape))
        self.orient_buttons = [self.btn_portrait, self.btn_landscape]
        orient_box.add_widget(self.btn_portrait)
        orient_box.add_widget(self.btn_landscape)
        self.sidebar.add_widget(orient_box)

        self.sidebar.add_widget(Label(text="Note Lyric & Chord", bold=True, size_hint_y=None, height=20))
        ch_box = BoxLayout(size_hint_y=None, height=28, spacing=4)
        self.chord_input = TextInput(text="", multiline=False, hint_text="Chord (e.g. C7)")
        self.lyric_input = TextInput(text="", multiline=False, hint_text="Lyric")
        ch_box.add_widget(self.chord_input)
        ch_box.add_widget(self.lyric_input)
        self.sidebar.add_widget(ch_box)

        self.sidebar.add_widget(Label(text="Instrument Clefs", bold=True, size_hint_y=None, height=20))
        clef_box = BoxLayout(size_hint_y=None, height=28, spacing=4)
        self.btn_treble = Button(text="Treble", background_color=(0.15, 0.7, 0.4, 1))
        self.btn_bass = Button(text="Bass", background_color=(0.3, 0.3, 0.35, 1))
        self.btn_alto = Button(text="Alto", background_color=(0.3, 0.3, 0.35, 1))

        self.btn_treble.bind(on_press=lambda x: self.set_clef("Treble", self.btn_treble))
        self.btn_bass.bind(on_press=lambda x: self.set_clef("Bass", self.btn_bass))
        self.btn_alto.bind(on_press=lambda x: self.set_clef("Alto", self.btn_alto))

        self.clef_buttons = [self.btn_treble, self.btn_bass, self.btn_alto]
        clef_box.add_widget(self.btn_treble)
        clef_box.add_widget(self.btn_bass)
        clef_box.add_widget(self.btn_alto)
        self.sidebar.add_widget(clef_box)

        self.sidebar.add_widget(Label(text="Accidentals", bold=True, size_hint_y=None, height=20))
        acc_box = BoxLayout(size_hint_y=None, height=28, spacing=4)
        self.btn_nat = Button(text="Natural", background_color=(0.15, 0.7, 0.4, 1))
        self.btn_flat = Button(text="Flat (b)", background_color=(0.3, 0.3, 0.35, 1))
        self.btn_sharp = Button(text="Sharp (#)", background_color=(0.3, 0.3, 0.35, 1))

        self.btn_nat.bind(on_press=lambda x: self.set_accidental("None", self.btn_nat))
        self.btn_flat.bind(on_press=lambda x: self.set_accidental("Flat", self.btn_flat))
        self.btn_sharp.bind(on_press=lambda x: self.set_accidental("Sharp", self.btn_sharp))

        self.acc_buttons = [self.btn_nat, self.btn_flat, self.btn_sharp]
        acc_box.add_widget(self.btn_nat)
        acc_box.add_widget(self.btn_flat)
        acc_box.add_widget(self.btn_sharp)
        self.sidebar.add_widget(acc_box)

        self.sidebar.add_widget(Label(text="Measures & Editing", bold=True, size_hint_y=None, height=20))

        btn_add_measure = Button(text="+ Add Measure (+4 Bars)", size_hint_y=None, height=28, background_color=(0.2, 0.6, 0.8, 1))
        btn_add_measure.bind(on_press=lambda x: self.canvas_area.add_measure())
        self.sidebar.add_widget(btn_add_measure)

        edit_box = BoxLayout(size_hint_y=None, height=28, spacing=4)
        btn_undo = Button(text="Undo", background_color=(0.4, 0.4, 0.4, 1))
        btn_redo = Button(text="Redo", background_color=(0.4, 0.4, 0.4, 1))
        btn_undo.bind(on_press=lambda x: self.canvas_area.undo())
        btn_redo.bind(on_press=lambda x: self.canvas_area.redo())
        edit_box.add_widget(btn_undo)
        edit_box.add_widget(btn_redo)
        self.sidebar.add_widget(edit_box)

        self.btn_eraser = Button(text="Eraser Tool", size_hint_y=None, height=28, background_color=(0.5, 0.2, 0.2, 1))
        self.btn_eraser.bind(on_press=lambda x: self.select_tool("Eraser", 0.0, self.btn_eraser))
        self.sidebar.add_widget(self.btn_eraser)

        self.sidebar.add_widget(Label(text="Select Note Tool", bold=True, size_hint_y=None, height=20))
        for name, dur in [("Whole Note (4.0)", 4.0), ("Half Note (2.0)", 2.0), ("Quarter Note (1.0)", 1.0), ("Eighth Note (0.5)", 0.5)]:
            btn = PaletteButton(symbol_type="Note", duration=dur, text=name, bg_color=(0.2, 0.5, 0.7, 1), editor_ref=self)
            self.tool_buttons.append((btn, "Note", dur))
            self.sidebar.add_widget(btn)

        self.sidebar.add_widget(Label(text="Select Rest Tool", bold=True, size_hint_y=None, height=20))
        for name, dur in [("Whole Rest", 4.0), ("Half Rest", 2.0), ("Quarter Rest", 1.0), ("Eighth Rest", 0.5)]:
            btn = PaletteButton(symbol_type="Rest", duration=dur, text=name, bg_color=(0.7, 0.3, 0.3, 1), editor_ref=self)
            self.tool_buttons.append((btn, "Rest", dur))
            self.sidebar.add_widget(btn)

        self.sidebar.add_widget(Widget())

        self.save_btn = Button(text="Save Score As...", size_hint_y=None, height=36, background_color=(0.15, 0.65, 0.35, 1))
        self.save_btn.bind(on_press=lambda x: self.canvas_area.show_export_options())
        self.sidebar.add_widget(self.save_btn)

        self.add_widget(self.sidebar)

        self.score_scroll = ScrollView(
            size_hint_x=0.72,
            do_scroll_x=False,
            bar_width=10,
            scroll_type=['bars', 'content'],
        )
        self.canvas_area = ScoreStaffCanvas(
            editor_reference=self,
            song_title=self.song_title,
            composer=self.composer,
            time_sig=self.time_sig,
            total_measures=self.total_measures,
            paper_size=self.paper_size,
            page_orientation=self.page_orientation,
            size_hint=(1, None),
            height=700,
        )
        self.score_scroll.add_widget(self.canvas_area)
        self.score_scroll.bind(size=self._resize_score_canvas)
        self.add_widget(self.score_scroll)
        self._resize_score_canvas(self.score_scroll, self.score_scroll.size)

        self.select_tool("Note", 1.0, self.tool_buttons[2][0])
        self.apply_role_permissions()

    def set_paper_size(self, paper_size):
        self.paper_size = paper_size
        if hasattr(self, "canvas_area"):
            self.canvas_area.paper_size = paper_size
            self._resize_score_canvas(self.score_scroll, self.score_scroll.size)

    def set_orientation(self, orientation, active_btn):
        self.page_orientation = orientation
        for btn in self.orient_buttons:
            btn.background_color = (0.3, 0.3, 0.35, 1)
        active_btn.background_color = (0.15, 0.7, 0.4, 1)
        if hasattr(self, "canvas_area"):
            self.canvas_area.page_orientation = orientation
            self._resize_score_canvas(self.score_scroll, self.score_scroll.size)

    def apply_role_permissions(self, force_read_only=None, conversion_read_only=None):
        """Hides editing tools for converted/arranged scores or protected parts viewed by non-Admin users."""
        app = App.get_running_app()
        role = getattr(app, 'active_role', 'Guest') if app else 'Guest'
        wizard_data = getattr(app, 'wizard_data', {}) if app else {}

        if conversion_read_only is not None:
            self.read_only_conversion = bool(conversion_read_only)
        elif wizard_data:
            self.read_only_conversion = bool(wizard_data.get('is_converted_score', False))

        if force_read_only is not None:
            self.is_read_only = bool(force_read_only)
        else:
            is_protected = bool(wizard_data.get('is_protected', False)) if wizard_data else False
            self.is_read_only = self.read_only_conversion or (role != 'Admin' and is_protected)

        if self.is_read_only:
            self.sidebar.opacity = 0
            self.sidebar.disabled = True
            self.sidebar.size_hint_x = 0
            self.score_scroll.size_hint_x = 1.0
        else:
            self.sidebar.opacity = 1
            self.sidebar.disabled = False
            self.sidebar.size_hint_x = 0.28
            self.score_scroll.size_hint_x = 0.72

        if hasattr(self, 'save_btn'):
            can_save = (not self.is_read_only) or (role == 'Admin')
            self.save_btn.disabled = not can_save
            self.save_btn.opacity = 1 if can_save else 0

        if hasattr(self, 'canvas_area'):
            self.canvas_area._schedule_redraw()

    def _resize_score_canvas(self, scroll_view, size):
        if not hasattr(self, 'canvas_area'):
            return
        self.canvas_area.width = max(180, size[0])
        _, _, _, total_h, _, _, _ = self.canvas_area.get_layout_metrics()
        self.canvas_area.height = max(size[1], total_h)
        self.canvas_area._schedule_redraw()

    def select_tool(self, symbol_type, duration, active_btn):
        if self.is_read_only:
            return
        self.active_symbol_type = symbol_type
        self.active_duration = duration
        for btn, stype, _ in self.tool_buttons:
            btn.background_color = (0.2, 0.5, 0.7, 1) if stype == "Note" else (0.7, 0.3, 0.3, 1)
        self.btn_eraser.background_color = (0.5, 0.2, 0.2, 1)
        active_btn.background_color = (0.15, 0.85, 0.35, 1)

    def set_accidental(self, acc_type, active_btn):
        if self.is_read_only:
            return
        self.active_accidental = acc_type
        for btn in self.acc_buttons:
            btn.background_color = (0.3, 0.3, 0.35, 1)
        active_btn.background_color = (0.15, 0.7, 0.4, 1)

    def set_clef(self, clef_type, active_btn):
        if self.is_read_only:
            return
        self.active_clef = clef_type
        for btn in self.clef_buttons:
            btn.background_color = (0.3, 0.3, 0.35, 1)
        active_btn.background_color = (0.15, 0.7, 0.4, 1)
        self.canvas_area._schedule_redraw()