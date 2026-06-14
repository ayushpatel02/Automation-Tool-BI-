"""Standalone password-reset page.

Deliberately does NOT call ensure_authenticated() — a user resetting their password
is locked out by definition. Reached via the link in the reset email
(`/Reset_Password?token=...`) or by pasting the code from that email.
"""

from __future__ import annotations

import streamlit as st

from api_client import ApiClient, ApiError

st.set_page_config(page_title="Reset password", page_icon="🔑")
st.title("🔑 Reset your password")

# Pre-fill the token from the email link (?token=...) if present.
token_from_url = st.query_params.get("token", "")

st.caption(
    "Enter the reset code from your email (it's filled in automatically if you opened "
    "the link) and choose a new password."
)

with st.form("reset_form"):
    token = st.text_input("Reset code", value=token_from_url)
    new_pw = st.text_input("New password (min 8 chars)", type="password")
    confirm = st.text_input("Confirm new password", type="password")
    submitted = st.form_submit_button("Set new password")

if submitted:
    if not token.strip():
        st.error("Missing reset code. Use the link from your email or paste the code.")
    elif len(new_pw) < 8:
        st.error("Password must be at least 8 characters.")
    elif new_pw != confirm:
        st.error("The two passwords don't match.")
    else:
        client = ApiClient()
        try:
            resp = client.reset_password(token.strip(), new_pw)
            st.success(
                (resp or {}).get("message")
                or "Your password has been reset. You can now log in."
            )
            st.page_link("app.py", label="Back to login", icon="🔐")
        except ApiError as exc:
            code = str(exc).split(":", 1)[0].strip()
            if code == "400":
                st.error(
                    "This reset link is invalid or has expired. Request a new one from "
                    "the **Forgot password** tab on the login screen."
                )
            else:
                st.error(f"Could not reset password: {exc}")
