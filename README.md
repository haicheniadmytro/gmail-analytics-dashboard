# 📊 Gmail Analytics Dashboard

![Streamlit](https://img.shields.io/badge/Streamlit-1.28+-FF4B4B?style=for-the-badge&logo=Streamlit&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.8+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Gmail API](https://img.shields.io/badge/Gmail_API-enabled-34A853?style=for-the-badge&logo=gmail&logoColor=white)

Інтерактивний веб-додаток для аналізу вашої електронної пошти Gmail. Отримуйте статистику, тренди, топ відправників, розподіл за мітками та багато іншого — у зручному дашборді на базі Streamlit.

[🌐 Демо-версія (приклад)](https://your-app-name.streamlit.app) — замініть на своє посилання після деплою.

---

## 🚀 Функціонал

- **Авторизація через Gmail API** — безпечний OAuth 2.0.
- **Завантаження метаданих** (до 1000 листів) — відправник, тема, дата, мітки, уривок.
- **Загальна статистика** — загальна кількість листів, унікальні відправники, найпопулярніший домен, кількість відповідей.
- **Часові тренди** — динаміка листів за днями та годинами (графіки Altair).
- **Топ відправників та доменів** — таблиця та стовпчикова діаграма.
- **Аналіз міток** — частота використання міток Gmail.
- **Детальна таблиця** з можливістю експорту в CSV.

---

## 🛠 Технології

- [Python 3.8+](https://www.python.org/)
- [Streamlit](https://streamlit.io/) — інтерактивний фронтенд
- [Gmail API](https://developers.google.com/gmail/api) — доступ до поштової скриньки
- [Pandas](https://pandas.pydata.org/) — обробка даних
- [Altair](https://altair-viz.github.io/) — візуалізація
- [Matplotlib](https://matplotlib.org/) — додаткові графіки (опціонально)

---

## 📋 Вимоги

- Обліковий запис Google (Gmail)
- Проєкт у [Google Cloud Console](https://console.cloud.google.com/)
- Файл `credentials.json` (OAuth 2.0 клієнтські облікові дані)
- (Для локального запуску) Python та pip

---

## 🔧 Інструкція з налаштування

### 1. Отримання `credentials.json`

1. Перейдіть до [Google Cloud Console](https://console.cloud.google.com/).
2. Створіть новий проєкт (або виберіть існуючий).
3. У меню **API & Services > Library** увімкніть **Gmail API**.
4. Перейдіть до **API & Services > OAuth consent screen**:
   - Виберіть **External**.
   - Заповніть назву додатку, email підтримки тощо.
   - Додайте свою Gmail-адресу до **Test users**.
5. Перейдіть до **API & Services > Credentials**:
   - Натисніть **+ Create Credentials > OAuth client ID**.
   - Виберіть **Desktop app**.
   - Завантажте файл `credentials.json`.

### 2. Підготовка проєкту

```bash
git clone https://github.com/your-username/gmail-analytics.git
cd gmail-analytics