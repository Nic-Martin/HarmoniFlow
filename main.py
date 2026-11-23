# filepath: main.py
from kivy.config import Config
from kivy.utils import platform

if platform == 'android':
    Config.set('kivy', 'keyboard_mode', 'systemanddock')
else:
    Config.set('kivy', 'keyboard_mode', 'system')

import os
import secrets
import threading
import socket
import glob
import calendar
import datetime
from kivy.app import App
from kivy.lang import Builder
from kivy.uix.screenmanager import ScreenManager, Screen
from kivy.properties import StringProperty, ListProperty, DictProperty
from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.filechooser import FileChooserIconView

# Import Native Interactive Score Editor Component
from native_score_editor import InteractiveSheetEditor

try:
    from audio_converter import convert_mp3_to_midi, arrange_midi_with_style, DEFAULT_API_URL
except ImportError:
    DEFAULT_API_URL = os.environ.get("HARMONIFLOW_AI_API_URL", "http://127.0.0.1:8000")

    def convert_mp3_to_midi(*args, **kwargs):
        print("[AUDIO ERROR] Audio processing dependencies are missing; no MIDI was created.")
        return False

    def arrange_midi_with_style(*args, **kwargs):
        print("[ARRANGER ERROR] MIDI arrangement dependencies are missing; no MIDI was created.")
        return False

try:
    from notation_generator import convert_midi_to_sheet_xml
except ImportError:
    def convert_midi_to_sheet_xml(*args, **kwargs):
        print("[NOTATION ERROR] MusicXML dependencies are missing; no score was created.")
        return False

import database_manager
import email_manager
import score_builder

os.environ['MUSIC21_DONT_LOAD_CONFIG'] = '1'


def set_screen_security(enable=True):
    """Blocks screenshots, screen recording, and task-switcher previews on Android via FLAG_SECURE."""
    if platform == 'android':
        try:
            from jnius import autoclass
            from android.runnable import run_on_ui_thread

            WindowManager = autoclass('android.view.WindowManager$LayoutParams')
            PythonActivity = autoclass('org.kivy.android.PythonActivity')

            @run_on_ui_thread
            def _update_flag():
                window = PythonActivity.mActivity.getWindow()
                if enable:
                    window.addFlags(WindowManager.FLAG_SECURE)
                else:
                    window.clearFlags(WindowManager.FLAG_SECURE)

            _update_flag()
            print(f"[SECURITY] Android FLAG_SECURE set to: {enable}")
        except Exception as e:
            print(f"[SECURITY ERROR] Could not set FLAG_SECURE: {e}")


def get_device_ip():
    """Retrieves the active local IP address of the device on the local network."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def get_ai_api_url():
    """Returns configured AI API URL or local server default."""
    env_url = os.environ.get("HARMONIFLOW_AI_API_URL", "").strip()
    if env_url:
        return env_url
    return DEFAULT_API_URL


def create_loading_popup(message="Loading..."):
    box = BoxLayout(orientation='vertical', padding=16, spacing=10)
    lbl = Label(text=message, halign='center', valign='middle')
    lbl.bind(size=lambda inst, val: setattr(inst, 'text_size', val))
    box.add_widget(lbl)
    popup = Popup(
        title="Please Wait",
        content=box,
        size_hint=(0.55, 0.25),
        auto_dismiss=False,
    )
    popup._status_label = lbl
    return popup


def set_loading_message(popup, message):
    if popup and hasattr(popup, "_status_label"):
        popup._status_label.text = message


class LoginScreen(Screen):
    def verify_login(self):
        username = self.ids.username_input.text.strip()
        password = self.ids.password_input.text.strip()

        if not username or not password:
            self.ids.error_label.text = "Username and password fields cannot be empty."
            return

        threading.Thread(target=self._bg_verify_login, args=(username, password), daemon=True).start()

    def _bg_verify_login(self, username, password):
        user_record = database_manager.validate_user(username, password)
        Clock.schedule_once(lambda dt: self._ui_verify_callback(user_record), 0)

    def _ui_verify_callback(self, user_record):
        if user_record:
            app = App.get_running_app()
            app.active_user = str(user_record.get('username', 'Guest'))
            user_role = str(user_record.get('role', 'Member'))
            app.active_role = user_role

            self.ids.error_label.text = ""
            self.ids.username_input.text = ""
            self.ids.password_input.text = ""

            if user_role == 'Admin':
                set_screen_security(False)
                self.manager.current = 'admin_dashboard'
            else:
                set_screen_security(True)
                self.manager.current = 'dashboard'
        else:
            self.ids.error_label.text = "Invalid credentials or database offline."
            self.ids.password_input.text = ""


class RegisterScreen(Screen):
    def register_user(self):
        username = self.ids.reg_username.text.strip()
        email = self.ids.reg_email.text.strip().lower()
        fullname = self.ids.reg_fullname.text.strip()
        password = self.ids.reg_password.text.strip()
        rank = self.ids.reg_rank.text.strip()
        instruments = self.ids.reg_instruments.text.strip()
        birthdate = self.ids.reg_birthdate.text.strip()

        if not (username and email and password and fullname and birthdate):
            self.ids.reg_status.text = "Required fields (*) cannot be blank."
            return

        if not email.endswith("@gmail.com") or "@" not in email:
            self.ids.reg_status.text = "Enter a valid Gmail address to verify your account."
            return

        cleaned_instrument_count = int(instruments) if instruments.isdigit() else 0
        otp_code = str(secrets.randbelow(900000) + 100000)
        self.ids.reg_status.text = "Sending a verification code to your Gmail..."
        self.ids.signup_send_btn.disabled = True

        threading.Thread(target=self._bg_start_registration, args=(
            username, email, password, fullname, rank, cleaned_instrument_count, birthdate, otp_code
        ), daemon=True).start()

    def _bg_start_registration(self, username, email, password, fullname, rank, instruments, birthdate, otp_code):
        success, message = database_manager.start_signup_verification(
            username=username,
            email=email,
            raw_password=password,
            full_name=fullname,
            member_rank=rank or "Beginner",
            instruments_played=instruments,
            birthdate=birthdate,
            otp_code=otp_code,
        )
        if success:
            sent = email_manager.send_signup_otp_email(email, username, otp_code)
            if not sent:
                database_manager.cancel_signup_verification(email)
                message = "Could not send the email. Check Gmail settings and try again."
                success = False
        Clock.schedule_once(lambda dt: self._ui_signup_code_sent(success, message, email), 0)

    def _ui_signup_code_sent(self, success, message, email):
        self.ids.signup_send_btn.disabled = False
        if success:
            self.ids.reg_status.text = f"Verification code sent to {email}. Enter it below to finish signup."
            self.ids.reg_otp.text = ""
        else:
            self.ids.reg_status.text = message

    def verify_signup(self):
        email = self.ids.reg_email.text.strip().lower()
        otp = self.ids.reg_otp.text.strip()
        if not email or not otp:
            self.ids.reg_status.text = "Enter your Gmail address and the 6-digit code."
            return
        if not otp.isdigit() or len(otp) != 6:
            self.ids.reg_status.text = "The verification code must be 6 digits."
            return
        self.ids.signup_verify_btn.disabled = True
        self.ids.reg_status.text = "Verifying code..."
        threading.Thread(target=self._bg_verify_signup, args=(email, otp), daemon=True).start()

    def _bg_verify_signup(self, email, otp):
        success, message = database_manager.verify_signup_otp(email, otp)
        Clock.schedule_once(lambda dt: self._ui_verify_signup_callback(success, message), 0)

    def _ui_verify_signup_callback(self, success, message):
        self.ids.signup_verify_btn.disabled = False
        self.ids.reg_status.text = message
        if success:
            for field_id in ('reg_username', 'reg_email', 'reg_fullname', 'reg_password',
                             'reg_rank', 'reg_instruments', 'reg_birthdate', 'reg_otp'):
                self.ids[field_id].text = ""
            Clock.schedule_once(lambda dt: setattr(self.manager, 'current', 'login'), 1.5)


class ForgotPasswordScreen(Screen):
    def request_otp(self):
        identifier = self.ids.reset_username_email.text.strip()
        if not identifier:
            self.ids.reset_status.text = "Enter your Username or Gmail address."
            return

        otp_code = str(secrets.randbelow(900000) + 100000)
        self.ids.reset_status.text = "Sending OTP to your Gmail..."

        def bg_send_otp():
            user_email, username = database_manager.store_reset_otp(identifier, otp_code)
            if user_email:
                sent = email_manager.send_otp_email(user_email, username, otp_code)
                Clock.schedule_once(lambda dt: self._ui_otp_callback(sent, user_email), 0)
            else:
                Clock.schedule_once(lambda dt: self._ui_otp_callback(False, None), 0)

        threading.Thread(target=bg_send_otp, daemon=True).start()

    def _ui_otp_callback(self, success, email):
        if success:
            self.ids.reset_status.text = f"OTP sent to {email} (Expires in 10 mins)!"
        else:
            self.ids.reset_status.text = "Account not found or Gmail dispatch failed."

    def submit_password_reset(self):
        identifier = self.ids.reset_username_email.text.strip()
        otp = self.ids.reset_otp_input.text.strip()
        new_pass = self.ids.reset_new_password.text.strip()

        if not identifier or not otp or not new_pass:
            self.ids.reset_status.text = "All fields required to reset password."
            return

        def bg_verify():
            success, message = database_manager.verify_and_reset_password(identifier, otp, new_pass)
            Clock.schedule_once(lambda dt: self._ui_reset_callback(success, message), 0)

        threading.Thread(target=bg_verify, daemon=True).start()

    def _ui_reset_callback(self, success, message):
        self.ids.reset_status.text = message
        if success:
            self.ids.reset_username_email.text = ""
            self.ids.reset_otp_input.text = ""
            self.ids.reset_new_password.text = ""
            Clock.schedule_once(lambda dt: setattr(self.manager, 'current', 'login'), 1.5)


class ModeSelectionScreen(Screen):
    def select_arrangement(self):
        if App.get_running_app().active_role != 'Admin':
            self.manager.current = 'dashboard'
            return
        self.manager.current = 'audio_converter'

    def select_composition(self):
        self.manager.current = 'composition_wizard'

    def select_dashboard(self):
        self.manager.current = 'dashboard'

    def logout(self):
        app = App.get_running_app()
        app.active_user = "Guest"
        app.active_role = "Guest"
        set_screen_security(False)
        self.manager.current = 'login'


class CompositionWizardScreen(Screen):
    """Composition Setup Wizard for Maestro (protected master scores) and Staff (personal compositions)."""
    selected_instruments = ListProperty([])

    def on_pre_enter(self):
        self.selected_instruments = [
            i for i in self.selected_instruments if i and i != "Select Instrument"
        ]
        if hasattr(self.ids, 'selected_inst_label'):
            label_text = ', '.join(self.selected_instruments) if self.selected_instruments else "None selected"
            self.ids.selected_inst_label.text = f"Instruments: {label_text}"

    def add_instrument(self, inst_name):
        if not inst_name or inst_name.strip() == "" or inst_name == "Select Instrument":
            if hasattr(self.ids, 'wizard_status'):
                self.ids.wizard_status.text = "Please choose an actual instrument first."
            return

        if inst_name not in self.selected_instruments:
            self.selected_instruments.append(inst_name)
            self.ids.selected_inst_label.text = f"Instruments: {', '.join(self.selected_instruments)}"
            if hasattr(self.ids, 'wizard_status'):
                self.ids.wizard_status.text = ""

    def clear_instruments(self):
        self.selected_instruments = []
        self.ids.selected_inst_label.text = "Instruments: None selected"

    def generate_blank_composition(self):
        title = self.ids.comp_title.text.strip() or "New Composition"
        app = App.get_running_app()
        composer = self.ids.comp_composer.text.strip() or app.active_user
        time_sig = self.ids.comp_time_sig.text
        key_sig = self.ids.comp_key_sig.text
        bpm = self.ids.comp_bpm.text.strip() or "120"
        measures = self.ids.comp_measures.text.strip() or "16"
        has_pickup = self.ids.comp_pickup.active

        valid_instruments = [i for i in self.selected_instruments if i and i != "Select Instrument"]
        if not valid_instruments:
            self.ids.wizard_status.text = "Please select and add at least one instrument."
            return

        is_maestro = (app.active_role == 'Admin')
        is_protected = is_maestro

        app.wizard_data = {
            'title': title,
            'composer': composer,
            'instruments': list(valid_instruments),
            'time_sig': time_sig,
            'key_sig': key_sig,
            'bpm': bpm,
            'measures': measures,
            'has_pickup': has_pickup,
            'is_protected': is_protected,
            'is_converted_score': False,
        }

        self.ids.wizard_status.text = "Building score template..."
        active_user = app.active_user
        active_role = app.active_role

        def bg_build():
            xml_path = score_builder.build_blank_score(
                title=title,
                composer=composer,
                instruments=list(valid_instruments),
                time_sig_str=time_sig,
                bpm=bpm,
                key_sig_str=key_sig,
                measure_count=measures,
                has_pickup=has_pickup
            )

            final_path = xml_path
            if not is_maestro and xml_path and os.path.exists(xml_path):
                project_dir = os.path.dirname(os.path.abspath(__file__))
                staff_dir = os.path.join(project_dir, "staff_songs", active_user)
                os.makedirs(staff_dir, exist_ok=True)
                target_file = os.path.join(staff_dir, os.path.basename(xml_path))
                try:
                    with open(xml_path, "rb") as src, open(target_file, "wb") as dst:
                        dst.write(src.read())
                    final_path = target_file
                except Exception as copy_err:
                    print(f"[COMPOSITION] Staff isolation copy warning: {copy_err}")

            database_manager.save_project_record(
                username=active_user,
                role=active_role,
                title=title,
                midi_path=final_path or "",
                tempo=bpm,
                instrument=valid_instruments[0],
                is_protected=is_protected
            )

            Clock.schedule_once(lambda dt: self._ui_build_callback(final_path), 0)

        threading.Thread(target=bg_build, daemon=True).start()

    def _ui_build_callback(self, xml_path):
        self.ids.wizard_status.text = ""
        if xml_path:
            self.manager.get_screen('sheet_viewer').render_xml_to_ui(xml_path)


class DashboardScreen(Screen):
    def on_enter(self):
        app = App.get_running_app()
        if app.active_user != 'Guest':
            self.load_member_dashboard_items(app.active_user, app.active_role)

    def load_member_dashboard_items(self, username, role):
        def bg_fetch():
            assigned = database_manager.get_assigned_pieces_for_user(username)
            own_comps = [] if role == 'Admin' else database_manager.get_staff_own_compositions(username)
            Clock.schedule_once(lambda dt: self._ui_render_dashboard(assigned, own_comps), 0)

        threading.Thread(target=bg_fetch, daemon=True).start()

    def _ui_render_dashboard(self, assigned, own_comps):
        if not hasattr(self.ids, 'assigned_parts_list'):
            return

        container = self.ids.assigned_parts_list
        container.clear_widgets()

        for row in assigned:
            title = row.get('title', 'Untitled Score')
            part = row.get('assigned_part', 'General')
            midi_path = row.get('midi_path', '')

            btn = Button(
                text=f"Assigned: {title} - [{part}]",
                size_hint_y=None,
                height=50,
                background_color=(0.15, 0.55, 0.75, 1)
            )
            btn.bind(on_press=lambda b, p=midi_path: self.open_score_path(p, is_protected=True))
            container.add_widget(btn)

        for comp in own_comps:
            comp_id = comp.get('project_id')
            title = comp.get('title', 'My Composition')
            inst = comp.get('track_instrument', 'Score')
            score_path = comp.get('midi_path', '')
            is_starred = bool(comp.get('is_starred', False))

            row_box = BoxLayout(orientation='horizontal', size_hint_y=None, height=50, spacing=6)

            star_btn = Button(
                text="★" if is_starred else "☆",
                size_hint_x=None,
                width=48,
                background_color=(0.85, 0.65, 0.1, 1) if is_starred else (0.3, 0.3, 0.35, 1)
            )
            star_btn.bind(on_press=lambda b, cid=comp_id, cur=is_starred: self.toggle_star_composition(cid, not cur))

            open_btn = Button(
                text=f"{'★ ' if is_starred else ''}My Composition: {title} ({inst})",
                background_color=(0.2, 0.5, 0.35, 1)
            )
            open_btn.bind(on_press=lambda b, p=score_path: self.open_score_path(p, is_protected=False))

            del_btn = Button(
                text="Delete",
                size_hint_x=None,
                width=78,
                background_color=(0.75, 0.2, 0.2, 1)
            )
            del_btn.bind(on_press=lambda b, cid=comp_id, p=score_path: self.delete_composition(cid, p))

            row_box.add_widget(star_btn)
            row_box.add_widget(open_btn)
            row_box.add_widget(del_btn)
            container.add_widget(row_box)

        if not assigned and not own_comps:
            lbl = Button(
                text="No assigned parts or personal compositions yet.",
                disabled=True,
                size_hint_y=None,
                height=45
            )
            container.add_widget(lbl)

    def toggle_star_composition(self, comp_id, starred):
        if not comp_id:
            return
        app = App.get_running_app()
        username = app.active_user
        role = app.active_role

        def bg_star():
            database_manager.set_composition_starred(comp_id, username, starred)
            Clock.schedule_once(lambda dt: self.load_member_dashboard_items(username, role), 0)

        threading.Thread(target=bg_star, daemon=True).start()

    def delete_composition(self, comp_id, score_path):
        if not comp_id:
            return
        app = App.get_running_app()
        username = app.active_user
        role = app.active_role

        def bg_delete():
            database_manager.delete_project_record(comp_id, username)
            if score_path and os.path.exists(score_path):
                abs_path = os.path.abspath(score_path)
                staff_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "staff_songs"))
                if abs_path.startswith(staff_root + os.sep):
                    try:
                        os.remove(abs_path)
                    except OSError as err:
                        print(f"[DELETE WARNING] Could not remove file: {err}")
            Clock.schedule_once(lambda dt: self.load_member_dashboard_items(username, role), 0)

        threading.Thread(target=bg_delete, daemon=True).start()

    def open_score_path(self, score_path, is_protected=True):
        if not score_path:
            return
        app = App.get_running_app()
        app.wizard_data = dict(app.wizard_data or {})
        app.wizard_data['is_protected'] = is_protected
        app.wizard_data['is_converted_score'] = False

        if score_path.lower().endswith(".xml"):
            xml_path = score_path
        else:
            xml_path = os.path.splitext(score_path)[0] + ".xml"

        if os.path.exists(xml_path):
            self.manager.get_screen('sheet_viewer').render_xml_to_ui(xml_path)
        else:
            print(f"[VIEWER ERROR] Score file not found at: {xml_path}")

    def logout(self):
        app = App.get_running_app()
        app.active_user = "Guest"
        app.active_role = "Guest"
        set_screen_security(False)
        self.manager.current = 'login'

    def navigate_to_audio_converter(self):
        if App.get_running_app().active_role != 'Admin':
            return
        self.manager.current = 'audio_converter'

    def navigate_to_note_generator(self):
        self.manager.current = 'composition_wizard'

    def navigate_to_schedules(self):
        self.manager.current = 'practice_schedules'

    def navigate_to_settings(self):
        self.manager.current = 'profile'

    def navigate_to_profile(self):
        self.manager.current = 'profile'


class PracticeScheduleScreen(Screen):
    selected_date = StringProperty("")
    attached_pdf_path = StringProperty("")

    def on_enter(self):
        self.refresh_schedules()

    def open_calendar_picker(self):
        today = datetime.date.today()
        if self.selected_date:
            try:
                today = datetime.date.fromisoformat(self.selected_date)
            except ValueError:
                pass

        state = {"year": today.year, "month": today.month}
        root_box = BoxLayout(orientation='vertical', spacing=8, padding=10)
        header_box = BoxLayout(size_hint_y=None, height=40, spacing=8)
        prev_btn = Button(text="< Prev", size_hint_x=None, width=80)
        month_lbl = Label(text="", bold=True, font_size='16sp')
        next_btn = Button(text="Next >", size_hint_x=None, width=80)
        header_box.add_widget(prev_btn)
        header_box.add_widget(month_lbl)
        header_box.add_widget(next_btn)

        days_grid = GridLayout(cols=7, spacing=4)
        cancel_btn = Button(text="Cancel", size_hint_y=None, height=40, background_color=(0.75, 0.2, 0.2, 1))

        root_box.add_widget(header_box)
        root_box.add_widget(days_grid)
        root_box.add_widget(cancel_btn)

        popup = Popup(title="Choose Practice Date", content=root_box, size_hint=(0.8, 0.75))
        cancel_btn.bind(on_press=popup.dismiss)

        def pick_day(day_num):
            chosen = datetime.date(state["year"], state["month"], day_num).isoformat()
            self.selected_date = chosen
            if hasattr(self.ids, 'sched_date_btn'):
                self.ids.sched_date_btn.text = f"📅 {chosen}"
            popup.dismiss()

        def render_month():
            yr, mo = state["year"], state["month"]
            month_lbl.text = f"{calendar.month_name[mo]} {yr}"
            days_grid.clear_widgets()
            for wd in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]:
                days_grid.add_widget(Button(text=wd, disabled=True, size_hint_y=None, height=32))
            for week in calendar.monthcalendar(yr, mo):
                for day_num in week:
                    if day_num == 0:
                        days_grid.add_widget(Button(text="", disabled=True))
                    else:
                        b = Button(text=str(day_num), background_color=(0.2, 0.5, 0.75, 1))
                        b.bind(on_press=lambda _btn, d=day_num: pick_day(d))
                        days_grid.add_widget(b)

        def shift_month(delta):
            yr, mo = state["year"], state["month"] + delta
            if mo < 1:
                mo = 12
                yr -= 1
            elif mo > 12:
                mo = 1
                yr += 1
            state["year"], state["month"] = yr, mo
            render_month()

        prev_btn.bind(on_press=lambda _btn: shift_month(-1))
        next_btn.bind(on_press=lambda _btn: shift_month(1))
        render_month()
        popup.open()

    def open_pdf_attachment_picker(self):
        project_dir = os.path.dirname(os.path.abspath(__file__))
        songs_dir = os.path.join(project_dir, "songs")
        os.makedirs(songs_dir, exist_ok=True)

        box = BoxLayout(orientation='vertical', spacing=8, padding=10)
        chooser = FileChooserIconView(
            filters=['*.pdf', '*.PDF', '*.xml', '*.XML', '*.mid', '*.MID', '*.midi', '*.MIDI'],
            path=songs_dir
        )
        select_btn = Button(text="Attach as PDF", size_hint_y=None, height=42, background_color=(0.15, 0.65, 0.35, 1))
        cancel_btn = Button(text="Cancel", size_hint_y=None, height=42, background_color=(0.75, 0.2, 0.2, 1))
        box.add_widget(chooser)
        box.add_widget(select_btn)
        box.add_widget(cancel_btn)

        popup = Popup(title="Select Arranged File or PDF", content=box, size_hint=(0.9, 0.9))
        cancel_btn.bind(on_press=popup.dismiss)
        select_btn.bind(on_press=lambda _btn: self._handle_pdf_selection(chooser.selection, popup))
        popup.open()

    def _handle_pdf_selection(self, selection, popup):
        popup.dismiss()
        if not selection:
            return
        chosen_path = selection[0]
        pdf_path = self._ensure_pdf_file(chosen_path)
        if pdf_path and os.path.exists(pdf_path):
            self.attached_pdf_path = pdf_path
            if hasattr(self.ids, 'sched_pdf_label'):
                self.ids.sched_pdf_label.text = f"PDF: {os.path.basename(pdf_path)}"
            self.ids.schedule_status.text = f"Attached PDF: {os.path.basename(pdf_path)}"
        else:
            self.ids.schedule_status.text = "Could not create/find PDF for selected file."

    def _ensure_pdf_file(self, source_path):
        ext = os.path.splitext(source_path)[1].lower()
        if ext == ".pdf":
            return source_path

        out_pdf = os.path.splitext(source_path)[0] + ".pdf"
        try:
            import music21
            if ext in [".mid", ".midi", ".xml"]:
                score = music21.converter.parse(source_path)
                score.write("musicxml.pdf", fp=out_pdf)
            if os.path.exists(out_pdf):
                return out_pdf
        except Exception as err:
            print(f"[SCHEDULE PDF] MusicXML/PDF export notice: {err}")

        try:
            title_line = f"HarmoniFlow Score Attachment: {os.path.basename(source_path)}".replace("(", "[").replace(")", "]")
            stream_txt = f"BT /F1 12 Tf 50 740 Td ({title_line}) Tj ET".encode("latin-1", errors="replace")
            pdf_bytes = (
                b"%PDF-1.4\n"
                b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
                b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
                b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                b"/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> "
                b"/Contents 4 0 R >> endobj\n"
                b"4 0 obj << /Length " + str(len(stream_txt)).encode("ascii") + b" >> stream\n"
                + stream_txt + b"\nendstream endobj\n"
                b"xref\n0 5\n0000000000 65535 f \n"
                b"trailer << /Size 5 /Root 1 0 R >>\nstartxref\n0\n%%EOF\n"
            )
            with open(out_pdf, "wb") as f:
                f.write(pdf_bytes)
            return out_pdf
        except Exception as e:
            print(f"[SCHEDULE PDF] Fallback PDF error: {e}")
            return None

    def refresh_schedules(self):
        threading.Thread(target=self._bg_load, daemon=True).start()

    def _bg_load(self):
        rows = database_manager.get_practice_schedules()
        Clock.schedule_once(lambda dt: self._ui_render(rows), 0)

    def _ui_render(self, rows):
        if not hasattr(self.ids, 'schedule_list_box'):
            return
        box = self.ids.schedule_list_box
        box.clear_widgets()

        if not rows:
            box.add_widget(Button(
                text="No practice schedules posted yet.",
                disabled=True,
                size_hint_y=None,
                height=45
            ))
            return

        for r in rows:
            summary = (
                f"{r.get('title')} | {r.get('practice_date')} @ {r.get('practice_time')}\n"
                f"{r.get('location')} - {r.get('focus_notes') or 'General Rehearsal'}"
            )
            box.add_widget(Button(
                text=summary,
                disabled=True,
                halign='left',
                valign='middle',
                size_hint_y=None,
                height=65,
                background_color=(0.18, 0.22, 0.28, 1)
            ))

    def add_schedule(self):
        app = App.get_running_app()
        if app.active_role != 'Admin':
            self.ids.schedule_status.text = "Only Maestro can post practice schedules."
            return

        title = self.ids.sched_title.text.strip()
        p_date = self.selected_date.strip()
        p_time = self.ids.sched_time.text.strip()
        loc = self.ids.sched_location.text.strip() or "Main Hall"
        base_notes = self.ids.sched_notes.text.strip()
        notes = base_notes
        if self.attached_pdf_path:
            pdf_tag = f"[PDF: {os.path.basename(self.attached_pdf_path)}]"
            notes = f"{base_notes} {pdf_tag}".strip()

        if not (title and p_date and p_time):
            self.ids.schedule_status.text = "Title, Calendar Date, and Time are required."
            return

        creator = app.active_user

        def bg_save():
            new_id = database_manager.create_practice_schedule(title, p_date, p_time, loc, notes, creator)
            Clock.schedule_once(lambda dt: self._ui_after_save(new_id), 0)

        threading.Thread(target=bg_save, daemon=True).start()

    def _ui_after_save(self, new_id):
        if new_id:
            self.ids.schedule_status.text = f"Schedule #{new_id} published!"
            self.ids.sched_title.text = ""
            self.selected_date = ""
            self.attached_pdf_path = ""
            if hasattr(self.ids, 'sched_date_btn'):
                self.ids.sched_date_btn.text = "📅 Select Date (YYYY-MM-DD)"
            if hasattr(self.ids, 'sched_pdf_label'):
                self.ids.sched_pdf_label.text = "No PDF attached"
            self.ids.sched_time.text = ""
            self.ids.sched_location.text = ""
            self.ids.sched_notes.text = ""
            self.refresh_schedules()
        else:
            self.ids.schedule_status.text = "Failed to save schedule."


class ProfileScreen(Screen):
    user_username = StringProperty("Guest")
    user_fullname = StringProperty("No active session profile")
    user_rank = StringProperty("Beginner")
    user_instruments = StringProperty("0")
    user_birthdate = StringProperty("YYYY-MM-DD")

    def on_enter(self):
        current_active = App.get_running_app().active_user
        if current_active == "Guest":
            self.reset_profile_fields()
            return

        threading.Thread(target=self._bg_load_profile, args=(current_active,), daemon=True).start()

    def _bg_load_profile(self, current_active):
        try:
            conn = database_manager.get_connection()
            from psycopg2.extras import RealDictCursor
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute(
                "SELECT username, full_name, member_rank, instruments_played, birthdate FROM users WHERE username = %s;",
                (current_active,)
            )
            row = cursor.fetchone()
            cursor.close()
            conn.close()
            Clock.schedule_once(lambda dt: self._ui_profile_callback(row, current_active), 0)
        except Exception as db_err:
            print(f"[PROFILE MODULE ERROR] Session read failure: {db_err}")
            Clock.schedule_once(lambda dt: self._ui_profile_callback(None, current_active), 0)

    def _ui_profile_callback(self, row, current_active):
        if row:
            self.user_username = str(row.get('username', current_active))
            self.user_fullname = str(row.get('full_name', ''))
            self.user_rank = str(row.get('member_rank', 'Beginner'))
            self.user_instruments = str(row.get('instruments_played', '0'))
            self.user_birthdate = str(row.get('birthdate', 'YYYY-MM-DD'))
        else:
            self.user_username = current_active
            self.user_fullname = "Offline Cache Session Mode"

    def reset_profile_fields(self):
        self.user_username = "Guest"
        self.user_fullname = "No active session profile"
        self.user_rank = "Beginner"
        self.user_instruments = "0"
        self.user_birthdate = "YYYY-MM-DD"


class SheetViewerScreen(Screen):
    current_xml_path = StringProperty("")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.editor_widget = None

    def on_kv_post(self, base_widget):
        if hasattr(self.ids, 'native_score_container') and self.editor_widget is None:
            self.editor_widget = InteractiveSheetEditor(size_hint=(1, 1))
            self.ids.native_score_container.add_widget(self.editor_widget)

    def on_enter(self):
        if self.editor_widget:
            app = App.get_running_app()
            is_conv = bool(getattr(app, 'wizard_data', {}).get('is_converted_score', False))
            self.editor_widget.apply_role_permissions(conversion_read_only=is_conv)
            if hasattr(self.editor_widget, 'canvas_area'):
                Clock.schedule_once(lambda dt: self.editor_widget.canvas_area._schedule_redraw(), 0.05)

    def open_export_dialog(self):
        if self.editor_widget and hasattr(self.editor_widget, 'canvas_area'):
            self.editor_widget.canvas_area.show_export_options()

    def render_xml_to_ui(self, xml_path="songs/new_Composition.xml"):
        self.current_xml_path = os.path.abspath(xml_path)

        if self.editor_widget and hasattr(self.editor_widget, 'canvas_area'):
            app = App.get_running_app()
            is_conv = False
            if hasattr(app, 'wizard_data') and app.wizard_data:
                self.editor_widget.song_title = app.wizard_data.get('title', 'NEW SCORE')
                self.editor_widget.composer = app.wizard_data.get('composer', 'COMPOSER')
                self.editor_widget.time_sig = app.wizard_data.get('time_sig', '4/4')
                is_conv = bool(app.wizard_data.get('is_converted_score', False))
                try:
                    self.editor_widget.total_measures = int(app.wizard_data.get('measures', 16))
                except (ValueError, TypeError):
                    pass
            self.editor_widget.canvas_area.load_from_musicxml(self.current_xml_path)
            self.editor_widget.apply_role_permissions(conversion_read_only=is_conv)
            self.editor_widget.canvas_area._schedule_redraw()

        self.manager.current = 'sheet_viewer'


class AudioConverterScreen(Screen):
    last_converted_midi = StringProperty("")
    _loading_popup = None

    def _show_loading(self, message):
        self._dismiss_loading()
        self._loading_popup = create_loading_popup(message)
        self._loading_popup.open()

    def _update_loading(self, message):
        set_loading_message(self._loading_popup, message)

    def _dismiss_loading(self):
        if self._loading_popup is not None:
            try:
                self._loading_popup.dismiss()
            except Exception:
                pass
            self._loading_popup = None

    def on_pre_enter(self):
        if App.get_running_app().active_role != 'Admin':
            self.manager.current = 'dashboard'

    def open_file_picker(self):
        if App.get_running_app().active_role != 'Admin':
            self.ids.conversion_log.text = "Access Denied: Maestro privileges required."
            return

        current_project_dir = os.path.dirname(os.path.abspath(__file__))
        songs_folder = os.path.join(current_project_dir, "songs")
        os.makedirs(songs_folder, exist_ok=True)

        layout = BoxLayout(orientation='vertical', spacing=10, padding=10)
        file_chooser = FileChooserIconView(
            filters=['*.mp3', '*.MP3', '*.wav', '*.WAV', '*.mid', '*.MID', '*.midi', '*.MIDI'],
            path=songs_folder
        )
        select_btn = Button(text="Convert -> Arrange -> Render Notesheet", size_hint_y=None, height=45)
        close_btn = Button(text="Cancel", size_hint_y=None, height=45, background_color=(0.8, 0.2, 0.2, 1))

        layout.add_widget(file_chooser)
        layout.add_widget(select_btn)
        layout.add_widget(close_btn)

        popup = Popup(title="Arrangement Engine: Select Audio or MIDI File", content=layout, size_hint=(0.9, 0.9))
        close_btn.bind(on_press=popup.dismiss)
        select_btn.bind(on_press=lambda btn: self.handle_transcription(file_chooser.selection, popup))
        popup.open()

    def handle_transcription(self, selection, popup):
        popup.dismiss()
        if not selection:
            return
        input_path = selection[0]
        ext = os.path.splitext(input_path)[1].lower()
        songs_dir = os.path.dirname(input_path)
        style_choice = (
            self.ids.arrangement_style_spinner.text
            if hasattr(self.ids, 'arrangement_style_spinner')
            else "Marching Band"
        )
        safe_style = style_choice.lower().replace(" ", "_")
        api_url = get_ai_api_url()

        if ext in ['.mid', '.midi']:
            self.ids.conversion_log.text = f"Arranging MIDI into [{style_choice}] via {api_url}..."
            self._show_loading(f"Arranging MIDI into [{style_choice}]...")
            arranged_midi = os.path.join(songs_dir, f"arranged_{safe_style}_{os.path.basename(input_path)}")
            output_xml_path = os.path.splitext(arranged_midi)[0] + ".xml"
            self.last_converted_midi = arranged_midi

            def bg_arrange_existing_midi():
                ok_mid = arrange_midi_with_style(
                    input_path, arranged_midi, style_name=style_choice, api_url=api_url
                )
                Clock.schedule_once(lambda dt: self._update_loading("Generating notesheet XML..."), 0)
                ok_xml = convert_midi_to_sheet_xml(arranged_midi, output_xml_path, style_choice) if ok_mid else False
                Clock.schedule_once(
                    lambda dt: self._ui_transcribe_callback(ok_mid, ok_xml, arranged_midi, output_xml_path),
                    0
                )

            threading.Thread(target=bg_arrange_existing_midi, daemon=True).start()
            return

        base_stem = os.path.splitext(os.path.basename(input_path))[0]
        destination_midi = os.path.join(songs_dir, f"converted_{base_stem}_{safe_style}.mid")
        self.last_converted_midi = destination_midi

        self.ids.conversion_log.text = f"Transcribing & arranging [{style_choice}] via {api_url}..."
        self._show_loading("Isolating stems, transcribing & arranging...")
        threading.Thread(
            target=self._bg_transcribe,
            args=(input_path, destination_midi, style_choice, api_url),
            daemon=True
        ).start()

    def _bg_transcribe(self, input_path, destination_midi, style_choice, api_url):
        midi_ok = convert_mp3_to_midi(input_path, destination_midi, style_choice, api_url=api_url)
        output_xml_path = os.path.splitext(destination_midi)[0] + ".xml"
        xml_ok = False
        if midi_ok and os.path.exists(destination_midi):
            Clock.schedule_once(lambda dt: self._update_loading("Generating notesheet XML..."), 0)
            xml_ok = convert_midi_to_sheet_xml(destination_midi, output_xml_path, style_choice)
        Clock.schedule_once(
            lambda dt: self._ui_transcribe_callback(midi_ok, xml_ok, destination_midi, output_xml_path),
            0
        )

    def _ui_transcribe_callback(self, midi_ok, xml_ok, destination_midi, output_xml_path):
        self._dismiss_loading()
        if midi_ok and xml_ok and os.path.exists(output_xml_path):
            app = App.get_running_app()
            app.wizard_data = dict(app.wizard_data or {})
            app.wizard_data['is_converted_score'] = True
            self.ids.conversion_log.text = f"Rendered notesheet:\n{os.path.basename(output_xml_path)}"
            if hasattr(self.ids, 'render_sheet_btn'):
                self.ids.render_sheet_btn.disabled = False
            self.manager.get_screen('sheet_viewer').render_xml_to_ui(output_xml_path)
        elif midi_ok:
            self.ids.conversion_log.text = f"MIDI arranged ({os.path.basename(destination_midi)}), XML failed."
            if hasattr(self.ids, 'render_sheet_btn'):
                self.ids.render_sheet_btn.disabled = False
        else:
            self.ids.conversion_log.text = "Conversion backend operation failed. Check logs."

    def render_notesheet_from_midi(self):
        target_midi = self.last_converted_midi
        if not target_midi or not os.path.exists(target_midi):
            midi_files = glob.glob("*.mid") + glob.glob("songs/*.mid")
            if midi_files:
                target_midi = max(midi_files, key=os.path.getmtime)
            else:
                self.ids.conversion_log.text = "Error: No MIDI file found to render."
                return

        style_choice = (
            self.ids.arrangement_style_spinner.text
            if hasattr(self.ids, 'arrangement_style_spinner')
            else "Marching Band"
        )
        self.ids.conversion_log.text = f"Generating sheet notation for:\n{os.path.basename(target_midi)}"
        self._show_loading("Generating sheet notation...")
        output_xml_path = os.path.splitext(target_midi)[0] + ".xml"

        def bg_convert():
            res = convert_midi_to_sheet_xml(target_midi, output_xml_path, style_choice)
            Clock.schedule_once(lambda dt: self._ui_midi_direct_callback(res, output_xml_path), 0)

        threading.Thread(target=bg_convert, daemon=True).start()

    def _ui_midi_direct_callback(self, res, output_xml_path):
        self._dismiss_loading()
        if os.path.exists(output_xml_path):
            app = App.get_running_app()
            app.wizard_data = dict(app.wizard_data or {})
            app.wizard_data['is_converted_score'] = True
            self.manager.get_screen('sheet_viewer').render_xml_to_ui(output_xml_path)
        else:
            self.ids.conversion_log.text = "MIDI Sheet Generation failed."


class NoteGeneratorScreen(Screen):
    active_midi_path = StringProperty("")
    _loading_popup = None

    def _show_loading(self, message):
        self._dismiss_loading()
        self._loading_popup = create_loading_popup(message)
        self._loading_popup.open()

    def _dismiss_loading(self):
        if self._loading_popup is not None:
            try:
                self._loading_popup.dismiss()
            except Exception:
                pass
            self._loading_popup = None

    def on_pre_enter(self):
        if App.get_running_app().active_role != 'Admin':
            self.manager.current = 'dashboard'

    def open_midi_picker(self):
        if App.get_running_app().active_role != 'Admin':
            self.ids.generation_log.text = "Access Denied: Maestro privileges required."
            return

        current_project_dir = os.path.dirname(os.path.abspath(__file__))
        songs_folder = os.path.join(current_project_dir, "songs")
        os.makedirs(songs_folder, exist_ok=True)

        layout = BoxLayout(orientation='vertical', spacing=10, padding=10)
        file_chooser = FileChooserIconView(
            filters=['*.mid', '*.MID', '*.midi', '*.MIDI'],
            path=songs_folder
        )
        select_btn = Button(text="Select Target Score File", size_hint_y=None, height=45)
        close_btn = Button(text="Cancel", size_hint_y=None, height=45, background_color=(0.8, 0.2, 0.2, 1))

        layout.add_widget(file_chooser)
        layout.add_widget(select_btn)
        layout.add_widget(close_btn)

        popup = Popup(title="Notation Loader: Select MIDI File", content=layout, size_hint=(0.9, 0.9))
        close_btn.bind(on_press=popup.dismiss)
        select_btn.bind(on_press=lambda btn: self.handle_midi_selection(file_chooser.selection, popup))
        popup.open()

    def handle_midi_selection(self, selection, popup):
        if not selection:
            return
        self.active_midi_path = selection[0]
        self.ids.generation_log.text = f"Loaded file:\n{os.path.basename(self.active_midi_path)}"
        self.ids.execute_orchestration_btn.disabled = False
        popup.dismiss()

    def trigger_sheet_orchestration(self):
        if not self.active_midi_path or not os.path.exists(self.active_midi_path):
            self.ids.generation_log.text = "Validation Error: Please select a valid file."
            return

        output_xml_path = os.path.splitext(self.active_midi_path)[0] + ".xml"
        chosen_instrument = self.ids.instrument_picker.text

        self.ids.generation_log.text = "Generating sheet notation..."
        self._show_loading("Generating instrument notesheet...")
        threading.Thread(target=self._bg_orchestrate, args=(output_xml_path, chosen_instrument), daemon=True).start()

    def _bg_orchestrate(self, output_xml_path, chosen_instrument):
        res = convert_midi_to_sheet_xml(self.active_midi_path, output_xml_path, chosen_instrument)
        Clock.schedule_once(lambda dt: self._ui_orchestrate_callback(res, output_xml_path), 0)

    def _ui_orchestrate_callback(self, res, output_xml_path):
        self._dismiss_loading()
        if res:
            app = App.get_running_app()
            app.wizard_data = dict(app.wizard_data or {})
            app.wizard_data['is_converted_score'] = True
            self.manager.get_screen('sheet_viewer').render_xml_to_ui(output_xml_path)
        else:
            self.ids.generation_log.text = "Sheet layout build failed."


class AdminDashboardScreen(Screen):
    def on_pre_enter(self):
        if App.get_running_app().active_role != 'Admin':
            self.manager.current = 'dashboard'

    def on_enter(self):
        print("[ADMIN SYSTEM] Initializing secure Maestro console panel...")

    def execute_group_assignment(self):
        if App.get_running_app().active_role != 'Admin':
            self.ids.admin_status.text = "Error: Maestro permissions required."
            return

        target_project = self.ids.assign_project_id.text.strip()
        target_musician = self.ids.assign_username.text.strip()
        instrument_part = self.ids.assign_part.text.strip()

        if not (target_project and target_musician and instrument_part):
            self.ids.admin_status.text = "Error: All assignment fields required."
            return

        def bg_assign():
            success = database_manager.assign_member_to_piece(int(target_project), target_musician, instrument_part)

            def ui_callback(res):
                if res:
                    self.ids.admin_status.text = f"Assigned {target_musician} to Part: {instrument_part}!"
                    self.ids.assign_username.text = ""
                    self.ids.assign_part.text = ""
                else:
                    self.ids.admin_status.text = "Assignment failed. Verify Project ID & Username."

            Clock.schedule_once(lambda dt: ui_callback(success), 0)

        threading.Thread(target=bg_assign, daemon=True).start()

    def logout(self):
        app = App.get_running_app()
        app.active_user = "Guest"
        app.active_role = "Guest"
        set_screen_security(False)
        self.manager.current = 'login'


class HarmoniFlowApp(App):
    active_user = StringProperty("Guest")
    active_role = StringProperty("Guest")
    wizard_data = DictProperty({})

    def build(self):
        database_manager.init_db()
        return Builder.load_file('main.kv')


if __name__ == '__main__':
    HarmoniFlowApp().run()