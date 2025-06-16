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
    """Setup OAuth configuration from Streamlit secrets or user input"""
    st.sidebar.subheader("🔐 Google API Configuration")
    
    if hasattr(st, 'secrets') and 'google_oauth' in st.secrets:
        client_id = st.secrets['google_oauth']['client_id']
        client_secret = st.secrets['google_oauth']['client_secret']
        st.sidebar.success("✅ Using configured OAuth credentials")
    else:
        st.sidebar.warning("⚠️ Configure OAuth credentials in Streamlit secrets or enter below:")
        client_id = st.sidebar.text_input("Google OAuth Client ID", 
                                        help="Get from Google Cloud Console")
        client_secret = st.sidebar.text_input("Google OAuth Client Secret", 
                                            type="password",
                                            help="Get from Google Cloud Console")
    
    if client_id and client_secret:
        CLIENT_CONFIG["installed"]["client_id"] = client_id
        CLIENT_CONFIG["installed"]["client_secret"] = client_secret
        return True
    return False

def get_gsc_service():
    """Initialize Google Search Console service"""
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
    """Handle GSC authentication flow using desktop application flow"""
    if not setup_oauth_config():
        return None
    
    st.subheader("🔗 Google Search Console Authentication")
    
    if st.button("🔗 Generate Authentication URL", type="primary"):
        try:
            flow = Flow.from_client_config(CLIENT_CONFIG, SCOPES)
            flow.redirect_uri = 'urn:ietf:wg:oauth:2.0:oob'
            auth_url, _ = flow.authorization_url(prompt='consent', access_type='offline')
            st.markdown(f"### Step 1: Authorize Access [🔗 Click here]({auth_url})")
            st.session_state['oauth_flow'] = flow
            
        except Exception as e:
            st.error(f"Error generating auth URL: {str(e)}")
    
    if 'oauth_flow' in st.session_state:
        auth_code = st.text_area("📋 Paste the authorization code here:")
        
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
    
    return None

def get_gsc_sites(service):
    """Get list of GSC properties"""
    try:
        sites = service.sites().list().execute()
        return [site['siteUrl'] for site in sites.get('siteEntry', [])]
    except Exception as e:
        st.error(f"Error fetching sites: {str(e)}")
        return []

def fetch_gsc_data(service, site_url, start_date, end_date, dimensions=['query']):
    """Fetch data from GSC API"""
    try:
        request = {
            'startDate': start_date.strftime('%Y-%m-%d'),
            'endDate': end_date.strftime('%Y-%m-%d'),
            'dimensions': dimensions,
            'rowLimit': 25000,
            'startRow': 0
        }
        
        with st.spinner('Fetching data from Google Search Console...'):
            response = service.searchanalytics().query(siteUrl=site_url, body=request).execute()
        
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
    """Calculate zero-click metrics and filter keywords"""
    if df.empty:
        return df
    
    df['Zero_Click_Score'] = np.where(df['Impressions'] > 0,
                                     (df['Impressions'] - df['Clicks']) / df['Impressions'] * 100,
                                     0)
    
    return df

def identify_zero_click_keywords(df, min_impressions, max_ctr, min_zero_click_score):
    """Identify potential zero-click keywords based on criteria"""
    if df.empty:
        return df
    
    zero_click_keywords = df[(
        df['Impressions'] >= min_impressions) &
        (df['CTR'] <= max_ctr) &
        (df['Zero_Click_Score'] >= min_zero_click_score)
    ].copy()
    
    return zero_click_keywords.sort_values('Zero_Click_Score', ascending=False)

def create_visualizations(df, zero_click_df):
    """Create visualizations for the data"""
    if df.empty:
        return None, None, None
    
    # CTR vs Impressions scatter plot
    fig1 = px.scatter(df, x='Impressions', y='CTR', hover_data=['Query'],
                     title='CTR vs Impressions', labels={'CTR': 'Click-Through Rate (%)', 'Impressions': 'Impressions'})
    
    # Make sure there is no non-numeric or NaN data
    try:
        fig1.update_xaxis(type="log")
    except:
        st.warning("Error updating X-axis to log scale. Ensure the data is valid.")
    
    # Zero-click score distribution
    fig2 = px.histogram(df, x='Zero_Click_Score', title='Distribution of Zero-Click Scores', labels={'Zero_Click_Score': 'Zero-Click Score (%)'})
    
    # Top zero-click keywords
    if not zero_click_df.empty:
        top_zero_click = zero_click_df.head(20)
        fig3 = px.bar(top_zero_click, x='Zero_Click_Score', y='Query', orientation='h', title='Top 20 Zero-Click Keywords', labels={'Zero_Click_Score': 'Zero-Click Score (%)'})
        fig3.update_layout(height=600)
    else:
        fig3 = None
    
    return fig1, fig2, fig3

def main():
    st.set_page_config(page_title="GSC Zero-Click Keywords Filter", page_icon="🔍", layout="wide")
    
    st.title("🔍 GSC Zero-Click Keywords Filter")
    st.markdown("Connect to Google Search Console API to identify potential zero-click keywords")
    
    if 'authenticated' not in st.session_state:
        st.session_state['authenticated'] = False
    
    service = get_gsc_service()
    
    if service is None:
        st.info("👆 Please authenticate with Google Search Console to get started")
        authenticate_gsc()
        return
    
    # GSC Property Selection with memory
    st.subheader("🌐 Select GSC Property")
    sites = get_gsc_sites(service)
    
    if not sites:
        st.error("No GSC properties found. Make sure you have access to at least one property.")
        return
    
    # Use selected site from session state, if available
    selected_site = st.selectbox("Choose a property:", sites, index=sites.index(st.session_state.get('selected_site', sites[0])))
    st.session_state['selected_site'] = selected_site

    # Date range selection
    st.subheader("📅 Select Date Range")
    col1, col2 = st.columns(2)
    
    with col1:
        start_date = st.date_input("Start Date", value=datetime.now() - timedelta(days=30))
    
    with col2:
        end_date = st.date_input("End Date", value=datetime.now())
    
    # Fetch data
    df = fetch_gsc_data(service, selected_site, start_date, end_date)
    df = calculate_zero_click_metrics(df)
    
    # Filter zero-click keywords
    min_impressions = st.slider("Minimum Impressions", 100, 10000, 1000)
    max_ctr = st.slider("Maximum CTR (%)", 0.0, 10.0, 1.0)
    min_zero_click_score = st.slider("Minimum Zero-Click Score (%)", 0.0, 100.0, 30.0)
    
    zero_click_df = identify_zero_click_keywords(df, min_impressions, max_ctr, min_zero_click_score)
    
    # Visualizations
    fig1, fig2, fig3 = create_visualizations(df, zero_click_df)
    
    if fig1:
        st.plotly_chart(fig1, use_container_width=True)
    if fig2:
        st.plotly_chart(fig2, use_container_width=True)
    if fig3:
        st.plotly_chart(fig3, use_container_width=True)

if __name__ == "__main__":
    main()
