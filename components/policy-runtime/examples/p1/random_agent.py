#!/usr/bin/env python3
"""Minimal external P1 Agent: knows only the public sts2.p1/agent-port-1 protocol (NDJSON on stdio).
Uniformly random over the offered catalog, seeded per episode. No game, Godot or native knowledge."""
import json, random, sys, hashlib

rng = random.Random(0)
for line in sys.stdin:
    msg = json.loads(line)
    t = msg.get("type")
    if t == "hello":
        seed = int(hashlib.sha256(msg["episode_id"].encode()).hexdigest()[:16], 16)
        rng = random.Random(seed)
        print(json.dumps({"type": "ready", "protocol": msg["protocol"], "agent_id": "external-uniform-random", "agent_version": "1"}), flush=True)
    elif t == "observation" and msg.get("requires_choice"):
        obs = msg["observation"]
        choice = rng.choice(obs["catalog"])
        print(json.dumps({"type": "choose", "occurrence": obs["envelope"]["occurrence"], "choice_id": choice["choice_id"]}), flush=True)
    elif t == "episode_end":
        break
