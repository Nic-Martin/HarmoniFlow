# filepath: database_manager.py

import psycopg2
from psycopg2.extras import RealDictCursor
import hashlib
import os

DB_HOST = "localhost"
DB_NAME = "postgres"
DB_USER = "postgres"
DB_PASSWORD = "postgres"


def get_connection():
    return psycopg2.connect(
        host=DB_HOST,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD
    )


# --- 🔐 PASSWORD HASHING HELPERS ---
def hash_password(password: str) -> str:
    salt = os.urandom(16)
    pwd_hash = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
    return salt.hex() + ":" + pwd_hash.hex()


def verify_password(stored_password_hash: str, provided_password: str) -> bool:
    try:
        salt_hex, pwd_hash_hex = stored_password_hash.split(":")
        salt = bytes.fromhex(salt_hex)
        new_hash = hashlib.pbkdf2_hmac('sha256', provided_password.encode('utf-8'), salt, 100000)
        return new_hash.hex() == pwd_hash_hex
    except Exception:
        return stored_password_hash == provided_password


def init_schedule_and_score_ownership():
    """Creates practice_schedules and adds ownership, protection, and star columns to projects."""
    conn = None
    try:
        conn = get_connection()
        with conn.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS public.practice_schedules (
                    schedule_id SERIAL PRIMARY KEY,
                    title VARCHAR(150) NOT NULL,
                    practice_date VARCHAR(50) NOT NULL,
                    practice_time VARCHAR(50) NOT NULL,
                    location VARCHAR(150) DEFAULT 'Main Hall',
                    focus_notes TEXT DEFAULT '',
                    created_by VARCHAR(50) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            cursor.execute("""
                ALTER TABLE public.projects
                ADD COLUMN IF NOT EXISTS owner_role VARCHAR(30) DEFAULT 'Admin';
            """)
            cursor.execute("""
                ALTER TABLE public.projects
                ADD COLUMN IF NOT EXISTS is_protected BOOLEAN DEFAULT TRUE;
            """)
            cursor.execute("""
                ALTER TABLE public.projects
                ADD COLUMN IF NOT EXISTS is_starred BOOLEAN DEFAULT FALSE;
            """)
        conn.commit()
    except Exception as e:
        if conn is not None:
            conn.rollback()
        print(f"[DATABASE] Schedule/ownership schema error: {e}")
    finally:
        if conn is not None:
            conn.close()


def init_db():
    try:
        conn = get_connection()
        cursor = conn.cursor()

        # 1. USERS TABLE
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS public.users (
                user_id SERIAL PRIMARY KEY,
                username VARCHAR(50) UNIQUE NOT NULL,
                email VARCHAR(120) UNIQUE NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                full_name VARCHAR(100) NOT NULL,
                role VARCHAR(50) DEFAULT 'Member',
                member_rank VARCHAR(50) DEFAULT 'Beginner',
                instruments_played INTEGER DEFAULT 0,
                birthdate DATE,
                reset_otp VARCHAR(10) DEFAULT NULL,
                reset_otp_expires_at TIMESTAMP DEFAULT NULL,
                reset_otp_attempts INTEGER DEFAULT 0
            );
        """)

        cursor.execute("ALTER TABLE public.users ADD COLUMN IF NOT EXISTS email VARCHAR(120) UNIQUE;")
        cursor.execute("ALTER TABLE public.users ADD COLUMN IF NOT EXISTS reset_otp VARCHAR(10) DEFAULT NULL;")
        cursor.execute("ALTER TABLE public.users ADD COLUMN IF NOT EXISTS reset_otp_expires_at TIMESTAMP DEFAULT NULL;")
        cursor.execute("ALTER TABLE public.users ADD COLUMN IF NOT EXISTS reset_otp_attempts INTEGER DEFAULT 0;")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS public.pending_signups (
                pending_id SERIAL PRIMARY KEY,
                username VARCHAR(50) UNIQUE NOT NULL,
                email VARCHAR(120) UNIQUE NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                full_name VARCHAR(100) NOT NULL,
                member_rank VARCHAR(50) DEFAULT 'Beginner',
                instruments_played INTEGER DEFAULT 0,
                birthdate DATE,
                signup_otp VARCHAR(10) NOT NULL,
                otp_expires_at TIMESTAMP NOT NULL,
                otp_attempts INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 2. PROJECTS TABLE
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS public.projects (
                project_id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES public.users(user_id) ON DELETE CASCADE,
                title VARCHAR(150) NOT NULL,
                midi_path VARCHAR(255) NOT NULL,
                tempo INTEGER DEFAULT 120,
                volume REAL DEFAULT 1.0,
                track_instrument VARCHAR(50) DEFAULT 'Trumpet Section',
                owner_role VARCHAR(30) DEFAULT 'Admin',
                is_protected BOOLEAN DEFAULT TRUE,
                is_starred BOOLEAN DEFAULT FALSE,
                last_modified TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 3. ENSEMBLE ASSIGNMENTS TABLE
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS public.ensemble_assignments (
                assignment_id SERIAL PRIMARY KEY,
                project_id INTEGER REFERENCES public.projects(project_id) ON DELETE CASCADE,
                user_id INTEGER REFERENCES public.users(user_id) ON DELETE CASCADE,
                assigned_part VARCHAR(100) NOT NULL,
                assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (project_id, user_id, assigned_part)
            );
        """)

        # 4. SEED DEFAULT ADMIN ACCOUNT
        hashed_ninja_pwd = hash_password('7300')
        cursor.execute("""
            INSERT INTO public.users (username, email, password_hash, full_name, role, member_rank)
            VALUES ('Ninja', 'admin.harmoniflow@gmail.com', %s, 'Maestro Ninja Admin', 'Admin', 'Maestro')
            ON CONFLICT (username) DO UPDATE SET role = 'Admin';
        """, (hashed_ninja_pwd,))

        conn.commit()
        cursor.close()
        conn.close()

        init_schedule_and_score_ownership()
        print("[DATABASE] Subsystem initialized with schedules, score ownership, and role access controls.")
    except Exception as e:
        print(f"[DATABASE] Schema initialization error: {e}")


def create_user(username, email, raw_password, full_name, member_rank, instruments_played, birthdate):
    query = """
        INSERT INTO public.users (username, email, password_hash, full_name, role, member_rank, instruments_played, birthdate)
        VALUES (%s, %s, %s, %s, 'Member', %s, %s, %s);
    """
    try:
        conn = get_connection()
        cursor = conn.cursor()
        formatted_birthdate = birthdate if birthdate and birthdate.strip() != "" else None
        secure_hash = hash_password(raw_password)

        cursor.execute(query, (
            username,
            email.strip().lower(),
            secure_hash,
            full_name,
            member_rank,
            instruments_played,
            formatted_birthdate
        ))

        conn.commit()
        cursor.close()
        conn.close()
        return True
    except Exception as e:
        print(f"[DATABASE] Registration failure: {e}")
        return False


def start_signup_verification(username, email, raw_password, full_name,
                              member_rank, instruments_played, birthdate, otp_code):
    conn = None
    try:
        conn = get_connection()
        with conn.cursor() as cursor:
            clean_email = email.strip().lower()
            cursor.execute(
                "SELECT 1 FROM public.users WHERE username = %s OR LOWER(email) = %s LIMIT 1;",
                (username.strip(), clean_email),
            )
            if cursor.fetchone():
                return False, "That username or email is already registered."

            cursor.execute(
                "DELETE FROM public.pending_signups WHERE username = %s OR email = %s;",
                (username.strip(), clean_email),
            )
            cursor.execute("""
                INSERT INTO public.pending_signups
                    (username, email, password_hash, full_name, member_rank,
                     instruments_played, birthdate, signup_otp, otp_expires_at, otp_attempts)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s,
                        NOW() + INTERVAL '10 minutes', 0);
            """, (
                username.strip(), clean_email, hash_password(raw_password), full_name.strip(),
                member_rank or "Beginner", int(instruments_played or 0),
                birthdate.strip() or None, str(otp_code),
            ))
        conn.commit()
        return True, "Verification code sent."
    except Exception as e:
        if conn is not None:
            conn.rollback()
        print(f"[DATABASE] Could not start signup verification: {e}")
        return False, "Signup could not be saved. Check the database and try again."
    finally:
        if conn is not None:
            conn.close()


def cancel_signup_verification(email):
    conn = None
    try:
        conn = get_connection()
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM public.pending_signups WHERE email = %s;",
                           (email.strip().lower(),))
        conn.commit()
    except Exception as e:
        if conn is not None:
            conn.rollback()
        print(f"[DATABASE] Could not clear pending signup: {e}")
    finally:
        if conn is not None:
            conn.close()


def verify_signup_otp(email, otp_code):
    conn = None
    try:
        conn = get_connection()
        with conn.cursor() as cursor:
            clean_email = email.strip().lower()
            cursor.execute("""
                SELECT pending_id, username, email, password_hash, full_name,
                       member_rank, instruments_played, birthdate, signup_otp,
                       otp_expires_at, otp_attempts
                FROM public.pending_signups WHERE email = %s FOR UPDATE;
            """, (clean_email,))
            row = cursor.fetchone()
            if not row:
                conn.commit()
                return False, "No pending signup found. Request a new verification code."

            (pending_id, username, stored_email, password_hash, full_name, rank,
             instruments, birthdate, stored_otp, expires_at, attempts) = row

            if attempts >= 3:
                cursor.execute("DELETE FROM public.pending_signups WHERE pending_id = %s;", (pending_id,))
                conn.commit()
                return False, "Too many incorrect attempts. Start signup again for a new code."

            cursor.execute("SELECT NOW() > %s;", (expires_at,))
            if cursor.fetchone()[0]:
                cursor.execute("DELETE FROM public.pending_signups WHERE pending_id = %s;", (pending_id,))
                conn.commit()
                return False, "The code expired. Start signup again to request a new one."

            if str(stored_otp) != str(otp_code).strip():
                cursor.execute("""
                    UPDATE public.pending_signups SET otp_attempts = otp_attempts + 1
                    WHERE pending_id = %s;
                """, (pending_id,))
                conn.commit()
                return False, f"Incorrect code. {2 - attempts} attempt(s) remaining."

            cursor.execute("""
                INSERT INTO public.users
                    (username, email, password_hash, full_name, role, member_rank,
                     instruments_played, birthdate)
                VALUES (%s, %s, %s, %s, 'Member', %s, %s, %s);
            """, (username, stored_email, password_hash, full_name, rank, instruments, birthdate))
            cursor.execute("DELETE FROM public.pending_signups WHERE pending_id = %s;", (pending_id,))
        conn.commit()
        return True, "Email verified and account created successfully. You can now log in."
    except Exception as e:
        if conn is not None:
            conn.rollback()
        print(f"[DATABASE] Signup verification failed: {e}")
        return False, "Could not create the account. Check the database and try again."
    finally:
        if conn is not None:
            conn.close()


def validate_user(username, password):
    query = """
        SELECT user_id, username, email, password_hash, full_name, role, member_rank, instruments_played, birthdate 
        FROM public.users 
        WHERE username = %s OR email = %s;
    """
    conn = None
    try:
        conn = get_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query, (username, username.strip().lower()))
            user_record = cursor.fetchone()

            if user_record:
                stored_hash = user_record['password_hash']
                if verify_password(stored_hash, password):
                    if user_record['birthdate']:
                        user_record['birthdate'] = str(user_record['birthdate'])
                    del user_record['password_hash']
                    return user_record

            return None
    except Exception as e:
        print(f"[DATABASE] Authentication subsystem failure: {e}")
        return None
    finally:
        if conn is not None:
            conn.close()


def store_reset_otp(username_or_email, otp_code):
    query = """
        UPDATE public.users 
        SET reset_otp = %s,
            reset_otp_expires_at = NOW() + INTERVAL '10 minutes',
            reset_otp_attempts = 0
        WHERE username = %s OR email = %s
        RETURNING email, username;
    """
    conn = None
    try:
        conn = get_connection()
        with conn.cursor() as cursor:
            clean_identifier = username_or_email.strip().lower()
            cursor.execute(query, (str(otp_code), username_or_email, clean_identifier))
            res = cursor.fetchone()
            conn.commit()
            if res:
                return res[0], res[1]
            return None, None
    except Exception as e:
        print(f"[DATABASE] Failed to set reset OTP: {e}")
        return None, None
    finally:
        if conn is not None:
            conn.close()


def verify_and_reset_password(username_or_email, otp_code, new_raw_password):
    query_user = """
        SELECT user_id, reset_otp, reset_otp_expires_at, reset_otp_attempts
        FROM public.users
        WHERE username = %s OR email = %s;
    """
    conn = None
    try:
        conn = get_connection()
        with conn.cursor() as cursor:
            clean_identifier = username_or_email.strip().lower()
            cursor.execute(query_user, (username_or_email, clean_identifier))
            user_row = cursor.fetchone()

            if not user_row or not user_row[1]:
                return False, "Invalid user or no OTP requested."

            user_id, stored_otp, expires_at, attempts = user_row[0], user_row[1], user_row[2], user_row[3]

            if attempts >= 3:
                cursor.execute("UPDATE public.users SET reset_otp = NULL, reset_otp_expires_at = NULL WHERE user_id = %s;", (user_id,))
                conn.commit()
                return False, "Too many failed attempts. Request a new OTP code."

            cursor.execute("SELECT NOW() > %s;", (expires_at,))
            is_expired = cursor.fetchone()[0]

            if is_expired:
                cursor.execute("UPDATE public.users SET reset_otp = NULL, reset_otp_expires_at = NULL WHERE user_id = %s;", (user_id,))
                conn.commit()
                return False, "OTP code has expired. Please request a new one."

            if stored_otp != str(otp_code).strip():
                cursor.execute("UPDATE public.users SET reset_otp_attempts = reset_otp_attempts + 1 WHERE user_id = %s;", (user_id,))
                conn.commit()
                return False, f"Incorrect OTP code. {2 - attempts} attempt(s) remaining."

            secure_hash = hash_password(new_raw_password)
            cursor.execute("""
                UPDATE public.users 
                SET password_hash = %s, reset_otp = NULL, reset_otp_expires_at = NULL, reset_otp_attempts = 0 
                WHERE user_id = %s;
            """, (secure_hash, user_id))
            conn.commit()
            return True, "Password updated successfully!"

    except Exception as e:
        print(f"[DATABASE] Password reset failure: {e}")
        return False, "Database operation error."
    finally:
        if conn is not None:
            conn.close()


def assign_member_to_piece(project_id, username, assigned_part):
    query_user = "SELECT user_id FROM public.users WHERE username = %s;"
    query_assign = """
        INSERT INTO public.ensemble_assignments (project_id, user_id, assigned_part)
        VALUES (%s, %s, %s)
        ON CONFLICT (project_id, user_id, assigned_part) DO UPDATE 
        SET assigned_part = EXCLUDED.assigned_part;
    """
    conn = None
    try:
        conn = get_connection()
        with conn.cursor() as cursor:
            cursor.execute(query_user, (username,))
            user_row = cursor.fetchone()

            if not user_row:
                print(f"[DATABASE] User '{username}' not found.")
                return False

            user_id = user_row[0]
            cursor.execute(query_assign, (project_id, user_id, assigned_part))
            conn.commit()
            return True
    except Exception as e:
        print(f"[DATABASE] Ensemble assignment failure: {e}")
        return False
    finally:
        if conn is not None:
            conn.close()


def get_assigned_pieces_for_user(username):
    """Retrieves all Maestro-assigned sheet music pieces for a specific member username."""
    query = """
        SELECT p.project_id, p.title, p.midi_path, p.track_instrument, ea.assigned_part 
        FROM public.ensemble_assignments ea
        JOIN public.projects p ON ea.project_id = p.project_id
        JOIN public.users u ON ea.user_id = u.user_id
        WHERE u.username = %s;
    """
    conn = None
    try:
        conn = get_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query, (username,))
            return cursor.fetchall()
    except Exception as e:
        print(f"[DATABASE] Error fetching assigned pieces: {e}")
        return []
    finally:
        if conn is not None:
            conn.close()


def create_practice_schedule(title, practice_date, practice_time, location, focus_notes, created_by):
    """Maestro creates a practice schedule entry."""
    conn = None
    try:
        conn = get_connection()
        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO public.practice_schedules
                    (title, practice_date, practice_time, location, focus_notes, created_by)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING schedule_id;
            """, (title.strip(), practice_date.strip(), practice_time.strip(),
                  location.strip() or "Main Hall", focus_notes.strip(), created_by))
            new_id = cursor.fetchone()[0]
        conn.commit()
        return new_id
    except Exception as e:
        if conn is not None:
            conn.rollback()
        print(f"[DATABASE] Create schedule error: {e}")
        return None
    finally:
        if conn is not None:
            conn.close()


def get_practice_schedules():
    """Fetches all practice schedules for Maestro and Staff."""
    conn = None
    try:
        conn = get_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("""
                SELECT schedule_id, title, practice_date, practice_time, location, focus_notes, created_by
                FROM public.practice_schedules
                ORDER BY schedule_id DESC;
            """)
            return cursor.fetchall()
    except Exception as e:
        print(f"[DATABASE] Fetch schedules error: {e}")
        return []
    finally:
        if conn is not None:
            conn.close()


def save_project_record(username, role, title, midi_path, tempo=120, instrument="Trumpet Section", is_protected=True):
    """Saves a score under Maestro (protected) or Staff (personal composition)."""
    conn = None
    try:
        conn = get_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT user_id FROM public.users WHERE username = %s;", (username,))
            row = cursor.fetchone()
            user_id = row[0] if row else None

            cursor.execute("""
                INSERT INTO public.projects
                    (user_id, title, midi_path, tempo, track_instrument, owner_role, is_protected, is_starred)
                VALUES (%s, %s, %s, %s, %s, %s, %s, FALSE)
                RETURNING project_id;
            """, (user_id, title, midi_path, int(tempo or 120), instrument, role, bool(is_protected)))
            project_id = cursor.fetchone()[0]
        conn.commit()
        return project_id
    except Exception as e:
        if conn is not None:
            conn.rollback()
        print(f"[DATABASE] Save project error: {e}")
        return None
    finally:
        if conn is not None:
            conn.close()


def get_staff_own_compositions(username):
    """Returns only personal compositions created by this Staff/Member user, starred items first."""
    conn = None
    try:
        conn = get_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("""
                SELECT p.project_id, p.title, p.midi_path, p.tempo, p.track_instrument,
                       COALESCE(p.is_starred, FALSE) AS is_starred, p.last_modified
                FROM public.projects p
                JOIN public.users u ON p.user_id = u.user_id
                WHERE u.username = %s AND p.owner_role = 'Member' AND p.is_protected = FALSE
                ORDER BY COALESCE(p.is_starred, FALSE) DESC, p.last_modified DESC, p.project_id DESC;
            """, (username,))
            return cursor.fetchall()
    except Exception as e:
        print(f"[DATABASE] Fetch staff compositions error: {e}")
        return []
    finally:
        if conn is not None:
            conn.close()


def set_composition_starred(project_id, username, starred: bool):
    """Toggles the important star marker on a user's personal composition."""
    conn = None
    try:
        conn = get_connection()
        with conn.cursor() as cursor:
            cursor.execute("""
                UPDATE public.projects p
                SET is_starred = %s
                FROM public.users u
                WHERE p.user_id = u.user_id
                  AND p.project_id = %s
                  AND u.username = %s
                  AND p.owner_role = 'Member'
                  AND p.is_protected = FALSE;
            """, (bool(starred), int(project_id), username))
        conn.commit()
        return True
    except Exception as e:
        if conn is not None:
            conn.rollback()
        print(f"[DATABASE] Toggle star error: {e}")
        return False
    finally:
        if conn is not None:
            conn.close()


def delete_project_record(project_id, username):
    """Deletes an unfinished/personal composition owned by the user."""
    conn = None
    try:
        conn = get_connection()
        with conn.cursor() as cursor:
            cursor.execute("""
                DELETE FROM public.projects p
                USING public.users u
                WHERE p.user_id = u.user_id
                  AND p.project_id = %s
                  AND u.username = %s
                  AND p.owner_role = 'Member'
                  AND p.is_protected = FALSE;
            """, (int(project_id), username))
        conn.commit()
        return True
    except Exception as e:
        if conn is not None:
            conn.rollback()
        print(f"[DATABASE] Delete project error: {e}")
        return False
    finally:
        if conn is not None:
            conn.close()