#!/usr/bin/env python3
"""
One-time helper: run this on YOUR machine (not in Cowork) to get a WHOOP
refresh_token for the daily sync script.

Prerequisites:
    1. Create an app at https://developer-dashboard.whoop.com
       - Set the Redirect URI to exactly match what you pass as the third
         argument below (default: http://localhost/callback) -- WHOOP requires
         an exact match, unlike Strava's more flexible domain-only setting.
       - Note the Client ID and Client Secret shown there.
    2. pip install requests

Usage:
    python get_whoop_tokens.py <client_id> <client_secret> [redirect_uri]

What it does:
    1. Prints an authorization URL for you to open in a browser and approve.
    2. Prompts you to paste back the "code" query param from the redirect URL.
    3. Exchanges that code for tokens and prints the refresh_token you need.

Note: WHOOP rotates refresh tokens on every use (unlike Strava's, which don't
expire). The daily sync script handles this automatically by storing the
current token in a hidden tab in your Google Sheet after each run -- this
script's output is only used to seed the very first run.
"""

import sys
import webbrowser
from urllib.parse import urlencode

import requests

WHOOP_AUTH_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
WHOOP_TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"


def main():
    if len(sys.argv) < 3:
        print("Usage: python get_whoop_tokens.py <client_id> <client_secret> [redirect_uri]")
        sys.exit(1)

    client_id, client_secret = sys.argv[1], sys.argv[2]
    redirect_uri = sys.argv[3] if len(sys.argv) > 3 else "http://localhost/callback"

    scope = "offline read:cycles read:recovery read:sleep"
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scope,
        "state": "cowork01",  # WHOOP requires state to be exactly 8 characters
    }
    auth_url = f"{WHOOP_AUTH_URL}?{urlencode(params)}"

    print("\n1. Opening (or copy/paste) this URL in your browser:\n")
    print(auth_url)
    print(f"\n2. Log in and click Authorize. Your browser will redirect to something like:")
    print(f"   {redirect_uri}?state=cowork01&code=THIS_PART")
    print("   That page will likely show a 'can't connect' error -- that's expected.")
    print("   Copy the 'code' query parameter value from the address bar.\n")

    try:
        webbrowser.open(auth_url)
    except Exception:
        pass

    code = input("Paste the code here: ").strip()

    resp = requests.post(
        WHOOP_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
        },
    )
    resp.raise_for_status()
    data = resp.json()

    print("\nSuccess! Save these as GitHub Actions repo secrets:\n")
    print(f"WHOOP_CLIENT_ID={client_id}")
    print(f"WHOOP_CLIENT_SECRET={client_secret}")
    print(f"WHOOP_REFRESH_TOKEN={data['refresh_token']}")
    print("\nThat refresh token is only a seed for the first run -- after that, the")
    print("sync script manages rotation itself via the Google Sheet. You won't need")
    print("to run this script again unless you revoke access or start over.")


if __name__ == "__main__":
    main()
