from collections import Counter
from datetime import date, datetime, timedelta
from email import message_from_bytes
from email.header import decode_header
from email.utils import parsedate_to_datetime
import email
import imaplib
import re

import altair as alt
import pandas as pd
import streamlit as st


# ============================================================
# CONFIG
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
    "мені", "вам", "нас", "вас", "цей", "ця", "це", "ці",
    "можна", "може", "буде", "був", "була", "були",
}


# ============================================================
# UI STYLE
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

        .section-title {
            font-size: 1.35rem;
            font-weight: 700;
            margin-top: 0.5rem;
            margin-bottom: 0.8rem;
        }

        .insight-card {
            background: rgba(128,128,128,0.08);
            border-radius: 12px;
            padding: 16px;
            margin-bottom: 10px;
        }

        .small-muted {
            color: #777;
            font-size: 0.85rem;
        }
    </style>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# HELPERS
# ============================================================

def decode_mime_words(header_val):
    if not header_val:
        return ""

    result = []

    for fragment, encoding in decode_header(header_val):
        if isinstance(fragment, bytes):
            charset = encoding or "utf-8"

            try:
                result.append(fragment.decode(charset, errors="ignore"))
            except Exception:
                result.append(fragment.decode("latin1", errors="ignore"))
        else:
            result.append(str(fragment))

    return "".join(result)


def parse_date(date_str):
    try:
        return parsedate_to_datetime(date_str)
    except Exception:
        return None


def extract_email_address(value):
    if not value:
        return ""

    match = re.search(
        r"[\w.!#$%&'*+/=?^_`{|}~-]+@[\w.-]+\.[A-Za-z]{2,}",
        value,
    )

    return match.group(0).lower() if match else value.lower().strip()


def extract_name(from_header):
    if not from_header:
        return ""

    if "<" in from_header:
        name = from_header.split("<")[0].strip().strip('"').strip("'")

        if name:
            return name

    return extract_email_address(from_header)


def normalize_subject(subject):
    """
    Видаляє Re:, Fwd:, FW: та інші префікси,
    щоб можна було визначати ланцюжок листування.
    """

    if not subject:
        return ""

    subject = subject.lower().strip()

    previous = None

    while previous != subject:
        previous = subject

        subject = re.sub(
            r"^\s*((re|fw|fwd|відповідь|переслано)\s*:\s*)+",
            "",
            subject,
            flags=re.IGNORECASE,
        )

    subject = re.sub(r"\s+", " ", subject)

    return subject.strip()


def is_reply_subject(subject):
    if not subject:
        return False

    return bool(
        re.match(
            r"^\s*(re|fw|fwd|відповідь)\s*:",
            subject,
            flags=re.IGNORECASE,
        )
    )


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


def format_timedelta(td):
    if pd.isna(td) or td is None:
        return "—"

    total_seconds = int(td.total_seconds())

    if total_seconds < 0:
        return "—"

    minutes = total_seconds // 60
    hours = minutes // 60
    days = hours // 24

    minutes = minutes % 60
    hours = hours % 24

    if days > 0:
        return f"{days} дн. {hours} год."

    if hours > 0:
        return f"{hours} год. {minutes} хв."

    return f"{minutes} хв."


def get_period_description(start_date, end_date):
    if start_date is None:
        return "Весь доступний період"

    return f"{start_date.strftime('%d.%m.%Y')} — {end_date.strftime('%d.%m.%Y')}"


# ============================================================
# IMAP
# ============================================================

def find_sent_folder(mail):
    """
    Автоматично знаходить папку Sent / Надіслані.
    """

    try:
        status, folders = mail.list()

        if status != "OK":
            return None

        for folder in folders:
            if not folder:
                continue

            decoded = folder.decode(errors="ignore")

            # Gmail позначає папку Sent спеціальним прапором \Sent
            if r"\Sent" in decoded:
                match = re.search(r'"([^"]+)"\s*$', decoded)

                if match:
                    return match.group(1)

                # запасний варіант
                if "[Gmail]/Sent Mail" in decoded:
                    return "[Gmail]/Sent Mail"

        return "[Gmail]/Sent Mail"

    except Exception:
        return "[Gmail]/Sent Mail"


def search_folder(mail, folder, start_date=None, end_date=None):
    """
    Повертає ID листів у папці за заданим періодом.
    """

    status, _ = mail.select(f'"{folder}"')

    if status != "OK":
        return []

    if start_date:
        search_date = start_date.strftime("%d-%b-%Y")

        if end_date:
            # IMAP SINCE включає дату start_date.
            # BEFORE має бути наступним днем після end_date.
            before_date = (end_date + timedelta(days=1)).strftime(
                "%d-%b-%Y"
            )

            status, data = mail.search(
                None,
                "SINCE",
                search_date,
                "BEFORE",
                before_date,
            )
        else:
            status, data = mail.search(
                None,
                "SINCE",
                search_date,
            )
    else:
        status, data = mail.search(None, "ALL")

    if status != "OK":
        return []

    return data[0].split()


def fetch_folder_headers(
    mail,
    folder,
    start_date=None,
    end_date=None,
    max_results=100000,
    folder_type="inbox",
):
    """
    Завантажує тільки потрібні заголовки листів.
    """

    ids = search_folder(
        mail,
        folder,
        start_date,
        end_date,
    )

    if not ids:
        return []

    # Останні листи
    ids = ids[-max_results:]

    ids.reverse()

    result = []

    chunk_size = 500
    total = len(ids)

    progress_bar = st.progress(0)

    for i in range(0, total, chunk_size):
        chunk_ids = ids[i : i + chunk_size]

        ids_str = b",".join(chunk_ids)

        status, msg_data = mail.fetch(
            ids_str,
            """
            (BODY.PEEK[
                HEADER.FIELDS (
                    DATE
                    FROM
                    TO
                    SUBJECT
                    MESSAGE-ID
                    IN-REPLY-TO
                    REFERENCES
                )
            ])
            """,
        )

        if status != "OK":
            continue

        for response_part in msg_data:

            if not isinstance(response_part, tuple):
                continue

            try:
                msg = message_from_bytes(response_part[1])

                result.append(
                    {
                        "date": msg.get("Date", ""),
                        "from": decode_mime_words(msg.get("From", "")),
                        "to": decode_mime_words(msg.get("To", "")),
                        "subject": decode_mime_words(
                            msg.get("Subject", "")
                        ),
                        "message_id": msg.get("Message-ID", ""),
                        "in_reply_to": msg.get("In-Reply-To", ""),
                        "references": msg.get("References", ""),
                        "folder_type": folder_type,
                    }
                )

            except Exception:
                continue

        progress_bar.progress(
            min(
                (i + chunk_size) / max(total, 1),
                1.0,
            )
        )

    progress_bar.empty()

    return result


def fetch_emails_imap(
    email_user,
    app_password,
    start_date=None,
    end_date=None,
    max_results=100000,
):
    """
    Основне завантаження Inbox + Sent.
    """

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")

        clean_password = app_password.replace(" ", "")

        mail.login(
            email_user,
            clean_password,
        )

        # ----------------------------------------------------
        # Inbox
        # ----------------------------------------------------

        st.info("📥 Завантаження вхідних листів...")

        inbox_data = fetch_folder_headers(
            mail=mail,
            folder="INBOX",
            start_date=start_date,
            end_date=end_date,
            max_results=max_results,
            folder_type="inbox",
        )

        # ----------------------------------------------------
        # Sent
        # ----------------------------------------------------

        sent_folder = find_sent_folder(mail)

        st.info(
            f"📤 Завантаження надісланих листів: {sent_folder}"
        )

        sent_data = fetch_folder_headers(
            mail=mail,
            folder=sent_folder,
            start_date=start_date,
            end_date=end_date,
            max_results=max_results,
            folder_type="sent",
        )

        mail.logout()

        all_data = inbox_data + sent_data

        if not all_data:
            st.warning(
                "За обраний період листів не знайдено."
            )

            return None

        df = pd.DataFrame(all_data)

        st.success(
            f"✅ Завантажено {len(df):,} листів "
            f"(вхідні: {len(inbox_data):,}, "
            f"надіслані: {len(sent_data):,})".replace(",", " ")
        )

        return df

    except imaplib.IMAP4.error:
        st.error(
            "❌ Помилка авторизації. "
            "Перевір Email та пароль додатка Gmail."
        )

        return None

    except Exception as e:
        st.error(
            f"❌ Помилка підключення до Gmail: {e}"
        )

        return None


# ============================================================
# DATA ANALYSIS
# ============================================================

def analyze_emails(df, user_email):

    df = df.copy()

    # --------------------------------------------------------
    # Dates
    # --------------------------------------------------------

    df["parsed_date"] = pd.to_datetime(
        df["date"],
        utc=True,
        errors="coerce",
    )

    # ВАЖЛИВО:
    # переводимо UTC у Київський час
    df["parsed_date"] = df["parsed_date"].dt.tz_convert(
        TIMEZONE
    )

    df["date_only"] = df["parsed_date"].dt.date

    df["year"] = df["parsed_date"].dt.year

    df["month"] = df["parsed_date"].dt.strftime(
        "%Y-%m"
    )

    df["hour"] = df["parsed_date"].dt.hour

    df["day_name"] = df["parsed_date"].dt.day_name()

    df["day_num"] = df["parsed_date"].dt.dayofweek

    # --------------------------------------------------------
    # Sender
    # --------------------------------------------------------

    df["clean_from"] = df["from"].apply(
        extract_email_address
    )

    df["sender_name"] = df["from"].apply(
        extract_name
    )

    # --------------------------------------------------------
    # Recipient
    # --------------------------------------------------------

    df["clean_to"] = df["to"].apply(
        extract_email_address
    )

    # --------------------------------------------------------
    # Reply detection
    # --------------------------------------------------------

    df["normalized_subject"] = df["subject"].apply(
        normalize_subject
    )

    df["is_reply_subject"] = df["subject"].apply(
        is_reply_subject
    )

    # --------------------------------------------------------
    # Working hours
    # --------------------------------------------------------

    df["is_workday"] = df["day_num"] < 5

    df["is_work_hours"] = (
        df["is_workday"]
        & (df["hour"] >= WORK_START)
        & (df["hour"] < WORK_END)
    )

    df["is_outside_work_hours"] = ~df["is_work_hours"]

    # --------------------------------------------------------
    # User's own messages
    # --------------------------------------------------------

    user_email = user_email.lower().strip()

    df["is_from_user"] = (
        df["clean_from"] == user_email
    )

    # --------------------------------------------------------
    # Communication direction
    # --------------------------------------------------------

    df["direction"] = df["folder_type"].map(
        {
            "inbox": "Вхідний",
            "sent": "Надісланий",
        }
    )

    # --------------------------------------------------------
    # Message ID cleanup
    # --------------------------------------------------------

    df["message_id_clean"] = (
        df["message_id"]
        .fillna("")
        .str.strip()
    )

    df["in_reply_to_clean"] = (
        df["in_reply_to"]
        .fillna("")
        .str.strip()
    )

    return df


# ============================================================
# RESPONSE TIME
# ============================================================

def calculate_response_times(df):
    """
    Визначає час відповіді на вхідні листи.

    Логіка:
    надісланий лист має In-Reply-To,
    який відповідає Message-ID вхідного листа.
    """

    incoming = df[
        (df["direction"] == "Вхідний")
        & (df["message_id_clean"] != "")
    ].copy()

    outgoing = df[
        (df["direction"] == "Надісланий")
        & (df["in_reply_to_clean"] != "")
    ].copy()

    if incoming.empty or outgoing.empty:
        return pd.DataFrame()

    incoming_lookup = incoming[
        [
            "message_id_clean",
            "parsed_date",
            "clean_from",
            "subject",
        ]
    ].rename(
        columns={
            "message_id_clean": "parent_message_id",
            "parsed_date": "received_at",
            "clean_from": "contact_email",
            "subject": "original_subject",
        }
    )

    outgoing_lookup = outgoing[
        [
            "in_reply_to_clean",
            "parsed_date",
            "subject",
        ]
    ].rename(
        columns={
            "in_reply_to_clean": "parent_message_id",
            "parsed_date": "response_at",
            "subject": "response_subject",
        }
    )

    response_df = outgoing_lookup.merge(
        incoming_lookup,
        on="parent_message_id",
        how="inner",
    )

    response_df["response_time"] = (
        response_df["response_at"]
        - response_df["received_at"]
    )

    response_df = response_df[
        response_df["response_time"]
        >= pd.Timedelta(0)
    ].copy()

    return response_df


# ============================================================
# TOPICS
# ============================================================

def tokenize_subject(subject):
    if not subject:
        return []

    text = re.sub(
        r"[^\w\s]",
        " ",
        subject.lower(),
        flags=re.UNICODE,
    )

    tokens = text.split()

    return [
        word
        for word in tokens
        if len(word) > 2
        and word not in STOP_WORDS
        and not word.isdigit()
    ]


def analyze_topics(df):
    words = []

    for subject in df["subject"].dropna():
        words.extend(
            tokenize_subject(subject)
        )

    word_counts = Counter(words)

    # --------------------------------------------------------
    # Окремі слова
    # --------------------------------------------------------

    top_words = (
        word_counts
        .most_common(30)
    )

    words_df = pd.DataFrame(
        top_words,
        columns=[
            "Слово",
            "Кількість",
        ],
    )

    # --------------------------------------------------------
    # Біграми
    # --------------------------------------------------------

    bigrams = []

    for subject in df["subject"].dropna():

        tokens = tokenize_subject(subject)

        for i in range(len(tokens) - 1):

            word1 = tokens[i]
            word2 = tokens[i + 1]

            if word1 != word2:
                bigrams.append(
                    f"{word1} {word2}"
                )

    bigram_counts = Counter(bigrams)

    top_bigrams = (
        bigram_counts
        .most_common(25)
    )

    bigrams_df = pd.DataFrame(
        top_bigrams,
        columns=[
            "Фраза",
            "Кількість",
        ],
    )

    return words_df, bigrams_df


# ============================================================
# CONTACT SCORE
# ============================================================

def build_contact_ranking(df):

    contacts = (
        df.groupby(
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

    # Вага кількості листів
    contacts["volume_score"] = (
        contacts["total_emails"]
        / max(
            contacts["total_emails"].max(),
            1,
        )
        * 50
    )

    # Регулярність
    contacts["days_active"] = (
        contacts["last_contact"]
        - contacts["first_contact"]
    ).dt.days + 1

    contacts["frequency"] = (
        contacts["total_emails"]
        / contacts["days_active"].clip(
            lower=1
        )
    )

    contacts["frequency_score"] = (
        contacts["frequency"]
        / max(
            contacts["frequency"].max(),
            0.0001,
        )
        * 25
    )

    # Актуальність контакту
    latest_date = df["date_only"].max()

    contacts["days_since_contact"] = (
        latest_date
        - contacts["last_contact"]
    ).apply(
        lambda x: x.days
    )

    contacts["recency_score"] = (
        25
        * (
            1
            / (
                1
                + contacts[
                    "days_since_contact"
                ]
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
# MAIN UI
# ============================================================

st.title("📊 Gmail Pro Analytics")

st.caption(
    "Аналітика вхідної та вихідної пошти, контактів, "
    "тем, навантаження та часу відповідей."
)


# ============================================================
# SIDEBAR
# ============================================================

is_data_loaded = (
    "raw_data" in st.session_state
    and st.session_state["raw_data"] is not None
)

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
                "Використовуйте саме пароль додатка Gmail, "
                "а не основний пароль Google."
            ),
        )

    st.divider()

    st.subheader("🗓️ Період аналізу")

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
            value=date.today() - timedelta(days=90),
        )

        custom_end = st.date_input(
            "Кінцева дата",
            value=date.today(),
        )

        start_date = custom_start
        end_date = custom_end

    else:

        start_date, end_date = calculate_period(
            period
        )

    st.caption(
        f"Період: {get_period_description(start_date, end_date)}"
    )

    st.divider()

    max_emails = st.number_input(
        "Максимум листів з кожної папки",
        min_value=500,
        max_value=100000,
        value=20000,
        step=1000,
        help=(
            "Обмеження застосовується окремо до "
            "вхідних та надісланих листів."
        ),
    )

    btn_fetch = st.button(
        "🔄 Завантажити пошту",
        type="primary",
        use_container_width=True,
    )


# ============================================================
# LOAD
# ============================================================

if btn_fetch:

    if not user_email or not app_password:

        st.sidebar.error(
            "⚠️ Введіть Email та пароль додатка."
        )

    elif start_date and end_date and start_date > end_date:

        st.sidebar.error(
            "⚠️ Початкова дата не може бути пізніше кінцевої."
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
                max_results=max_emails,
            )

            if raw_df is not None and not raw_df.empty:

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
# DASHBOARD
# ============================================================

if (
    "raw_data" in st.session_state
    and st.session_state["raw_data"] is not None
):

    full_df = st.session_state["raw_data"]

    df = full_df.copy()

    total_count = len(df)

    incoming_count = (
        df["direction"] == "Вхідний"
    ).sum()

    outgoing_count = (
        df["direction"] == "Надісланий"
    ).sum()

    unique_contacts = (
        df["clean_from"]
        .replace("", pd.NA)
        .nunique()
    )

    outside_hours = (
        df["is_outside_work_hours"]
    ).sum()

    response_df = calculate_response_times(
        df
    )

    # ========================================================
    # TOP KPI
    # ========================================================

    st.caption(
        f"📅 {st.session_state.get('period_description', '')}"
    )

    col1, col2, col3, col4, col5 = st.columns(5)

    with col1:
        st.metric(
            "📧 Всього листів",
            f"{total_count:,}".replace(",", " "),
        )

    with col2:
        st.metric(
            "📥 Вхідні",
            f"{incoming_count:,}".replace(",", " "),
        )

    with col3:
        st.metric(
            "📤 Надіслані",
            f"{outgoing_count:,}".replace(",", " "),
        )

    with col4:
        st.metric(
            "👥 Контактів",
            f"{unique_contacts:,}".replace(",", " "),
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
    # DASHBOARD
    # ========================================================

    with tab_dashboard:

        st.subheader("🏠 Загальний огляд")

        # ----------------------------------------------------
        # Incoming / outgoing
        # ----------------------------------------------------

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

        chart = (
            alt.Chart(direction_df)
            .mark_bar()
            .encode(
                x=alt.X(
                    "Тип:N",
                    title=None,
                ),
                y=alt.Y(
                    "Кількість:Q",
                    title="Кількість листів",
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
            chart,
            use_container_width=True,
        )

        # ----------------------------------------------------
        # Insights
        # ----------------------------------------------------

        st.subheader("💡 Основні інсайти")

        daily = (
            df.groupby("date_only")
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

            peak_hour = (
                df["hour"]
                .value_counts()
                .idxmax()
            )

            peak_hour_count = (
                df["hour"]
                .value_counts()
                .max()
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
                ☕ <b>Пошта у вихідні:</b>
                {weekend_count} листів
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
                {format_timedelta(median_response)}.
                </div>
                """,
                unsafe_allow_html=True,
            )

    # ========================================================
    # CONTACTS
    # ========================================================

    with tab_contacts:

        st.subheader(
            "🌟 Найактивніші контакти"
        )

        contacts = build_contact_ranking(
            df
        )

        contacts_display = contacts.head(
            30
        ).copy()

        contacts_display["Частка пошти"] = (
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
                "first_contact": "Перший контакт",
                "last_contact": "Останній контакт",
                "outside_hours": "Поза робочим часом",
                "contact_score": "Рейтинг контакту",
                "days_since_contact": "Днів від останнього контакту",
            },
            inplace=True,
        )

        cols = [
            "Email",
            "Ім'я",
            "Листів",
            "Частка пошти",
            "Рейтинг контакту",
            "Перший контакт",
            "Останній контакт",
            "Днів від останнього контакту",
        ]

        st.dataframe(
            contacts_display[cols],
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

        sender_df.columns = [
            "Email",
            "Кількість",
        ]

        if not sender_df.empty:

            chart = (
                alt.Chart(sender_df)
                .mark_bar()
                .encode(
                    x=alt.X(
                        "Кількість:Q",
                        title="Кількість листів",
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
                chart,
                use_container_width=True,
            )

    # ========================================================
    # ACTIVITY
    # ========================================================

    with tab_activity:

        st.subheader(
            "🔥 Теплова карта активності"
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

            clean_hm["День"] = (
                clean_hm["day_num"]
                .map(day_map)
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
                        sort=[
                            "1. Понеділок",
                            "2. Вівторок",
                            "3. Середа",
                            "4. Четвер",
                            "5. П'ятниця",
                            "6. Субота",
                            "7. Неділя",
                        ],
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

        # ----------------------------------------------------
        # Work hours
        # ----------------------------------------------------

        st.subheader(
            "🕐 Робочий та неробочий час"
        )

        work_count = (
            df["is_work_hours"]
        ).sum()

        outside_count = (
            df["is_outside_work_hours"]
        ).sum()

        c1, c2, c3 = st.columns(3)

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
    # TOPICS
    # ========================================================

    with tab_topics:

        st.subheader(
            "🔤 Аналіз тем листів"
        )

        words_df, bigrams_df = (
            analyze_topics(df)
        )

        col1, col2 = st.columns(2)

        with col1:

            st.markdown(
                "### Найчастіші слова"
            )

            if not words_df.empty:

                chart = (
                    alt.Chart(words_df.head(20))
                    .mark_bar()
                    .encode(
                        x=alt.X(
                            "Кількість:Q"
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
                            "Кількість:Q"
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

        st.subheader(
            "🔁 Листи-відповіді"
        )

        reply_by_subject = (
            df[
                df["is_reply_subject"]
            ]
            .shape[0]
        )

        st.metric(
            "Листів із позначкою відповіді",
            reply_by_subject,
        )

    # ========================================================
    # PRODUCTIVITY
    # ========================================================

    with tab_productivity:

        st.subheader(
            "⚖️ Email Productivity"
        )

        workdays = (
            df["day_num"] < 5
        ).sum()

        weekends = (
            df["day_num"] >= 5
        ).sum()

        num_days = (
            df["date_only"].nunique()
        )

        avg_per_day = (
            total_count
            / max(num_days, 1)
        )

        avg_workday = (
            workdays
            / max(
                df[
                    df["day_num"] < 5
                ]["date_only"].nunique(),
                1,
            )
        )

        c1, c2, c3, c4 = st.columns(4)

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

        # ----------------------------------------------------
        # Workday / weekend
        # ----------------------------------------------------

        productivity_df = pd.DataFrame(
            {
                "Тип дня": [
                    "Робочі дні",
                    "Вихідні",
                ],
                "Кількість": [
                    workdays,
                    weekends,
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

        # ----------------------------------------------------
        # Outside work hours by direction
        # ----------------------------------------------------

        st.subheader(
            "🌙 Пошта поза робочим часом"
        )

        outside_direction = (
            df[
                df["is_outside_work_hours"]
            ]
            .groupby("direction")
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
                        "Кількість:Q"
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
    # RESPONSE TIME
    # ========================================================

    with tab_response:

        st.subheader(
            "⏱️ Час відповіді"
        )

        if response_df.empty:

            st.info(
                "Не вдалося знайти достатньо "
                "даних для розрахунку часу відповіді. "
                "Для цього потрібні коректні заголовки "
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

            c1, c2, c3, c4 = st.columns(4)

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
                )["response_time"]
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
            ] = contact_response[
                "mean"
            ].apply(
                format_timedelta
            )

            contact_response[
                "Медіанний час"
            ] = contact_response[
                "median"
            ].apply(
                format_timedelta
            )

            contact_response.rename(
                columns={
                    "contact_email": "Контакт",
                    "count": "Відповідей",
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
    # TRENDS
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
                        title="Кількість листів",
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
            "🏆 Пікові періоди"
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
        )

        if not daily_counts.empty:

            daily_display = (
                daily_counts.head(10)
                .copy()
            )

            daily_display.rename(
                columns={
                    "date_only": "Дата",
                    "Кількість": "Листів",
                },
                inplace=True,
            )

            st.dataframe(
                daily_display,
                use_container_width=True,
                hide_index=True,
            )

else:

    st.info(
        "👈 Відкрийте «Авторизація Gmail», "
        "введіть Email та пароль додатка, "
        "оберіть період і натисніть "
        "«Завантажити пошту»."
    )
