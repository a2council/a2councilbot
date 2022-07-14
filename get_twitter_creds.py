from oauthlib.oauth2 import WebApplicationClient
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qsl
from random import SystemRandom
import base64
import hashlib
import string
import requests
import webbrowser

authorize_uri = "https://twitter.com/i/oauth2/authorize"
client_id = ""
client_secret = ""


def main():
    code_verifier = "".join(
        SystemRandom().choice(string.ascii_letters + string.digits) for x in range(32)
    )
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode("ascii")).digest()
    )

    client = WebApplicationClient(client_id)
    server = None
    response_uri = None

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            print("HI")
            nonlocal response_uri
            response_uri = self.path
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Success!")
            return

    server = HTTPServer(("localhost", 8080), Handler)
    redirect_uri = "http://{}:{}".format(*server.server_address)

    uri = client.prepare_request_uri(
        authorize_uri,
        redirect_uri,
        "tweet.read tweet.write users.read offline.access",
        state="state",
        # XXX S256 broken somehow
        code_challenge=code_verifier,
        code_challenge_method="plain",
    )
    print(uri)
    server.handle_request()

    code = client.parse_request_uri_response(
        "https://localhost" + response_uri, state="state"
    )["code"]
    print(code)

    body = client.prepare_request_body(
        code,
        redirect_uri,
        code_verifier=code_verifier
    )
    print(body)

    r = requests.post(
        "https://api.twitter.com/2/oauth2/token",
        auth=(client_id, client_secret),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=body,
    )
    print(r.text)

    for i in range(5):
        refresh_token = r.json()["refresh_token"]
        body = client.prepare_refresh_body(refresh_token=refresh_token)
        r = requests.post(
            "https://api.twitter.com/2/oauth2/token",
            auth=(client_id, client_secret),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=body,
        )
        print(r.text)



if __name__ == "__main__":
    main()

