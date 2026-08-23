# --- Інтерфейс Streamlit ---
st.set_page_config(page_title="Gmail Pro Analytics", layout="wide")
st.title("📊 Gmail Pro Analytics Dashboard")

# Сайдбар налаштувань
with st.sidebar:
    # 🔐 Схований висувний блок авторизації
    # За замовчуванням відкритий, якщо немає завантажених даних
    is_data_loaded = "raw_data" in st.session_state and st.session_state["raw_data"] is not None
    
    with st.expander("🔑 Авторизація IMAP", expanded=not is_data_loaded):
        user_email = st.text_input("Ваш Email", placeholder="name@domain.com")
        app_password = st.text_input(
            "Пароль додатка (16 символів)",
            type="password",
            placeholder="abcd efgh ijkl mnop",
        )

    st.divider()
    
    # Повзунок ліміту
    max_emails = st.slider(
        "Кількість останніх листів",
        min_value=500,
        max_value=100000,
        value=10000,
        step=1000,
    )
    btn_fetch = st.button("🔄 Завантажити пошту", type="primary")
