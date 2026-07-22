#!/usr/bin/env python3
"""
One-time helper: run this on YOUR machine (not in Cowork) to get a Strava
refresh_token for the daily sync script.

Prerequisites:
    1. Create a Strava API app at https://www.strava.com/settings/api
       - Authorization Callback Domain: localhost
       - Note the Client ID and Client Secret shown there.
    2. pip install requests

Usage:
    python get_strava_tokens.py <client_id> <client_secret>

What it does:
    1. Prints an authorization URL for you to open in a browser and approve.
    2. Prompts you to paste back the "code" query param from the redirect URL.
    3. Exchanges that code for tokens and prints the refresh_token you need.

The refresh_token does not expire (unless revoked) and is what the daily
sync script uses to mint new access tokens automatically.
"""

import sys
import webbrowser
import requests

def main():
    if len(sys.argv) != 3:
        print("Usage: python get_strava_tokens.py <client_id> <client_secret>")
        sys.exit(1)

    client_id, client_secret = sys.argv[1], sys.argv[2]

    scope = "read,activity:read_all,profile:read_all"
    auth_url = (
        "https://www.strava.com/oauth/authorize"
        f"?client_id={client_id}"
        "&redirect_uri=http://localhost/exchange_token"
        "&response_type=code"
        f"&scope={scope}"
        "&approval_prompt=force"
    )

    print("\n1. Opening (or copy/paste) this URL in your browser:\n")
    print(auth_url)
    print("\n2. Click 'Authorize'. Your browser will redirect to a localhost URL")
    print("   that looks like a 'can't connect' error page -- that's expected.")
    print("   Copy the 'code' query parameter value from that URL's address bar.")
    print("   Example: http://localhost/exchange_token?state=&code=THIS_PART&scope=...\n")

    try:
        webbrowser.open(auth_url)
    except Exception:
        pass

    code = input("Paste the code here: ").strip()

    resp = requests.post(
        "https://www.strava.com/oauth/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
        },
    )
    resp.raise_for_status()
    data = resp.json()

    print("\nSuccess! Save these as GitHub Actions repo secrets:\n")
    print(f"STRAVA_CLIENT_ID={client_id}")
    print(f"STRAVA_CLIENT_SECRET={client_secret}")
    print(f"STRAVA_REFRESH_TOKEN={data['refresh_token']}")
    print(f"\n(athlete: {data.get('athlete', {}).get('firstname', '')} {data.get('athlete', {}).get('lastname', '')})")

if __name__ == "__main__":
    main()
