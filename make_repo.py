import argparse
import datetime
import json
import pathlib
import string
import subprocess
import os

from datetime import timezone

import council_twitter_bot


def get_date_string_from_file(file):
    date_string = file.name.rsplit(".", 1)[0][-15:]
    return date_string


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("meeting_data")
    parser.add_argument("git_repo")
    args = parser.parse_args()

    meeting_data_dir = pathlib.Path(args.meeting_data)
    git_repo_dir = pathlib.Path(args.git_repo)

    if not meeting_data_dir.is_dir:
        raise RuntimeError(f"{meeting_data_dir} is not an existing directory")
    if not git_repo_dir.is_dir:
        raise RuntimeError(f"{git_repo_dir} is not an existing directory")

    for current_file in sorted(
        (f for f in meeting_data_dir.iterdir() if f.suffix == ".json"),
        key=get_date_string_from_file,
    ):
        date_string = get_date_string_from_file(current_file)
        dt = datetime.datetime.strptime(date_string, "%Y%m%dT%H%M%S")
        dt = dt.replace(tzinfo=timezone.utc)

        with open(current_file, "r") as infp:
            event = json.load(infp)

        council_twitter_bot.save_snapshot_in_git_repo(
            git_repo_dir, event, dt
        )


if __name__ == "__main__":
    main()
