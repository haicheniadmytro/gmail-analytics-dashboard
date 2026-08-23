import email
from email.header import decode_header
from email.utils import parsedate_to_datetime
import imaplib
import re
from collections import Counter

import altair as alt
import pandas as pd
import streamlit as st


# --- Функції декодування та обробки ---
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
    return match.group(0) if match else from_header


# --- ВИПРАВЛЕНА ФУНКЦІЯ АНАЛІЗУ ---
def analyze_emails(df):
    # 1. Парсимо дати
    parsed_dates = df["date"].apply(parse_date)

    # 2. Примусово перетворюємо в тип datetime Pandas (з підтримкою часових поясів)
    df["parsed_date"] = pd.to_datetime(parsed_dates, utc=True, errors="coerce")

    # 3. Безпечно витягуємо дати та години (працює навіть якщо є порожні значення)
    df["date_only"] = df["parsed_date"].dt.date
    df["hour"] = df["parsed_date"].dt.hour
    df["day_of_week"] = df["parsed_date"].dt.day_name()

    df["clean_from"] = df["from"].apply(extract_email_address)
    df["sender_domain"] = df["clean_from"].apply(
        lambda x: x.split("@")[-1] if "@" in x else ""
    )
    df["is_reply"] = df["subject"].str.startswith("Re:", na=False)

    return df


# --- Функція отримання листів через IMAP ---
def fetch_emails_imap(email_user, app_password, max_results=200):
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

        for idx, e_id in enumerate(email_ids_to_fetch):
            status_text.text(
                f"Завантаження листа {idx + 1} з {len(email_ids_to_fetch)}..."
            )
            _, msg_data = mail.fetch(
                e_id, "(BODY.PEEK[HEADER.FIELDS (DATE FROM SUBJECT)])"
            )
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    subject = decode_mime_words(msg.get("Subject", ""))
                    sender = decode_mime_words(msg.get("From", ""))
                    date_raw = msg.get("Date", "")

                    email_data.append(
                        {
                            "id": e_id.decode("utf-8"),
                            "date": date_raw,
                            "from": sender,
                            "subject": subject,
                        }
                    )

            progress_bar.progress((idx + 1) / len(email_ids_to_fetch))

        mail.logout()
        progress_bar.empty()
        status_text.empty()
        st.success(
            f"✅ Завантажено {len(email_data)} листів із {total_emails} наявних!"
        )

        return pd.DataFrame(email_data)

    except imaplib.IMAP4.error as e:
        st.error(
            "❌ Помилка авторизації! Перевірте правильність Email та 16-значного Пароля додатка."
        )
        return None
    except Exception as e:
        st.error(f"❌ Виникла помилка: {e}")
        return None


# --- Інтерфейс Streamlit ---
st.set_page_config(page_title="Gmail Analytics (IMAP)", layout="wide")
st.title("📊 Gmail Analytics Dashboard")

with st.sidebar:
    st.header("🔑 Авторизація IMAP")
    user_email = st.text_input(
        "Ваш Gmail / корпоративний email", placeholder="name@domain.com"
    )
    app_password = st.text_input(
        "Пароль додатка (16 символів)",
        type="password",
        placeholder="abcd efgh ijkl mnop",
    )

    st.divider()
    max_emails = st.slider("Кількість останніх листів", 50, 1000, 200)
    btn_fetch = st.button("🔄 Завантажити пошту", type="primary")

if btn_fetch:
    if not user_email or not app_password:
        st.sidebar.error("⚠️ Будь ласка, введіть Email та Пароль додатка!")
    else:
        with st.spinner("Підключення до пошти..."):
            raw_df = fetch_emails_imap(
                user_email, app_password, max_results=max_emails
            )
            if raw_df is not None and not raw_df.empty:
                analyzed_df = analyze_emails(raw_df)
                st.session_state["data"] = analyzed_df

# --- Відображення аналітики ---
if "data" in st.session_state and st.session_state["data"] is not None:
    df = st.session_state["data"]

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
        st.metric("🌐 Найпопулярніший домен", top_domain)
    with col4:
        st.metric("↩️ Листів-відповідей (Re:)", df["is_reply"].sum())

    st.divider()

    tab1, tab2, tab3 = st.tabs(
        [
            "📈 Часові тренди",
            "👥 Відправники та домени",
            "📊 Детальна таблиця",
        ]
    )

    with tab1:
        st.subheader("📅 Динаміка листів за датами")
        clean_dates = df.dropna(subset=["date_only"])
        if not clean_dates.empty:
            daily_counts = clean_dates["date_only"].value_counts().sort_index()
            st.altair_chart(
                alt.Chart(
                    pd.DataFrame(
                        {
                            "date": daily_counts.index,
                            "count": daily_counts.values,
                        }
                    )
                )
                .mark_bar()
                .encode(
                    x=alt.X("date:T", title="Дата"),
                    y=alt.Y("count:Q", title="Кількість листів"),
                )
                .properties(height=300),
                use_container_width=True,
            )

        st.subheader("⏰ Активність за годинами доби")
        clean_hours = df.dropna(subset=["hour"])
        if not clean_hours.empty:
            hour_counts = clean_hours["hour"].value_counts().sort_index()
            st.altair_chart(
                alt.Chart(
                    pd.DataFrame(
                        {"hour": hour_counts.index, "count": hour_counts.values}
                    )
                )
                .mark_bar(color="steelblue")
                .encode(
                    x=alt.X("hour:O", title="Година (0-23)"),
                    y=alt.Y("count:Q", title="Кількість листів"),
                )
                .properties(height=250),
                use_container_width=True,
            )

    with tab2:
        col_senders, col_domains = st.columns(2)
        with col_senders:
            st.subheader("👥 Топ-10 відправників")
            top_senders = df["clean_from"].value_counts().head(10)
            st.dataframe(
                pd.DataFrame(
                    {
                        "Email відправника": top_senders.index,
                        "Кількість": top_senders.values,
                    }
                ),
                use_container_width=True,
            )

        with col_domains:
            st.subheader("🌐 Топ-10 доменів")
            top_domains = df["sender_domain"].value_counts().head(10)
            st.bar_chart(top_domains)

    with tab3:
        st.subheader("📊 Усі проаналізовані листи")
        st.dataframe(
            df[["parsed_date", "clean_from", "subject", "is_reply"]],
            use_container_width=True,
        )

        csv = df.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="📥 Завантажити CSV",
            data=csv,
            file_name="gmail_imap_analytics.csv",
            mime="text/csv",
        )
else:
    st.info(
        "👈 Введіть Email і Пароль додатка ліворуч у панелі та натисніть 'Завантажити пошту'."
    )
