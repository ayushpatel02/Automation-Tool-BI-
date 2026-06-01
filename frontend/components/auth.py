"""Login / registration gate for the Streamlit app."""

from __future__ import annotations

import streamlit as st

from api_client import ApiClient, ApiError


def ensure_authenticated() -> ApiClient:
    """Render a login/register form until the user is authenticated; return an ApiClient."""
    if st.session_state.get("token"):
        return ApiClient(token=st.session_state["token"])

    st.title("AI Power BI Report Generator")
    st.caption("Sign in to generate Power BI reports from natural language.")

    tab_login, tab_register = st.tabs(["Log in", "Register"])

    with tab_login:
        with st.form("login_form"):
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            if st.form_submit_button("Log in"):
                client = ApiClient()
                try:
                    token = client.login(email, password)
                    st.session_state["token"] = token
                    st.session_state["email"] = email
                    st.rerun()
                except ApiError as exc:
                    st.error(str(exc))

    with tab_register:
        with st.form("register_form"):
            email_r = st.text_input("Email", key="reg_email")
            password_r = st.text_input(
                "Password (min 8 chars)", type="password", key="reg_pw"
            )
            if st.form_submit_button("Create account"):
                client = ApiClient()
                try:
                    client.register(email_r, password_r)
                    st.success("Account created — log in on the other tab.")
                except ApiError as exc:
                    st.error(str(exc))

    st.stop()
