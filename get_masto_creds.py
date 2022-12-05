import json
from oauthlib.oauth2 import WebApplicationClient
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests

authorize_uri = "https://a2mi.social/oauth/authorize"


def main():
    with open("mastodon_creds.json") as fp:
        masto_creds = json.load(fp)
    client_id = masto_creds["client_credentials"]["client_id"]
    client_secret = masto_creds["client_credentials"]["client_secret"]
    instance = masto_creds["instance"]

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
        "{}/oauth/authorize".format(instance),
        redirect_uri,
        "read write:statuses",
        state="state",
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
    )
    print(body)

    r = requests.post(
        "{}/oauth/token".format(instance),
        auth=(client_id, client_secret),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=body,
    )
    print(r.text)


if __name__ == "__main__":
    main()

