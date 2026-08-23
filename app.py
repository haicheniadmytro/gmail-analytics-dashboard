import streamlit as st
import pandas as pd
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import os
import pickle
import base64
from datetime import datetime, timedelta
import re
from collections import Counter
import altair as alt

# --- Конфігурація ---
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

# --- Функції для роботи з Gmail API ---
@st.cache_resource
def get_gmail_service():
    """Авторизація та створення сервісу Gmail API."""
    creds = None
    # Шлях до файлу з токеном
    token_file = 'token.pickle'
    
    # Завантажуємо збережені облікові дані
    if os.path.exists(token_file):
        with open(token_file, 'rb') as token:
            creds = pickle.load(token)
    
    # Якщо немає дійсних облікових даних, виконуємо вхід
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # Переконуємось, що файл credentials.json існує
            if not os.path.exists('credentials.json'):
                st.error("❌ Файл 'credentials.json' не знайдено. Будь ласка, завантажте його в кореневу папку проєкту.")
                st.stop()
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        
        # Зберігаємо облікові дані для наступного використання
        with open(token_file, 'wb') as token:
            pickle.dump(creds, token)
    
    return build('gmail', 'v1', credentials=creds)

def get_email_metadata(service, max_results=500):
    """Отримує метадані електронних листів."""
    st.info(f"📧 Завантаження метаданих до {max_results} листів...")
    results = service.users().messages().list(userId='me', maxResults=max_results).execute()
    messages = results.get('messages', [])
    
    email_data = []
    progress_bar = st.progress(0)
    
    for idx, msg in enumerate(messages):
        msg_data = service.users().messages().get(userId='me', id=msg['id']).execute()
        headers = msg_data['payload'].get('headers', [])
        
        # Витягуємо необхідні заголовки
        subject = next((h['value'] for h in headers if h['name'] == 'Subject'), '')
        sender = next((h['value'] for h in headers if h['name'] == 'From'), '')
        date = next((h['value'] for h in headers if h['name'] == 'Date'), '')
        
        # Отримуємо мітки
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
    """Парсить дату з заголовка email."""
    try:
        # Спроба різних форматів
        for fmt in ['%a, %d %b %Y %H:%M:%S %z', '%d %b %Y %H:%M:%S %z', '%a, %d %b %Y %H:%M:%S %Z', '%d %b %Y %H:%M:%S %Z']:
            try:
                return datetime.strptime(date_str, fmt)
            except:
                continue
        return pd.NaT
    except:
        return pd.NaT

def analyze_emails(df):
    """Аналізує дані email."""
    # Парсимо дати
    df['parsed_date'] = df['date'].apply(parse_email_date)
    df['date_only'] = df['parsed_date'].dt.date
    df['hour'] = df['parsed_date'].dt.hour
    df['day_of_week'] = df['parsed_date'].dt.day_name()
    
    # Витягуємо домени відправників
    df['sender_domain'] = df['from'].apply(lambda x: x.split('@')[-1] if '@' in x else '')
    
    # Визначаємо, чи це відповідь
    df['is_reply'] = df['subject'].str.startswith('Re:', na=False)
    
    return df

# --- Основний інтерфейс Streamlit ---
st.set_page_config(page_title="Gmail Analytics Dashboard", layout="wide")

st.title("📊 Gmail Analytics Dashboard")
st.markdown("Аналізуйте свою електронну пошту за допомогою Gmail API")

# Бічна панель для налаштувань
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
                st.info("Переконайтеся, що файл 'credentials.json' знаходиться в кореневій папці проєкту.")
    
    st.divider()
    st.markdown("### 📋 Інструкція")
    st.markdown("""
    1. Отримайте `credentials.json` у Google Cloud Console
    2. Завантажте файл у корінь проєкту
    3. Натисніть кнопку вище для завантаження даних
    4. Аналізуйте свою пошту!
    """)

# Головна область
if 'data' in st.session_state and st.session_state['data'] is not None:
    df = st.session_state['data']
    
    # Загальна статистика
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
    
    # Вкладки для різних видів аналітики
    tab1, tab2, tab3, tab4 = st.tabs(["📈 Часові тренди", "👥 Відправники", "🏷️ Мітки", "📊 Детальна таблиця"])
    
    with tab1:
        # Часовий графік
        st.subheader("📅 Активність за часом")
        
        # Графік за днями
        if not df['date_only'].isna().all():
            daily_counts = df['date_only'].value_counts().sort_index()
            if not daily_counts.empty:
                st.altair_chart(alt.Chart(
                    pd.DataFrame({'date': daily_counts.index, 'count': daily_counts.values})
                ).mark_bar().encode(
                    x=alt.X('date:T', title='Дата'),
                    y=alt.Y('count:Q', title='Кількість листів')
                ).properties(height=300), use_container_width=True)
        
        # Годинна активність
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
        
        # Домени відправників
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
        
        # Експорт
        csv = df.to_csv(index=False)
        st.download_button(
            label="📥 Завантажити CSV",
            data=csv,
            file_name="gmail_analytics.csv",
            mime="text/csv"
        )
else:
    st.info("👈 Натисніть кнопку 'Завантажити дані з Gmail' у бічній панелі, щоб почати аналіз.")
    st.image("https://streamlit.io/images/brand/streamlit-logo-primary-colormark-darktext.png", width=200)
