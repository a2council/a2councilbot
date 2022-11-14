import argparse
import csv
import logging
import json
import re
import sys

import council_twitter_bot

def get_class(ei):
    if ei["EventItemAgendaNumber"] is None or ei["EventItemActionName"] is None:
        return None

    event_class_items = []
    if re.match(r"MC-\d+", ei["EventItemAgendaNumber"]):
        # XXX some MC items are purely informational, others get a vote.
        # until that vote has happened, it's difficult to determine which are which
        event_class_items.append("nomination")
    elif re.match(r"CA-\d+", ei["EventItemAgendaNumber"]):
        event_class_items.append("consent")
        if not ei["EventItemConsent"]:
            event_class_items.append("pulled")
    elif ei["EventItemMatterType"] == "Ordinance":
        event_class_items.append("ordinance")
    elif ei["EventItemMatterType"] == "Resolution":
        event_class_items.append("resolution")
    else:
        # skip this one, it's not interesting
        return None

    if not ei["EventItemActionName"].startswith("Approved"):
        event_class_items.append("amendment")

    if ei["EventItemPassedFlag"] == 1:
        event_class_items.append("pass")
    elif ei["EventItemPassedFlag"] == 0:
        event_class_items.append("fail")

    return " ".join(event_class_items)


def get_display_agenda_number(event_item):
    if (
        event_item["EventItemActionName"] == "Approved"
        or event_item["EventItemMover"] is None
    ):
        return event_item["EventItemAgendaNumber"]
    else:
        return "{} - Motion to {} by {}".format(
            event_item["EventItemAgendaNumber"],
            council_twitter_bot.fixup_action_tense(event_item["EventItemActionName"]),
            event_item["EventItemMover"].split()[-1],
        )


COUNCILMEMBERS = (
    "Taylor",
    "Disch",
    "Harrison",
    "Song",
    "Watson",
    "Radina",
    "Ghazi-Edwin",
    "Eyer",
    "Akmon",
    "Briggs",
    "Cornell",
)


def get_votes(ei):
    # Return a list of empty results if no vote has been taken
    if ei["EventItemPassedFlag"] is None:
        return ["" for cm in COUNCILMEMBERS]

    votes = {}
    vote_value_map = {"Yea": "TRUE", "Nay": "FALSE"}
    for vi in ei["EventItemVoteInfo"]:
        lastname = vi["VotePersonName"].split()[-1]

        if vi["VoteValueName"] is not None:
            votes[lastname] = vote_value_map.get(
                vi["VoteValueName"], vi["VoteValueName"]
            )

    # basically, for voice votes, we assume it was unanimous
    default_vote = "TRUE" if ei["EventItemPassedFlag"] else "FALSE"
    return [votes.get(cm, default_vote) for cm in COUNCILMEMBERS]


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--event-id", help="event id to query in Legistar")
    group.add_argument("--event-file", help="json file containing meeting info")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s"
    )

    if args.event_id:
        source = council_twitter_bot.LegistarMinutesSource(args.event_id)
        minutes = source.get_minutes()
    else:
        with open(args.event_file, "r") as fp:
            minutes = json.load(fp)

    # Fill in agenda numbers
    council_twitter_bot.fixup_minutes(minutes["EventItems"])

    # Make the CSV
    rows = []
    for ei in minutes["EventItems"]:
        event_class = get_class(ei)
        if event_class is None:
            # XXX we're using this as our signal that we should skip this eventitem
            # probably should clean this up and make it more explicit
            continue

        cols = [
            ei["EventItemInSiteURL"],
            event_class,
            get_display_agenda_number(ei),
            ei["EventItemTitle"],
        ]
        cols += get_votes(ei)
        rows.append(cols)

    w = csv.writer(sys.stdout)
    w.writerow(
        [
            "link",
            "class",
            "Agenda Number",
            "Agenda Item",
            "Mayor Taylor",
            "Disch (Ward 1)",
            "Harrison (Ward 1)",
            "Song (Ward 2)",
            "Watson (Ward 2)",
            "Radina (Ward 3)",
            "Ghazi-Edwin (Ward 3)",
            "Eyer (Ward 4)",
            "Akmon (Ward 4)",
            "Briggs (Ward 5)",
            "Cornell (Ward 5)",
        ]
    )
    w.writerows(rows)


if __name__ == "__main__":
    main()
