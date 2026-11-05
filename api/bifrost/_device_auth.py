"""Browser instructions shared by CLI and SDK device login."""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def device_login_prompt(api_url: str, verification_url: str, user_code: str) -> str:
    """Display the code for comparison and return the browser shortcut URL."""
    url = urlsplit(f"{api_url.rstrip('/')}{verification_url}")
    query = [(key, value) for key, value in parse_qsl(url.query) if key != "user_code"]
    query.append(("user_code", user_code))
    full_url = urlunsplit(url._replace(query=urlencode(query)))
    print("\nConnect Bifrost CLI\n")
    print(f"Open: {full_url}")
    print(f"Code: {user_code}\n")
    print("Check that the browser shows this code, then choose Connect CLI.")
    print("Only approve a login you started.\n")
    return full_url
