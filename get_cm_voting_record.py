import argparse
import datetime
import logging
import pathlib
import re
import requests
import json
import time
import csv
import sys

import council_twitter_bot

# Stuff to do still...
# - cleanup logic on friendly amendments?
# - switch to MatterType to filter which agenda items show up
#   - (should we only consider items with matters? probably, this is a legislative voting record)

MATTER_TYPES = set(
    [
        "Appointment",
        # "Introduction",
        # "Minutes",
        "Ordinance",
        # "Proclamation",
        # "Public Hearing Only",
        # "Report or Communication ",
        "Resolution",
        "Resolution/Public Hearing",
        # "Work Session",
    ]
)

CSV_FIELDNAMES = [
    "Meeting Time",
    "Agenda ID",
    "Title",
    "Motion To...",
    "Motion Result",
    "CM's Vote",
    "Description",
    "URL",
]


def get_voting_results(minutes, council_member, csvwriter):
    council_twitter_bot.fixup_minutes(minutes["EventItems"])

    absent_members = set()
    for ei in minutes["EventItems"]:
        # occasionally consent agenda items don't get a result set. fix this.
        if ei["EventItemConsent"] and ei["EventItemPassedFlag"] is None:
            ei["EventItemPassedFlag"] = 1

        # if we have a roll call, identify absent members
        if ei["EventItemRollCallFlag"]:
            absent_members.clear()
            members = set()
            for rc in ei["EventItemRollCallInfo"]:
                if rc["RollCallValueName"] == "Absent":
                    absent_members.add(rc["RollCallPersonName"])
                members.add(rc["RollCallPersonName"])
            if council_member not in members:
                raise Exception("CM doesn't exist for {}".format(minutes["EventDate"]))

        elif ei["EventItemMatterType"] in MATTER_TYPES and (
            ei["EventItemActionName"] == "Amended"
            or ei["EventItemPassedFlag"] is not None
        ):
            action_name = council_twitter_bot.fixup_action_tense(
                ei["EventItemActionName"]
            )
            votes = {
                vi["VotePersonName"]: vi["VoteValueName"]
                for vi in ei["EventItemVoteInfo"]
            }
            if "Nay" in votes.values() or "Yea" in votes.values():
                cm_vote = votes[council_member]
            else:
                if council_member in absent_members:
                    cm_vote = "Absent"
                elif (
                    ei["EventItemPassedFlag"] is None
                    and ei["EventItemActionName"] == "Amended"
                ):
                    cm_vote = "Friendly Amendment"
                elif ei["EventItemConsent"]:
                    cm_vote = "Consent agenda"
                else:
                    cm_vote = "Voice vote"

            motion_result = ""
            if (
                ei["EventItemPassedFlag"] is None
                and ei["EventItemActionText"] == "Amended"
            ) or ei["EventItemPassedFlag"]:
                motion_result = "Success"
            else:
                motion_result = "Fail"

            csvwriter.writerow(
                [
                    council_twitter_bot.get_meeting_start(minutes).strftime(
                        "%Y-%m-%d %-I:%M %p"
                    ),
                    ei["EventItemAgendaNumber"],
                    ei["EventItemTitle"],
                    action_name,
                    motion_result,
                    cm_vote,
                    (
                        ei["EventItemMinutesNote"]
                        if ei["EventItemActionText"] == "Amended"
                        else ei["EventItemActionText"]
                    ),
                    ei["EventItemInSiteURL"],
                ]
            )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("start_date")
    parser.add_argument("end_date")
    parser.add_argument("council_member")
    parser.add_argument("--csvfile", type=argparse.FileType("w"), default=sys.stdout)
    parser.add_argument("--cache-dir")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s"
    )

    if args.cache_dir:
        if not pathlib.Path(args.cache_dir).is_dir():
            raise Exception("Cache directory {} doesn't exist".format(args.cache_dir))

    events: list[dict] = requests.get(
        "https://webapi.legistar.com/v1/a2gov/events",
        params={
            "$filter": "EventDate ge datetime'{}' and EventDate le datetime'{}' and EventBodyName eq 'City Council'".format(
                args.start_date, args.end_date
            )
        },
    ).json()
    events.sort(key=lambda e: e["EventDate"])

    csvwriter = csv.writer(args.csvfile)
    csvwriter.writerow(CSV_FIELDNAMES)
    for event in events:
        logging.info("Getting votes from {}".format(event["EventDate"]))
        m = council_twitter_bot.LegistarMinutesSource(event["EventId"])
        minutes = None
        if args.cache_dir:
            event_cache_file = (
                pathlib.Path(args.cache_dir) / "{}.json".format(event["EventId"])
            ).absolute()
            if event_cache_file.is_file():
                logging.info("Using cache: {}".format(event_cache_file))
                with open(event_cache_file, "r") as fp:
                    minutes = json.load(fp)
            else:
                minutes = m.get_minutes()
                with open(event_cache_file, "w") as fp:
                    json.dump(minutes, fp)
        else:
            minutes = m.get_minutes()

        get_voting_results(minutes, args.council_member, csvwriter)


if __name__ == "__main__":
    main()
