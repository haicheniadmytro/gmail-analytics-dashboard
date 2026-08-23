from collections import Counter
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
    """Розшифровує MIME-заголовки листа."""

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
                result.append(
                    fragment.decode(
                        charset,
                        errors="ignore",
                    )
                )
            except Exception:
                result.append(
                    fragment.decode(
                        "latin1",
                        errors="ignore",
                    )
                )

        else:
            result.append(str(fragment))

    return "".join(result)


def parse_date(date_str):
    """Парсить дату листа."""

    if not date_str:
        return None

    try:
        return parsedate_to_datetime(date_str)
    except Exception:
        return None


def extract_email_address(value):
    """Витягує email із From / To."""

    if not value:
        return ""

    match = re.search(
        r"[\w.!#$%&'*+/=?^_`{|}~-]+@[\w.-]+\.[A-Za-z]{2,}",
        value,
    )

    if match:
        return match.group(0).lower()

    return value.lower().strip()


def extract_name(from_header):
    """Витягує ім'я відправника."""

    if not from_header:
        return ""

    if "<" in from_header:

        name = (
            from_header
            .split("<")[0]
            .strip()
            .strip('"')
            .strip("'")
        )

        if name:
            return name

    return extract_email_address(from_header)


def normalize_subject(subject):
    """
    Нормалізує тему:
    Re: Договір
    Fwd: Re: Договір
    -> договір
    """

    if not subject:
        return ""

    subject = str(subject).lower().strip()

    previous = None

    while previous != subject:

        previous = subject

        subject = re.sub(
            r"^\s*((re|fw|fwd|відповідь|переслано)\s*:\s*)+",
            "",
            subject,
            flags=re.IGNORECASE,
        )

    subject = re.sub(
        r"\s+",
        " ",
        subject,
    )

    return subject.strip()


def is_reply_subject(subject):
    """Визначає Re:/Fwd:/Відповідь."""

    if not subject:
        return False

    return bool(
        re.match(
            r"^\s*(re|fw|fwd|відповідь)\s*:",
            str(subject),
            flags=re.IGNORECASE,
        )
    )


def calculate_period(period_name):
    """Розрахунок періоду."""

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
    """Текстове представлення періоду."""

    if start_date is None:
        return "Весь доступний період"

    return (
        f"{start_date.strftime('%d.%m.%Y')} — "
        f"{end_date.strftime('%d.%m.%Y')}"
    )


def format_timedelta(value):
    """Красиве представлення timedelta."""

    if value is None or pd.isna(value):
        return "—"

    total_seconds = int(
        value.total_seconds()
    )

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
    """
    Автоматично знаходить папку Gmail для надісланих листів.
    """

    try:

        status, folders = mail.list()

        if status != "OK":
            return "[Gmail]/Sent Mail"

        for folder in folders:

            if not folder:
                continue

            decoded = folder.decode(
                "utf-8",
                errors="ignore",
            )

            # Gmail може повертати \Sent
            if r"\Sent" in decoded:

                # Витягуємо назву папки
                match = re.search(
                    r'"([^"]+)"\s*$',
                    decoded,
                )

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

def search_folder(
    mail,
    folder,
    start_date=None,
    end_date=None,
):
    """
    Шукає листи в конкретній папці.
    """

    try:

        status, _ = mail.select(
            f'"{folder}"'
        )

        if status != "OK":
            return []

        if start_date is None:

            status, data = mail.search(
                None,
                "ALL",
            )

        else:

            since_date = start_date.strftime(
                "%d-%b-%Y"
            )

            if end_date:

                before_date = (
                    end_date
                    + timedelta(days=1)
                ).strftime(
                    "%d-%b-%Y"
                )

                status, data = mail.search(
                    None,
                    "SINCE",
                    since_date,
                    "BEFORE",
                    before_date,
                )

            else:

                status, data = mail.search(
                    None,
                    "SINCE",
                    since_date,
                )

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

def fetch_folder_headers(
    mail,
    folder,
    start_date=None,
    end_date=None,
    max_results=20000,
    folder_type="inbox",
):
    """
    Завантажує тільки заголовки листів.

    ВАЖЛИВО:
    IMAP команда повинна бути одним рядком.
    """

    ids = search_folder(
        mail=mail,
        folder=folder,
        start_date=start_date,
        end_date=end_date,
    )

    if not ids:
        return []

    # Беремо останні листи
    ids = ids[-int(max_results):]

    # Новіші спочатку
    ids.reverse()

    result = []

    chunk_size = 500

    total = len(ids)

    progress_bar = st.progress(
        0
    )

    status_text = st.empty()

    for i in range(
        0,
        total,
        chunk_size,
    ):

        chunk_ids = ids[
            i : i + chunk_size
        ]

        ids_str = b",".join(
            chunk_ids
        )

        status_text.text(
            f"Завантаження "
            f"{i + 1:,}–"
            f"{min(i + chunk_size, total):,} "
            f"з {total:,}..."
        )

        try:

            # НЕ переносимо цей рядок на декілька рядків!
            status, msg_data = mail.fetch(
                ids_str,
                "(BODY.PEEK[HEADER.FIELDS (DATE FROM TO SUBJECT MESSAGE-ID IN-REPLY-TO REFERENCES)])",
            )

        except Exception as e:

            st.warning(
                f"⚠️ Не вдалося отримати частину листів: {e}"
            )

            continue

        if status != "OK":
            continue

        for response_part in msg_data:

            if not isinstance(
                response_part,
                tuple,
            ):
                continue

            try:

                msg = message_from_bytes(
                    response_part[1]
                )

                result.append(
                    {
                        "date": msg.get(
                            "Date",
                            "",
                        ),
                        "from": decode_mime_words(
                            msg.get(
                                "From",
                                "",
                            )
                        ),
                        "to": decode_mime_words(
                            msg.get(
                                "To",
                                "",
                            )
                        ),
                        "subject": decode_mime_words(
                            msg.get(
                                "Subject",
                                "",
                            )
                        ),
                        "message_id": msg.get(
                            "Message-ID",
                            "",
                        ),
                        "in_reply_to": msg.get(
                            "In-Reply-To",
                            "",
                        ),
                        "references": msg.get(
                            "References",
                            "",
                        ),
                        "folder_type": folder_type,
                    }
                )

            except Exception:
                continue

        progress_bar.progress(
            min(
                (i + chunk_size)
                / max(total, 1),
                1.0,
            )
        )

    progress_bar.empty()
    status_text.empty()

    return result


# ============================================================
# ОСНОВНЕ ЗАВАНТАЖЕННЯ GMAIL
# ============================================================

def fetch_emails_imap(
    email_user,
    app_password,
    start_date=None,
    end_date=None,
    max_results=20000,
):
    """
    Завантажує Inbox + Sent.
    """

    mail = None

    try:

        mail = imaplib.IMAP4_SSL(
            "imap.gmail.com"
        )

        clean_password = (
            app_password
            .replace(" ", "")
            .replace("\n", "")
            .replace("\r", "")
        )

        mail.login(
            email_user.strip(),
            clean_password,
        )

        # ----------------------------------------------------
        # INBOX
        # ----------------------------------------------------

        st.info(
            "📥 Завантаження вхідних листів..."
        )

        inbox_data = fetch_folder_headers(
            mail=mail,
            folder="INBOX",
            start_date=start_date,
            end_date=end_date,
            max_results=max_results,
            folder_type="inbox",
        )

        # ----------------------------------------------------
        # SENT
        # ----------------------------------------------------

        sent_folder = find_sent_folder(
            mail
        )

        st.info(
            f"📤 Завантаження надісланих листів "
            f"({sent_folder})..."
        )

        sent_data = fetch_folder_headers(
            mail=mail,
            folder=sent_folder,
            start_date=start_date,
            end_date=end_date,
            max_results=max_results,
            folder_type="sent",
        )

        # ----------------------------------------------------
        # LOGOUT
        # ----------------------------------------------------

        try:
            mail.logout()
        except Exception:
            pass

        all_data = (
            inbox_data
            + sent_data
        )

        if not all_data:

            st.warning(
                "За обраний період листів не знайдено."
            )

            return None

        df = pd.DataFrame(
            all_data
        )

        st.success(
            "✅ Завантажено "
            f"{len(df):,} листів "
            f"(вхідні: {len(inbox_data):,}, "
            f"надіслані: {len(sent_data):,})"
            .replace(",", " ")
        )

        return df

    except imaplib.IMAP4.error:

        st.error(
            "❌ Помилка авторизації Gmail.\n\n"
            "Перевір:\n"
            "• правильність Email;\n"
            "• пароль додатка Gmail;\n"
            "• що використовується саме App Password, "
            "а не звичайний пароль Google."
        )

        return None

    except Exception as e:

        st.error(
            f"❌ Помилка підключення до Gmail: {e}"
        )

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

def analyze_emails(
    df,
    user_email,
):
    """
    Підготовка даних для аналітики.
    """

    df = df.copy()

    # --------------------------------------------------------
    # Переконуємося, що необхідні колонки існують
    # --------------------------------------------------------

    required_columns = [
        "date",
        "from",
        "to",
        "subject",
        "message_id",
        "in_reply_to",
        "references",
        "folder_type",
    ]

    for column in required_columns:

        if column not in df.columns:
            df[column] = ""

    # --------------------------------------------------------
    # Дата
    # --------------------------------------------------------

    df["parsed_date"] = pd.to_datetime(
        df["date"],
        utc=True,
        errors="coerce",
    )

    # Переводимо UTC → Київ
    df["parsed_date"] = (
        df["parsed_date"]
        .dt.tz_convert(
            TIMEZONE
        )
    )

    df["date_only"] = (
        df["parsed_date"].dt.date
    )

    df["year"] = (
        df["parsed_date"].dt.year
    )

    df["month"] = (
        df["parsed_date"]
        .dt.strftime("%Y-%m")
    )

    df["hour"] = (
        df["parsed_date"].dt.hour
    )

    df["day_name"] = (
        df["parsed_date"]
        .dt.day_name()
    )

    df["day_num"] = (
        df["parsed_date"]
        .dt.dayofweek
    )

    # --------------------------------------------------------
    # Відправник
    # --------------------------------------------------------

    df["clean_from"] = (
        df["from"]
        .fillna("")
        .apply(
            extract_email_address
        )
    )

    df["sender_name"] = (
        df["from"]
        .fillna("")
        .apply(
            extract_name
        )
    )

    # --------------------------------------------------------
    # Отримувач
    # --------------------------------------------------------

    df["clean_to"] = (
        df["to"]
        .fillna("")
        .apply(
            extract_email_address
        )
    )

    # --------------------------------------------------------
    # Тема
    # --------------------------------------------------------

    df["subject"] = (
        df["subject"]
        .fillna("")
        .astype(str)
    )

    df["normalized_subject"] = (
        df["subject"]
        .apply(
            normalize_subject
        )
    )

    df["is_reply_subject"] = (
        df["subject"]
        .apply(
            is_reply_subject
        )
    )

    # --------------------------------------------------------
    # Message-ID
    # --------------------------------------------------------

    df["message_id_clean"] = (
        df["message_id"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    df["in_reply_to_clean"] = (
        df["in_reply_to"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    df["references_clean"] = (
        df["references"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    # --------------------------------------------------------
    # Власна пошта
    # --------------------------------------------------------

    normalized_user_email = (
        user_email
        .strip()
        .lower()
    )

    df["is_from_user"] = (
        df["clean_from"]
        == normalized_user_email
    )

    # --------------------------------------------------------
    # Напрямок
    # --------------------------------------------------------

    df["direction"] = (
        df["folder_type"]
        .map(
            {
                "inbox": "Вхідний",
                "sent": "Надісланий",
            }
        )
        .fillna("Інший")
    )

    # --------------------------------------------------------
    # Робочий день
    # --------------------------------------------------------

    df["is_workday"] = (
        df["day_num"] < 5
    )

    # --------------------------------------------------------
    # Робочий час
    # --------------------------------------------------------

    df["is_work_hours"] = (
        df["is_workday"]
        & (
            df["hour"]
            >= WORK_START
        )
        & (
            df["hour"]
            < WORK_END
        )
    )

    df["is_outside_work_hours"] = (
        ~df["is_work_hours"]
    )

    return df


# ============================================================
# ЧАС ВІДПОВІДІ
# ============================================================

def calculate_response_times(df):
    """
    Розрахунок часу відповіді.

    Вхідний лист:
        Message-ID = ABC

    Відповідь:
        In-Reply-To = ABC

    Тоді:
        час відповіді =
        дата відповіді - дата вхідного листа
    """

    if df.empty:
        return pd.DataFrame()

    incoming = df[
        (
            df["direction"]
            == "Вхідний"
        )
        & (
            df["message_id_clean"]
            != ""
        )
    ].copy()

    outgoing = df[
        (
            df["direction"]
            == "Надісланий"
        )
        & (
            df["in_reply_to_clean"]
            != ""
        )
    ].copy()

    if incoming.empty:
        return pd.DataFrame()

    if outgoing.empty:
        return pd.DataFrame()

    incoming_lookup = (
        incoming[
            [
                "message_id_clean",
                "parsed_date",
                "clean_from",
                "subject",
            ]
        ]
        .drop_duplicates(
            "message_id_clean"
        )
        .rename(
            columns={
                "message_id_clean":
                    "parent_message_id",
                "parsed_date":
                    "received_at",
                "clean_from":
                    "contact_email",
                "subject":
                    "original_subject",
            }
        )
    )

    outgoing_lookup = (
        outgoing[
            [
                "in_reply_to_clean",
                "parsed_date",
                "subject",
            ]
        ]
        .rename(
            columns={
                "in_reply_to_clean":
                    "parent_message_id",
                "parsed_date":
                    "response_at",
                "subject":
                    "response_subject",
            }
        )
    )

    response_df = outgoing_lookup.merge(
        incoming_lookup,
        on="parent_message_id",
        how="inner",
    )

    if response_df.empty:
        return response_df

    response_df[
        "response_time"
    ] = (
        response_df["response_at"]
        - response_df["received_at"]
    )

    # Тільки логічні відповіді
    response_df = response_df[
        response_df[
            "response_time"
        ]
        >= pd.Timedelta(0)
    ].copy()

    # Не враховуємо надзвичайно великі значення
    # у статистиці більше 30 днів
    response_df = response_df[
        response_df[
            "response_time"
        ]
        <= pd.Timedelta(days=30)
    ].copy()

    return response_df


# ============================================================
# АНАЛІЗ ТЕМ
# ============================================================

def tokenize_subject(subject):

    if not subject:
        return []

    text = re.sub(
        r"[^\w\s]",
        " ",
        str(subject).lower(),
        flags=re.UNICODE,
    )

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

    for subject in df[
        "subject"
    ].dropna():

        words.extend(
            tokenize_subject(
                subject
            )
        )

    word_counts = Counter(
        words
    )

    words_df = pd.DataFrame(
        word_counts.most_common(30),
        columns=[
            "Слово",
            "Кількість",
        ],
    )

    # --------------------------------------------------------
    # Фрази з двох слів
    # --------------------------------------------------------

    bigrams = []

    for subject in df[
        "subject"
    ].dropna():

        tokens = tokenize_subject(
            subject
        )

        for i in range(
            len(tokens) - 1
        ):

            first = tokens[i]
            second = tokens[i + 1]

            if first != second:

                bigrams.append(
                    f"{first} {second}"
                )

    bigram_counts = Counter(
        bigrams
    )

    bigrams_df = pd.DataFrame(
        bigram_counts.most_common(30),
        columns=[
            "Фраза",
            "Кількість",
        ],
    )

    return (
        words_df,
        bigrams_df,
    )


# ============================================================
# РЕЙТИНГ КОНТАКТІВ (ВИПРАВЛЕНО)
# ============================================================

def build_contact_ranking(df):
    """
    Побудова рейтингу контактів на основі вхідних листів.
    """

    incoming = df[
        df["direction"]
        == "Вхідний"
    ].copy()

    if incoming.empty:
        return pd.DataFrame()

    contacts = (
        incoming
        .groupby(
            [
                "clean_from",
                "sender_name",
            ]
        )
        .agg(
            total_emails=(
                "subject",
                "count",
            ),
            first_contact=(
                "date_only",
                "min",
            ),
            last_contact=(
                "date_only",
                "max",
            ),
            outside_hours=(
                "is_outside_work_hours",
                "sum",
            ),
        )
        .reset_index()
    )

    if contacts.empty:
        return pd.DataFrame()

    # Переконуємося, що total_emails числові
    contacts["total_emails"] = pd.to_numeric(
        contacts["total_emails"],
        errors="coerce",
    ).fillna(0).astype(int)

    # Якщо немає листів, повертаємо порожній DataFrame
    if contacts["total_emails"].sum() == 0:
        return pd.DataFrame()

    max_volume = contacts["total_emails"].max()

    contacts["volume_score"] = (
        contacts["total_emails"]
        / max_volume
        * 50
    )

    # Тривалість взаємодії
    contacts["first_contact"] = pd.to_datetime(
        contacts["first_contact"]
    )
    contacts["last_contact"] = pd.to_datetime(
        contacts["last_contact"]
    )

    contacts["days_active"] = (
        contacts["last_contact"]
        - contacts["first_contact"]
    ).dt.days + 1

    contacts["frequency"] = (
        contacts["total_emails"]
        / contacts["days_active"].clip(lower=1)
    )

    max_frequency = max(
        contacts["frequency"].max(),
        0.0001,
    )

    contacts["frequency_score"] = (
        contacts["frequency"]
        / max_frequency
        * 25
    )

    # Актуальність
    # Беремо останню дату з усього датафрейму (не тільки вхідні)
    latest_date_series = df["date_only"].dropna()
    if latest_date_series.empty:
        latest_date = date.today()
    else:
        latest_date = latest_date_series.max()
        # Якщо це pandas Timestamp, перетворюємо на date
        if hasattr(latest_date, 'date'):
            latest_date = latest_date.date()

    contacts["days_since_contact"] = (
        pd.to_datetime(latest_date)
        - contacts["last_contact"]
    ).dt.days

    contacts["recency_score"] = (
        25
        * (
            1
            / (
                1
                + contacts["days_since_contact"]
                / 30
            )
        )
    )

    contacts["contact_score"] = (
        contacts["volume_score"]
        + contacts["frequency_score"]
        + contacts["recency_score"]
    ).round(1)

    contacts = contacts.sort_values(
        "contact_score",
        ascending=False,
    )

    return contacts


# ============================================================
# ПЕРЕВІРКА DATAFRAME
# ============================================================

def has_valid_data():

    if (
        "raw_data"
        not in st.session_state
    ):
        return False

    df = st.session_state[
        "raw_data"
    ]

    if df is None:
        return False

    if not isinstance(
        df,
        pd.DataFrame,
    ):
        return False

    if df.empty:
        return False

    return "direction" in df.columns


# ============================================================
# SIDEBAR
# ============================================================

is_data_loaded = has_valid_data()

with st.sidebar:

    with st.expander(
        "🔑 Авторизація Gmail",
        expanded=not is_data_loaded,
    ):

        user_email = st.text_input(
            "Email",
            placeholder="name@gmail.com",
        )

        app_password = st.text_input(
            "Пароль додатка",
            type="password",
            placeholder="abcd efgh ijkl mnop",
            help=(
                "Використовуйте пароль додатка Gmail."
            ),
        )

    st.divider()

    st.subheader(
        "🗓️ Період аналізу"
    )

    period = st.selectbox(
        "Оберіть період",
        [
            "Останні 7 днів",
            "Останні 30 днів",
            "Останні 3 місяці",
            "Останні 6 місяців",
            "Останній рік",
            "Поточний рік",
            "Весь доступний період",
            "Власний період",
        ],
        index=2,
    )

    if period == "Власний період":

        custom_start = st.date_input(
            "Початкова дата",
            value=(
                date.today()
                - timedelta(days=90)
            ),
        )

        custom_end = st.date_input(
            "Кінцева дата",
            value=date.today(),
        )

        start_date = custom_start
        end_date = custom_end

    else:

        start_date, end_date = (
            calculate_period(
                period
            )
        )

    st.caption(
        get_period_description(
            start_date,
            end_date,
        )
    )

    st.divider()

    max_emails = st.number_input(
        "Максимум листів з кожної папки",
        min_value=500,
        max_value=100000,
        value=20000,
        step=1000,
    )

    btn_fetch = st.button(
        "🔄 Завантажити пошту",
        type="primary",
        use_container_width=True,
    )


# ============================================================
# ЗАВАНТАЖЕННЯ
# ============================================================

if btn_fetch:

    # Видаляємо старі дані
    st.session_state.pop(
        "raw_data",
        None,
    )

    if not user_email:

        st.sidebar.error(
            "⚠️ Введіть Email."
        )

    elif not app_password:

        st.sidebar.error(
            "⚠️ Введіть пароль додатка."
        )

    elif (
        start_date
        and end_date
        and start_date > end_date
    ):

        st.sidebar.error(
            "⚠️ Початкова дата "
            "не може бути пізніше "
            "кінцевої."
        )

    else:

        with st.spinner(
            "Отримання листів з Gmail..."
        ):

            raw_df = fetch_emails_imap(
                email_user=user_email,
                app_password=app_password,
                start_date=start_date,
                end_date=end_date,
                max_results=int(
                    max_emails
                ),
            )

            if (
                raw_df is not None
                and not raw_df.empty
            ):

                analyzed = analyze_emails(
                    raw_df,
                    user_email,
                )

                st.session_state[
                    "raw_data"
                ] = analyzed

                st.session_state[
                    "user_email"
                ] = user_email

                st.session_state[
                    "period_description"
                ] = get_period_description(
                    start_date,
                    end_date,
                )

                st.rerun()


# ============================================================
# ОСНОВНИЙ ДОДАТОК
# ============================================================

if has_valid_data():

    full_df = st.session_state[
        "raw_data"
    ]

    df = full_df.copy()

    total_count = len(df)

    incoming_count = (
        df["direction"]
        == "Вхідний"
    ).sum()

    outgoing_count = (
        df["direction"]
        == "Надісланий"
    ).sum()

    unique_contacts = (
        df["clean_from"]
        .replace(
            "",
            pd.NA,
        )
        .nunique()
    )

    outside_hours = (
        df[
            "is_outside_work_hours"
        ]
        .sum()
    )

    response_df = (
        calculate_response_times(
            df
        )
    )

    # ========================================================
    # HEADER
    # ========================================================

    st.title(
        "📊 Gmail Pro Analytics"
    )

    st.caption(
        "Персональна аналітика "
        "електронної пошти"
    )

    st.caption(
        f"📅 "
        f"{st.session_state.get(
            'period_description',
            ''
        )}"
    )

    st.divider()

    # ========================================================
    # KPI
    # ========================================================

    col1, col2, col3, col4, col5 = (
        st.columns(5)
    )

    with col1:
        st.metric(
            "📧 Всього листів",
            f"{total_count:,}"
            .replace(",", " "),
        )

    with col2:
        st.metric(
            "📥 Вхідні",
            f"{incoming_count:,}"
            .replace(",", " "),
        )

    with col3:
        st.metric(
            "📤 Надіслані",
            f"{outgoing_count:,}"
            .replace(",", " "),
        )

    with col4:
        st.metric(
            "👥 Контактів",
            f"{unique_contacts:,}"
            .replace(",", " "),
        )

    with col5:

        outside_pct = (
            outside_hours
            / total_count
            * 100
            if total_count
            else 0
        )

        st.metric(
            "🌙 Поза робочим часом",
            f"{outside_pct:.1f}%",
        )

    st.divider()

    # ========================================================
    # TABS
    # ========================================================

    (
        tab_dashboard,
        tab_contacts,
        tab_activity,
        tab_topics,
        tab_productivity,
        tab_response,
        tab_trends,
    ) = st.tabs(
        [
            "🏠 Огляд",
            "👥 Контакти",
            "🔥 Активність",
            "🔤 Теми",
            "⚖️ Продуктивність",
            "⏱️ Час відповіді",
            "📈 Динаміка",
        ]
    )

    # ========================================================
    # TAB 1 — ОГЛЯД
    # ========================================================

    with tab_dashboard:

        st.subheader(
            "🏠 Загальний огляд"
        )

        direction_df = pd.DataFrame(
            {
                "Тип": [
                    "Вхідні",
                    "Надіслані",
                ],
                "Кількість": [
                    incoming_count,
                    outgoing_count,
                ],
            }
        )

        direction_chart = (
            alt.Chart(
                direction_df
            )
            .mark_bar()
            .encode(
                x=alt.X(
                    "Тип:N",
                    title=None,
                ),
                y=alt.Y(
                    "Кількість:Q",
                    title="Листів",
                ),
                tooltip=[
                    "Тип",
                    "Кількість",
                ],
            )
            .properties(
                height=350
            )
        )

        st.altair_chart(
            direction_chart,
            use_container_width=True,
        )

        st.subheader(
            "💡 Основні інсайти"
        )

        daily = (
            df.groupby(
                "date_only"
            )
            .size()
        )

        if not daily.empty:

            peak_date = daily.idxmax()
            peak_count = daily.max()

            st.markdown(
                f"""
                <div class="insight-card">
                🏆 <b>Найбільш завантажений день:</b>
                {peak_date.strftime('%d.%m.%Y')}
                — {peak_count} листів.
                </div>
                """,
                unsafe_allow_html=True,
            )

        if not df.empty:

            hour_counts = (
                df["hour"]
                .value_counts()
            )

            peak_hour = (
                hour_counts.idxmax()
            )

            peak_hour_count = (
                hour_counts.max()
            )

            st.markdown(
                f"""
                <div class="insight-card">
                ⏰ <b>Найактивніша година:</b>
                {peak_hour}:00
                — {peak_hour_count} листів.
                </div>
                """,
                unsafe_allow_html=True,
            )

        if total_count:

            weekend_count = (
                df["day_num"] >= 5
            ).sum()

            weekend_pct = (
                weekend_count
                / total_count
                * 100
            )

            st.markdown(
                f"""
                <div class="insight-card">
                ☕ <b>Листи у вихідні:</b>
                {weekend_count}
                ({weekend_pct:.1f}%).
                </div>
                """,
                unsafe_allow_html=True,
            )

        if not response_df.empty:

            median_response = (
                response_df[
                    "response_time"
                ].median()
            )

            st.markdown(
                f"""
                <div class="insight-card">
                ⏱️ <b>Медіанний час відповіді:</b>
                {format_timedelta(
                    median_response
                )}.
                </div>
                """,
                unsafe_allow_html=True,
            )

    # ========================================================
    # TAB 2 — КОНТАКТИ
    # ========================================================

    with tab_contacts:

        st.subheader(
            "🌟 Найактивніші контакти"
        )

        contacts = (
            build_contact_ranking(
                df
            )
        )

        if contacts.empty:

            st.info(
                "Немає даних про вхідні контакти."
            )

        else:

            contacts_display = (
                contacts.head(30)
                .copy()
            )

            contacts_display[
                "Частка пошти"
            ] = (
                contacts_display[
                    "total_emails"
                ]
                / total_count
                * 100
            ).round(1)

            contacts_display.rename(
                columns={
                    "clean_from": "Email",
                    "sender_name": "Ім'я",
                    "total_emails": "Листів",
                    "first_contact":
                        "Перший контакт",
                    "last_contact":
                        "Останній контакт",
                    "contact_score":
                        "Рейтинг контакту",
                    "days_since_contact":
                        "Днів від останнього контакту",
                },
                inplace=True,
            )

            st.dataframe(
                contacts_display[
                    [
                        "Email",
                        "Ім'я",
                        "Листів",
                        "Частка пошти",
                        "Рейтинг контакту",
                        "Перший контакт",
                        "Останній контакт",
                        "Днів від останнього контакту",
                    ]
                ],
                use_container_width=True,
                hide_index=True,
            )

        st.subheader(
            "👥 Найактивніші відправники"
        )

        sender_df = (
            df[
                df["direction"]
                == "Вхідний"
            ]["clean_from"]
            .value_counts()
            .head(20)
            .reset_index()
        )

        if not sender_df.empty:

            sender_df.columns = [
                "Email",
                "Кількість",
            ]

            sender_chart = (
                alt.Chart(
                    sender_df
                )
                .mark_bar()
                .encode(
                    x=alt.X(
                        "Кількість:Q",
                        title="Листів",
                    ),
                    y=alt.Y(
                        "Email:N",
                        sort="-x",
                        title="Відправник",
                    ),
                    tooltip=[
                        "Email",
                        "Кількість",
                    ],
                )
                .properties(
                    height=500
                )
            )

            st.altair_chart(
                sender_chart,
                use_container_width=True,
            )

    # ========================================================
    # TAB 3 — АКТИВНІСТЬ
    # ========================================================

    with tab_activity:

        st.subheader(
            "🔥 Активність за днями та годинами"
        )

        clean_hm = df.dropna(
            subset=[
                "day_num",
                "hour",
            ]
        ).copy()

        if not clean_hm.empty:

            day_map = {
                0: "1. Понеділок",
                1: "2. Вівторок",
                2: "3. Середа",
                3: "4. Четвер",
                4: "5. П'ятниця",
                5: "6. Субота",
                6: "7. Неділя",
            }

            clean_hm[
                "День"
            ] = (
                clean_hm[
                    "day_num"
                ].map(
                    day_map
                )
            )

            heatmap_data = (
                clean_hm.groupby(
                    [
                        "День",
                        "hour",
                    ]
                )
                .size()
                .reset_index(
                    name="Кількість"
                )
            )

            chart = (
                alt.Chart(
                    heatmap_data
                )
                .mark_rect()
                .encode(
                    x=alt.X(
                        "hour:O",
                        title="Година",
                    ),
                    y=alt.Y(
                        "День:O",
                        sort=list(
                            day_map.values()
                        ),
                        title="День",
                    ),
                    color=alt.Color(
                        "Кількість:Q",
                        scale=alt.Scale(
                            scheme="reds"
                        ),
                        title="Листів",
                    ),
                    tooltip=[
                        "День",
                        "hour",
                        "Кількість",
                    ],
                )
                .properties(
                    height=350
                )
            )

            st.altair_chart(
                chart,
                use_container_width=True,
            )

        st.subheader(
            "🕐 Робочий та неробочий час"
        )

        work_count = (
            df["is_work_hours"]
            .sum()
        )

        outside_count = (
            df[
                "is_outside_work_hours"
            ]
            .sum()
        )

        c1, c2, c3 = (
            st.columns(3)
        )

        with c1:
            st.metric(
                "💼 Робочий час",
                work_count,
            )

        with c2:
            st.metric(
                "🌙 Поза робочим часом",
                outside_count,
            )

        with c3:

            pct = (
                outside_count
                / total_count
                * 100
                if total_count
                else 0
            )

            st.metric(
                "Частка поза робочим часом",
                f"{pct:.1f}%",
            )

    # ========================================================
    # TAB 4 — ТЕМИ
    # ========================================================

    with tab_topics:

        st.subheader(
            "🔤 Аналіз тем листів"
        )

        words_df, bigrams_df = (
            analyze_topics(
                df
            )
        )

        col1, col2 = (
            st.columns(2)
        )

        with col1:

            st.markdown(
                "### Найчастіші слова"
            )

            if not words_df.empty:

                chart = (
                    alt.Chart(
                        words_df.head(20)
                    )
                    .mark_bar()
                    .encode(
                        x=alt.X(
                            "Кількість:Q",
                            title="Згадувань",
                        ),
                        y=alt.Y(
                            "Слово:N",
                            sort="-x",
                        ),
                        tooltip=[
                            "Слово",
                            "Кількість",
                        ],
                    )
                    .properties(
                        height=500
                    )
                )

                st.altair_chart(
                    chart,
                    use_container_width=True,
                )

            else:

                st.info(
                    "Недостатньо даних."
                )

        with col2:

            st.markdown(
                "### Найчастіші фрази"
            )

            if not bigrams_df.empty:

                chart = (
                    alt.Chart(
                        bigrams_df.head(20)
                    )
                    .mark_bar()
                    .encode(
                        x=alt.X(
                            "Кількість:Q",
                            title="Згадувань",
                        ),
                        y=alt.Y(
                            "Фраза:N",
                            sort="-x",
                        ),
                        tooltip=[
                            "Фраза",
                            "Кількість",
                        ],
                    )
                    .properties(
                        height=500
                    )
                )

                st.altair_chart(
                    chart,
                    use_container_width=True,
                )

            else:

                st.info(
                    "Недостатньо даних."
                )

        st.divider()

        reply_count = (
            df[
                "is_reply_subject"
            ]
            .sum()
        )

        st.metric(
            "↩️ Листів із позначкою відповіді",
            reply_count,
        )

    # ========================================================
    # TAB 5 — ПРОДУКТИВНІСТЬ
    # ========================================================

    with tab_productivity:

        st.subheader(
            "⚖️ Email Productivity"
        )

        unique_days = (
            df["date_only"]
            .nunique()
        )

        workday_df = df[
            df["day_num"] < 5
        ]

        workday_count = len(
            workday_df
        )

        workday_unique_days = (
            workday_df[
                "date_only"
            ].nunique()
        )

        avg_per_day = (
            total_count
            / max(
                unique_days,
                1,
            )
        )

        avg_workday = (
            workday_count
            / max(
                workday_unique_days,
                1,
            )
        )

        c1, c2, c3, c4 = (
            st.columns(4)
        )

        with c1:
            st.metric(
                "📅 Середньо листів/день",
                f"{avg_per_day:.1f}",
            )

        with c2:
            st.metric(
                "💼 Середньо у робочий день",
                f"{avg_workday:.1f}",
            )

        with c3:
            st.metric(
                "📥 Вхідні",
                incoming_count,
            )

        with c4:
            st.metric(
                "📤 Надіслані",
                outgoing_count,
            )

        st.divider()

        st.subheader(
            "📊 Робочі дні vs вихідні"
        )

        productivity_df = pd.DataFrame(
            {
                "Тип дня": [
                    "Робочі дні",
                    "Вихідні",
                ],
                "Кількість": [
                    workday_count,
                    (
                        df["day_num"]
                        >= 5
                    ).sum(),
                ],
            }
        )

        chart = (
            alt.Chart(
                productivity_df
            )
            .mark_bar()
            .encode(
                x=alt.X(
                    "Тип дня:N",
                    title=None,
                ),
                y=alt.Y(
                    "Кількість:Q",
                    title="Листів",
                ),
                tooltip=[
                    "Тип дня",
                    "Кількість",
                ],
            )
            .properties(
                height=350
            )
        )

        st.altair_chart(
            chart,
            use_container_width=True,
        )

        st.subheader(
            "🌙 Пошта поза робочим часом"
        )

        outside_direction = (
            df[
                df[
                    "is_outside_work_hours"
                ]
            ]
            .groupby(
                "direction"
            )
            .size()
            .reset_index(
                name="Кількість"
            )
        )

        if not outside_direction.empty:

            chart = (
                alt.Chart(
                    outside_direction
                )
                .mark_bar()
                .encode(
                    x=alt.X(
                        "direction:N",
                        title=None,
                    ),
                    y=alt.Y(
                        "Кількість:Q",
                        title="Листів",
                    ),
                    tooltip=[
                        "direction",
                        "Кількість",
                    ],
                )
                .properties(
                    height=300
                )
            )

            st.altair_chart(
                chart,
                use_container_width=True,
            )

    # ========================================================
    # TAB 6 — ЧАС ВІДПОВІДІ
    # ========================================================

    with tab_response:

        st.subheader(
            "⏱️ Час відповіді"
        )

        if response_df.empty:

            st.info(
                "Не вдалося знайти листи, "
                "для яких можна достовірно "
                "визначити час відповіді."
            )

            st.caption(
                "Для цього Gmail має передавати "
                "Message-ID та In-Reply-To."
            )

        else:

            avg_response = (
                response_df[
                    "response_time"
                ].mean()
            )

            median_response = (
                response_df[
                    "response_time"
                ].median()
            )

            fastest_response = (
                response_df[
                    "response_time"
                ].min()
            )

            slowest_response = (
                response_df[
                    "response_time"
                ].max()
            )

            c1, c2, c3, c4 = (
                st.columns(4)
            )

            with c1:
                st.metric(
                    "Середній час",
                    format_timedelta(
                        avg_response
                    ),
                )

            with c2:
                st.metric(
                    "Медіанний час",
                    format_timedelta(
                        median_response
                    ),
                )

            with c3:
                st.metric(
                    "Найшвидша відповідь",
                    format_timedelta(
                        fastest_response
                    ),
                )

            with c4:
                st.metric(
                    "Найдовша відповідь",
                    format_timedelta(
                        slowest_response
                    ),
                )

            st.divider()

            st.subheader(
                "👥 Час відповіді за контактами"
            )

            contact_response = (
                response_df
                .groupby(
                    "contact_email"
                )[
                    "response_time"
                ]
                .agg(
                    [
                        "count",
                        "mean",
                        "median",
                    ]
                )
                .reset_index()
            )

            contact_response[
                "Середній час"
            ] = (
                contact_response[
                    "mean"
                ]
                .apply(
                    format_timedelta
                )
            )

            contact_response[
                "Медіанний час"
            ] = (
                contact_response[
                    "median"
                ]
                .apply(
                    format_timedelta
                )
            )

            contact_response.rename(
                columns={
                    "contact_email":
                        "Контакт",
                    "count":
                        "Відповідей",
                },
                inplace=True,
            )

            st.dataframe(
                contact_response[
                    [
                        "Контакт",
                        "Відповідей",
                        "Середній час",
                        "Медіанний час",
                    ]
                ].sort_values(
                    "Відповідей",
                    ascending=False,
                ),
                use_container_width=True,
                hide_index=True,
            )

    # ========================================================
    # TAB 7 — ДИНАМІКА
    # ========================================================

    with tab_trends:

        st.subheader(
            "📈 Динаміка листування"
        )

        monthly = (
            df.groupby(
                [
                    "month",
                    "direction",
                ]
            )
            .size()
            .reset_index(
                name="Кількість"
            )
        )

        if not monthly.empty:

            chart = (
                alt.Chart(
                    monthly
                )
                .mark_line(
                    point=True
                )
                .encode(
                    x=alt.X(
                        "month:O",
                        title="Місяць",
                    ),
                    y=alt.Y(
                        "Кількість:Q",
                        title="Листів",
                    ),
                    color=alt.Color(
                        "direction:N",
                        title="Тип",
                    ),
                    tooltip=[
                        "month",
                        "direction",
                        "Кількість",
                    ],
                )
                .properties(
                    height=450
                )
            )

            st.altair_chart(
                chart,
                use_container_width=True,
            )

        st.subheader(
            "🏆 Найбільш завантажені дні"
        )

        daily_counts = (
            df.groupby(
                "date_only"
            )
            .size()
            .reset_index(
                name="Кількість"
            )
            .sort_values(
                "Кількість",
                ascending=False,
            )
            .head(10)
        )

        if not daily_counts.empty:

            daily_display = (
                daily_counts
                .rename(
                    columns={
                        "date_only":
                            "Дата",
                        "Кількість":
                            "Листів",
                    }
                )
                .copy()
            )

            st.dataframe(
                daily_display,
                use_container_width=True,
                hide_index=True,
            )

else:

    st.title(
        "📊 Gmail Pro Analytics"
    )

    st.info(
        "👈 Відкрийте «Авторизація Gmail», "
        "введіть Email та пароль додатка, "
        "оберіть період і натисніть "
        "«Завантажити пошту»."
    )
