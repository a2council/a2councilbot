import argparse
import base64
import datetime
from datetime import timezone
import glob
import logging
import pathlib
import re
from typing import Callable, Optional
import requests
from urllib.parse import urlparse
import json
import time
import sys
import subprocess

import pytz
from oauthlib.oauth2 import WebApplicationClient
from bs4 import BeautifulSoup


def truncate(text: str, length: int) -> str:
    if length < 0:
        raise ValueError("bad length")

    if len(text) > length:
        if length < 3:
            return "." * length
        else:
            return text[: length - 3] + "..."
    else:
        return text


class SocialMediaPostComponent:
    def __init__(self, component_type: int, text: str, truncate: bool = False):
        self.component_type = component_type
        self.text = text
        self.truncate = truncate


class SocialMediaPost:
    COMPONENT_TYPE_TEXT = 0
    COMPONENT_TYPE_URL = 1
    COMPONENT_TYPE_HASHTAG = 2

    def __init__(self):
        self.components: list[SocialMediaPostComponent] = []

    def add_text(self, text: str, truncate: bool = False):
        self.components.append(
            SocialMediaPostComponent(self.COMPONENT_TYPE_TEXT, text, truncate)
        )

    def add_url(self, text: str):
        self.components.append(
            SocialMediaPostComponent(self.COMPONENT_TYPE_URL, text, False)
        )

    def add_hashtag(self, text: str):
        self.components.append(
            SocialMediaPostComponent(self.COMPONENT_TYPE_HASHTAG, text, False)
        )

    def get_post_length(self, url_length: int) -> int:
        length = 0
        for c in self.components:
            if c.component_type == self.COMPONENT_TYPE_URL:
                length += url_length
            else:
                length += len(c.text)
        return length

    def get_plaintext_post(
        self,
        url_length: int,
        max_post_length: int,
        url_callback: Callable[[str, str], str] = lambda prefix, url: url,
        hashtag_callback: Callable[[str, str], str] = lambda prefix, hashtag: hashtag,
    ) -> str:
        proposed_length = self.get_post_length(url_length)
        post_text = ""

        for c in self.components:
            if c.component_type == self.COMPONENT_TYPE_TEXT:
                # this code is written to generally assume only one component will be truncated.
                # it should in theory truncate multiple components if asked to do so, but the first
                # will be entirely deleted before it will consider truncating a second...
                if c.truncate and proposed_length > max_post_length:
                    component_length = len(c.text)
                    target_length = component_length - (
                        proposed_length - max_post_length
                    )
                    if target_length < 0:
                        target_length = 0
                    proposed_length -= component_length - target_length
                    post_text += truncate(c.text, target_length)
                else:
                    post_text += c.text
            elif c.component_type == self.COMPONENT_TYPE_URL:
                post_text += url_callback(post_text, c.text)
            elif c.component_type == self.COMPONENT_TYPE_HASHTAG:
                post_text += hashtag_callback(post_text, c.text)

        if proposed_length > max_post_length:
            raise RuntimeError("Post is too long!")
        return post_text


class MockTwitterApiClient:
    URL_LENGTH = 23
    MAX_POST_LENGTH = 279

    def __init__(self, creds_file=None):
        pass

    def refresh_creds(self):
        pass

    def send_tweet(self, message: SocialMediaPost, in_reply_to=None):
        post_text = message.get_plaintext_post(self.URL_LENGTH, self.MAX_POST_LENGTH)

        # sanity check to confirm we're truncating things to the correct length
        match = re.search(r"(^|\s)(https?:\/\/[\S]+)", post_text)
        if match is not None:
            twitter_calculated_len = (
                match.start(2) + self.URL_LENGTH + (len(post_text) - match.end(2))
            )
        else:
            twitter_calculated_len = len(post_text)
        logging.info(
            "would send tweet ({}, {}): {}".format(
                len(post_text), twitter_calculated_len, post_text
            )
        )
        if in_reply_to is not None:
            parts = in_reply_to.split()
            return '{} {}'.format(parts[0], int(parts[1]) + 1)
        return "hi_this_is_a_tweet_id 0"


class BskyApiClient:
    # Unlike Twitter and Mastodon, the URL_LENGTH is really up to us
    # rather than an immutable constant for the platform
    URL_LENGTH = 34
    MAX_POST_LENGTH = 300

    def __init__(self, creds_filename=None):
        self.creds_filename = creds_filename or "bsky_creds.json"
        with open(self.creds_filename, "r") as fp:
            creds_dict = json.load(fp)
        self.pds_url = creds_dict["pds_url"]
        self.handle = creds_dict["handle"]
        self.app_password = creds_dict["app_password"]
        self.session = None
        self.access_jwt_expire = 0

    def refresh_creds(self):
        if self.session is None:
            resp = requests.post(
                self.pds_url + "/xrpc/com.atproto.server.createSession",
                json={"identifier": self.handle, "password": self.app_password},
            )
            resp.raise_for_status()
            self.session = resp.json()
        else:
            resp = requests.post(
                self.pds_url + "/xrpc/com.atproto.server.refreshSession",
                headers={"Authorization": "Bearer " + self.session["refreshJwt"]},
            )
            resp.raise_for_status()
            self.session = resp.json()

        access_jwt_content_encoded = self.session["accessJwt"].split(".")[1]
        access_jwt_content_json = base64.b64decode(access_jwt_content_encoded)
        access_jwt_content = json.loads(access_jwt_content_json)
        self.access_jwt_expire = access_jwt_content["exp"] - 60

    def send_tweet(self, message: SocialMediaPost, in_reply_to=None):
        if time.time() > self.access_jwt_expire:
            self.refresh_creds()

        facets = []

        def handle_url(prefix: str, url: str):
            match = re.match(r"^(https?:\/\/)([\S]+)$", url)
            if match is None:
                logging.warning("Bad URL: {}".format(url))
                shortened_url = truncate(url, self.URL_LENGTH)
            else:
                shortened_url = truncate(match.group(2), self.URL_LENGTH)

            byte_start = len(prefix.encode("utf-8"))
            byte_end = byte_start + len(shortened_url.encode("utf-8"))

            facets.append(
                {
                    "index": {
                        "byteStart": byte_start,
                        "byteEnd": byte_end,
                    },
                    "features": [
                        {
                            "$type": "app.bsky.richtext.facet#link",
                            "uri": match.group(1) + match.group(2),
                        }
                    ],
                }
            )

            return shortened_url

        def handle_hashtag(prefix: str, hashtag: str):
            byte_start = len(prefix.encode("utf-8"))
            byte_end = byte_start + len(hashtag.encode("utf-8"))

            facets.append(
                {
                    "index": {
                        "byteStart": byte_start,
                        "byteEnd": byte_end,
                    },
                    "features": [
                        {
                            "$type": "app.bsky.richtext.facet#tag",
                             # strip off the leading "#"
                            "tag": hashtag[1:] if hashtag.startswith("#") else hashtag,
                        }
                    ],
                }
            )
            return hashtag

        post_text = message.get_plaintext_post(
            self.URL_LENGTH, self.MAX_POST_LENGTH, handle_url, handle_hashtag
        )

        # trailing "Z" is preferred over "+00:00"
        now = datetime.datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        post = {
            "$type": "app.bsky.feed.post",
            "text": post_text,
            "createdAt": now,
            "facets": facets,
        }
        if in_reply_to is not None:
            post["reply"] = in_reply_to

        resp = requests.post(
            self.pds_url + "/xrpc/com.atproto.repo.createRecord",
            headers={"Authorization": "Bearer " + self.session["accessJwt"]},
            json={
                "repo": self.session["did"],
                "collection": "app.bsky.feed.post",
                "record": post,
            },
        ).json()  # XXX ERROR HANDLING?

        new_reply_info = {
            "parent": {"uri": resp["uri"], "cid": resp["cid"]},
        }
        new_reply_info["root"] = (
            new_reply_info["parent"] if in_reply_to is None else in_reply_to["root"]
        )

        return new_reply_info


class MastodonApiClient:
    URL_LENGTH = 23
    MAX_POST_LENGTH = 499

    def __init__(self, creds_filename=None):
        self.creds_filename = creds_filename or "mastodon_creds.json"
        with open(self.creds_filename, "r") as fp:
            creds_dict = json.load(fp)
        self.bearer_token = creds_dict["access_token"]["access_token"]
        self.client_id = creds_dict["client_credentials"]["client_id"]
        self.client_secret = creds_dict["client_credentials"]["client_secret"]
        self.client = WebApplicationClient(self.client_id)
        self.instance = creds_dict["instance"]

    def refresh_creds(self):
        pass

    def send_tweet(self, message, in_reply_to=None):
        post_text = message.get_plaintext_post(self.URL_LENGTH, self.MAX_POST_LENGTH)
        logging.info("Sending Toot: {}".format(post_text))

        params = {
            "status": post_text,
        }
        if in_reply_to is not None:
            params["in_reply_to_id"] = in_reply_to

        r = requests.post(
            "{}/api/v1/statuses".format(self.instance),
            data=json.dumps(params),
            headers={
                "Authorization": f"Bearer {self.bearer_token}",
                "Content-Type": "application/json",
            },
        ).json()

        # TODO: ERROR HANDLING
        return r["id"]


class TwitterApiClient:
    URL_LENGTH = 23
    MAX_POST_LENGTH = 279

    def __init__(self, creds_filename=None):
        self.creds_filename = creds_filename or "twitter_creds.json"
        with open(self.creds_filename, "r") as fp:
            creds_dict = json.load(fp)
        self.refresh_token = creds_dict["refresh_token"]
        self.client_id = creds_dict["client_id"]
        self.client_secret = creds_dict["client_secret"]
        self.client = WebApplicationClient(self.client_id)

        self.bearer_token = None
        self.bearer_token_expire = 0

    def refresh_creds(self):
        body = self.client.prepare_refresh_body(refresh_token=self.refresh_token)
        r = requests.post(
            "https://api.twitter.com/2/oauth2/token",
            auth=(self.client_id, self.client_secret),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=body,
        ).json()
        if "error" in r:
            raise RuntimeError(str(r))

        self.refresh_token = r["refresh_token"]
        with open(self.creds_filename, "w") as fp:
            json.dump(
                {
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": self.refresh_token,
                },
                fp,
            )
        self.bearer_token = r["access_token"]
        self.bearer_token_expire = time.time() + (r["expires_in"] - 60)
        pass

    def send_tweet(self, message, in_reply_to=None):
        post_text = message.get_plaintext_post(self.URL_LENGTH, self.MAX_POST_LENGTH)
        logging.info("Sending Tweet: {}".format(post_text))

        if time.time() > self.bearer_token_expire:
            self.refresh_creds()

        params = {
            "text": post_text,
        }
        if in_reply_to is not None:
            params["reply"] = {"in_reply_to_tweet_id": in_reply_to}

        r = requests.post(
            "https://api.twitter.com/2/tweets",
            data=json.dumps(params),
            headers={
                "Authorization": f"Bearer {self.bearer_token}",
                "Content-Type": "application/json",
            },
        ).json()
        if "error" in r:
            raise RuntimeError(str(r))

        # TODO: ERROR HANDLING
        return r["data"]["id"]


class LegistarMinutesSource:
    def __init__(self, event_id):
        self.event_id = event_id

    def get_current_time(self):
        return datetime.datetime.now(timezone.utc)

    def wait(self, seconds):
        time.sleep(seconds)

    def get_minutes(self):
        matter_file_to_url = {}

        logging.info("Starting new polling run...")
        with requests.Session() as s:
            event = s.get(
                f"https://webapi.legistar.com/v1/a2gov/events/{self.event_id}",
            ).json()

            # We cannot construct URLs for individual "matters" in the legistar web UI
            # based on API information alone. The unique IDs and GUIDs, somehow, have
            # no relationship to the query params that show up in the website.

            # What we *can* do is scrape the webpage for the event (the API does contain the URL
            # for this) and find all the links, then map them to matters / eventitems based on
            # the public-facing "file number"
            event_url = event["EventInSiteURL"]
            if event_url:
                # this should just be "a2gov.legistar.org" but we'll do it the "right" way
                event_hostname = urlparse(event_url).netloc

                event_page_html = s.get(event["EventInSiteURL"]).text

                try:
                    soup = BeautifulSoup(event_page_html, "html.parser")

                    # we're looking for links to individual pieces of legislation (a.k.a. "matters")
                    links = soup.find_all(
                        "a", href=re.compile(r"LegislationDetail\.aspx.*")
                    )

                    for link in links:
                        # The file number will be the inner text of the <a> tag
                        matter_file = link.get_text().strip()
                        if matter_file:
                            file_href = link.attrs.get("href")
                            matter_file_to_url[matter_file] = "https://{}/{}".format(
                                event_hostname, file_href
                            )
                except Exception:
                    # scraping HTML is fragile, so if it fails, let's be tolerant of that
                    # and move on
                    logging.exception("Failed to parse event page HTML")

            eventitems = s.get(
                f"https://webapi.legistar.com/v1/a2gov/events/{self.event_id}/eventitems?MinutesNote=1&AgendaNote=1",
            ).json()
            # "or 0" because sometimes it's None and that throws exceptions
            eventitems = sorted(
                eventitems, key=lambda e: e["EventItemMinutesSequence"] or 0
            )
            for item in eventitems:
                matter_file = item["EventItemMatterFile"]
                if matter_file:
                    item["EventItemInSiteURL"] = matter_file_to_url.get(matter_file)
                else:
                    item["EventItemInSiteURL"] = None
            event["EventItems"] = eventitems

            for item in eventitems:
                # TODO: only fetch if recently updated
                event_item_id = item["EventItemId"]
                item["EventItemVoteInfo"] = s.get(
                    f"https://webapi.legistar.com/v1/a2gov/eventitems/{event_item_id}/votes"
                ).json()

                if item["EventItemRollCallFlag"]:
                    item["EventItemRollCallInfo"] = s.get(
                        f"https://webapi.legistar.com/v1/a2gov/eventitems/{event_item_id}/RollCalls"
                    ).json()
                else:
                    item["EventItemRollCallInfo"] = []

        logging.info("Polling run complete")
        return event


class MockMinutesSource:
    MEETING_OVER = object()

    def __init__(self, file_prefix, file_suffix=".json"):
        self.files = sorted(
            [
                p
                for p in pathlib.Path(".").glob(
                    "{}*{}".format(glob.escape(file_prefix), glob.escape(file_suffix))
                )
            ]
        )
        self._idx = 0

    def get_current_time(self):
        current_file = self.files[self._idx]
        date_string = current_file.name.rsplit(".", 1)[0][-15:]
        dt = datetime.datetime.strptime(date_string, "%Y%m%dT%H%M%S")
        dt = dt.replace(tzinfo=timezone.utc)
        return dt

    def wait(self, seconds):
        now = self.get_current_time()
        while self.get_current_time() < (now + datetime.timedelta(seconds=seconds)):
            if self._idx >= len(self.files):
                return
            self._idx += 1

    def get_minutes(self):
        logging.info(
            "Starting new mock polling run at {}...".format(
                self.get_current_time().astimezone()
            )
        )
        if self._idx >= len(self.files):
            return self.MEETING_OVER
        with open(self.files[self._idx], "r") as fp:
            return json.load(fp)


class MockGitMinutesSource:
    MEETING_OVER = object()

    def __init__(self, filename):
        self.filepath = pathlib.Path(filename).resolve()
        git_log = subprocess.check_output(
            ["git", "log", "--pretty=%H %ad", "--date=iso8601", self.filepath.name],
            cwd=self.filepath.parent,
        )
        self.commits = []
        for line in git_log.splitlines():
            commit_hash, datestring = line.split(None, 1)
            self.commits.insert(0, (commit_hash.decode(), datestring.decode()))
        self._idx = 0

    def get_current_time(self):
        datestring = self.commits[self._idx][1]
        dt = datetime.datetime.strptime(datestring.strip(), "%Y-%m-%d %H:%M:%S %z")
        return dt

    def wait(self, seconds):
        now = self.get_current_time()
        while self.get_current_time() < (now + datetime.timedelta(seconds=seconds)):
            if self._idx >= len(self.commits):
                return
            self._idx += 1

    def get_minutes(self):
        logging.info(
            "Starting new mock polling run at {}...".format(
                self.get_current_time().astimezone()
            )
        )
        if self._idx >= len(self.commits):
            return self.MEETING_OVER
        output = subprocess.check_output(
            [
                "git",
                "show",
                "{}:{}".format(self.commits[self._idx][0], self.filepath.name),
            ],
            cwd=self.filepath.parent,
        )
        return json.loads(output)


ACTION_TENSE_MAP = {
    "Accepted": "Accept",
    "Adjourn": "Adjourn",
    "Adopted": "Adopt",
    "Amended": "Amend",
    "Approved": "Approve",
    "Deleted": "Delete",
    "Postponed": "Postpone",
    "Presented": "Present",
    "Postponed": "Postpone",
    "Reconsidered": "Reconsider",
    "Referred": "Refer",
    "Withdrawn": "Withdraw",
}


def fixup_action_tense(action_name):
    if not action_name:
        return action_name

    parts = action_name.split()
    parts[0] = ACTION_TENSE_MAP.get(parts[0], parts[0])
    return " ".join(parts)


def fixup_minutes(eventitems):
    matter_to_agenda_number = {}

    # first pass to map Matter ID to Agenda Number
    for item in eventitems:
        if item["EventItemMatterId"] is not None:
            if item["EventItemAgendaNumber"] is not None:
                matter_to_agenda_number[item["EventItemMatterId"]] = item[
                    "EventItemAgendaNumber"
                ]

    # 2nd pass to fill in missing Agenda Numbers
    for item in eventitems:
        if item["EventItemMatterId"] is not None:
            if item["EventItemAgendaNumber"] is None:
                item["EventItemAgendaNumber"] = matter_to_agenda_number.get(
                    item["EventItemMatterId"]
                )


def process_event_item(ei: dict, previous_ei: dict) -> SocialMediaPost:
    if (
        ei["EventItemPassedFlag"] is not None
        and (previous_ei is None or previous_ei["EventItemPassedFlag"] is None)
        and (
            not ei["EventItemAgendaNumber"]
            or re.match(r"^(MC|CC|B|C|D).*$", ei["EventItemAgendaNumber"])
        )
        and ei["EventItemTitle"].lower() != "passed on consent agenda"
    ):
        post = SocialMediaPost()

        # Agenda number
        if ei["EventItemAgendaNumber"] is not None:
            post.add_text("{}: ".format(ei["EventItemAgendaNumber"]))

        # title (truncate to fit)
        post.add_text(ei["EventItemTitle"], True)

        # url if present
        legistar_url = ei.get("EventItemInSiteURL")
        if legistar_url:
            post.add_text("\n")
            post.add_url(legistar_url)

        # everything else
        action_name = fixup_action_tense(ei["EventItemActionName"])
        suffix = "\nAction: {} ({})\n".format(
            action_name,
            ei["EventItemMover"].split()[-1] if ei["EventItemMover"] else None,
        )

        suffix += "Result: {}\n\n".format(ei["EventItemPassedFlagName"])
        votes = {}
        for vi in ei["EventItemVoteInfo"]:
            if vi["VoteValueName"] is None:
                continue
            lastname = vi["VotePersonName"].split()[-1]
            votes.setdefault(vi["VoteValueName"], set()).add(lastname)

        if "Nay" in votes or "Yea" in votes:
            for value in sorted(votes):
                suffix += "{}: {}\n".format(value, ", ".join(sorted(votes[value])))
        else:
            suffix += "Voice vote\n"

        post.add_text(suffix)
        post.add_hashtag("#a2council")

        return post
    else:
        return None


def get_meeting_start(event):
    dt = datetime.datetime.strptime(
        event["EventDate"].split("T")[0] + " " + event["EventTime"], "%Y-%m-%d %I:%M %p"
    )
    dt = pytz.timezone("America/Detroit").localize(dt)
    return dt


def has_meeting_ended(eventitems, start, now):
    for ei in eventitems:
        if ei["EventItemActionName"] == "Adjourn" and ei["EventItemPassedFlag"]:
            return True

    # failsafe - assume the meeting has ended if 12h have elapsed!
    if now > (start + datetime.timedelta(hours=12)):
        return True

    return False


def send_posts(
    message: SocialMediaPost,
    posting_clients: dict[str, object],
    previous_post_ids: Optional[dict] = None,
) -> dict:
    if previous_post_ids is None:
        previous_post_ids = {}
    new_previous_post_ids = {}
    for platform_name, client in posting_clients.items():
        previous_post_id = previous_post_ids.get(platform_name)
        new_previous_post_ids[platform_name] = client.send_tweet(
            message, previous_post_id
        )
    return new_previous_post_ids


def main():
    POSTING_CLIENT_CLASSES = {
        "twitter": TwitterApiClient,
        "mastodon": MastodonApiClient,
        "mock": MockTwitterApiClient,
        "bsky": BskyApiClient,
    }

    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--event-id", help="event id to query in Legistar")
    group.add_argument(
        "--event-file-pattern", help="run parser against stored json files"
    )
    group.add_argument(
        "--event-git-repo-file", help="run parser against a json file in a git repo"
    )
    parser.add_argument(
        "--save-snapshots-in-dir",
        help="save legistar data in json files for each polling run",
        metavar="PATH",
    )
    parser.add_argument(
        "--posting-platforms",
        choices=POSTING_CLIENT_CLASSES.keys(),
        nargs="+",
        required=True,
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s"
    )

    state = {"event_id": None, "known_event_items": {}, "previous_post_ids": None}
    try:
        with open("state.json", "r") as fp:
            state = json.load(fp)
    except Exception as e:
        logging.debug("Could not load state file: {}".format(e))

    posting_clients = {}
    for platform in args.posting_platforms:
        instance = POSTING_CLIENT_CLASSES[platform]()
        # get initial creds *now* to ensure they work
        instance.refresh_creds()
        posting_clients[platform] = instance

    if args.event_id is not None:
        minutes_source = LegistarMinutesSource(args.event_id)
    elif args.event_file_pattern is not None:
        minutes_source = MockMinutesSource(args.event_file_pattern)
    else:
        minutes_source = MockGitMinutesSource(args.event_git_repo_file)

    while True:
        event = None
        try:
            event = minutes_source.get_minutes()
            if event is MockMinutesSource.MEETING_OVER:
                break
        except Exception:
            logging.exception("Polling run failed!")
            minutes_source.wait(60)
            continue

        now = minutes_source.get_current_time()
        if args.save_snapshots_in_dir is not None:
            snapshot_path = pathlib.Path(
                args.save_snapshots_in_dir,
                "meeting-{}-{}.json".format(
                    event["EventId"], now.strftime("%Y%m%dT%H%M%S")
                ),
            )
            with open(snapshot_path, "w") as fp:
                json.dump(event, fp)

        meeting_start_time = get_meeting_start(event)
        if now < meeting_start_time:
            logging.info(
                "Meeting hasn't started yet - now {}, start {}".format(
                    now, meeting_start_time
                )
            )
            minutes_source.wait(60)
            continue

        try:
            # check for mismatch in event id in saved state!
            if state["event_id"] is not None and state["event_id"] != event["EventId"]:
                logging.warning(
                    "Event ID mismatches saved state. Clearing saved state!"
                )
                state = {
                    "event_id": None,
                    "known_event_items": {},
                    "previous_post_ids": None,
                }

            # store current event id
            if state["event_id"] is None:
                state["event_id"] = event["EventId"]

            # start the twitter thread
            if not state["previous_post_ids"]:
                message = SocialMediaPost()
                message.add_hashtag("#a2council")
                message.add_text(
                    " voting results thread for {}...\n\n\U0001F9F5".format(
                        event["EventDate"].split("T")[0]
                    ),
                    False,
                )
                state["previous_post_ids"] = send_posts(message, posting_clients)

            eventitems = event["EventItems"]
            fixup_minutes(eventitems)
            for ei in eventitems:
                guid = ei["EventItemGuid"]
                previous_ei = state["known_event_items"].get(guid)
                output = process_event_item(ei, previous_ei)
                if output:
                    state["previous_post_ids"] = send_posts(
                        output, posting_clients, state["previous_post_ids"]
                    )
                state["known_event_items"][guid] = ei
        except Exception:
            logging.exception("Processing minutes failed!")

        # store updated state
        with open("state.json", "w") as fp:
            json.dump(state, fp)
        sys.stdout.flush()

        if has_meeting_ended(eventitems, meeting_start_time, now):
            logging.info("Meeting adjourned or timed out!")
            break
        else:
            minutes_source.wait(60)


if __name__ == "__main__":
    main()
