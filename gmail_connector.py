#!/usr/bin/env python3
"""
gmail_connector.py

Read-only Gmail connector that works with MULTIPLE Gmail accounts at once.

Each account is authorised once via OAuth (browser or console flow) and its
refresh token is stored in its own file under ./.gmail_tokens/<alias>.json.
After that, searches and message fetches fan out across every registered
account and results are tagged with the account alias they came from.

Setup (one time):
  1. pip install google-api-python-client google-auth-oauthlib google-auth-httplib2
  2. In Google Cloud Console create an OAuth client ID of type "Desktop app",
     enable the Gmail API, and download the JSON as ./gmail_credentials.json
     (or point GMAIL_CREDENTIALS_FILE at it).
  3. Add each account:
         python3 gmail_connector.py add personal
         python3 gmail_connector.py add work --console   # headless machines

Usage (CLI):
    python3 gmail_connector.py list
    python3 gmail_connector.py search 'from:thelonginvestor newer_than:30d'
    python3 gmail_connector.py search 'subject:"$AAPL"' --account work --max 20
    python3 gmail_connector.py get <alias> <message_id>
    python3 gmail_connector.py remove work

Usage (library):
    from gmail_connector import GmailConnector
    gc = GmailConnector()
    for m in gc.search("newer_than:7d"):          # all accounts
        print(m["account"], m["subject"])
    body = gc.get_message("work", m["id"])["body"]

Scope is gmail.readonly: this module cannot send, modify or delete mail.
Tokens and credentials are secrets - they are listed in .gitignore.
"""
import argparse
import base64
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
BASE_DIR = Path(__file__).parent
CREDENTIALS_FILE = Path(os.environ.get("GMAIL_CREDENTIALS_FILE", BASE_DIR / "gmail_credentials.json"))
TOKEN_DIR = Path(os.environ.get("GMAIL_TOKEN_DIR", BASE_DIR / ".gmail_tokens"))


def _google():
    """Import Google libs lazily so --help works without them installed."""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        sys.exit(
            "Missing dependencies. Run:\n"
            "  pip install google-api-python-client google-auth-oauthlib google-auth-httplib2"
        )
    return Request, Credentials, InstalledAppFlow, build


def _token_path(alias):
    if not alias or not alias.replace("-", "").replace("_", "").isalnum():
        raise ValueError(f"Invalid account alias {alias!r}: use letters, digits, - or _")
    return TOKEN_DIR / f"{alias}.json"


class GmailConnector:
    def __init__(self, token_dir=None):
        self.token_dir = Path(token_dir) if token_dir else TOKEN_DIR
        self._services = {}

    # ---- account management -------------------------------------------------
    def accounts(self):
        """Registered account aliases."""
        if not self.token_dir.exists():
            return []
        return sorted(p.stem for p in self.token_dir.glob("*.json"))

    def add_account(self, alias, console=False):
        """Run the OAuth flow for a new account and store its token."""
        _, _, InstalledAppFlow, _ = _google()
        if not CREDENTIALS_FILE.exists():
            sys.exit(f"OAuth client file not found: {CREDENTIALS_FILE}")
        path = _token_path(alias)
        flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
        creds = flow.run_console() if console and hasattr(flow, "run_console") else (
            flow.run_local_server(port=0, open_browser=not console)
        )
        self.token_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.token_dir, 0o700)
        path.write_text(creds.to_json())
        os.chmod(path, 0o600)
        self._services.pop(alias, None)
        email = self.profile(alias)["emailAddress"]
        return email

    def remove_account(self, alias):
        path = _token_path(alias)
        if path.exists():
            path.unlink()
        self._services.pop(alias, None)

    # ---- service handling ---------------------------------------------------
    def _service(self, alias):
        if alias in self._services:
            return self._services[alias]
        Request, Credentials, _, build = _google()
        path = _token_path(alias)
        if not path.exists():
            raise KeyError(f"Unknown account {alias!r}. Registered: {self.accounts() or 'none'}")
        creds = Credentials.from_authorized_user_file(str(path), SCOPES)
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                path.write_text(creds.to_json())
                os.chmod(path, 0o600)
            else:
                raise RuntimeError(f"Token for {alias!r} is invalid; re-run: add {alias}")
        svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
        self._services[alias] = svc
        return svc

    def profile(self, alias):
        return self._service(alias).users().getProfile(userId="me").execute()

    # ---- reading mail -------------------------------------------------------
    def _search_one(self, alias, query, max_results):
        svc = self._service(alias)
        out, page = [], None
        while len(out) < max_results:
            resp = svc.users().messages().list(
                userId="me", q=query, maxResults=min(100, max_results - len(out)), pageToken=page
            ).execute()
            for ref in resp.get("messages", []):
                out.append(self.get_message(alias, ref["id"], full=False))
            page = resp.get("nextPageToken")
            if not page:
                break
        return out

    def search(self, query="", accounts=None, max_results=50):
        """
        Search one or more accounts (default: all). Returns message summaries
        sorted newest first, each tagged with its 'account' alias.
        An account that fails (expired token, API error) is reported in the
        'errors' attribute of the returned list rather than aborting the rest.
        """
        aliases = accounts or self.accounts()
        results, errors = [], {}
        # Build services serially (token refresh writes files), query in parallel.
        for a in aliases:
            try:
                self._service(a)
            except Exception as e:  # noqa: BLE001
                errors[a] = str(e)
        live = [a for a in aliases if a not in errors]

        def run(a):
            try:
                return a, self._search_one(a, query, max_results), None
            except Exception as e:  # noqa: BLE001
                return a, [], str(e)

        with ThreadPoolExecutor(max_workers=max(1, min(len(live), 8))) as pool:
            for a, msgs, err in pool.map(run, live):
                results.extend(msgs)
                if err:
                    errors[a] = err
        results.sort(key=lambda m: m["timestamp"], reverse=True)
        results = _ResultList(results[:max_results])
        results.errors = errors
        return results

    def get_message(self, alias, message_id, full=True):
        """Fetch a message. full=True adds the plain-text 'body'."""
        svc = self._service(alias)
        msg = svc.users().messages().get(
            userId="me", id=message_id, format="full" if full else "metadata",
            metadataHeaders=["From", "To", "Subject", "Date"],
        ).execute()
        headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
        try:
            ts = parsedate_to_datetime(headers["date"]).timestamp()
        except (KeyError, TypeError, ValueError):
            ts = int(msg.get("internalDate", 0)) / 1000
        item = {
            "account": alias,
            "id": msg["id"],
            "threadId": msg["threadId"],
            "from": headers.get("from", ""),
            "to": headers.get("to", ""),
            "subject": headers.get("subject", ""),
            "date": headers.get("date", ""),
            "timestamp": ts,
            "snippet": msg.get("snippet", ""),
            "labels": msg.get("labelIds", []),
        }
        if full:
            item["body"] = _extract_text(msg.get("payload", {}))
        return item


class _ResultList(list):
    errors = {}


def _decode(data):
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")


def _extract_text(payload):
    """Prefer text/plain; fall back to text/html with tags stripped."""
    plain, html = [], []

    def walk(part):
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if data and mime == "text/plain":
            plain.append(_decode(data))
        elif data and mime == "text/html":
            html.append(_decode(data))
        for sub in part.get("parts", []) or []:
            walk(sub)

    walk(payload)
    if plain:
        return "\n".join(plain)
    if html:
        import html as htmllib
        import re
        text = re.sub(r"(?is)<(script|style).*?</\1>", "", "\n".join(html))
        text = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", text)
        return htmllib.unescape(re.sub(r"<[^>]+>", "", text)).strip()
    return ""


def main(argv=None):
    ap = argparse.ArgumentParser(description="Multi-account read-only Gmail connector")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add", help="authorise a new Gmail account under an alias")
    p.add_argument("alias")
    p.add_argument("--console", action="store_true", help="don't open a browser; print the URL")
    p = sub.add_parser("remove", help="forget an account (deletes its stored token)")
    p.add_argument("alias")
    sub.add_parser("list", help="list registered accounts and their addresses")
    p = sub.add_parser("search", help="search with Gmail query syntax")
    p.add_argument("query", nargs="?", default="")
    p.add_argument("--account", action="append", help="limit to alias (repeatable)")
    p.add_argument("--max", type=int, default=25)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("get", help="print one message")
    p.add_argument("alias")
    p.add_argument("message_id")

    args = ap.parse_args(argv)
    gc = GmailConnector()

    if args.cmd == "add":
        print(f"Authorised {args.alias}: {gc.add_account(args.alias, console=args.console)}")
    elif args.cmd == "remove":
        gc.remove_account(args.alias)
        print(f"Removed {args.alias}")
    elif args.cmd == "list":
        for a in gc.accounts():
            try:
                print(f"{a}\t{gc.profile(a)['emailAddress']}")
            except Exception as e:  # noqa: BLE001
                print(f"{a}\t(error: {e})")
        if not gc.accounts():
            print("No accounts registered. Run: gmail_connector.py add <alias>")
    elif args.cmd == "search":
        res = gc.search(args.query, accounts=args.account, max_results=args.max)
        if args.json:
            print(json.dumps({"messages": res, "errors": res.errors}, indent=2))
        else:
            for m in res:
                print(f"[{m['account']}] {m['date']}  {m['from']}\n    {m['subject']}  ({m['id']})")
        for a, e in res.errors.items():
            print(f"warning: {a}: {e}", file=sys.stderr)
        return 1 if res.errors and not res else 0
    elif args.cmd == "get":
        m = gc.get_message(args.alias, args.message_id)
        print(f"From: {m['from']}\nTo: {m['to']}\nDate: {m['date']}\nSubject: {m['subject']}\n\n{m['body']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
