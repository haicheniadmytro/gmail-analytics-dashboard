from collections import Counter
import email
from email.header import decode_header
from email.utils import parsedate_to_datetime
import imaplib
import re

import altair as alt
import pandas as pd
import streamlit as st


# --- 1. Допоміжні функції ---
def decode_mime_words(header_val):
    if not header_val:
        return ""
    decoded_fragments = decode_header(header_val)
    header_text = ""
    for fragment, encoding in decoded_fragments:
        if isinstance(fragment, bytes):
            charset = encoding or "utf-8"
            try:
                header_text += fragment.decode(charset, errors="ignore")
            except Exception:
                header_text += fragment.decode("latin1", errors="ignore")
        else:
            header_text += str(fragment)
    return header_text


def parse_date(date_str):
    try:
        return parsedate_to_datetime(date_str)
    except Exception:
        return None


def extract_email_address(from_header):
    match = re.search(r"[\w\.-]+@[\w\.-]+", from_header)
    return match.group(0).lower() if match else from_header.lower()


def extract_name(from_header):
    if "<" in from_header:
        name = from_header.split("<")[0].strip().strip('"').strip("'")
        if name:
            return name
    return extract_email_address(from_header)


# --- 2. Завантаження через IMAP (пакетне завантаження) ---
def fetch_emails_imap(email_user, app_password, max_results=1000):
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        clean_password = app_password.replace(" ", "")
        mail.login(email_user, clean_password)
        mail.select("INBOX")

        status, messages = mail.search(None, "ALL")
        if status != "OK":
            st.error("❌ Не вдалося отримати список листів.")
            return None

        email_ids = messages[0].split()
        total_emails = len(email_ids)

        email_ids_to_fetch = email_ids[-max_results:]
        email_ids_to_fetch.reverse()

        email_data = []
        progress_bar = st.progress(0)
        status_text = st.empty()

        chunk_size = 100
        total_to_fetch = len(email_ids_to_fetch)

        for i in range(0, total_to_fetch, chunk_size):
            chunk_ids = email_ids_to_fetch[i : i + chunk_size]
            ids_str = b",".join(chunk_ids)

            status_text.text(
                f"Завантаження листів {i + 1} - {min(i + chunk_size, total_to_fetch)} з {total_to_fetch}..."
            )

            _, msg_data = mail.fetch(
                ids_str, "(BODY.PEEK[HEADER.FIELDS (DATE FROM SUBJECT)])"
            )

            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    subject = decode_mime_words(msg.get("Subject", ""))
                    sender = decode_mime_words(msg.get("From", ""))
                    date_raw = msg.get("Date", "")

                    email_data.append(
                        {
                            "date": date_raw,
                            "from": sender,
                            "subject": subject,
                        }
                    )

            progress_bar.progress(min((i + chunk_size) / total_to_fetch, 1.0))

        mail.logout()
        progress_bar.empty()
        status_text.empty()
        st.success(
            f"✅ Успішно завантажено {len(email_data)} листів із {total_emails} наявних!"
        )

        return pd.DataFrame(email_data)

    except imaplib.IMAP4.error:
        st.error(
            "❌ Помилка авторизації! Перевірте правильність Email та Пароля додатка."
        )
        return None
    except Exception as e:
        st.error(f"❌ Помилка підключення: {e}")
        return None


# --- 3. Обробка та аналіз даних ---
def analyze_emails(df):
    parsed_dates = df["date"].apply(parse_date)
    df["parsed_date"] = pd.to_datetime(parsed_dates, utc=True, errors="coerce")

    df["date_only"] = df["parsed_date"].dt.date
    df["year"] = df["parsed_date"].dt.year
    df["month"] = df["parsed_date"].dt.strftime("%Y-%m")
    df["hour"] = df["parsed_date"].dt.hour
    df["day_name"] = df["parsed_date"].dt.day_name()
    df["day_num"] = df["parsed_date"].dt.dayofweek  # 0 = Monday, 6 = Sunday

    df["clean_from"] = df["from"].apply(extract_email_address)
    df["sender_name"] = df["from"].apply(extract_name)
    df["sender_domain"] = df["clean_from"].apply(
        lambda x: x.split("@")[-1] if "@" in x else ""
    )
    df["is_reply"] = df["subject"].str.startswith("Re:", na=False)

    return df


# --- 4. Інтерфейс Streamlit ---
st.set_page_config(page_title="Gmail Pro Analytics", layout="wide")
st.title("📊 Gmail Pro Analytics Dashboard")

# Сайдбар авторизації та налаштувань
with st.sidebar:
    st.header("🔑 Авторизація IMAP")
    user_email = st.text_input(
        "Ваш Email", placeholder="name@domain.com"
    )
    app_password = st.text_input(
        "Пароль додатка (16 символів)",
        type="password",
        placeholder="abcd efgh ijkl mnop",
    )

    st.divider()
    max_emails = st.slider(
        "Кількість останніх листів",
        min_value=100,
        max_value=10000,
        value=1000,
        step=100,
    )
    btn_fetch = st.button("🔄 Завантажити пошту", type="primary")

# Завантаження даних
if btn_fetch:
    if not user_email or not app_password:
        st.sidebar.error("⚠️ Введіть Email та Пароль додатка!")
    else:
        with st.spinner("Отримання листів з сервера..."):
            raw_df = fetch_emails_imap(
                user_email, app_password, max_results=max_emails
            )
            if raw_df is not None and not raw_df.empty:
                st.session_state["raw_data"] = analyze_emails(raw_df)

# Робота з завантаженими даними
if "raw_data" in st.session_state and st.session_state["raw_data"] is not None:
    full_df = st.session_state["raw_data"]

    # Фільтрація за датами в сайдбарі
    st.sidebar.divider()
    st.sidebar.header("🗓️ Фільтрація за датами")

    valid_dates = full_df["date_only"].dropna()
    min_d, max_d = valid_dates.min(), valid_dates.max()

    date_range = st.sidebar.date_input(
        "Оберіть діапазон дат",
        value=(min_d, max_d),
        min_value=min_d,
        max_value=max_d,
    )

    # Застосування фільтра за датами
    if isinstance(date_range, tuple) and len(date_range) == 2:
        start_date, end_date = date_range
        df = full_df[
            (full_df["date_only"] >= start_date)
            & (full_df["date_only"] <= end_date)
        ].copy()
    else:
        df = full_df.copy()

    # Показ загальних метрик
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("📧 Проаналізовано листів", len(df))
    with col2:
        st.metric("👤 Унікальних відправників", df["clean_from"].nunique())
    with col3:
        top_domain = (
            df["sender_domain"].mode().iloc[0]
            if not df["sender_domain"].mode().empty
            else "N/A"
        )
        st.metric("🌐 Топ-домен", top_domain)
    with col4:
        st.metric("↩️ Листів-відповідей (Re:)", df["is_reply"].sum())

    st.divider()

    # Таби аналітики
    tab_vip, tab_heatmap, tab_senders, tab_time, tab_data = st.tabs(
        [
            "🌟 Контакти Top-VIP",
            "🔥 Heatmap (Години × Дні)",
            "👥 Найпопулярніші адресати",
            "📅 Тренд за місяцями/роками",
            "📊 Таблиця даних",
        ]
    )

    # TAB 1: Top-VIP Контакти
    with tab_vip:
        st.subheader("🌟 Рейтинг Top-VIP Контактів")
        st.markdown(
            "Топ найактивніших відправників з деталізацією листування."
        )

        vip_df = (
            df.groupby(["clean_from", "sender_name"])
            .agg(
                total_emails=("subject", "count"),
                replies=("is_reply", "sum"),
                first_email=("date_only", "min"),
                last_email=("date_only", "max"),
            )
            .reset_index()
            .sort_values(by="total_emails", ascending=False)
        )

        vip_df.rename(
            columns={
                "clean_from": "Email",
                "sender_name": "Ім'я",
                "total_emails": "Всього листів",
                "replies": "З них відповідей (Re:)",
                "first_email": "Перший лист",
                "last_email": "Останній лист",
            },
            inplace=True,
        )

        st.dataframe(vip_df.head(20), use_container_width=True, hide_index=True)

    # TAB 2: Heatmap (Теплова карта)
    with tab_heatmap:
        st.subheader("🔥 Теплова карта активності (Години × Дні тижня)")
        st.markdown(
            "Темніші квадрати показують години з найбільшою кількістю листів."
        )

        clean_hm = df.dropna(subset=["day_num", "hour"]).copy()
        if not clean_hm.empty:
            days_order = [
                "Monday",
                "Tuesday",
                "Wednesday",
                "Thursday",
                "Friday",
                "Saturday",
                "Sunday",
            ]
            days_ua = [
                "1. Понеділок",
                "2. Вівторок",
                "3. Середа",
                "4. Четвер",
                "5. П'ятниця",
                "6. Субота",
                "7. Неділя",
            ]
            day_map = dict(zip(days_order, days_ua))
            clean_hm["day_ua"] = clean_hm["day_name"].map(day_map)

            heatmap_data = (
                clean_hm.groupby(["day_ua", "hour"])
                .size()
                .reset_index(name="count")
            )

            heatmap_chart = (
                alt.Chart(heatmap_data)
                .mark_rect()
                .encode(
                    x=alt.X("hour:O", title="Година доби (0-23)"),
                    y=alt.Y("day_ua:O", title="День тижня", sort="ascending"),
                    color=alt.Color(
                        "count:Q",
                        scale=alt.Scale(scheme="reds"),
                        title="Кількість",
                    ),
                    tooltip=["day_ua", "hour", "count"],
                )
                .properties(height=350)
            )

            st.altair_chart(heatmap_chart, use_container_width=True)

    # TAB 3: Найпопулярніші адресати
    with tab_senders:
        col_s1, col_s2 = st.columns(2)

        with col_s1:
            st.subheader("👥 Топ-15 Відправників")
            top_s = df["clean_from"].value_counts().head(15).reset_index()
            top_s.columns = ["Email", "Кількість"]

            chart_senders = (
                alt.Chart(top_s)
                .mark_bar()
                .encode(
                    x=alt.X("Кількість:Q"),
                    y=alt.Y("Email:N", sort="-x", title="Email"),
                    color=alt.value("#4C78A8"),
                )
                .properties(height=400)
            )
            st.altair_chart(chart_senders, use_container_width=True)

        with col_s2:
            st.subheader("🌐 Топ-15 Доменів")
            top_d = df["sender_domain"].value_counts().head(15).reset_index()
            top_d.columns = ["Домен", "Кількість"]

            chart_domains = (
                alt.Chart(top_d)
                .mark_bar()
                .encode(
                    x=alt.X("Кількість:Q"),
                    y=alt.Y("Домен:N", sort="-x", title="Домен"),
                    color=alt.value("#F28E2B"),
                )
                .properties(height=400)
            )
            st.altair_chart(chart_domains, use_container_width=True)

    # TAB 4: Тренд за місяцями/роками
    with tab_time:
        st.subheader("📅 Динаміка листів за місяцями")
        clean_m = df.dropna(subset=["month"]).copy()
        if not clean_m.empty:
            m_counts = (
                clean_m.groupby("month").size().reset_index(name="count")
            )

            chart_month = (
                alt.Chart(m_counts)
                .mark_line(point=True, color="#2CA02C")
                .encode(
                    x=alt.X("month:O", title="Місяць (РРРР-ММ)"),
                    y=alt.Y("count:Q", title="Кількість листів"),
                    tooltip=["month", "count"],
                )
                .properties(height=350)
            )
            st.altair_chart(chart_month, use_container_width=True)

    # TAB 5: Таблиця даних та експорт
    with tab_data:
        st.subheader("📊 Детальний список листів")
        st.dataframe(
            df[
                [
                    "parsed_date",
                    "sender_name",
                    "clean_from",
                    "subject",
                    "is_reply",
                ]
            ],
            use_container_width=True,
        )

        csv_data = df.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="📥 Завантажити повний CSV",
            data=csv_data,
            file_name="gmail_full_analytics.csv",
            mime="text/csv",
        )

else:
    st.info(
        "👈 Введіть Email і Пароль додатка ліворуч у сайдбарі та натисніть 'Завантажити пошту'."
    )
