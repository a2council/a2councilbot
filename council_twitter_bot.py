import argparse
import datetime
import logging
import re
import requests
import json
import time
import sys

from oauthlib.oauth2 import WebApplicationClient    

class MockTwitterApiClient:
    def __init__(self, client_id, client_secret, refresh_token):
        self.refresh_token = refresh_token
        self.client_id = client_id
        self.client_secret = client_secret

    def refresh_creds(self):
        pass

    def send_tweet(self, message, in_reply_to=None):
        logging.info("SEND TWEET ({})".format(len(message)))
        return None

class TwitterApiClient:
    def __init__(self, client_id, client_secret, refresh_token):
        self.client = WebApplicationClient(client_id)
        self.refresh_token = refresh_token
        self.client_id = client_id
        self.client_secret = client_secret

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
        self.bearer_token = r["access_token"]
        self.bearer_token_expire = time.time() + (r["expires_in"] - 60)
        pass

    def send_tweet(self, message, in_reply_to=None):
        if time.time() > self.bearer_token_expire:
            self.refresh_creds()

        params = {
            "text": message,
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
        return datetime.datetime.now()

    def wait(self, seconds):
        time.sleep(seconds)

    def get_minutes():
        pass

class MockMinutesSource:
    def __init__(self, file_prefix):
        pass

    def get_current_time(self):
        pass

    def wait(self, seconds):
        pass

    def get_minutes():
        pass

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
    # XXX should maybe do this with Matter ID matching instead?
    last_agenda_number = None
    interesting_agenda_items = False
    for item in eventitems:
        if item["EventItemAgendaNumber"] == "CA":
            interesting_agenda_items = True
        if item["EventItemAgendaNumber"] == "E":
            interesting_agenda_items = False

        if item["EventItemAgendaNumber"] is None and interesting_agenda_items and last_agenda_number is not None:
            if re.match(r"[a-zA-Z]{1,2}-[0-9]+", last_agenda_number):
                item["EventItemAgendaNumber"] = last_agenda_number
        last_agenda_number = item["EventItemAgendaNumber"]


def process_event_item(ei, previous_ei):
    if (
        ei["EventItemPassedFlag"] is not None
        and (previous_ei is None or previous_ei["EventItemPassedFlag"] is None)
        and ei["EventItemAgendaNumber"]
        and re.match(r"^(MC|CC|B|C|D).*$", ei["EventItemAgendaNumber"])
    ):
        prefix = "{}: ".format(ei["EventItemAgendaNumber"])

        action_name = fixup_action_tense(ei["EventItemActionName"])
        suffix = "\nAction: {} ({})\n".format(
            action_name,
            ei["EventItemMover"].split()[-1],
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

        # the limit should be 280 but I'm gonna just be slightly conservative here...
        remaining = 279 - len(prefix + suffix)
        title = ei["EventItemTitle"]
        if len(title) < remaining:
            output = prefix + title + suffix
        else:
            output = prefix + title[:remaining-3] + "..." + suffix

        return output


def collect_minutes(event_id, last_updated="1970-01-01"):
    logging.info("Starting new polling run...")
    with requests.Session() as s:
        event = s.get(
            f"https://webapi.legistar.com/v1/a2gov/events/{event_id}",
        ).json()

        eventitems = s.get(
            f"https://webapi.legistar.com/v1/a2gov/events/{event_id}/eventitems",
            # params={
            #     "$filter": "EventItemLastModifiedUtc gt datetime'{}'".format(
            #         last_updated
            #     ),
            # },
        ).json()
        # "or 0" because sometimes it's None and that throws exceptions
        eventitems = sorted(
            eventitems, key=lambda e: e["EventItemMinutesSequence"] or 0
        )
        event["EventItems"] = eventitems

        for item in eventitems:
            # TODO: only fetch if recently updated
            event_item_id = item["EventItemId"]
            item["EventItemVoteInfo"] = s.get(
                f"https://webapi.legistar.com/v1/a2gov/eventitems/{event_item_id}/votes"
            ).json()

    last_updated = max(
        [ei["EventItemLastModifiedUtc"] for ei in eventitems] + [last_updated]
    )
    logging.info("Polling run complete")
    return event, last_updated


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("event_id")
    parser.add_argument("--mock", action="store_true", default=False)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s"
    )

    state = {"known_event_items": {}, "last_tweet_id": None, "refresh_token": None}
    try:
        with open("state.json", "r") as fp:
            state = json.load(fp)
    except Exception as e:
        logging.debug("Could not load state file: {}".format(e))

    twitter_client_class = TwitterApiClient if not args.mock else MockTwitterApiClient
    refresh_token = state["refresh_token"]
    if refresh_token is None:
        refresh_token = "xxxx"
    client = twitter_client_class(
        "xxxx",
        "xxxx",
        refresh_token,
    )
    # get initial creds *now* to ensure they work
    client.refresh_creds()

    while True:
        # HACK wait for the meeting to start
        now = datetime.datetime.now()
        # XXX get this out of the actual event info!
        start = now.replace(hour=19, minute=0, second=0, microsecond=0)
        if now < start:
            logging.info("Meeting hasn't started yet - now {}, start {}".format(now, start))
            time.sleep(60)
            continue

        try:
            event, _ = collect_minutes(args.event_id)
            if not state["last_tweet_id"]:
                state["last_tweet_id"] = client.send_tweet(
                    "#a2council voting results thread for {}...\n\n\U0001F9F5".format(
                        event["EventDate"].split("T")[0]
                    )
                )

            eventitems = event["EventItems"]
            fixup_minutes(eventitems)
            for ei in eventitems:
                guid = ei["EventItemGuid"]
                previous_ei = state["known_event_items"].get(guid)
                output = process_event_item(ei, previous_ei)
                if output:
                    logging.info("Sending Tweet: {}".format(output))
                    state["last_tweet_id"] = client.send_tweet(
                        output, state["last_tweet_id"]
                    )
                state["known_event_items"][guid] = ei
        except Exception:
            logging.exception("Polling run failed!")

        state["refresh_token"] = client.refresh_token
        with open("state.json", "w") as fp:
            json.dump(state, fp)
        sys.stdout.flush()
        time.sleep(60)


if __name__ == "__main__":
    main()
