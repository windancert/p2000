#!/usr/bin/env python3

import hashlib, json, os, subprocess, sys, threading, time
from datetime import datetime, timezone
import paho.mqtt.client as mqtt

FREQ = os.environ.get("SDR_FREQ", "169.65M")
GAIN = os.environ.get("SDR_GAIN", "30")
PPM  = os.environ.get("SDR_PPM", "0")

MQTT_HOST = os.environ.get("MQTT_HOST", "mosquitto")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USER = os.environ.get("MQTT_USER", "p2000")
MQTT_PASS = os.environ.get("MQTT_PASS", "")
TOPIC     = os.environ.get("MQTT_TOPIC", "p2000")

DEDUP_WINDOW = float(os.environ.get("DEDUP_WINDOW", "6"))
CAPCODES = {c.strip() for c in os.environ.get("CAPCODES", "").split(",") if c.strip()}
KEYWORDS = [k.strip().lower() for k in os.environ.get("KEYWORDS", "").split(",") if k.strip()]

pending, lock = {}, threading.Lock()


def parse(line):
    # FLEX|2026-08-29 22:15:03|1600/2/K/A|11.028|001234567|ALN|A1 Ambu ...
    if not line.startswith("FLEX"):
        return None
    p = line.split("|")
    if len(p) < 7 or p[5].strip() not in ("ALN", "GPN"):
        return None
    text = "|".join(p[6:]).strip()
    if not text:
        return None
    return set(p[4].split()), text


def wanted(item):
    if CAPCODES and not (CAPCODES & item["capcodes"]):
        return False
    if KEYWORDS and not any(k in item["text"].lower() for k in KEYWORDS):
        return False
    return True


def flusher(client):
    while True:
        time.sleep(1)
        now, ready = time.time(), []
        with lock:
            for k in [k for k, v in pending.items() if now - v["first"] >= DEDUP_WINDOW]:
                ready.append(pending.pop(k))
        for item in ready:
            if not wanted(item):
                continue
            payload = json.dumps({
                "message": item["text"],
                "capcodes": sorted(item["capcodes"]),
                "timestamp": int(item["first"]),
                "received": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            }, ensure_ascii=False)
            client.publish(f"{TOPIC}/melding", payload, qos=1, retain=True)
            client.publish(f"{TOPIC}/event", payload, qos=1, retain=False)
            print(f"[pub] {sorted(item['capcodes'])} {item['text'][:80]}", flush=True)


def on_connect(client, userdata, flags, rc, properties=None):
    client.publish(f"{TOPIC}/status", "online", qos=1, retain=True)
    print(f"[mqtt] verbonden (rc={rc})", flush=True)


def main():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="p2000-decoder")
    if MQTT_USER:
        client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.will_set(f"{TOPIC}/status", "offline", qos=1, retain=True)
    client.on_connect = on_connect
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_start()
    
    rtl = subprocess.Popen(
        ["rtl_fm", "-f", FREQ, "-M", "fm", "-s", "22050", "-g", GAIN, "-p", PPM, "-"],
        stdout=subprocess.PIPE)
    mm = subprocess.Popen(
        ["multimon-ng", "-a", "FLEX", "-t", "raw", "-"],
        stdin=rtl.stdout, stdout=subprocess.PIPE, text=True, errors="replace")
    rtl.stdout.close()

    threading.Thread(target=flusher, args=(client,), daemon=True).start()

    for line in mm.stdout:
        parsed = parse(line.strip())
        if not parsed:
            continue
        caps, text = parsed
        open("/tmp/last_message", "w").write(str(int(time.time())))
        key = hashlib.sha1(text.encode()).hexdigest()
        with lock:
            if key in pending:
                pending[key]["capcodes"] |= caps
            else:
                pending[key] = {"text": text, "capcodes": caps, "first": time.time()}

    print("pipeline gestopt", file=sys.stderr, flush=True)
    sys.exit(1)


if __name__ == "__main__":
    main()