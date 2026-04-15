"""Bind all EUD queues to the firehose exchange.

OTS 1.7.10 on Python 3.13 doesn't create the firehose bindings for EUD
queues, so meshtastic CoT never reaches connected ATAK clients. This
script re-creates the bindings and can be run on a timer or after OTS
restarts.
"""

import pika


def bind_firehose():
    conn = pika.BlockingConnection(
        pika.ConnectionParameters(
            "127.0.0.1", credentials=pika.PlainCredentials("guest", "guest")
        )
    )
    ch = conn.channel()
    conn.close()

    # Discover queues via rabbitmqctl
    import subprocess

    result = subprocess.run(
        ["sudo", "rabbitmqctl", "list_queues", "name", "--quiet"],
        capture_output=True,
        text=True,
    )

    conn = pika.BlockingConnection(
        pika.ConnectionParameters(
            "127.0.0.1", credentials=pika.PlainCredentials("guest", "guest")
        )
    )
    ch = conn.channel()

    skip = {"cot_parser", "meshtastic", "", "name", "Listing queues"}
    for line in result.stdout.strip().splitlines():
        queue = line.strip()
        if not queue or queue in skip or queue.startswith("mqtt-") or queue.startswith("python-"):
            continue
        try:
            ch.queue_bind(queue=queue, exchange="firehose")
            print(f"Bound {queue} to firehose")
        except Exception as e:
            print(f"Failed to bind {queue}: {e}")
            # Channel dies on error — reopen it
            try:
                conn.close()
            except Exception:
                pass
            conn = pika.BlockingConnection(
                pika.ConnectionParameters(
                    "127.0.0.1", credentials=pika.PlainCredentials("guest", "guest")
                )
            )
            ch = conn.channel()

    conn.close()


if __name__ == "__main__":
    bind_firehose()