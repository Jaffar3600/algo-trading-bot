"""
modules/auth.py — Module 1: Authentication & Session Manager
════════════════════════════════════════════════════════════════
Handles Zerodha Kite Connect login and daily session management.

How it works:
  1. At 8:55 AM, bot calls ensure_session()
  2. If a valid token exists for today → reuse it (no login needed)
  3. If no token → opens login URL in browser + starts a local HTTP
     server on port 5000 to capture the redirect request_token
  4. Exchanges request_token for access_token via Kite API
  5. Saves token to data/session_token.json with today's date
  6. Returns an authenticated KiteConnect instance ready to use

Usage:
    from modules.auth import AuthManager
    auth = AuthManager()
    kite = auth.ensure_session()   # returns authenticated KiteConnect
"""

import json
import threading
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from kiteconnect import KiteConnect

import config
from modules.logger import get_logger

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
#  Internal: One-shot HTTP server to capture Zerodha's redirect
# ─────────────────────────────────────────────────────────────────────────────

class _TokenCaptureServer:
    """
    Starts a temporary HTTP server on localhost:5000.
    Zerodha redirects to http://127.0.0.1:5000/callback?request_token=XXX
    We capture that token and immediately shut down the server.
    """

    def __init__(self, port: int = 5000):
        self.port          = port
        self.request_token = None
        self._server       = None

    def _make_handler(self):
        """Creates a request handler that captures the token."""
        capture = self  # reference to outer instance

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urlparse(self.path)
                params = parse_qs(parsed.query)

                if "request_token" in params:
                    capture.request_token = params["request_token"][0]

                    # Send a nice success page to the browser
                    body = b"""
                    <html><head><title>Login Successful</title></head>
                    <body style="font-family:Arial;text-align:center;padding:60px">
                        <h2 style="color:#1B4F72">&#x2705; Login Successful!</h2>
                        <p>AlgoBot has captured the session token.</p>
                        <p>You can close this tab and return to your terminal.</p>
                    </body></html>
                    """
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(body)

                elif "error" in params:
                    error = params.get("error", ["unknown"])[0]
                    body = f"<html><body><h2>❌ Login Failed: {error}</h2></body></html>".encode()
                    self.send_response(400)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(200)
                    self.end_headers()

            def log_message(self, format, *args):
                pass  # Suppress default HTTP server logs

        return Handler

    def wait_for_token(self, timeout: int = 300) -> str | None:
        """
        Starts the HTTP server and blocks until token is received
        or timeout (seconds) is reached. Returns the request_token or None.
        """
        handler_class = self._make_handler()
        self._server  = HTTPServer(("127.0.0.1", self.port), handler_class)
        self._server.timeout = 1  # 1-second poll intervals

        log.info(f"Auth server listening on http://127.0.0.1:{self.port}/callback ...")

        elapsed = 0
        while self.request_token is None and elapsed < timeout:
            self._server.handle_request()
            elapsed += 1

        self._server.server_close()

        if self.request_token:
            log.info("request_token captured successfully.")
        else:
            log.warning(f"Timed out waiting for login after {timeout}s.")

        return self.request_token


# ─────────────────────────────────────────────────────────────────────────────
#  AuthManager — main public class
# ─────────────────────────────────────────────────────────────────────────────

class AuthManager:
    """
    Manages Zerodha session lifecycle.
    Call ensure_session() to get an authenticated KiteConnect object.
    """

    def __init__(self):
        self.session_file = config.SESSION_FILE
        self._kite: KiteConnect | None = None

    # ── Public API ────────────────────────────────────────────────────────────

    def ensure_session(self) -> KiteConnect:
        """
        Main entry point. Returns an authenticated KiteConnect instance.
        Reuses today's saved token if available, otherwise triggers login.
        """
        log.info("Checking for existing session...")

        saved = self._load_saved_session()
        if saved:
            log.info(f"✅ Valid session found for today ({date.today()}). Reusing.")
            self._kite = self._build_kite(saved["access_token"])
            return self._kite

        log.info("No valid session found. Starting login flow...")
        return self._login_flow()

    def get_kite(self) -> KiteConnect:
        """Returns the current KiteConnect instance. Call ensure_session() first."""
        if self._kite is None:
            raise RuntimeError("Session not initialised. Call ensure_session() first.")
        return self._kite

    def is_session_valid(self) -> bool:
        """Returns True if a valid today's session exists."""
        return self._load_saved_session() is not None

    def logout(self):
        """Invalidates the current session (useful for testing)."""
        if self._kite:
            try:
                self._kite.invalidate_access_token()
                log.info("Session invalidated successfully.")
            except Exception as e:
                log.warning(f"Could not invalidate session: {e}")
        self._delete_saved_session()
        self._kite = None

    # ── Login Flow ───────────────────────────────────────────────────────────

    def _login_flow(self) -> KiteConnect:
        """
        Full login flow:
        1. Generate login URL
        2. Open in browser
        3. Capture request_token via local HTTP server
        4. Exchange for access_token
        5. Save and return authenticated KiteConnect
        """
        kite      = KiteConnect(api_key=config.KITE_API_KEY)
        login_url = kite.login_url()

        log.info("=" * 60)
        log.info("  ZERODHA LOGIN REQUIRED")
        log.info("=" * 60)
        log.info(f"  Opening browser to: {login_url}")
        log.info("  Please log in with your Zerodha credentials.")
        log.info("  After login, the browser will redirect automatically.")
        log.info("  Waiting for up to 5 minutes...")
        log.info("=" * 60)

        print(f"\n🔐 LOGIN URL (if browser didn't open):\n   {login_url}\n")

        # Open browser
        try:
            webbrowser.open(login_url)
        except Exception:
            log.warning("Could not auto-open browser. Please open the URL manually.")

        # Start capture server in main thread (blocks until token received)
        capture_server = _TokenCaptureServer(port=config.AUTH_SERVER_PORT)
        request_token  = capture_server.wait_for_token(timeout=300)

        if not request_token:
            raise TimeoutError(
                "Login timed out after 5 minutes. "
                "Please restart the bot and try again."
            )

        # Exchange request_token for access_token
        log.info("Exchanging request_token for access_token...")
        try:
            session_data = kite.generate_session(
                request_token, api_secret=config.KITE_API_SECRET
            )
        except Exception as e:
            raise RuntimeError(f"Failed to generate session: {e}") from e

        access_token = session_data["access_token"]
        user_name    = session_data.get("user_name", "Unknown")
        user_id      = session_data.get("user_id", "")

        log.info(f"✅ Login successful! Welcome, {user_name} ({user_id})")

        # Save session
        self._save_session(access_token, user_id, user_name)

        # Build and return authenticated kite
        self._kite = self._build_kite(access_token)
        return self._kite

    # ── Session Persistence ──────────────────────────────────────────────────

    def _save_session(self, access_token: str, user_id: str, user_name: str):
        """Saves session token to JSON file with today's date."""
        data = {
            "date":         str(date.today()),
            "access_token": access_token,
            "user_id":      user_id,
            "user_name":    user_name,
        }
        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.session_file, "w") as f:
            json.dump(data, f, indent=2)
        log.info(f"Session saved to {self.session_file}")

    def _load_saved_session(self) -> dict | None:
        """
        Loads session from file. Returns session dict if valid for today,
        otherwise returns None.
        """
        if not self.session_file.exists():
            return None

        try:
            with open(self.session_file) as f:
                data = json.load(f)

            # Check if token is from today
            if data.get("date") == str(date.today()):
                return data
            else:
                log.info(
                    f"Session found but is from {data.get('date')} — "
                    "need fresh login for today."
                )
                return None

        except (json.JSONDecodeError, KeyError) as e:
            log.warning(f"Could not read session file: {e}")
            return None

    def _delete_saved_session(self):
        """Removes saved session file."""
        if self.session_file.exists():
            self.session_file.unlink()
            log.info("Session file deleted.")

    def _build_kite(self, access_token: str) -> KiteConnect:
        """Creates an authenticated KiteConnect instance."""
        kite = KiteConnect(api_key=config.KITE_API_KEY)
        kite.set_access_token(access_token)
        return kite
