import argparse
import datetime
from datetime import timezone
import glob
import logging
import pathlib
import re
import requests
import json
import time
import sys

import pytz
from oauthlib.oauth2 import WebApplicationClient


class MockTwitterApiClient:
    def __init__(self, creds_file=None):
        pass

    def refresh_creds(self):
        pass

    def send_tweet(self, message, in_reply_to=None):
        logging.info("would send tweet ({}): {}".format(len(message), message))
        return "hi_this_is_a_tweet_id"


class TwitterApiClient:
    def __init__(self, creds_filename):
        self.creds_filename = creds_filename
        with open(creds_filename, "r") as fp:
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
        logging.info("Sending Tweet: {}".format(message))

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
        return datetime.datetime.now(timezone.utc)

    def wait(self, seconds):
        time.sleep(seconds)

    def get_minutes(self):
        logging.info("Starting new polling run...")
        with requests.Session() as s:
            event = s.get(
                f"https://webapi.legistar.com/v1/a2gov/events/{self.event_id}",
            ).json()

            eventitems = s.get(
                f"https://webapi.legistar.com/v1/a2gov/events/{self.event_id}/eventitems",
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

        # last_updated = max(
        #     [ei["EventItemLastModifiedUtc"] for ei in eventitems] + [last_updated]
        # )
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
            "Starting new mock polling run at {}...".format(self.get_current_time())
        )
        if self._idx >= len(self.files):
            return self.MEETING_OVER
        with open(self.files[self._idx], "r") as fp:
            return json.load(fp)


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


def process_event_item(ei, previous_ei):
    if (
        ei["EventItemPassedFlag"] is not None
        and (previous_ei is None or previous_ei["EventItemPassedFlag"] is None)
        and (
            not ei["EventItemAgendaNumber"]
            or re.match(r"^(MC|CC|B|C|D).*$", ei["EventItemAgendaNumber"])
        )
        and ei["EventItemTitle"].lower() != "passed on consent agenda"
    ):
        if ei["EventItemAgendaNumber"] is not None:
            prefix = "{}: ".format(ei["EventItemAgendaNumber"])
        else:
            prefix = ""

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
            output = prefix + title[: remaining - 3] + "..." + suffix

        return output
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
    if now > (start + datetime.timedelta(12)):
        return True

    return False


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--event-id", help="event id to query in Legistar")
    group.add_argument(
        "--event-file-pattern", help="run parser against stored json files"
    )
    parser.add_argument(
        "--save-snapshots-in-dir",
        help="save legistar data in json files for each polling run",
        metavar="PATH",
    )
    parser.add_argument("--mock", action="store_true", default=False)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s"
    )

    state = {"event_id": None, "known_event_items": {}, "last_tweet_id": None}
    try:
        with open("state.json", "r") as fp:
            state = json.load(fp)
    except Exception as e:
        logging.debug("Could not load state file: {}".format(e))

    twitter_client_class = TwitterApiClient if not args.mock else MockTwitterApiClient
    client = twitter_client_class("twitter_creds.json")

    # get initial creds *now* to ensure they work
    client.refresh_creds()

    if args.event_id is not None:
        minutes_source = LegistarMinutesSource(args.event_id)
    else:
        minutes_source = MockMinutesSource(args.event_file_pattern)

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
                    "last_tweet_id": None,
                }

            # store current event id
            if state["event_id"] is None:
                state["event_id"] = event["EventId"]

            # start the twitter thread
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
                    state["last_tweet_id"] = client.send_tweet(
                        output, state["last_tweet_id"]
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
