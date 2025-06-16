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
    
    # Try to get from secrets first
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
        # Check if we have stored credentials
        if 'gsc_credentials' in st.session_state:
            creds = Credentials.from_authorized_user_info(
                st.session_state['gsc_credentials'], SCOPES)
            
            # Refresh if expired
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
            
            # Generate authorization URL
            auth_url, _ = flow.authorization_url(
                prompt='consent',
                access_type='offline'
            )
            
            st.markdown(f"""
            ### Step 1: Authorize Access
            Click the link below to authorize access to your Google Search Console:
            
            **[🔗 Click here to authorize GSC access]({auth_url})**
            
            ### Step 2: Copy Authorization Code
            After authorizing, Google will display an authorization code. Copy the entire code and paste it below.
            
            *Note: The code will look something like: 4/0AfJohXn... (it's usually quite long)*""")
            
            st.session_state['oauth_flow'] = flow
            
        except Exception as e:
            st.error(f"Error generating auth URL: {str(e)}")
            st.error("Make sure you created a 'Desktop Application' OAuth client, not 'Web Application'")
    
    # Show input for authorization code if flow is initialized
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
                
                # Store credentials
                st.session_state['gsc_credentials'] = json.loads(flow.credentials.to_json())
                
                # Clean up
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
            'rowLimit': 25000,  # Maximum allowed
            'startRow': 0
        }
        
        with st.spinner('Fetching data from Google Search Console...'):
            response = service.searchanalytics().query(
                siteUrl=site_url, body=request).execute()
        
        if 'rows' not in response:
            st.warning("No data found for the selected date range.")
            return pd.DataFrame()
        
        # Convert to DataFrame
        data = []
        for row in response['rows']:
            data.append({
                'Query': row['keys'][0] if dimensions == ['query'] else row['keys'],
                'Clicks': row['clicks'],
                'Impressions': row['impressions'],
                'CTR': row['ctr'] * 100,  # Convert to percentage
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
    
    # Calculate zero-click score (higher score = more likely zero-click)
    df['Zero_Click_Score'] = np.where(df['Impressions'] > 0,
                                     (df['Impressions'] - df['Clicks']) / df['Impressions'] * 100,
                                     0)
    
    return df

def identify_zero_click_keywords(df, min_impressions, max_ctr, min_zero_click_score):
    """Identify potential zero-click keywords based on criteria"""
    if df.empty:
        return df
    
    zero_click_keywords = df[ 
        (df['Impressions'] >= min_impressions) & 
        (df['CTR'] <= max_ctr) & 
        (df['Zero_Click_Score'] >= min_zero_click_score)
    ].copy()
    
    return zero_click_keywords.sort_values('Zero_Click_Score', ascending=False)

def create_visualizations(df, zero_click_df):
    """Create visualizations for the data"""
    
    # Check if df is empty, and return None for the figures
    if df.empty:
        st.warning("Data is empty. No visualizations will be created.")
        return None, None, None
    
    # CTR vs Impressions scatter plot
    fig1 = px.scatter(df, x='Impressions', y='CTR', 
                     hover_data=['Query'],
                     title='CTR vs Impressions',
                     labels={'CTR': 'Click-Through Rate (%)', 'Impressions': 'Impressions'})
    
    if fig1:  # Ensure fig1 is a valid Plotly figure
        try:
            fig1.update_xaxis(type="log")
        except Exception as e:
            st.error(f"Error updating x-axis for fig1: {e}")
            return None, None, None
    
    # Zero-click score distribution
    fig2 = px.histogram(df, x='Zero_Click_Score', 
                       title='Distribution of Zero-Click Scores',
                       labels={'Zero_Click_Score': 'Zero-Click Score (%)'})
    
    # Top zero-click keywords
    if not zero_click_df.empty:
        top_zero_click = zero_click_df.head(20)
        fig3 = px.bar(top_zero_click, x='Zero_Click_Score', y='Query',
                     orientation='h',
                     title='Top 20 Zero-Click Keywords',
                     labels={'Zero_Click_Score': 'Zero-Click Score (%)'})
        fig3.update_layout(height=600)
    else:
        fig3 = None
    
    # Debug output
    st.write("Visualization Debug:")
    st.write(f"fig1: {fig1 is not None}")
    st.write(f"fig2: {fig2 is not None}")
    st.write(f"fig3: {fig3 is not None}")
    
    return fig1, fig2, fig3

def main():
    """Main app flow"""
    st.set_page_config(page_title="Zero Click Impact", layout="wide")
    
    # Authenticate and setup GSC API
    service = authenticate_gsc()
    
    if service:
        sites = get_gsc_sites(service)
        
        if not sites:
            st.warning("No GSC properties found.")
            return
        
        # Select site
        site_url = st.selectbox("Select GSC Property", sites)
        
        # Date input
        today = datetime.today()
        start_date = st.date_input("Start Date", today - timedelta(days=30))
        end_date = st.date_input("End Date", today)
        
        # Fetch GSC Data
        df = fetch_gsc_data(service, site_url, start_date, end_date)
        
        if df.empty:
            st.warning("No data to display.")
            return
        
        # Calculate Zero-Click Metrics
        df = calculate_zero_click_metrics(df)
        
        # Filter zero-click keywords
        zero_click_df = identify_zero_click_keywords(df, min_impressions=100, max_ctr=0.5, min_zero_click_score=50)
        
        # Create and display visualizations
        fig1, fig2, fig3 = create_visualizations(df, zero_click_df)
        
        # Display the visualizations
        if fig1:
            st.plotly_chart(fig1, use_container_width=True)
        if fig2:
            st.plotly_chart(fig2, use_container_width=True)
        if fig3:
            st.plotly_chart(fig3, use_container_width=True)

if __name__ == "__main__":
    main()
