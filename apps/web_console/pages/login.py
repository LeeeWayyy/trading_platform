"""OAuth2 login page for Streamlit web console.

Displays Auth0 login button and handles OAuth2 redirect flow.
"""

import os

import streamlit as st


def main() -> None:
    """Render OAuth2 login page."""
    st.set_page_config(page_title="Login - Trading Platform", page_icon="🔐")

    st.title("Trading Platform - Login")
    st.markdown("### Secure Access via Auth0")

    # Get login URL from environment
    login_url = os.getenv("OAUTH2_LOGIN_URL", "/login")

    st.info(
        "🔒 This application uses **OAuth2/OIDC** authentication via Auth0.\n\n"
        "Click the button below to log in securely."
    )

    # Login button (links to FastAPI /login endpoint)
    st.markdown(
        f'<a href="{login_url}"><button style="background-color:#4CAF50;color:white;'
        "padding:15px 32px;text-align:center;font-size:16px;border:none;"
        'border-radius:4px;cursor:pointer;">Login with Auth0</button></a>',
        unsafe_allow_html=True,
    )

    # Development info
    if os.getenv("ENVIRONMENT", "production") == "development":
        st.divider()
        st.markdown("**Development Info:**")
        st.code(f"Login URL: {login_url}")
        st.caption("Auth0 will redirect back to /callback after authentication")


if __name__ == "__main__":
    main()
