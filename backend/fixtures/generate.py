"""Generate fixture data with seeded RNG (seed=42) for deterministic output.

Run: python -m fixtures.generate
Produces: crm.json, threads.json, calendar.json, signals.json, transcripts.json, event_timeline.json
"""

import json
import random
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

SEED = 42
FIXTURES_DIR = Path(__file__).resolve().parent


def _seeded_uuid(rng: random.Random) -> str:
    return str(UUID(int=rng.getrandbits(128), version=4))


def generate_crm(rng: random.Random) -> dict:
    tiers = ["strategic"] * 3 + ["target"] * 10 + ["standard"] * 12
    rng.shuffle(tiers)
    accounts = []
    contacts = []
    tasks = []

    domains = [
        "acmecorp.com", "techwave.io", "devstream.dev", "cloudnine.co", "buildfast.ai",
        "codeflow.com", "stackpush.io", "deploybot.dev", "infrascale.co", "pipelineops.ai",
        "velocityeng.com", "shiphero.io", "testify.dev", "cicdpro.co", "gitforge.ai",
        "kubecraft.com", "dockerly.io", "terracloud.dev", "helmhub.co", "argoflow.ai",
        "datamesh.com", "mlpipe.io", "featurestore.dev", "modelserve.co", "aiops.ai",
    ]

    titles_by_tier = {
        "global_c_suite": ["CTO", "VP Engineering", "Chief Architect"],
        "vp_level": ["VP Engineering", "VP Platform", "VP Developer Experience"],
        "director": ["Director of Engineering", "Director of Platform", "Engineering Director"],
        "manager": ["Engineering Manager", "Senior Engineering Manager", "Tech Lead Manager"],
        "ic": ["Senior Software Engineer", "Staff Engineer", "Principal Engineer"],
    }
    persona_tiers = ["vp_level", "director", "manager", "ic", "vp_level", "director", "manager", "ic", "ic", "ic"]

    for i, tier in enumerate(tiers):
        ref = f"acct-{i+1:03d}"
        accounts.append({
            "ref": ref,
            "name": domains[i].split(".")[0].replace("_", " ").title(),
            "domain": domains[i],
            "tier": tier,
            "owner": "malay",
            "custom": {},
        })

        n_contacts = rng.randint(2, 4)
        for j in range(n_contacts):
            pt = rng.choice(persona_tiers)
            title = rng.choice(titles_by_tier[pt])
            first = f"Contact{i*4+j+1}"
            contacts.append({
                "ref": f"con-{i*4+j+1:03d}",
                "account_ref": ref,
                "name": f"{first} Lastname",
                "title": title,
                "email": f"{first.lower()}@{domains[i]}",
                "phone": f"+1-555-{rng.randint(100,999)}-{rng.randint(1000,9999)}",
                "linkedin_url": f"https://linkedin.com/in/{first.lower()}",
            })

    contacts = contacts[:60]

    for i in range(20):
        tasks.append({
            "ref": f"task-{i+1:03d}",
            "account_ref": accounts[rng.randint(0, len(accounts) - 1)]["ref"],
            "subject": rng.choice(["Follow up on demo", "Send case study", "Schedule call", "Review proposal"]),
            "due_date": (datetime(2026, 6, 15) + timedelta(days=rng.randint(0, 14))).strftime("%Y-%m-%d"),
            "status": "open",
        })

    return {"accounts": accounts, "contacts": contacts, "tasks": tasks}


def generate_threads(rng: random.Random) -> dict:
    thread_types = (
        ["positive"] * 6 + ["objection"] * 8 + ["ooo"] * 4 + ["bounce"] * 2 + ["cold"] * 10
    )
    rng.shuffle(thread_types)

    threads = []
    thread_summaries = []
    inbound_messages = []

    positive_bodies = [
        "This is interesting, can you send more info?",
        "Sure, happy to chat. What does next week look like?",
        "We've been looking at tools like this. Let's set up time.",
        "Sounds relevant — can you show me a quick demo?",
        "I'd be open to 15 minutes. Send me some times.",
        "Yes, this is timely for us. Let's connect.",
    ]
    objection_bodies = [
        "We already use Copilot Enterprise, not interested.",
        "Budget is locked for this quarter.",
        "We tried something similar and it didn't work.",
        "Not a priority right now.",
        "Our team is too small for this.",
        "Send me info and I'll review when I have time.",
        "We're happy with our current setup.",
        "Can you reach out again in Q1?",
    ]

    for i, ttype in enumerate(thread_types):
        ref = f"thread-{i+1:03d}"
        last_at = datetime(2026, 6, 1) + timedelta(hours=rng.randint(0, 240))
        subject = f"Re: Quick question about developer productivity ({i+1})"

        messages = [{
            "id": f"msg-{i+1:03d}-1",
            "thread_ref": ref,
            "sender": "malay@example.com",
            "to": [f"contact{i+1}@example.com"],
            "subject": subject,
            "body": "Hey — saw your team is growing fast. Worth 15 min to see how Devin can help?",
            "sent_at": (last_at - timedelta(hours=rng.randint(24, 72))).isoformat(),
        }]

        if ttype == "positive":
            body = positive_bodies[i % len(positive_bodies)]
            reply_msg = {
                "id": f"msg-{i+1:03d}-2",
                "thread_ref": ref,
                "sender": f"contact{i+1}@example.com",
                "to": ["malay@example.com"],
                "subject": subject,
                "body": body,
                "sent_at": last_at.isoformat(),
            }
            messages.append(reply_msg)
            inbound_messages.append({
                "id": reply_msg["id"],
                "thread_ref": ref,
                "sender": reply_msg["sender"],
                "subject": subject,
                "body": body,
                "received_at": last_at.isoformat(),
            })
        elif ttype == "objection":
            body = objection_bodies[i % len(objection_bodies)]
            reply_msg = {
                "id": f"msg-{i+1:03d}-2",
                "thread_ref": ref,
                "sender": f"contact{i+1}@example.com",
                "to": ["malay@example.com"],
                "subject": subject,
                "body": body,
                "sent_at": last_at.isoformat(),
            }
            messages.append(reply_msg)
            inbound_messages.append({
                "id": reply_msg["id"],
                "thread_ref": ref,
                "sender": reply_msg["sender"],
                "subject": subject,
                "body": body,
                "received_at": last_at.isoformat(),
            })
        elif ttype == "ooo":
            body = "Out of office: I'll be away until July 15. For urgent matters contact my colleague."
            reply_msg = {
                "id": f"msg-{i+1:03d}-2",
                "thread_ref": ref,
                "sender": f"contact{i+1}@example.com",
                "to": ["malay@example.com"],
                "subject": f"Automatic reply: {subject}",
                "body": body,
                "sent_at": last_at.isoformat(),
            }
            messages.append(reply_msg)
            inbound_messages.append({
                "id": reply_msg["id"],
                "thread_ref": ref,
                "sender": reply_msg["sender"],
                "subject": reply_msg["subject"],
                "body": body,
                "received_at": last_at.isoformat(),
            })
        elif ttype == "bounce":
            body = "Undeliverable: The email address could not be found."
            reply_msg = {
                "id": f"msg-{i+1:03d}-2",
                "thread_ref": ref,
                "sender": "mailer-daemon@example.com",
                "to": ["malay@example.com"],
                "subject": f"Undeliverable: {subject}",
                "body": body,
                "sent_at": last_at.isoformat(),
            }
            messages.append(reply_msg)

        threads.append({"ref": ref, "subject": subject, "messages": messages})
        thread_summaries.append({
            "ref": ref,
            "subject": subject,
            "last_message_at": last_at.isoformat(),
            "snippet": messages[-1]["body"][:80],
        })

    return {
        "threads": threads,
        "thread_summaries": thread_summaries,
        "inbound_messages": inbound_messages,
    }


def generate_calendar(rng: random.Random) -> dict:
    base = datetime(2026, 6, 9, 9, 0)
    events = []
    for i in range(5):
        start = base + timedelta(days=rng.randint(0, 13), hours=rng.randint(0, 7))
        is_no_show = i == 4
        events.append({
            "ref": f"cal-{i+1:03d}",
            "title": f"Devin Demo — Account {i+1}",
            "start": start.isoformat(),
            "end": (start + timedelta(minutes=30)).isoformat(),
            "attendees": [
                {
                    "email": f"contact{i+1}@example.com",
                    "response_status": "accepted" if not is_no_show else "needsAction",
                },
                {"email": "malay@example.com", "response_status": "accepted"},
            ],
            "body": "Demo: how Devin handles a real ticket from your backlog.",
            "meeting_link": f"https://meet.example.com/demo-{i+1}",
        })

    slots = []
    for d in range(7):
        slot_start = base + timedelta(days=d, hours=rng.choice([1, 3, 5]))
        slots.append({
            "start": slot_start.isoformat(),
            "end": (slot_start + timedelta(minutes=30)).isoformat(),
            "days_out": d,
            "pull_in_candidate": d > 4,
        })

    return {
        "events": events,
        "slots": slots,
        "capacity": {"business_days": 22, "pto_dates": [], "blocked_hours": 0},
    }


def generate_signals(rng: random.Random) -> dict:
    kinds = ["hiring_surge", "eng_leadership_change", "dev_velocity_pain", "funding", "tech_adoption"]
    signals = []
    companies = []

    for i in range(15):
        domain = f"company{i+1}.com"
        kind = rng.choice(kinds)
        signals.append({
            "kind": kind,
            "account_domain": domain,
            "strength": round(rng.uniform(0.3, 1.0), 2),
            "evidence": f"Signal evidence for {kind} at {domain}",
            "detected_at": (datetime(2026, 6, 1) + timedelta(days=rng.randint(0, 30))).isoformat(),
        })
        companies.append({
            "domain": domain,
            "name": f"Company {i+1}",
            "size": rng.choice(["50-200", "200-1000", "1000-5000", "5000+"]),
            "funding": rng.choice(["Series A", "Series B", "Series C", "Public", None]),
            "stack": rng.sample(["Python", "Go", "TypeScript", "Java", "Rust", "Kubernetes", "AWS"], k=3),
            "eng_headcount_trend": rng.choice(["growing", "stable", "shrinking"]),
        })

    return {"signals": signals, "companies": companies, "contacts": []}


def generate_transcripts(rng: random.Random) -> dict:
    calls = []
    transcripts = []
    scenarios = [
        ("Good discovery call", [
            ("rep", "Thanks for joining. I saw your team just raised Series B — congrats."),
            ("prospect", "Thanks! Yeah, we're hiring like crazy right now."),
            ("rep", "That's usually when onboarding becomes a bottleneck. How are you handling new eng ramp-up?"),
            ("prospect", "It's painful honestly. Seniors spend weeks onboarding each new hire."),
            ("rep", "That's exactly what Devin helps with. Can I show you a quick example?"),
            ("prospect", "Sure, that'd be useful."),
        ]),
        ("Objection-heavy call", [
            ("rep", "Appreciate you taking the time. How's your team thinking about developer productivity?"),
            ("prospect", "We already have Copilot Enterprise. Not sure we need another tool."),
            ("rep", "Totally fair. Copilot is great for autocomplete. Devin is different — it takes a whole ticket."),
            ("prospect", "I don't know, our devs are pretty set in their ways."),
            ("rep", "What if I showed you a side-by-side on one of your real tickets?"),
            ("prospect", "Maybe. Send me something and I'll look at it."),
        ]),
        ("No-show rescheduled call", [
            ("rep", "Hi — we had you on the calendar for 2pm, wanted to check in."),
            ("prospect", "Oh man, I'm so sorry. Got pulled into a fire drill."),
            ("rep", "No worries at all. How's Thursday at the same time?"),
            ("prospect", "Thursday works. Sorry again."),
        ]),
    ]

    for i, (title, turns) in enumerate(scenarios):
        ref = f"call-{i+1:03d}"
        occurred = datetime(2026, 6, 5) + timedelta(days=i * 3)
        calls.append({
            "ref": ref,
            "account_ref": f"acct-{i+1:03d}",
            "contact_ref": f"con-{i+1:03d}",
            "title": title,
            "occurred_at": occurred.isoformat(),
            "duration_seconds": rng.randint(180, 900),
        })

        segments = []
        t = 0.0
        for speaker, text in turns:
            duration = len(text) * 0.06
            segments.append({
                "speaker": speaker,
                "text": text,
                "start_seconds": round(t, 1),
                "end_seconds": round(t + duration, 1),
            })
            t += duration + rng.uniform(0.5, 2.0)

        transcripts.append({"call_ref": ref, "segments": segments})

    return {"calls": calls, "transcripts": transcripts}


def generate_event_timeline(rng: random.Random, crm_data: dict) -> list[dict]:
    """Generate 90 days of synthetic EventLog history from known ground-truth rates.

    Ground-truth rates (from DATA_MODEL.md seed benchmarks):
        reply_rate email=0.04, call(connect)=0.08, linkedin=0.08
        positive_reply_rate=0.35 of replies
        book_rate=0.55 of positive
        show_rate=0.70
        qualify_rate=0.60
    """
    GROUND_TRUTH = {
        "reply_rate_email": 0.04,
        "reply_rate_call": 0.08,
        "reply_rate_linkedin": 0.08,
        "positive_reply_rate": 0.35,
        "book_rate": 0.55,
        "show_rate": 0.70,
        "qualify_rate": 0.60,
    }

    accounts = [a["ref"] for a in crm_data.get("accounts", [])]
    contacts = crm_data.get("contacts", [])
    channels = ["email", "call", "linkedin"]
    persona_tiers = ["vp_level", "director", "manager", "ic"]

    start_date = datetime(2026, 3, 1)
    events = []

    for day_offset in range(90):
        current_day = start_date + timedelta(days=day_offset)
        if current_day.weekday() >= 5:
            continue

        daily_touches = rng.randint(30, 50)

        for _ in range(daily_touches):
            channel = rng.choice(channels)
            acct = rng.choice(accounts)
            matching_contacts = [c for c in contacts if c["account_ref"] == acct]
            contact = rng.choice(matching_contacts) if matching_contacts else None
            contact_ref = contact["ref"] if contact else None
            persona = rng.choice(persona_tiers)
            hour = rng.randint(8, 17)
            minute = rng.randint(0, 59)
            occurred = current_day.replace(hour=hour, minute=minute)

            events.append({
                "event_type": "touch_sent",
                "persona_tier": persona,
                "channel": channel,
                "account_ref": acct,
                "contact_ref": contact_ref,
                "occurred_at": occurred.isoformat(),
                "source": "mock",
                "payload": {"generated": True},
            })

            reply_rate = GROUND_TRUTH[f"reply_rate_{channel}"]
            if rng.random() < reply_rate:
                reply_time = occurred + timedelta(hours=rng.randint(1, 48))
                events.append({
                    "event_type": "reply_received",
                    "persona_tier": persona,
                    "channel": channel,
                    "account_ref": acct,
                    "contact_ref": contact_ref,
                    "occurred_at": reply_time.isoformat(),
                    "source": "mock",
                    "payload": {"generated": True},
                })

                if rng.random() < GROUND_TRUTH["positive_reply_rate"]:
                    events.append({
                        "event_type": "positive_reply",
                        "persona_tier": persona,
                        "channel": channel,
                        "account_ref": acct,
                        "contact_ref": contact_ref,
                        "occurred_at": (reply_time + timedelta(minutes=5)).isoformat(),
                        "source": "mock",
                        "payload": {"generated": True},
                    })

                    if rng.random() < GROUND_TRUTH["book_rate"]:
                        book_time = reply_time + timedelta(hours=rng.randint(2, 24))
                        events.append({
                            "event_type": "meeting_booked",
                            "persona_tier": persona,
                            "account_ref": acct,
                            "contact_ref": contact_ref,
                            "occurred_at": book_time.isoformat(),
                            "source": "mock",
                            "payload": {"generated": True},
                        })

                        if rng.random() < GROUND_TRUTH["show_rate"]:
                            held_time = book_time + timedelta(days=rng.randint(1, 5))
                            events.append({
                                "event_type": "meeting_held",
                                "persona_tier": persona,
                                "account_ref": acct,
                                "contact_ref": contact_ref,
                                "occurred_at": held_time.isoformat(),
                                "source": "mock",
                                "payload": {"generated": True},
                            })

                            if rng.random() < 0.9:
                                events.append({
                                    "event_type": "ad_accepted",
                                    "persona_tier": persona,
                                    "account_ref": acct,
                                    "contact_ref": contact_ref,
                                    "occurred_at": (held_time + timedelta(days=rng.randint(1, 3))).isoformat(),
                                    "source": "mock",
                                    "payload": {"generated": True},
                                })
                        else:
                            events.append({
                                "event_type": "meeting_no_show",
                                "persona_tier": persona,
                                "account_ref": acct,
                                "contact_ref": contact_ref,
                                "occurred_at": (book_time + timedelta(days=rng.randint(1, 5))).isoformat(),
                                "source": "mock",
                                "payload": {"generated": True},
                            })

    events.sort(key=lambda e: e["occurred_at"])
    return events


def main():
    rng = random.Random(SEED)

    crm_data = generate_crm(rng)
    threads_data = generate_threads(rng)
    calendar_data = generate_calendar(rng)
    signals_data = generate_signals(rng)
    transcripts_data = generate_transcripts(rng)
    timeline = generate_event_timeline(rng, crm_data)

    for name, data in [
        ("crm.json", crm_data),
        ("threads.json", threads_data),
        ("calendar.json", calendar_data),
        ("signals.json", signals_data),
        ("transcripts.json", transcripts_data),
        ("event_timeline.json", timeline),
    ]:
        path = FIXTURES_DIR / name
        path.write_text(json.dumps(data, indent=2, default=str) + "\n")
        print(f"  wrote {path} ({len(json.dumps(data)):,} bytes)")


if __name__ == "__main__":
    main()
