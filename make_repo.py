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

        meeting_start = council_twitter_bot.get_meeting_start(event)
        body_name_filtered = "".join(
            c for c in event["EventBodyName"] if c in string.ascii_letters
        )

        # {bodyname}-{datetime *in America/Detroit timezone*}-{id}
        git_repo_filename = "{}-{}-{}.json".format(
            body_name_filtered, meeting_start.strftime("%Y%m%dT%H%M"), event["EventId"]
        )
        with open(git_repo_dir / git_repo_filename, "w") as outfp:
            json.dump(event, outfp, indent=4, sort_keys=True)

        subprocess.run(["git", "add", git_repo_filename], cwd=git_repo_dir)

        git_env = os.environ.copy()
        git_env.update(
            {"GIT_AUTHOR_DATE": dt.isoformat(), "GIT_COMMITTER_DATE": dt.isoformat()}
        )
        subprocess.run(
            [
                "git",
                "commit",
                "-m",
                "Meeting update at {}".format(dt.isoformat()),
            ],
            cwd=git_repo_dir,
            env=git_env,
        )


if __name__ == "__main__":
    main()
