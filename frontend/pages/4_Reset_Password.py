"""Standalone password-reset page (two-step flow).

  1. Enter your email — we send a reset code to it.
  2. Once sent (or if you arrived via the email link), enter the code + new password.

Deliberately does NOT call ensure_authenticated() — a user resetting their password is
locked out by definition.
"""

from __future__ import annotations

import streamlit as st

from api_client import ApiClient, ApiError

st.set_page_config(page_title="Reset password", page_icon="🔑")
st.title("🔑 Reset your password")

# Arriving via the email link (?token=...) means the email was already sent, so skip
# straight to step 2 with the code pre-filled.
token_from_url = st.query_params.get("token", "")
if token_from_url:
    st.session_state["reset_email_sent"] = True


def _start_over() -> None:
    for key in ("reset_email_sent", "reset_email", "reset_complete"):
        st.session_state.pop(key, None)
    st.query_params.clear()  # drop any (now-consumed) token from the URL


# --- Done: password was changed ---------------------------------------------
if st.session_state.get("reset_complete"):
    st.success("Your password has been reset. You can now log in.")
    st.page_link("app.py", label="Back to login", icon="🔐")
    if st.button("Reset another password"):
        _start_over()
        st.rerun()
    st.stop()


# --- Step 1: request a reset email ------------------------------------------
if not st.session_state.get("reset_email_sent"):
    st.caption("Enter your account email and we'll send you a password-reset code.")
    with st.form("request_form"):
        email = st.text_input("Email")
        send = st.form_submit_button("Send reset email")
    if send:
        if not email.strip():
            st.error("Please enter your email address.")
        else:
            try:
                ApiClient().forgot_password(email.strip())
                st.session_state["reset_email_sent"] = True
                st.session_state["reset_email"] = email.strip()
                st.rerun()
            except ApiError as exc:
                st.error(f"Could not send reset email: {exc}")
    st.stop()


# --- Step 2: enter the code + new password (revealed after sending) ---------
if not token_from_url:
    sent_to = st.session_state.get("reset_email", "your email")
    st.success(
        f"If an account exists for **{sent_to}**, a reset code has been sent. "
        "Check your inbox (and spam folder), then paste the code below."
    )
st.caption("Enter the reset code and choose a new password.")

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
        try:
            ApiClient().reset_password(token.strip(), new_pw)
            _start_over()
            st.session_state["reset_complete"] = True
            st.rerun()
        except ApiError as exc:
            code = str(exc).split(":", 1)[0].strip()
            if code == "400":
                st.error("This reset code is invalid or has expired. Request a new one below.")
            else:
                st.error(f"Could not reset password: {exc}")

if not token_from_url and st.button("← Use a different email"):
    _start_over()
    st.rerun()
