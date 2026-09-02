from __future__ import annotations

import argparse
import json
from urllib.request import Request, urlopen


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze one UAV/FBG window")
    parser.add_argument("window_id", help="Window identifier, for example W027")
    parser.add_argument(
        "--url", default="http://127.0.0.1:8000/v1/analyze", help="Agent endpoint"
    )
    args = parser.parse_args()

    payload = json.dumps({"window_id": args.window_id}).encode("utf-8")
    request = Request(
        args.url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=90) as response:
        result = json.loads(response.read().decode("utf-8"))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

