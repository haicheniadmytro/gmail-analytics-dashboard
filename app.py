from collections import Counter, defaultdict
from datetime import date, timedelta
from email import message_from_bytes
from email.header import decode_header
from email.utils import parsedate_to_datetime
import imaplib
import re

import altair as alt
import pandas as pd
import streamlit as st


# ============================================================
# НАЛАШТУВАННЯ
# ============================================================

st.set_page_config(
    page_title="Gmail Pro Analytics",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

TIMEZONE = "Europe/Kyiv"
WORK_START = 9
WORK_END = 18


# ============================================================
# STOP WORDS
# ============================================================

STOP_WORDS = {
    # English
    "re", "fw", "fwd", "the", "to", "and", "a", "of", "in", "for",
    "is", "on", "that", "by", "this", "with", "i", "you", "it",
    "not", "or", "be", "are", "from", "at", "as", "your", "all",
    "have", "new", "more", "an", "was", "we", "will", "home",
    "can", "us", "about", "if", "page", "my", "has", "search",
    "free", "but", "our", "one", "other", "do", "no",
    "information", "time", "they", "see", "only", "so", "his",
    "when", "contact", "here", "business", "who", "web", "also",
    "now", "help", "get", "pm", "am", "what", "news", "out", "use",
    "any", "there",

    # Ukrainian
    "та", "в", "і", "на", "з", "для", "по", "до", "не", "про",
    "як", "за", "від", "що", "чи", "це", "а", "при", "або", "у",
    "я", "ви", "ми", "вони", "його", "її", "їх", "теж", "також",
    "щоб", "було", "бути", "є", "де", "коли", "то", "лише",
    "після", "під", "але", "ще", "вже", "дуже", "так", "ні",
    "мені", "вам", "нас", "вас", "цей", "ця", "ці", "можна",
    "може", "буде", "був", "була", "були", "має", "мають",
    "якщо", "тому", "тут", "там", "через", "щодо", "між",
}


# ============================================================
# CSS / UI
# ============================================================

st.markdown(
    """
    <style>
        .block-container {
            padding-top: 1.5rem;
            padding-bottom: 2rem;
        }

        div[data-testid="stMetric"] {
            background: rgba(128,128,128,0.08);
            border-radius: 12px;
            padding: 14px;
        }

        div[data-testid="stMetricLabel"] {
            font-size: 0.9rem;
        }

        .insight-card {
            background: rgba(128,128,128,0.08);
            border-radius: 12px;
            padding: 16px;
            margin-bottom: 10px;
        }

        .section-title {
            font-size: 1.25rem;
            font-weight: 700;
            margin-top: 0.5rem;
            margin-bottom: 0.8rem;
        }
    </style>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# ДОПОМІЖНІ ФУНКЦІЇ
# ============================================================

def decode_mime_words(header_val):
    if not header_val:
        return ""
    result = []
    try:
        fragments = decode_header(header_val)
    except Exception:
        return str(header_val)
    for fragment, encoding in fragments:
        if isinstance(fragment, bytes):
            charset = encoding or "utf-8"
            try:
                result.append(fragment.decode(charset, errors="ignore"))
            except Exception:
                result.append(fragment.decode("latin1", errors="ignore"))
        else:
            result.append(str(fragment))
    return "".join(result)


def extract_email_address(value):
    if not value:
        return ""
    match = re.search(r"[\w.!#$%&'*+/=?^_`{|}~-]+@[\w.-]+\.[A-Za-z]{2,}", value)
    if match:
        return match.group(0).lower()
    return value.lower().strip()


def extract_name(from_header):
    if not from_header:
        return ""
    if "<" in from_header:
        name = from_header.split("<")[0].strip().strip('"').strip("'")
        if name:
            return name
    return extract_email_address(from_header)


def normalize_subject(subject):
    if not subject:
        return ""
    subject = str(subject).lower().strip()
    previous = None
    while previous != subject:
        previous = subject
        subject = re.sub(r"^\s*((re|fw|fwd|відповідь|переслано)\s*:\s*)+", "", subject, flags=re.IGNORECASE)
    subject = re.sub(r"\s+", " ", subject)
    return subject.strip()


def is_reply_subject(subject):
    if not subject:
        return False
    return bool(re.match(r"^\s*(re|fw|fwd|відповідь)\s*:", str(subject), flags=re.IGNORECASE))


def calculate_period(period_name):
    today = date.today()
    if period_name == "Останні 7 днів":
        return today - timedelta(days=7), today
    if period_name == "Останні 30 днів":
        return today - timedelta(days=30), today
    if period_name == "Останні 3 місяці":
        return today - timedelta(days=90), today
    if period_name == "Останні 6 місяців":
        return today - timedelta(days=180), today
    if period_name == "Останній рік":
        return today - timedelta(days=365), today
    if period_name == "Поточний рік":
        return date(today.year, 1, 1), today
    if period_name == "Весь доступний період":
        return None, today
    return None, today


def get_period_description(start_date, end_date):
    if start_date is None:
        return "Весь доступний період"
    return f"{start_date.strftime('%d.%m.%Y')} — {end_date.strftime('%d.%m.%Y')}"


def format_timedelta(value):
    if value is None or pd.isna(value):
        return "—"
    total_seconds = int(value.total_seconds())
    if total_seconds < 0:
        return "—"
    minutes = total_seconds // 60
    days = minutes // (24 * 60)
    minutes = minutes % (24 * 60)
    hours = minutes // 60
    minutes = minutes % 60
    if days > 0:
        return f"{days} дн. {hours} год."
    if hours > 0:
        return f"{hours} год. {minutes} хв."
    return f"{minutes} хв."


# ============================================================
# ПОШУК ПАПКИ НАДІСЛАНИХ
# ============================================================

def find_sent_folder(mail):
    try:
        status, folders = mail.list()
        if status != "OK":
            return "[Gmail]/Sent Mail"
        for folder in folders:
            if not folder:
                continue
            decoded = folder.decode("utf-8", errors="ignore")
            if r"\Sent" in decoded:
                match = re.search(r'"([^"]+)"\s*$', decoded)
                if match:
                    return match.group(1)
                if "[Gmail]/Sent Mail" in decoded:
                    return "[Gmail]/Sent Mail"
        return "[Gmail]/Sent Mail"
    except Exception:
        return "[Gmail]/Sent Mail"


# ============================================================
# ПОШУК ЛИСТІВ
# ============================================================

def search_folder(mail, folder, start_date=None, end_date=None):
    try:
        status, _ = mail.select(f'"{folder}"')
        if status != "OK":
            return []
        if start_date is None:
            status, data = mail.search(None, "ALL")
        else:
            since_date = start_date.strftime("%d-%b-%Y")
            if end_date:
                before_date = (end_date + timedelta(days=1)).strftime("%d-%b-%Y")
                status, data = mail.search(None, "SINCE", since_date, "BEFORE", before_date)
            else:
                status, data = mail.search(None, "SINCE", since_date)
        if status != "OK":
            return []
        if not data or not data[0]:
            return []
        return data[0].split()
    except Exception:
        return []


# ============================================================
# ЗАВАНТАЖЕННЯ ЗАГОЛОВКІВ
# ============================================================

def fetch_folder_headers(mail, folder, start_date=None, end_date=None, folder_type="inbox"):
    ids = search_folder(mail, folder, start_date, end_date)
    if not ids:
        return []
    ids.reverse()
    result = []
    chunk_size = 500
    total = len(ids)
    progress_bar = st.progress(0)
    status_text = st.empty()
    for i in range(0, total, chunk_size):
        chunk_ids = ids[i:i+chunk_size]
        ids_str = b",".join(chunk_ids)
        status_text.text(f"Завантаження {i+1:,}–{min(i+chunk_size, total):,} з {total:,}...")
        try:
            status, msg_data = mail.fetch(ids_str, "(BODY.PEEK[HEADER.FIELDS (DATE FROM TO SUBJECT MESSAGE-ID IN-REPLY-TO REFERENCES)])")
        except Exception as e:
            st.warning(f"⚠️ Не вдалося отримати частину листів: {e}")
            continue
        if status != "OK":
            continue
        for response_part in msg_data:
            if not isinstance(response_part, tuple):
                continue
            try:
                msg = message_from_bytes(response_part[1])
                result.append({
                    "date": msg.get("Date", ""),
                    "from": decode_mime_words(msg.get("From", "")),
                    "to": decode_mime_words(msg.get("To", "")),
                    "subject": decode_mime_words(msg.get("Subject", "")),
                    "message_id": msg.get("Message-ID", ""),
                    "in_reply_to": msg.get("In-Reply-To", ""),
                    "references": msg.get("References", ""),
                    "folder_type": folder_type,
                })
            except Exception:
                continue
        progress_bar.progress(min((i+chunk_size)/max(total,1), 1.0))
    progress_bar.empty()
    status_text.empty()
    return result


# ============================================================
# ОСНОВНЕ ЗАВАНТАЖЕННЯ GMAIL
# ============================================================

def fetch_emails_imap(email_user, app_password, start_date=None, end_date=None):
    mail = None
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        clean_password = app_password.replace(" ", "").replace("\n", "").replace("\r", "")
        mail.login(email_user.strip(), clean_password)

        st.info("📥 Завантаження вхідних листів...")
        inbox_data = fetch_folder_headers(mail, "INBOX", start_date, end_date, "inbox")

        sent_folder = find_sent_folder(mail)
        st.info(f"📤 Завантаження надісланих листів ({sent_folder})...")
        sent_data = fetch_folder_headers(mail, sent_folder, start_date, end_date, "sent")

        try:
            mail.logout()
        except Exception:
            pass

        all_data = inbox_data + sent_data
        if not all_data:
            st.warning("За обраний період листів не знайдено.")
            return None

        df = pd.DataFrame(all_data)
        st.success(f"✅ Завантажено {len(df):,} листів (вхідні: {len(inbox_data):,}, надіслані: {len(sent_data):,})".replace(",", " "))
        return df

    except imaplib.IMAP4.error:
        st.error("❌ Помилка авторизації Gmail.\n\nПеревір:\n• правильність Email;\n• пароль додатка Gmail;\n• що використовується саме App Password, а не звичайний пароль Google.")
        return None
    except Exception as e:
        st.error(f"❌ Помилка підключення до Gmail: {e}")
        return None
    finally:
        if mail is not None:
            try:
                mail.logout()
            except Exception:
                pass


# ============================================================
# ОБРОБКА DATAFRAME
# ============================================================

def analyze_emails(df, user_email):
    df = df.copy()
    required_columns = ["date", "from", "to", "subject", "message_id", "in_reply_to", "references", "folder_type"]
    for col in required_columns:
        if col not in df.columns:
            df[col] = ""

    df["parsed_date"] = pd.to_datetime(df["date"], utc=True, errors="coerce")
    df["parsed_date"] = df["parsed_date"].dt.tz_convert(TIMEZONE)
    df["date_only"] = df["parsed_date"].dt.date
    df["year"] = df["parsed_date"].dt.year
    df["month"] = df["parsed_date"].dt.strftime("%Y-%m")
    df["hour"] = df["parsed_date"].dt.hour
    df["day_name"] = df["parsed_date"].dt.day_name()
    df["day_num"] = df["parsed_date"].dt.dayofweek

    df["clean_from"] = df["from"].fillna("").apply(extract_email_address)
    df["sender_name"] = df["from"].fillna("").apply(extract_name)
    df["clean_to"] = df["to"].fillna("").apply(extract_email_address)

    df["subject"] = df["subject"].fillna("").astype(str)
    df["normalized_subject"] = df["subject"].apply(normalize_subject)
    df["is_reply_subject"] = df["subject"].apply(is_reply_subject)

    df["message_id_clean"] = df["message_id"].fillna("").astype(str).str.strip()
    df["in_reply_to_clean"] = df["in_reply_to"].fillna("").astype(str).str.strip()
    df["references_clean"] = df["references"].fillna("").astype(str).str.strip()

    normalized_user_email = user_email.strip().lower()
    df["is_from_user"] = df["clean_from"] == normalized_user_email

    df["direction"] = df["folder_type"].map({"inbox": "Вхідний", "sent": "Надісланий"}).fillna("Інший")
    df["is_workday"] = df["day_num"] < 5
    df["is_work_hours"] = df["is_workday"] & (df["hour"] >= WORK_START) & (df["hour"] < WORK_END)
    df["is_outside_work_hours"] = ~df["is_work_hours"]

    return df


# ============================================================
# АНАЛІЗ ЦЕПОЧОК (виправлено)
# ============================================================

def build_threads_analysis(df):
    if df.empty:
        return None

    # Додаємо clean_to до списку колонок
    emails = df[["message_id_clean", "in_reply_to_clean", "references_clean", "normalized_subject",
                 "parsed_date", "direction", "clean_from", "clean_to", "sender_name", "subject",
                 "is_reply_subject"]].copy()

    graph = defaultdict(set)
    all_ids = set(emails["message_id_clean"].dropna().tolist())

    for _, row in emails.iterrows():
        msg_id = row["message_id_clean"]
        if not msg_id:
            continue
        refs = row["references_clean"].split()
        for ref in refs:
            if ref in all_ids:
                graph[ref].add(msg_id)
                graph[msg_id].add(ref)
        in_reply = row["in_reply_to_clean"]
        if in_reply and in_reply in all_ids:
            graph[in_reply].add(msg_id)
            graph[msg_id].add(in_reply)

    visited = set()
    threads = []
    for node in all_ids:
        if node not in visited:
            queue = [node]
            visited.add(node)
            comp = []
            while queue:
                curr = queue.pop()
                comp.append(curr)
                for neighbor in graph.get(curr, []):
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append(neighbor)
            threads.append(comp)

    thread_map = {}
    for idx, comp in enumerate(threads):
        for msg_id in comp:
            thread_map[msg_id] = idx

    remaining = emails[~emails["message_id_clean"].isin(thread_map.keys())]
    if not remaining.empty:
        remaining_sorted = remaining.sort_values("parsed_date")
        used = set()
        for i, row in remaining_sorted.iterrows():
            if row["message_id_clean"] in used:
                continue
            thread_id = len(threads) + len(used)
            comp = [row["message_id_clean"]]
            used.add(row["message_id_clean"])
            for j, row2 in remaining_sorted.iterrows():
                if row2["message_id_clean"] in used:
                    continue
                if row2["normalized_subject"] == row["normalized_subject"]:
                    time_diff = abs((row2["parsed_date"] - row["parsed_date"]).days)
                    if time_diff <= 7:
                        comp.append(row2["message_id_clean"])
                        used.add(row2["message_id_clean"])
            threads.append(comp)
            for msg_id in comp:
                thread_map[msg_id] = thread_id

    thread_info = []
    for thread_id, comp in enumerate(threads):
        if not comp:
            continue
        thread_emails = emails[emails["message_id_clean"].isin(comp)]
        if thread_emails.empty:
            continue
        first = thread_emails.iloc[0]
        last = thread_emails.iloc[-1]
        length = len(thread_emails)
        initiator = first["clean_from"] if first["direction"] == "Вхідний" else "Я"
        initiator_name = first["sender_name"] if first["direction"] == "Вхідний" else "Я"
        # Тепер clean_to існує
        participants = set(thread_emails["clean_from"].dropna().tolist() + thread_emails["clean_to"].dropna().tolist())
        participants = [p for p in participants if p]
        num_participants = len(participants)
        first_date = first["parsed_date"]
        last_date = last["parsed_date"]
        lifespan = last_date - first_date
        subject_norm = first["normalized_subject"]
        has_reply = length > 1
        user_emails = thread_emails[thread_emails["direction"] == "Надісланий"]
        user_count = len(user_emails)
        others_count = length - user_count

        sorted_dates = thread_emails.sort_values("parsed_date")["parsed_date"]
        if len(sorted_dates) > 1:
            gaps = sorted_dates.diff().dropna()
            avg_gap = gaps.mean()
            median_gap = gaps.median()
        else:
            avg_gap = None
            median_gap = None

        thread_info.append({
            "thread_id": thread_id,
            "length": length,
            "first_date": first_date,
            "last_date": last_date,
            "lifespan": lifespan,
            "initiator": initiator,
            "initiator_name": initiator_name,
            "participants": participants,
            "num_participants": num_participants,
            "subject": subject_norm,
            "has_reply": has_reply,
            "user_count": user_count,
            "others_count": others_count,
            "avg_gap": avg_gap,
            "median_gap": median_gap,
        })

    if not thread_info:
        return None

    thread_df = pd.DataFrame(thread_info)

    total_threads = len(thread_df)
    avg_length = thread_df["length"].mean()
    median_length = thread_df["length"].median()
    max_length = thread_df["length"].max()
    threads_with_reply = thread_df[thread_df["has_reply"]].shape[0]
    reply_rate = threads_with_reply / total_threads if total_threads > 0 else 0

    thread_df["first_month"] = thread_df["first_date"].dt.strftime("%Y-%m")
    monthly_new = thread_df.groupby("first_month").size().reset_index(name="new_threads")

    now = pd.Timestamp.now(tz=TIMEZONE)
    stale = thread_df[(now - thread_df["last_date"]).dt.days > 7]
    stale_count = len(stale)

    initiator_counts = thread_df["initiator"].value_counts().head(10).reset_index()
    initiator_counts.columns = ["Контакт", "Цепочок"]

    length_dist = thread_df["length"].value_counts().sort_index().reset_index()
    length_dist.columns = ["Довжина", "Кількість"]

    avg_participants = thread_df["num_participants"].mean()

    return {
        "thread_df": thread_df,
        "total_threads": total_threads,
        "avg_length": avg_length,
        "median_length": median_length,
        "max_length": max_length,
        "longest_thread": thread_df.loc[thread_df["length"].idxmax()] if not thread_df.empty else None,
        "reply_rate": reply_rate,
        "threads_with_reply": threads_with_reply,
        "monthly_new": monthly_new,
        "stale_count": stale_count,
        "stale_df": stale,
        "initiator_counts": initiator_counts,
        "length_dist": length_dist,
        "avg_participants": avg_participants,
    }


# ============================================================
# ЧАС ВІДПОВІДІ
# ============================================================

def calculate_response_times(df):
    if df.empty:
        return pd.DataFrame()
    incoming = df[(df["direction"] == "Вхідний") & (df["message_id_clean"] != "")].copy()
    outgoing = df[(df["direction"] == "Надісланий") & (df["in_reply_to_clean"] != "")].copy()
    if incoming.empty or outgoing.empty:
        return pd.DataFrame()
    incoming_lookup = incoming[["message_id_clean", "parsed_date", "clean_from", "subject"]].drop_duplicates("message_id_clean").rename(columns={"message_id_clean": "parent_message_id", "parsed_date": "received_at", "clean_from": "contact_email", "subject": "original_subject"})
    outgoing_lookup = outgoing[["in_reply_to_clean", "parsed_date", "subject"]].rename(columns={"in_reply_to_clean": "parent_message_id", "parsed_date": "response_at", "subject": "response_subject"})
    response_df = outgoing_lookup.merge(incoming_lookup, on="parent_message_id", how="inner")
    if response_df.empty:
        return response_df
    response_df["response_time"] = response_df["response_at"] - response_df["received_at"]
    response_df = response_df[response_df["response_time"] >= pd.Timedelta(0)].copy()
    response_df = response_df[response_df["response_time"] <= pd.Timedelta(days=30)].copy()
    return response_df


# ============================================================
# АНАЛІЗ ТЕМ
# ============================================================

def tokenize_subject(subject):
    if not subject:
        return []
    text = re.sub(r"[^\w\s]", " ", str(subject).lower(), flags=re.UNICODE)
    tokens = text.split()
    cleaned = []
    for word in tokens:
        if len(word) <= 2:
            continue
        if word in STOP_WORDS:
            continue
        if word.isdigit():
            continue
        cleaned.append(word)
    return cleaned


def analyze_topics(df):
    words = []
    for subject in df["subject"].dropna():
        words.extend(tokenize_subject(subject))
    word_counts = Counter(words)
    words_df = pd.DataFrame(word_counts.most_common(30), columns=["Слово", "Кількість"])
    bigrams = []
    for subject in df["subject"].dropna():
        tokens = tokenize_subject(subject)
        for i in range(len(tokens)-1):
            first = tokens[i]
            second = tokens[i+1]
            if first != second:
                bigrams.append(f"{first} {second}")
    bigram_counts = Counter(bigrams)
    bigrams_df = pd.DataFrame(bigram_counts.most_common(30), columns=["Фраза", "Кількість"])
    return words_df, bigrams_df


# ============================================================
# РЕЙТИНГ КОНТАКТІВ
# ============================================================

def build_contact_ranking(df):
    incoming = df[df["direction"] == "Вхідний"].copy()
    if incoming.empty:
        return pd.DataFrame()
    contacts = incoming.groupby(["clean_from", "sender_name"]).agg(
        total_emails=("subject", "count"),
        first_contact=("date_only", "min"),
        last_contact=("date_only", "max"),
        outside_hours=("is_outside_work_hours", "sum"),
    ).reset_index()
    if contacts.empty:
        return pd.DataFrame()
    contacts["total_emails"] = pd.to_numeric(contacts["total_emails"], errors="coerce").fillna(0).astype(int)
    if contacts["total_emails"].sum() == 0:
        return pd.DataFrame()
    max_volume = contacts["total_emails"].max()
    contacts["volume_score"] = contacts["total_emails"] / max_volume * 50
    contacts["first_contact"] = pd.to_datetime(contacts["first_contact"])
    contacts["last_contact"] = pd.to_datetime(contacts["last_contact"])
    contacts["days_active"] = (contacts["last_contact"] - contacts["first_contact"]).dt.days + 1
    contacts["frequency"] = contacts["total_emails"] / contacts["days_active"].clip(lower=1)
    max_frequency = max(contacts["frequency"].max(), 0.0001)
    contacts["frequency_score"] = contacts["frequency"] / max_frequency * 25
    latest_date_series = df["date_only"].dropna()
    if latest_date_series.empty:
        latest_date = date.today()
    else:
        latest_date = latest_date_series.max()
        if hasattr(latest_date, 'date'):
            latest_date = latest_date.date()
    contacts["days_since_contact"] = (pd.to_datetime(latest_date) - contacts["last_contact"]).dt.days
    contacts["recency_score"] = 25 * (1 / (1 + contacts["days_since_contact"] / 30))
    contacts["contact_score"] = (contacts["volume_score"] + contacts["frequency_score"] + contacts["recency_score"]).round(1)
    contacts = contacts.sort_values("contact_score", ascending=False)
    return contacts


# ============================================================
# ПЕРЕВІРКА DATAFRAME
# ============================================================

def has_valid_data():
    if "raw_data" not in st.session_state:
        return False
    df = st.session_state["raw_data"]
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return False
    return "direction" in df.columns


# ============================================================
# SIDEBAR
# ============================================================

is_data_loaded = has_valid_data()

with st.sidebar:
    with st.expander("🔑 Авторизація Gmail", expanded=not is_data_loaded):
        user_email = st.text_input("Email", placeholder="name@gmail.com")
        app_password = st.text_input("Пароль додатка", type="password", placeholder="abcd efgh ijkl mnop", help="Використовуйте пароль додатка Gmail.")
    st.divider()
    st.subheader("🗓️ Період аналізу")
    period = st.selectbox("Оберіть період", ["Останні 7 днів", "Останні 30 днів", "Останні 3 місяці", "Останні 6 місяців", "Останній рік", "Поточний рік", "Весь доступний період", "Власний період"], index=2)
    if period == "Власний період":
        custom_start = st.date_input("Початкова дата", value=date.today() - timedelta(days=90))
        custom_end = st.date_input("Кінцева дата", value=date.today())
        start_date = custom_start
        end_date = custom_end
    else:
        start_date, end_date = calculate_period(period)
    st.caption(get_period_description(start_date, end_date))
    st.divider()
    btn_fetch = st.button("🔄 Завантажити пошту", type="primary", use_container_width=True)


# ============================================================
# ЗАВАНТАЖЕННЯ
# ============================================================

if btn_fetch:
    st.session_state.pop("raw_data", None)
    if not user_email:
        st.sidebar.error("⚠️ Введіть Email.")
    elif not app_password:
        st.sidebar.error("⚠️ Введіть пароль додатка.")
    elif start_date and end_date and start_date > end_date:
        st.sidebar.error("⚠️ Початкова дата не може бути пізніше кінцевої.")
    else:
        with st.spinner("Отримання листів з Gmail..."):
            raw_df = fetch_emails_imap(user_email, app_password, start_date, end_date)
            if raw_df is not None and not raw_df.empty:
                analyzed = analyze_emails(raw_df, user_email)
                st.session_state["raw_data"] = analyzed
                st.session_state["user_email"] = user_email
                st.session_state["period_description"] = get_period_description(start_date, end_date)
                st.rerun()


# ============================================================
# ОСНОВНИЙ ДОДАТОК
# ============================================================

if has_valid_data():
    full_df = st.session_state["raw_data"]
    df = full_df.copy()

    total_count = len(df)
    incoming_count = (df["direction"] == "Вхідний").sum()
    outgoing_count = (df["direction"] == "Надісланий").sum()
    unique_contacts = df["clean_from"].replace("", pd.NA).nunique()
    outside_hours = df["is_outside_work_hours"].sum()
    response_df = calculate_response_times(df)

    thread_analysis = build_threads_analysis(df)
    thread_df = thread_analysis["thread_df"] if thread_analysis else None

    st.title("📊 Gmail Pro Analytics")
    st.caption("Персональна аналітика електронної пошти")
    st.caption(f"📅 {st.session_state.get('period_description', '')}")
    st.divider()

    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric("📧 Всього листів", f"{total_count:,}".replace(",", " "))
    with col2:
        st.metric("📥 Вхідні", f"{incoming_count:,}".replace(",", " "))
    with col3:
        st.metric("📤 Надіслані", f"{outgoing_count:,}".replace(",", " "))
    with col4:
        st.metric("👥 Контактів", f"{unique_contacts:,}".replace(",", " "))
    with col5:
        outside_pct = (outside_hours / total_count * 100) if total_count else 0
        st.metric("🌙 Поза робочим часом", f"{outside_pct:.1f}%")

    st.divider()

    tab_dashboard, tab_contacts, tab_activity, tab_topics, tab_productivity, tab_response_time, tab_trends, tab_threads = st.tabs(
        ["🏠 Огляд", "👥 Контакти", "🔥 Активність", "🔤 Теми", "⚖️ Продуктивність", "⏱️ Час відповіді", "📈 Динаміка", "🧵 Цепочки"]
    )

    # ========== TAB 1 — ОГЛЯД ==========
    with tab_dashboard:
        st.subheader("🏠 Загальний огляд")
        direction_df = pd.DataFrame({"Тип": ["Вхідні", "Надіслані"], "Кількість": [incoming_count, outgoing_count]})
        direction_chart = alt.Chart(direction_df).mark_bar().encode(
            x=alt.X("Тип:N", title=None),
            y=alt.Y("Кількість:Q", title="Листів"),
            tooltip=["Тип", "Кількість"]
        ).properties(height=350)
        st.altair_chart(direction_chart, use_container_width=True)

        st.subheader("💡 Основні інсайти")
        daily = df.groupby("date_only").size()
        if not daily.empty:
            peak_date = daily.idxmax()
            peak_count = daily.max()
            st.markdown(f"<div class='insight-card'>🏆 <b>Найбільш завантажений день:</b> {peak_date.strftime('%d.%m.%Y')} — {peak_count} листів.</div>", unsafe_allow_html=True)
        if not df.empty:
            hour_counts = df["hour"].value_counts()
            peak_hour = hour_counts.idxmax()
            peak_hour_count = hour_counts.max()
            st.markdown(f"<div class='insight-card'>⏰ <b>Найактивніша година:</b> {peak_hour}:00 — {peak_hour_count} листів.</div>", unsafe_allow_html=True)
        if total_count:
            weekend_count = (df["day_num"] >= 5).sum()
            weekend_pct = weekend_count / total_count * 100
            st.markdown(f"<div class='insight-card'>☕ <b>Листи у вихідні:</b> {weekend_count} ({weekend_pct:.1f}%).</div>", unsafe_allow_html=True)
        if not response_df.empty:
            median_response = response_df["response_time"].median()
            st.markdown(f"<div class='insight-card'>⏱️ <b>Медіанний час відповіді:</b> {format_timedelta(median_response)}.</div>", unsafe_allow_html=True)

    # ========== TAB 2 — КОНТАКТИ ==========
    with tab_contacts:
        st.subheader("🌟 Найактивніші контакти")
        contacts = build_contact_ranking(df)
        if contacts.empty:
            st.info("Немає даних про вхідні контакти.")
        else:
            contacts_display = contacts.head(30).copy()
            contacts_display["Частка пошти"] = (contacts_display["total_emails"] / total_count * 100).round(1)
            contacts_display.rename(columns={
                "clean_from": "Email",
                "sender_name": "Ім'я",
                "total_emails": "Листів",
                "first_contact": "Перший контакт",
                "last_contact": "Останній контакт",
                "contact_score": "Рейтинг контакту ℹ️",
                "days_since_contact": "Днів від останнього контакту",
            }, inplace=True)
            st.caption("ℹ️ **Рейтинг контакту** = (обсяг листів × 50 / max_volume) + (частота × 25 / max_frequency) + (актуальність × 25 / (1 + днів_від_контакту/30)). Враховується кількість листів, регулярність спілкування та давність останнього контакту.")
            st.dataframe(
                contacts_display[["Email", "Ім'я", "Листів", "Частка пошти", "Рейтинг контакту ℹ️", "Перший контакт", "Останній контакт", "Днів від останнього контакту"]],
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Рейтинг контакту ℹ️": st.column_config.Column(help="Рейтинг = (обсяг листів*50/max_volume) + (частота*25/max_frequency) + (актуальність*25/(1+днів_від_контакту/30))")
                }
            )

        st.subheader("👥 Найактивніші відправники")
        sender_df = df[df["direction"] == "Вхідний"]["clean_from"].value_counts().head(20).reset_index()
        if not sender_df.empty:
            sender_df.columns = ["Email", "Кількість"]
            sender_chart = alt.Chart(sender_df).mark_bar().encode(
                x=alt.X("Кількість:Q", title="Листів"),
                y=alt.Y("Email:N", sort="-x", title="Відправник"),
                tooltip=["Email", "Кількість"]
            ).properties(height=500)
            st.altair_chart(sender_chart, use_container_width=True)

    # ========== TAB 3 — АКТИВНІСТЬ ==========
    with tab_activity:
        st.subheader("🔥 Активність за днями та годинами")
        clean_hm = df.dropna(subset=["day_num", "hour"]).copy()
        if not clean_hm.empty:
            day_map = {0: "1. Понеділок", 1: "2. Вівторок", 2: "3. Середа", 3: "4. Четвер", 4: "5. П'ятниця", 5: "6. Субота", 6: "7. Неділя"}
            clean_hm["День"] = clean_hm["day_num"].map(day_map)
            heatmap_data = clean_hm.groupby(["День", "hour"]).size().reset_index(name="Кількість")
            chart = alt.Chart(heatmap_data).mark_rect().encode(
                x=alt.X("hour:O", title="Година"),
                y=alt.Y("День:O", sort=list(day_map.values()), title="День"),
                color=alt.Color("Кількість:Q", scale=alt.Scale(scheme="reds"), title="Листів"),
                tooltip=["День", "hour", "Кількість"]
            ).properties(height=350)
            st.altair_chart(chart, use_container_width=True)

        st.subheader("🕐 Робочий та неробочий час")
        work_count = df["is_work_hours"].sum()
        outside_count = df["is_outside_work_hours"].sum()
        c1, c2, c3 = st.columns(3)
        with c1:
            st.metric("💼 Робочий час", work_count)
        with c2:
            st.metric("🌙 Поза робочим часом", outside_count)
        with c3:
            pct = (outside_count / total_count * 100) if total_count else 0
            st.metric("Частка поза робочим часом", f"{pct:.1f}%")

    # ========== TAB 4 — ТЕМИ ==========
    with tab_topics:
        st.subheader("🔤 Аналіз тем листів")
        words_df, bigrams_df = analyze_topics(df)
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("### Найчастіші слова")
            if not words_df.empty:
                chart = alt.Chart(words_df.head(20)).mark_bar().encode(
                    x=alt.X("Кількість:Q", title="Згадувань"),
                    y=alt.Y("Слово:N", sort="-x"),
                    tooltip=["Слово", "Кількість"]
                ).properties(height=500)
                st.altair_chart(chart, use_container_width=True)
            else:
                st.info("Недостатньо даних.")
        with col2:
            st.markdown("### Найчастіші фрази")
            if not bigrams_df.empty:
                chart = alt.Chart(bigrams_df.head(20)).mark_bar().encode(
                    x=alt.X("Кількість:Q", title="Згадувань"),
                    y=alt.Y("Фраза:N", sort="-x"),
                    tooltip=["Фраза", "Кількість"]
                ).properties(height=500)
                st.altair_chart(chart, use_container_width=True)
            else:
                st.info("Недостатньо даних.")
        st.divider()
        reply_count = df["is_reply_subject"].sum()
        st.metric("↩️ Листів із позначкою відповіді", reply_count)

    # ========== TAB 5 — ПРОДУКТИВНІСТЬ ==========
    with tab_productivity:
        st.subheader("⚖️ Email Productivity")
        unique_days = df["date_only"].nunique()
        workday_df = df[df["day_num"] < 5]
        workday_count = len(workday_df)
        workday_unique_days = workday_df["date_only"].nunique()
        avg_per_day = total_count / max(unique_days, 1)
        avg_workday = workday_count / max(workday_unique_days, 1)
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.metric("📅 Середньо листів/день", f"{avg_per_day:.1f}")
        with c2:
            st.metric("💼 Середньо у робочий день", f"{avg_workday:.1f}")
        with c3:
            st.metric("📥 Вхідні", incoming_count)
        with c4:
            st.metric("📤 Надіслані", outgoing_count)
        st.divider()
        st.subheader("📊 Робочі дні vs вихідні")
        productivity_df = pd.DataFrame({"Тип дня": ["Робочі дні", "Вихідні"], "Кількість": [workday_count, (df["day_num"] >= 5).sum()]})
        chart = alt.Chart(productivity_df).mark_bar().encode(
            x=alt.X("Тип дня:N", title=None),
            y=alt.Y("Кількість:Q", title="Листів"),
            tooltip=["Тип дня", "Кількість"]
        ).properties(height=350)
        st.altair_chart(chart, use_container_width=True)

        st.subheader("🌙 Пошта поза робочим часом")
        outside_direction = df[df["is_outside_work_hours"]].groupby("direction").size().reset_index(name="Кількість")
        if not outside_direction.empty:
            chart = alt.Chart(outside_direction).mark_bar().encode(
                x=alt.X("direction:N", title=None),
                y=alt.Y("Кількість:Q", title="Листів"),
                tooltip=["direction", "Кількість"]
            ).properties(height=300)
            st.altair_chart(chart, use_container_width=True)

    # ========== TAB 6 — ЧАС ВІДПОВІДІ ==========
    with tab_response_time:
        st.subheader("⏱️ Час відповіді")
        if response_df.empty:
            st.info("Не вдалося знайти листи, для яких можна достовірно визначити час відповіді.")
            st.caption("Для цього Gmail має передавати Message-ID та In-Reply-To.")
        else:
            avg_response = response_df["response_time"].mean()
            median_response = response_df["response_time"].median()
            fastest_response = response_df["response_time"].min()
            slowest_response = response_df["response_time"].max()
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                st.metric("Середній час", format_timedelta(avg_response))
            with c2:
                st.metric("Медіанний час", format_timedelta(median_response))
            with c3:
                st.metric("Найшвидша відповідь", format_timedelta(fastest_response))
            with c4:
                st.metric("Найдовша відповідь", format_timedelta(slowest_response))

            st.divider()
            st.subheader("👥 Час відповіді за контактами")
            name_map = df[["clean_from", "sender_name"]].drop_duplicates("clean_from")
            name_dict = dict(zip(name_map["clean_from"], name_map["sender_name"]))
            contact_response = response_df.groupby("contact_email")["response_time"].agg(["count", "mean", "median"]).reset_index()
            contact_response["Ім'я"] = contact_response["contact_email"].map(name_dict).fillna("")
            contact_response["Середній час"] = contact_response["mean"].apply(format_timedelta)
            contact_response["Медіанний час"] = contact_response["median"].apply(format_timedelta)
            contact_response.rename(columns={"contact_email": "Контакт", "count": "Відповідей"}, inplace=True)
            st.dataframe(
                contact_response[["Контакт", "Ім'я", "Відповідей", "Середній час", "Медіанний час"]].sort_values("Відповідей", ascending=False),
                use_container_width=True,
                hide_index=True,
            )

    # ========== TAB 7 — ДИНАМІКА ==========
    with tab_trends:
        st.subheader("📈 Динаміка листування")
        monthly = df.groupby(["month", "direction"]).size().reset_index(name="Кількість")
        if not monthly.empty:
            chart = alt.Chart(monthly).mark_line(point=True).encode(
                x=alt.X("month:O", title="Місяць"),
                y=alt.Y("Кількість:Q", title="Листів"),
                color=alt.Color("direction:N", title="Тип"),
                tooltip=["month", "direction", "Кількість"]
            ).properties(height=450)
            st.altair_chart(chart, use_container_width=True)

        st.subheader("🏆 Найбільш завантажені дні")
        daily_counts = df.groupby("date_only").size().reset_index(name="Кількість").sort_values("Кількість", ascending=False).head(10)
        if not daily_counts.empty:
            daily_display = daily_counts.rename(columns={"date_only": "Дата", "Кількість": "Листів"}).copy()
            st.dataframe(daily_display, use_container_width=True, hide_index=True)

    # ========== TAB 8 — ЦЕПОЧКИ ==========
    with tab_threads:
        st.subheader("🧵 Аналіз цепочок листування")

        if thread_analysis is None or thread_df is None or thread_df.empty:
            st.info("Недостатньо даних для аналізу цепочок. Переконайтеся, що у листів є Message-ID та In-Reply-To/References.")
        else:
            col1, col2, col3, col4, col5 = st.columns(5)
            with col1:
                st.metric("🧵 Всього цепочок", thread_analysis["total_threads"])
            with col2:
                st.metric("📊 Середня довжина", f"{thread_analysis['avg_length']:.1f}")
            with col3:
                st.metric("📊 Медіанна довжина", f"{thread_analysis['median_length']:.0f}")
            with col4:
                st.metric("📈 Найдовша цепочка", f"{thread_analysis['max_length']} листів")
            with col5:
                st.metric("🔄 Відповідей", f"{thread_analysis['reply_rate']*100:.1f}%")

            st.divider()

            st.subheader("📊 Розподіл цепочок за довжиною")
            if not thread_analysis["length_dist"].empty:
                chart = alt.Chart(thread_analysis["length_dist"]).mark_bar().encode(
                    x=alt.X("Довжина:O", title="Кількість листів у цепочці"),
                    y=alt.Y("Кількість:Q", title="Кількість цепочок"),
                    tooltip=["Довжина", "Кількість"]
                ).properties(height=300)
                st.altair_chart(chart, use_container_width=True)

            st.subheader("🏆 Найдовші цепочки")
            longest_threads = thread_df.nlargest(10, "length")
            if not longest_threads.empty:
                display = longest_threads[["subject", "length", "initiator_name", "num_participants", "first_date", "last_date"]].copy()
                display["first_date"] = display["first_date"].dt.strftime("%d.%m.%Y %H:%M")
                display["last_date"] = display["last_date"].dt.strftime("%d.%m.%Y %H:%M")
                display.rename(columns={
                    "subject": "Тема",
                    "length": "Листів",
                    "initiator_name": "Ініціатор",
                    "num_participants": "Учасників",
                    "first_date": "Початок",
                    "last_date": "Кінець"
                }, inplace=True)
                st.dataframe(display, use_container_width=True, hide_index=True)

            st.subheader("🔄 Статистика відповідей у цепочках")
            c1, c2, c3 = st.columns(3)
            with c1:
                st.metric("Цепочок з відповідями (>1 листа)", thread_analysis["threads_with_reply"])
            with c2:
                st.metric("Цепочок без відповідей (1 лист)", thread_analysis["total_threads"] - thread_analysis["threads_with_reply"])
            with c3:
                st.metric("Середня кількість учасників", f"{thread_analysis['avg_participants']:.1f}")

            st.subheader("⏳ Завислі цепочки (без відповіді >7 днів)")
            if thread_analysis["stale_count"] > 0:
                stale_display = thread_analysis["stale_df"][["subject", "length", "last_date", "initiator_name"]].copy()
                stale_display["last_date"] = stale_display["last_date"].dt.strftime("%d.%m.%Y %H:%M")
                stale_display.rename(columns={
                    "subject": "Тема",
                    "length": "Листів",
                    "last_date": "Останній лист",
                    "initiator_name": "Ініціатор"
                }, inplace=True)
                st.dataframe(stale_display.head(20), use_container_width=True, hide_index=True)
                st.caption(f"Всього завислих: {thread_analysis['stale_count']}")
            else:
                st.success("✅ Немає завислих цепочок (усі мають відповідь протягом останніх 7 днів).")

            st.subheader("👥 Топ контактів за кількістю цепочок")
            if not thread_analysis["initiator_counts"].empty:
                chart = alt.Chart(thread_analysis["initiator_counts"].head(10)).mark_bar().encode(
                    x=alt.X("Цепочок:Q", title="Кількість цепочок"),
                    y=alt.Y("Контакт:N", sort="-x", title="Контакт"),
                    tooltip=["Контакт", "Цепочок"]
                ).properties(height=350)
                st.altair_chart(chart, use_container_width=True)

            st.subheader("📈 Динаміка нових цепочок за місяцями")
            if not thread_analysis["monthly_new"].empty:
                chart = alt.Chart(thread_analysis["monthly_new"]).mark_bar().encode(
                    x=alt.X("first_month:O", title="Місяць"),
                    y=alt.Y("new_threads:Q", title="Нових цепочок"),
                    tooltip=["first_month", "new_threads"]
                ).properties(height=300)
                st.altair_chart(chart, use_container_width=True)

else:
    st.title("📊 Gmail Pro Analytics")
    st.info("👈 Відкрийте «Авторизація Gmail», введіть Email та пароль додатка, оберіть період і натисніть «Завантажити пошту».")
