import streamlit as st
import pandas as pd
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import os
import pickle
import json
from datetime import datetime
from collections import Counter
import altair as alt
import webbrowser

# --- Конфігурація ---
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

# --- Універсальна авторизація (працює і в хмарі, і локально) ---
@st.cache_resource
def get_gmail_service():
    creds = None
    token_file = 'token.pickle'

    # Якщо є збережений токен – використовуємо його
    if os.path.exists(token_file):
        with open(token_file, 'rb') as token:
            creds = pickle.load(token)

    # Якщо токен невалідний або відсутній – запускаємо потік авторизації
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # --- Отримуємо облікові дані (Secrets або локальний файл) ---
            if 'gmail_credentials' in st.secrets:
                try:
                    credentials_dict = json.loads(st.secrets['gmail_credentials'])
                    flow = InstalledAppFlow.from_client_config(credentials_dict, SCOPES)
                    st.info("🔐 Використовую облікові дані з Streamlit Secrets")
                except Exception as e:
                    st.error(f"❌ Помилка парсингу Secrets: {e}")
                    st.stop()
            elif os.path.exists('credentials.json'):
                flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
                st.info("📁 Використовую локальний файл credentials.json")
            else:
                st.error("❌ Облікові дані не знайдено! Додайте 'gmail_credentials' у Secrets або завантажте 'credentials.json'.")
                st.stop()

            # --- Визначення середовища: хмара, якщо є Secrets ---
            in_cloud = 'gmail_credentials' in st.secrets

            if in_cloud:
                # ========== РЕЖИМ ХМАРИ (ручне введення коду) ==========
                st.info("🌐 Ви в хмарному середовищі. Авторизація відбуватиметься вручну.")
                auth_url, state = flow.authorization_url(prompt='consent')
                st.markdown(f"**1. Перейдіть за посиланням:** [Натисніть тут, щоб авторизуватися]({auth_url})")
                st.markdown("**2. Після входу скопіюйте код із адресного рядка браузера (параметр `code=...`) та вставте його нижче:**")

                auth_code = st.text_input("Введіть код авторизації:", type="password")

                if auth_code:
                    try:
                        flow.fetch_token(code=auth_code)
                        creds = flow.credentials
                        st.success("✅ Авторизацію успішно завершено!")
                    except Exception as e:
                        st.error(f"❌ Помилка обміну коду на токен: {e}")
                        st.stop()
                else:
                    st.warning("⏳ Очікуємо введення коду...")
                    st.stop()
            else:
                # ========== ЛОКАЛЬНИЙ РЕЖИМ (автоматичне відкриття браузера) ==========
                st.info("🖥️ Локальне середовище. Відкриваємо браузер для авторизації...")
                try:
                    webbrowser.open(flow.authorization_url()[0])
                except:
                    pass
                # Запускаємо локальний сервер (без відкриття браузера, оскільки ми вже відкрили)
                creds = flow.run_local_server(port=0, open_browser=False)

        # Зберігаємо токен для наступних запусків
        with open(token_file, 'wb') as token:
            pickle.dump(creds, token)

    return build('gmail', 'v1', credentials=creds)


# --- Функції завантаження та аналізу даних ---
def get_email_metadata(service, max_results=500):
    st.info(f"📧 Завантаження метаданих до {max_results} листів...")
    results = service.users().messages().list(userId='me', maxResults=max_results).execute()
    messages = results.get('messages', [])

    email_data = []
    progress_bar = st.progress(0)

    for idx, msg in enumerate(messages):
        msg_data = service.users().messages().get(userId='me', id=msg['id']).execute()
        headers = msg_data['payload'].get('headers', [])

        subject = next((h['value'] for h in headers if h['name'] == 'Subject'), '')
        sender = next((h['value'] for h in headers if h['name'] == 'From'), '')
        date = next((h['value'] for h in headers if h['name'] == 'Date'), '')
        labels = msg_data.get('labelIds', [])

        email_data.append({
            'id': msg['id'],
            'date': date,
            'from': sender,
            'subject': subject,
            'labels': ', '.join(labels),
            'snippet': msg_data.get('snippet', '')
        })

        progress_bar.progress((idx + 1) / len(messages))

    progress_bar.empty()
    st.success(f"✅ Завантажено {len(email_data)} листів!")
    return pd.DataFrame(email_data)


def parse_email_date(date_str):
    try:
        for fmt in ['%a, %d %b %Y %H:%M:%S %z', '%d %b %Y %H:%M:%S %z',
                    '%a, %d %b %Y %H:%M:%S %Z', '%d %b %Y %H:%M:%S %Z']:
            try:
                return datetime.strptime(date_str, fmt)
            except:
                continue
        return pd.NaT
    except:
        return pd.NaT


def analyze_emails(df):
    df['parsed_date'] = df['date'].apply(parse_email_date)
    df['date_only'] = df['parsed_date'].dt.date
    df['hour'] = df['parsed_date'].dt.hour
    df['day_of_week'] = df['parsed_date'].dt.day_name()
    df['sender_domain'] = df['from'].apply(lambda x: x.split('@')[-1] if '@' in x else '')
    df['is_reply'] = df['subject'].str.startswith('Re:', na=False)
    return df


# --- Інтерфейс Streamlit ---
st.set_page_config(page_title="Gmail Analytics Dashboard", layout="wide")
st.title("📊 Gmail Analytics Dashboard")
st.markdown("Аналізуйте свою електронну пошту за допомогою Gmail API")

with st.sidebar:
    st.header("⚙️ Налаштування")
    max_emails = st.slider("Кількість листів для аналізу", 50, 1000, 500)

    if st.button("🔄 Завантажити дані з Gmail", type="primary"):
        with st.spinner("Підключення до Gmail API..."):
            try:
                service = get_gmail_service()
                df = get_email_metadata(service, max_results=max_emails)
                df = analyze_emails(df)
                st.session_state['data'] = df
                st.success("✅ Дані завантажено успішно!")
            except Exception as e:
                st.error(f"❌ Помилка: {str(e)}")

    st.divider()
    st.markdown("### 📋 Інструкція")
    st.markdown("""
    1. Натисніть кнопку вище.
    2. Якщо ви в хмарі — перейдіть за посиланням, скопіюйте код та вставте його.
    3. Якщо локально — браузер відкриється автоматично.
    4. Аналізуйте свою пошту!
    """)

# --- Відображення даних (якщо вони вже завантажені) ---
if 'data' in st.session_state and st.session_state['data'] is not None:
    df = st.session_state['data']

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("📧 Загальна кількість", len(df))
    with col2:
        senders = df['from'].nunique()
        st.metric("👤 Унікальних відправників", senders)
    with col3:
        top_domain = df['sender_domain'].mode().iloc[0] if not df['sender_domain'].mode().empty else "N/A"
        st.metric("🌐 Найпопулярніший домен", top_domain)
    with col4:
        replies = df['is_reply'].sum()
        st.metric("↩️ Відповідей", replies)

    st.divider()

    tab1, tab2, tab3, tab4 = st.tabs(["📈 Часові тренди", "👥 Відправники", "🏷️ Мітки", "📊 Детальна таблиця"])

    with tab1:
        st.subheader("📅 Активність за часом")
        if not df['date_only'].isna().all():
            daily_counts = df['date_only'].value_counts().sort_index()
            if not daily_counts.empty:
                st.altair_chart(alt.Chart(
                    pd.DataFrame({'date': daily_counts.index, 'count': daily_counts.values})
                ).mark_bar().encode(
                    x=alt.X('date:T', title='Дата'),
                    y=alt.Y('count:Q', title='Кількість листів')
                ).properties(height=300), use_container_width=True)

        if not df['hour'].isna().all():
            hour_counts = df['hour'].value_counts().sort_index()
            st.subheader("⏰ Активність за годинами")
            st.altair_chart(alt.Chart(
                pd.DataFrame({'hour': hour_counts.index, 'count': hour_counts.values})
            ).mark_bar(color='steelblue').encode(
                x=alt.X('hour:O', title='Година'),
                y=alt.Y('count:Q', title='Кількість листів')
            ).properties(height=300), use_container_width=True)

    with tab2:
        st.subheader("👥 Топ відправників")
        top_senders = df['from'].value_counts().head(10)
        st.dataframe(pd.DataFrame({
            'Відправник': top_senders.index,
            'Кількість листів': top_senders.values
        }), use_container_width=True)

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
            st.dataframe(pd.DataFrame({
                'Мітка': list(label_counts.keys()),
                'Кількість': list(label_counts.values())
            }).sort_values('Кількість', ascending=False), use_container_width=True)

    with tab4:
        st.subheader("📊 Детальна таблиця даних")
        st.dataframe(df[['date', 'from', 'subject', 'labels', 'is_reply']], use_container_width=True)

        csv = df.to_csv(index=False)
        st.download_button(
            label="📥 Завантажити CSV",
            data=csv,
            file_name="gmail_analytics.csv",
            mime="text/csv"
        )
else:
    st.info("👈 Натисніть кнопку 'Завантажити дані з Gmail' у бічній панелі, щоб почати аналіз.")
