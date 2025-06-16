import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
import json
from datetime import datetime, timedelta
import time

# Configuration
SCOPES = ['https://www.googleapis.com/auth/webmasters.readonly']

CLIENT_CONFIG = {
    "installed": {
        "client_id": "",
        "client_secret": "",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token"
    }
}

def setup_oauth_config():
    st.sidebar.subheader("🔐 Google API Configuration")
    if hasattr(st, 'secrets') and 'google_oauth' in st.secrets:
        client_id = st.secrets['google_oauth']['client_id']
        client_secret = st.secrets['google_oauth']['client_secret']
        st.sidebar.success("✅ Using configured OAuth credentials")
    else:
        st.sidebar.warning("⚠️ Configure OAuth credentials in Streamlit secrets or enter below:")
        client_id = st.sidebar.text_input("Google OAuth Client ID", help="Get from Google Cloud Console")
        client_secret = st.sidebar.text_input("Google OAuth Client Secret", type="password", help="Get from Google Cloud Console")
    if client_id and client_secret:
        CLIENT_CONFIG["installed"]["client_id"] = client_id
        CLIENT_CONFIG["installed"]["client_secret"] = client_secret
        return True
    return False

def get_gsc_service():
    try:
        if 'gsc_credentials' in st.session_state:
            creds = Credentials.from_authorized_user_info(
                st.session_state['gsc_credentials'], SCOPES)
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                st.session_state['gsc_credentials'] = json.loads(creds.to_json())
            return build('searchconsole', 'v1', credentials=creds)
        return None
    except Exception as e:
        st.error(f"Error initializing GSC service: {str(e)}")
        return None

def authenticate_gsc():
    if not setup_oauth_config():
        return None
    st.subheader("🔗 Google Search Console Authentication")
    if st.button("🔗 Generate Authentication URL", type="primary"):
        try:
            flow = Flow.from_client_config(CLIENT_CONFIG, SCOPES)
            flow.redirect_uri = 'urn:ietf:wg:oauth:2.0:oob'
            auth_url, _ = flow.authorization_url(prompt='consent', access_type='offline')
            st.markdown(f"""
            ### Step 1: Authorize Access
            Click the link below to authorize access to your Google Search Console:

            **[🔗 Click here to authorize GSC access]({auth_url})**

            ### Step 2: Copy Authorization Code
            After authorizing, Google will display an authorization code. Copy the entire code and paste it below.

            *Note: The code will look something like: `4/0AfJohXn...` (it's usually quite long)*
            """)
            st.session_state['oauth_flow'] = flow
        except Exception as e:
            st.error(f"Error generating auth URL: {str(e)}")
            st.error("Make sure you created a 'Desktop Application' OAuth client, not 'Web Application'")
    if 'oauth_flow' in st.session_state:
        auth_code = st.text_area(
            "📋 Paste the authorization code here:",
            placeholder="4/0AfJohXn...",
            help="Paste the complete authorization code from Google. It should start with '4/' and be quite long.",
            height=100
        )
        if auth_code and st.button("✅ Complete Authentication"):
            try:
                flow = st.session_state['oauth_flow']
                flow.fetch_token(code=auth_code.strip())
                st.session_state['gsc_credentials'] = json.loads(flow.credentials.to_json())
                del st.session_state['oauth_flow']
                st.success("✅ Successfully connected to Google Search Console!")
                st.balloons()
                time.sleep(2)
                st.rerun()
            except Exception as e:
                st.error(f"Authentication error: {str(e)}")
                if "invalid_grant" in str(e):
                    st.error("The authorization code may have expired. Please generate a new one.")
                else:
                    st.error("Please make sure you copied the complete authorization code.")
    return None

def get_gsc_sites(service):
    try:
        sites = service.sites().list().execute()
        return [site['siteUrl'] for site in sites.get('siteEntry', [])]
    except Exception as e:
        st.error(f"Error fetching sites: {str(e)}")
        return []

def fetch_gsc_data(service, site_url, start_date, end_date, dimensions=['query']):
    try:
        request = {
            'startDate': start_date.strftime('%Y-%m-%d'),
            'endDate': end_date.strftime('%Y-%m-%d'),
            'dimensions': dimensions,
            'rowLimit': 25000,
            'startRow': 0
        }
        with st.spinner('Fetching data from Google Search Console...'):
            response = service.searchanalytics().query(
                siteUrl=site_url, body=request).execute()
        if 'rows' not in response:
            st.warning("No data found for the selected date range.")
            return pd.DataFrame()
        data = []
        for row in response['rows']:
            data.append({
                'Query': row['keys'][0] if dimensions == ['query'] else row['keys'],
                'Clicks': row['clicks'],
                'Impressions': row['impressions'],
                'CTR': row['ctr'] * 100,
                'Position': row['position']
            })
        df = pd.DataFrame(data)
        return df
    except Exception as e:
        st.error(f"Error fetching GSC data: {str(e)}")
        return pd.DataFrame()

def calculate_zero_click_metrics(df):
    if df.empty:
        return df
    df['Zero_Click_Score'] = np.where(df['Impressions'] > 0,
                                     (df['Impressions'] - df['Clicks']) / df['Impressions'] * 100,
                                     0)
    return df

def identify_zero_click_keywords(df, min_impressions, max_ctr, min_zero_click_score):
    if df.empty:
        return df
    zero_click_keywords = df[
        (df['Impressions'] >= min_impressions) &
        (df['CTR'] <= max_ctr) &
        (df['Zero_Click_Score'] >= min_zero_click_score)
    ].copy()
    return zero_click_keywords.sort_values('Zero_Click_Score', ascending=False)

def create_visualizations(df, zero_click_df):
    if df.empty:
        return None, None, None
    fig1 = px.scatter(df, x='Impressions', y='CTR',
                     hover_data=['Query'],
                     title='CTR vs Impressions',
                     labels={'CTR': 'Click-Through Rate (%)', 'Impressions': 'Impressions'})
    fig1.update_xaxes(type="log")
    fig2 = px.histogram(df, x='Zero_Click_Score',
                       title='Distribution of Zero-Click Scores',
                       labels={'Zero_Click_Score': 'Zero-Click Score (%)'})
    if not zero_click_df.empty:
        top_zero_click = zero_click_df.head(20)
        fig3 = px.bar(top_zero_click, x='Zero_Click_Score', y='Query',
                     orientation='h',
                     title='Top 20 Zero-Click Keywords',
                     labels={'Zero_Click_Score': 'Zero-Click Score (%)'})
        fig3.update_layout(height=600)
    else:
        fig3 = None
    return fig1, fig2, fig3

def main():
    st.set_page_config(page_title="GSC Zero-Click Keywords Filter",
                      page_icon="🔍",
                      layout="wide")
    st.title("🔍 GSC Zero-Click Keywords Filter")
    st.markdown("Connect to Google Search Console API to identify potential zero-click keywords")

    if 'authenticated' not in st.session_state:
        st.session_state['authenticated'] = False

    service = get_gsc_service()

    if service is None:
        st.info("👆 Please authenticate with Google Search Console to get started")
        authenticate_gsc()
        with st.expander("📋 Setup Instructions"):
            st.markdown("""
            ### How to set up GSC API access:

            1. **Create a Google Cloud Project**:
               - Go to [Google Cloud Console](https://console.cloud.google.com/)
               - Create a new project or select existing one

            2. **Enable Search Console API**:
               - Go to APIs & Services → Library
               - Search for "Google Search Console API"
               - Click "Enable"

            3. **Create OAuth 2.0 Credentials**:
               - Go to APIs & Services → Credentials
               - Click "Create Credentials" → "OAuth 2.0 Client ID"
               - **Important**: Choose "Desktop Application" (NOT Web Application)
               - Copy Client ID and Client Secret

            4. **Configure Streamlit Secrets** (recommended):
               - Add to `.streamlit/secrets.toml`:
               ```toml
               [google_oauth]
               client_id = "your-client-id"
               client_secret = "your-client-secret"
               ```
               - For Streamlit Cloud: Add via the app settings → Secrets

            5. **Alternative**: Enter credentials in the sidebar

            ### 🔧 Troubleshooting:
            - **redirect_uri_mismatch error**: Make sure you created a "Desktop Application" OAuth client
            - **invalid_grant error**: The authorization code expired, generate a new one
            - Make sure you have access to at least one Google Search Console property
            - The authorization code is usually a long string starting with "4/"
            - If authentication fails, try generating a new authorization URL
            - Ensure your Google Cloud project has the Search Console API enabled
            """)
        return

    st.sidebar.header("🎯 Filter Parameters")
    min_impressions = st.sidebar.number_input("Minimum Impressions",
                                            min_value=1,
                                            value=100,
                                            help="Keywords must have at least this many impressions")
    max_ctr = st.sidebar.number_input("Maximum CTR (%)",
                                     min_value=0.0,
                                     max_value=100.0,
                                     value=5.0,
                                     step=0.1,
                                     help="Keywords with CTR below this threshold")
    min_zero_click_score = st.sidebar.number_input("Minimum Zero-Click Score (%)",
                                                  min_value=0.0,
                                                  max_value=100.0,
                                                  value=80.0,
                                                  step=1.0,
                                                  help="Minimum zero-click score to be considered")

    st.subheader("🌐 Select GSC Property")
    sites = get_gsc_sites(service)

    if not sites:
        st.error("No GSC properties found. Make sure you have access to at least one property.")
        return

    if (
        "selected_site" not in st.session_state
        or st.session_state.selected_site not in sites
    ):
        st.session_state.selected_site = sites[0] if sites else None

    selected_site = st.selectbox(
        "Choose a property:", sites, key="selected_site"
    )

    st.subheader("📅 Select Date Range")
    col1, col2 = st.columns(2)

    with col1:
        start_date = st.date_input("Start Date",
                                 value=datetime.now() - timedelta(days=28),
                                 max_value=datetime.now() - timedelta(days=3))
    with col2:
        end_date = st.date_input("End Date",
                               value=datetime.now() - timedelta(days=3),
                               max_value=datetime.now() - timedelta(days=3))

    if start_date >= end_date:
        st.error("Start date must be before end date")
        return

    if st.button("📊 Fetch GSC Data", type="primary"):
        df = fetch_gsc_data(service, selected_site, start_date, end_date)
        if not df.empty:
            st.session_state['gsc_data'] = df
            st.success(f"✅ Data fetched successfully! {len(df)} keywords found.")
        else:
            st.error("No data retrieved. Please check your date range and try again.")

    if 'gsc_data' in st.session_state:
        df = st.session_state['gsc_data']
        df = calculate_zero_click_metrics(df)
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Total Keywords", len(df))
        with col2:
            st.metric("Total Impressions", f"{df['Impressions'].sum():,}")
        with col3:
            st.metric("Total Clicks", f"{df['Clicks'].sum():,}")
        with col4:
            avg_ctr = df['CTR'].mean() if len(df) > 0 else 0
            st.metric("Average CTR", f"{avg_ctr:.2f}%")

        zero_click_df = identify_zero_click_keywords(df, min_impressions, max_ctr, min_zero_click_score)

        st.subheader(f"🎯 Zero-Click Keywords ({len(zero_click_df)} found)")

        if not zero_click_df.empty:
            display_columns = ['Query', 'Impressions', 'Clicks', 'CTR', 'Position', 'Zero_Click_Score']
            st.dataframe(zero_click_df[display_columns].round(2), use_container_width=True)
            csv = zero_click_df.to_csv(index=False)
            st.download_button(
                label="📥 Download Zero-Click Keywords CSV",
                data=csv,
                file_name=f"zero_click_keywords_{selected_site.replace('https://', '').replace('/', '_')}_{start_date}_{end_date}.csv",
                mime="text/csv"
            )
            st.subheader("📊 Data Visualizations")
            fig1, fig2, fig3 = create_visualizations(df, zero_click_df)
            if fig1 and fig2:
                tab1, tab2, tab3 = st.tabs(["CTR vs Impressions", "Zero-Click Distribution", "Top Zero-Click Keywords"])
                with tab1:
                    st.plotly_chart(fig1, use_container_width=True)
                with tab2:
                    st.plotly_chart(fig2, use_container_width=True)
                with tab3:
                    if fig3:
                        st.plotly_chart(fig3, use_container_width=True)
                    else:
                        st.info("No zero-click keywords found with current filters")
            st.subheader("💡 Insights")
            col1, col2 = st.columns(2)
            with col1:
                st.write("**Zero-Click Keyword Statistics:**")
                st.write(f"• {len(zero_click_df)} keywords identified as potential zero-click")
                st.write(f"• {zero_click_df['Impressions'].sum():,} total impressions for zero-click keywords")
                st.write(f"• {zero_click_df['Clicks'].sum():,} total clicks for zero-click keywords")
                avg_zero_click_score = zero_click_df['Zero_Click_Score'].mean()
                avg_position = zero_click_df['Position'].mean()
                st.write(f"• Average zero-click score: {avg_zero_click_score:.1f}%")
                st.write(f"• Average position: {avg_position:.1f}")
            with col2:
                st.write("**Recommendations:**")
                st.write("• Focus on featured snippets optimization for high-scoring keywords")
                st.write("• Consider creating more engaging meta descriptions")
                st.write("• Analyze SERP features for these keywords")
                st.write("• Monitor brand visibility in knowledge panels")
                st.write("• Optimize for position 1 to capture more clicks")
        else:
            st.info("No zero-click keywords found with the current filter settings. Try adjusting the parameters in the sidebar.")
        st.subheader("📋 All Keywords Sample")
        display_columns = ['Query', 'Impressions', 'Clicks', 'CTR', 'Position', 'Zero_Click_Score']
        st.dataframe(df[display_columns].head(10).round(2),
                    use_container_width=True)

    if st.sidebar.button("🔓 Disconnect GSC"):
        if 'gsc_credentials' in st.session_state:
            del st.session_state['gsc_credentials']
        if 'gsc_data' in st.session_state:
            del st.session_state['gsc_data']
        st.session_state['authenticated'] = False
        st.rerun()

if __name__ == "__main__":
    main()
