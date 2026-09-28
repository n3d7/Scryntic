"""Bounded public fixture capture for F10; not a production historical adapter."""

import argparse
import json
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

SYMBOLS = (
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "ADAUSDT",
    "AVAXUSDT",
    "LINKUSDT",
    "DOTUSDT",
    "LTCUSDT",
    "BCHUSDT",
    "TRXUSDT",
    "ATOMUSDT",
    "SHIBUSDT",
    "UNIUSDT",
    "AAVEUSDT",
    "XLMUSDT",
    "HBARUSDT",
    "SUIUSDT",
    "NEARUSDT",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Fixture exists; choose a new output path")
    captures = []
    for symbol in SYMBOLS:
        # One completed minute; small fixed acquisition, no credentials or cursors.
        query = urllib.parse.urlencode(
            {
                "category": "spot",
                "symbol": symbol,
                "interval": "1",
                "limit": "1",
                "start": "1704067200000",
                "end": "1704067259999",
            }
        )
        url = "https://api.bybit.com/v5/market/kline?" + query
        with urllib.request.urlopen(url, timeout=10) as response:
            payload = response.read(1_048_577)
            if len(payload) > 1_048_576:
                raise ValueError("Public response exceeds fixture bound")
        body = json.loads(payload)
        result = body["result"]
        if (
            body["retCode"] != 0
            or result["symbol"] != symbol
            or len(result["list"]) != 1
        ):
            raise ValueError(
                f"Unexpected public candle fixture response for {symbol}: code={body['retCode']}, rows={len(result.get('list', []))}"
            )
        captures.append(
            {
                "symbol": symbol,
                "url": url,
                "captured_at_utc": datetime.now(UTC).isoformat(),
                "payload": payload.decode("utf-8"),
                "sha256": sha256(payload).hexdigest(),
            }
        )
        time.sleep(0.2)
    fixture = {
        "provenance": "Public Bybit V5 spot kline responses; one completed 2024-01-01 UTC minute per instrument. Replayed verbatim, not a live market chronology.",
        "records": captures,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(fixture, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(f"Captured {len(captures)} bounded public responses to {args.output}")


if __name__ == "__main__":
    main()
