import argparse
import datetime
import logging
import requests
import json
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("date")
    args = parser.parse_args()

    events = requests.get(
        "https://webapi.legistar.com/v1/a2gov/events",
        params={"$filter": "EventDate eq datetime'{}'".format(args.date)},
    ).json()

    for event in events:
        print(
            event["EventId"],
            event["EventDate"],
            event["EventTime"],
            event["EventBodyName"],
        )


if __name__ == "__main__":
    main()
