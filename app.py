import json
import os
from collections import Counter
from datetime import datetime

import altair as alt
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
import pandas as pd
import streamlit as st

# --- Конфігурація ---
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']


# --- Функція авторизації через Redirect URL ---
def get_credentials():
    # 1. Перевірка наявного токена у сесії
    if 'creds' in st.session_state and st.session_state['creds'] is not None:
        creds = st.session_state['creds']
        if creds.valid:
            st.sidebar.success("✅ Токен дійсний")
            return creds
        elif creds.expired and creds.refresh_token:
            try:
                st.sidebar.info("🔄 Оновлюємо токен...")
                creds.refresh(Request())
                st.session_state['creds'] = creds
                return creds
            except Exception:
                del st.session_state['creds']

    # 2. Завантаження конфігурації Google API
    credentials_dict = None
    if 'gmail_credentials' in st.secrets:
        try:
            credentials_dict = json.loads(st.secrets['gmail_credentials'])
        except Exception as e:
            st.sidebar.error(f"❌ Помилка Secrets: {e}")
            return None
    elif os.path.exists('credentials.json'):
        try:
            with open('credentials.json', 'r') as f:
                credentials_dict = json.load(f)
        except Exception as e:
            st.sidebar.error(f"❌ Помилка credentials.json: {e}")
            return None
    else:
        st.sidebar.error("❌ Облікові дані не знайдено!")
        return None

    # 3. Визначення URL перенаправлення (Redirect URI)
    # За замовчуванням для локальної розробки: http://localhost:8501/
    redirect_uri = st.secrets.get("REDIRECT_URI", "http://localhost:8501/")

    flow = Flow.from_client_config(
        credentials_dict, scopes=SCOPES, redirect_uri=redirect_uri
    )

    # 4. Перевіряємо, чи повернула Google код авторизації в URL адресі
    auth_code = st.query_params.get("code")

    if auth_code:
        try:
            flow.fetch_token(code=auth_code)
            st.session_state['creds'] = flow.credentials
            st.query_params.clear()  # Очищаємо URL від коду
            st.sidebar.success("✅ Успішна авторизація!")
            st.rerun()
        except Exception as e:
            st.sidebar.error(f"❌ Помилка отримання токена: {e}")
            st.query_params.clear()
            return None

    # 5. Якщо не авторизовано — показуємо кнопку входу
    auth_url, _ = flow.authorization_url(
        prompt='consent', access_type='offline'
    )
    st.sidebar.info("🔑 Потрібна авторизація")
    st.sidebar.link_button("🌐 Увійти через Google", auth_url, type="primary")
    return None


# --- Кешована функція для сервісу Gmail ---
@st.cache_resource
def get_gmail_service(creds):
    return build('gmail', 'v1', credentials=creds)


# --- Завантаження та обробка даних ---
def get_email_metadata(service, max_results=500):
    st.info(f"📧 Завантаження до {max_results} листів...")
    results = (
        service.users()
        .messages()
        .list(userId='me', maxResults=max_results)
        .execute()
    )
    messages = results.get('messages', [])
    email_data = []
    progress_bar = st.progress(0)

    for idx, msg in enumerate(messages):
        msg_data = (
            service.users().messages().get(userId='me', id=msg['id']).execute()
        )
        headers = msg_data['payload'].get('headers', [])
        subject = next(
            (h['value'] for h in headers if h['name'] == 'Subject'), ''
        )
        sender = next((h['value'] for h in headers if h['name'] == 'From'), '')
        date = next((h['value'] for h in headers if h['name'] == 'Date'), '')
        labels = msg_data.get('labelIds', [])

        email_data.append({
            'id': msg['id'],
            'date': date,
            'from': sender,
            'subject': subject,
            'labels': ', '.join(labels),
            'snippet': msg_data.get('snippet', ''),
        })
        progress_bar.progress((idx + 1) / len(messages))

    progress_bar.empty()
    st.success(f"✅ Завантажено {len(email_data)} листів!")
    return pd.DataFrame(email_data)


def parse_email_date(date_str):
    try:
        for fmt in [
            '%a, %d %b %Y %H:%M:%S %z',
            '%d %b %Y %H:%M:%S %z',
            '%a, %d %b %Y %H:%M:%S %Z',
            '%d %b %Y %H:%M:%S %Z',
        ]:
            try:
                return datetime.strptime(date_str, fmt)
            except Exception:
                continue
        return pd.NaT
    except Exception:
        return pd.NaT


def analyze_emails(df):
    df['parsed_date'] = df['date'].apply(parse_email_date)
    df['date_only'] = df['parsed_date'].dt.date
    df['hour'] = df['parsed_date'].dt.hour
    df['day_of_week'] = df['parsed_date'].dt.day_name()
    df['sender_domain'] = df['from'].apply(
        lambda x: x.split('@')[-1] if '@' in x else ''
    )
    df['is_reply'] = df['subject'].str.startswith('Re:', na=False)
    return df


# --- Інтерфейс Streamlit ---
st.set_page_config(page_title="Gmail Analytics Dashboard", layout="wide")
st.title("📊 Gmail Analytics Dashboard")
st.markdown("Аналізуйте свою електронну пошту за допомогою Gmail API")

with st.sidebar:
    st.header("⚙️ Налаштування")
    max_emails = st.slider("Кількість листів для аналізу", 50, 1000, 500)

    creds = get_credentials()

    if creds and creds.valid:
        if st.button("🔄 Завантажити дані з Gmail", type="primary"):
            with st.spinner("Підключення до Gmail API..."):
                try:
                    service = get_gmail_service(creds)
                    df = get_email_metadata(service, max_results=max_emails)
                    df = analyze_emails(df)
                    st.session_state['data'] = df
                    st.success("✅ Дані завантажено!")
                except Exception as e:
                    st.error(f"❌ Помилка: {str(e)}")

# --- Відображення результатів ---
if 'data' in st.session_state and st.session_state['data'] is not None:
    df = st.session_state['data']
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric("📧 Загальна кількість", len(df))
    with col2:
        st.metric("👤 Унікальних відправників", df['from'].nunique())
    with col3:
        top_domain = (
            df['sender_domain'].mode().iloc[0]
            if not df['sender_domain'].mode().empty
            else "N/A"
        )
        st.metric("🌐 Найпопулярніший домен", top_domain)
    with col4:
        st.metric("↩️ Відповідей", df['is_reply'].sum())

    st.divider()
    tab1, tab2, tab3, tab4 = st.tabs(
        ["📈 Часові тренди", "👥 Відправники", "🏷️ Мітки", "📊 Детальна таблиця"]
    )

    with tab1:
        st.subheader("📅 Активність за часом")
        if not df['date_only'].isna().all():
            daily_counts = df['date_only'].value_counts().sort_index()
            if not daily_counts.empty:
                st.altair_chart(
                    alt.Chart(
                        pd.DataFrame({
                            'date': daily_counts.index,
                            'count': daily_counts.values,
                        })
                    )
                    .mark_bar()
                    .encode(
                        x=alt.X('date:T', title='Дата'),
                        y=alt.Y('count:Q', title='Кількість листів'),
                    )
                    .properties(height=300),
                    use_container_width=True,
                )

        if not df['hour'].isna().all():
            hour_counts = df['hour'].value_counts().sort_index()
            st.subheader("⏰ Активність за годинами")
            st.altair_chart(
                alt.Chart(
                    pd.DataFrame({
                        'hour': hour_counts.index,
                        'count': hour_counts.values,
                    })
                )
                .mark_bar(color='steelblue')
                .encode(
                    x=alt.X('hour:O', title='Година'),
                    y=alt.Y('count:Q', title='Кількість листів'),
                )
                .properties(height=300),
                use_container_width=True,
            )

    with tab2:
        st.subheader("👥 Топ відправників")
        top_senders = df['from'].value_counts().head(10)
        st.dataframe(
            pd.DataFrame({
                'Відправник': top_senders.index,
                'Кількість листів': top_senders.values,
            }),
            use_container_width=True,
        )
        st.subheader("🌐 Топ доменів відправників")
        top_domains = df['sender_domain'].value_counts().head(10)
        st.bar_chart(top_domains)

    with tab3:
        st.subheader("🏷️ Аналіз міток")
        all_labels = []
        for labels in df['labels'].dropna():
            all_labels.extend(labels.split(', '))
        label_counts = Counter(all_labels)
        if label_counts:
            st.dataframe(
                pd.DataFrame({
                    'Мітка': list(label_counts.keys()),
                    'Кількість': list(label_counts.values()),
                }).sort_values('Кількість', ascending=False),
                use_container_width=True,
            )

    with tab4:
        st.subheader("📊 Детальна таблиця даних")
        st.dataframe(
            df[['date', 'from', 'subject', 'labels', 'is_reply']],
            use_container_width=True,
        )
        csv = df.to_csv(index=False)
        st.download_button(
            label="📥 Завантажити CSV",
            data=csv,
            file_name="gmail_analytics.csv",
            mime="text/csv",
        )
else:
    st.info("👈 Авторизуйтесь у бічній панелі та натисніть кнопку для аналізу.")
