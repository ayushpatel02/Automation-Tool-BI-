"""Login / registration gate for the Streamlit app."""

from __future__ import annotations

import streamlit as st

from api_client import ApiClient, ApiError


def _status_code(exc: ApiError) -> str:
    return str(exc).split(":", 1)[0].strip()


def _login_error_message(exc: ApiError) -> str:
    code = _status_code(exc)
    if code == "401":
        return (
            "Incorrect email or password. If you don't have an account yet, "
            "create one in the **Register** tab."
        )
    if code == "422":
        return "Please enter a valid email address and password."
    return f"Login failed: {exc}"


def _register_error_message(exc: ApiError) -> str:
    code = _status_code(exc)
    if code == "409":
        return "An account with this email already exists. Try logging in instead."
    if code == "422":
        return "Please enter a valid email address and a password with at least 8 characters."
    return f"Registration failed: {exc}"


def ensure_authenticated() -> ApiClient:
    """Render a login/register form until the user is authenticated; return an ApiClient."""
    if st.session_state.get("token"):
        return ApiClient(token=st.session_state["token"])

    st.title("AI Power BI Report Generator")
    st.caption("Sign in to generate Power BI reports from natural language.")

    tab_login, tab_register, tab_forgot = st.tabs(
        ["Log in", "Register", "Forgot password"]
    )

    with tab_login:
        with st.form("login_form"):
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            if st.form_submit_button("Log in"):
                if not email.strip() or not password:
                    st.error("Please enter both email and password.")
                else:
                    client = ApiClient()
                    try:
                        token = client.login(email.strip(), password)
                        st.session_state["token"] = token
                        st.session_state["email"] = email.strip()
                        st.rerun()
                    except ApiError as exc:
                        st.error(_login_error_message(exc))

    with tab_register:
        with st.form("register_form"):
            email_r = st.text_input("Email", key="reg_email")
            password_r = st.text_input(
                "Password (min 8 chars)", type="password", key="reg_pw"
            )
            if st.form_submit_button("Create account"):
                if not email_r.strip() or not password_r:
                    st.error("Please enter both email and password.")
                elif len(password_r) < 8:
                    st.error("Password must be at least 8 characters.")
                else:
                    client = ApiClient()
                    try:
                        client.register(email_r.strip(), password_r)
                        st.success("Account created — log in on the other tab.")
                    except ApiError as exc:
                        st.error(_register_error_message(exc))

    with tab_forgot:
        st.caption(
            "Enter your account email and we'll send a link to reset your password."
        )
        with st.form("forgot_form"):
            email_f = st.text_input("Email", key="forgot_email")
            if st.form_submit_button("Send reset link"):
                if not email_f.strip():
                    st.error("Please enter your email address.")
                else:
                    client = ApiClient()
                    try:
                        resp = client.forgot_password(email_f.strip())
                        st.success(
                            (resp or {}).get("message")
                            or "If an account exists, a reset link has been sent."
                        )
                        st.caption(
                            "Check your inbox for the link. Have a reset code already? "
                            "Open the **Reset Password** page from the sidebar."
                        )
                    except ApiError as exc:
                        st.error(f"Could not start password reset: {exc}")

    st.stop()
