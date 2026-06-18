import os
import pandas as pd
import plotly.express as px
import streamlit as st

# Configure page settings
st.set_page_config(
    page_title="Brazil Politicians Contact Dashboard",
    page_icon="🇧🇷",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Premium dark-theme custom CSS injection
st.markdown(
    """
    <style>
    /* Import modern font */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
    
    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
    }
    
    /* Main app container background */
    .stApp {
        background-color: #0A0E16;
        color: #EDEFF3;
    }
    
    /* Custom header with glowing gradient */
    .glowing-header {
        background: linear-gradient(135deg, #1f2d47 0%, #121a2b 100%);
        border: 1px solid #232C40;
        border-radius: 16px;
        padding: 24px;
        margin-bottom: 25px;
        box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
    }
    
    .glowing-header h1 {
        margin: 0;
        font-weight: 800;
        letter-spacing: -1px;
        color: #EDEFF3;
        background: linear-gradient(to right, #57E2C6, #F4D27A);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
    }
    
    .glowing-header p {
        margin: 8px 0 0 0;
        color: #8089A0;
        font-size: 16px;
    }
    
    /* KPI Card styling with glassmorphism */
    .kpi-card {
        background: rgba(20, 28, 48, 0.65);
        border: 1px solid #232C40;
        border-radius: 12px;
        padding: 20px;
        text-align: center;
        box-shadow: 0 4px 20px 0 rgba(0, 0, 0, 0.25);
        margin-bottom: 15px;
    }
    
    .kpi-label {
        font-family: 'Inter', sans-serif;
        color: #8089A0;
        font-size: 13px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 1px;
        margin-bottom: 8px;
    }
    
    .kpi-value {
        font-size: 32px;
        font-weight: 800;
        color: #EDEFF3;
    }
    
    /* Accent borders for specific KPI cards */
    .kpi-total { border-top: 4px solid #C97D3D; }
    .kpi-emails { border-top: 4px solid #57E2C6; }
    .kpi-phones { border-top: 4px solid #F4D27A; }
    .kpi-coverage { border-top: 4px solid #B9C0CC; }
    
    /* Styled container headers */
    h2, h3 {
        color: #EDEFF3 !important;
        font-weight: 700;
    }
    
    /* Sidebar adjustments */
    section[data-testid="stSidebar"] {
        background-color: #0E1422 !important;
        border-right: 1px solid #232C40;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# Title Banner
st.markdown(
    """
    <div class="glowing-header">
        <h1>Brazil Politician Leads Dashboard 🇧🇷</h1>
        <p>Real-time visualization of the largest open contact dataset for Brazilian municipal legislators, harvested via AI (Playwright + Ollama).</p>
    </div>
    """,
    unsafe_allow_html=True,
)

# File paths
DEFAULT_CSV_PATH = "data/silver/contatos-vereadores.csv"

# Load the dataset
@st.cache_data
def load_data(filepath):
    if not os.path.exists(filepath):
        return None
    df = pd.read_csv(filepath)
    # Clean up fields
    df["email"] = df["email"].astype(str).str.strip().replace({"nan": None, "None": None, "": None})
    df["telefone"] = df["telefone"].astype(str).str.strip().replace({"nan": None, "None": None, "": None})
    return df

df_raw = load_data(DEFAULT_CSV_PATH)

if df_raw is None:
    st.error(f"Error: Data file not found at `{DEFAULT_CSV_PATH}`.")
    st.info("If you want to test locally, you can upload the CSV file manually using the button below:")
    uploaded_file = st.file_uploader("Choose a CSV file", type=["csv"])
    if uploaded_file is not None:
        df_raw = pd.read_csv(uploaded_file)
        df_raw["email"] = df_raw["email"].astype(str).str.strip().replace({"nan": None, "None": None, "": None})
        df_raw["telefone"] = df_raw["telefone"].astype(str).str.strip().replace({"nan": None, "None": None, "": None})
else:
    # Sidebar Filters
    st.sidebar.markdown("### 🔍 Filters & Search")
    
    # State Selector
    available_states = sorted(df_raw["UF"].dropna().unique())
    selected_states = st.sidebar.multiselect("Select States (UF):", options=available_states, default=[])
    
    # City Search Selector
    if selected_states:
        available_cities = sorted(df_raw[df_raw["UF"].isin(selected_states)]["Municipio"].dropna().unique())
    else:
        available_cities = sorted(df_raw["Municipio"].dropna().unique())
        
    selected_cities = st.sidebar.multiselect("Filter by City:", options=available_cities, default=[])
    
    # Search Box for Politician/Email
    search_query = st.sidebar.text_input("Search by Politician Name or Email:")

    # Apply filters to working dataset
    df_filtered = df_raw.copy()
    if selected_states:
        df_filtered = df_filtered[df_filtered["UF"].isin(selected_states)]
    if selected_cities:
        df_filtered = df_filtered[df_filtered["Municipio"].isin(selected_cities)]
    if search_query:
        query = search_query.lower()
        df_filtered = df_filtered[
            df_filtered["Nome Vereador"].astype(str).str.lower().str.contains(query) |
            df_filtered["email"].astype(str).str.lower().str.contains(query)
        ]

    # Calculate metrics
    total_records = len(df_filtered)
    emails_found = df_filtered["email"].notna().sum()
    phones_found = df_filtered["telefone"].notna().sum()
    
    email_cov_pct = (emails_found / total_records * 100) if total_records > 0 else 0
    phone_cov_pct = (phones_found / total_records * 100) if total_records > 0 else 0

    # Layout: KPIs
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.markdown(
            f"""
            <div class="kpi-card kpi-total">
                <div class="kpi-label">Total Politicians</div>
                <div class="kpi-value">{total_records:,}</div>
            </div>
            """,
            unsafe_allow_html=True
        )
        
    with col2:
        st.markdown(
            f"""
            <div class="kpi-card kpi-emails">
                <div class="kpi-label">Emails Found</div>
                <div class="kpi-value">{emails_found:,}</div>
            </div>
            """,
            unsafe_allow_html=True
        )
        
    with col3:
        st.markdown(
            f"""
            <div class="kpi-card kpi-phones">
                <div class="kpi-label">Phones Found</div>
                <div class="kpi-value">{phones_found:,}</div>
            </div>
            """,
            unsafe_allow_html=True
        )
        
    with col4:
        st.markdown(
            f"""
            <div class="kpi-card kpi-coverage">
                <div class="kpi-label">Email Coverage Rate</div>
                <div class="kpi-value">{email_cov_pct:.1f}%</div>
            </div>
            """,
            unsafe_allow_html=True
        )

    # Layout: Plots
    chart_col1, chart_col2 = st.columns(2)
    
    # 1. State Coverage Bar Chart
    with chart_col1:
        st.subheader("Contact Distribution by State")
        
        state_stats = df_filtered.groupby("UF").agg(
            Total=("Nome Vereador", "count"),
            Emails=("email", lambda x: x.notna().sum())
        ).reset_index()
        state_stats["Coverage (%)"] = (state_stats["Emails"] / state_stats["Total"] * 100).round(1)
        state_stats = state_stats.sort_values(by="Total", ascending=False)
        
        fig_states = px.bar(
            state_stats,
            x="UF",
            y=["Total", "Emails"],
            barmode="group",
            title="Contacts Found vs. Total Roster per UF (State)",
            labels={"value": "Count", "variable": "Metric"},
            color_discrete_sequence=["#1f2d47", "#57E2C6"],
            template="plotly_dark"
        )
        fig_states.update_layout(
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            margin=dict(l=20, r=20, t=40, b=20),
        )
        st.plotly_chart(fig_states, use_container_width=True)

    # 2. Email domain distribution (Gmail vs Institutional)
    with chart_col2:
        st.subheader("Email Domain Distribution")
        
        valid_emails = df_filtered[df_filtered["email"].notna()]["email"]
        
        def extract_domain(email_str):
            if "@" in email_str:
                return email_str.split("@")[-1].lower()
            return "Invalid"
            
        domains = valid_emails.apply(extract_domain)
        
        # Categorize domains
        def categorize_domain(domain):
            if "leg.br" in domain or "gov.br" in domain:
                return "Official (.gov.br / .leg.br)"
            elif "gmail.com" in domain:
                return "Gmail"
            elif "hotmail.com" in domain or "outlook.com" in domain or "live.com" in domain:
                return "Microsoft (Hotmail/Outlook)"
            else:
                return "Other Providers"
                
        domains_cat = domains.apply(categorize_domain).value_counts().reset_index()
        domains_cat.columns = ["Domain", "Count"]
        
        fig_domains = px.pie(
            domains_cat,
            values="Count",
            names="Domain",
            hole=0.4,
            title="Personal vs. Official Contact Distribution",
            color_discrete_sequence=["#57E2C6", "#1f2d47", "#F4D27A", "#C97D3D"],
            template="plotly_dark"
        )
        fig_domains.update_layout(
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            margin=dict(l=20, r=20, t=40, b=20),
        )
        st.plotly_chart(fig_domains, use_container_width=True)

    # Data Explorer Table
    st.subheader("🔎 Data Explorer")
    st.markdown("Use the table below to search, sort, and filter the dataset, and export the subset.")
    
    # Render interactive DataFrame
    st.dataframe(
        df_filtered[["UF", "Municipio", "Nome Vereador", "email", "telefone"]],
        use_container_width=True,
        hide_index=True
    )
    
    # Download Button
    csv = df_filtered.to_csv(index=False).encode('utf-8')
    st.download_button(
        label="📥 Export Filtered Data as CSV",
        data=csv,
        file_name="filtered-contacts.csv",
        mime="text/csv",
    )
    
    # Sidebar footer info
    st.sidebar.markdown("---")
    st.sidebar.markdown(
        """
        <div style='text-align: center; color: #5C6478; font-size: 11px;'>
            Developed by Anderson Monteiro<br/>
            Stack: Streamlit + Pandas + Plotly
        </div>
        """,
        unsafe_allow_html=True
    )
