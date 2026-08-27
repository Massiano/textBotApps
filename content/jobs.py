"""The batch worker.

Runs in a background thread so the dashboard stays responsive. Jobs are rows in
the database rather than in-memory state, so a restart resumes rather than
losing work.

The binding constraint on free models is rate limiting, not money, so the
generator tracks per-model cooldowns and the worker simply waits when every
model is cooling. Progress and ETA are surfaced from the job rows.
"""

import random
import threading
import time
import traceback

import config
from content import probes, riddles, store


class Worker:
    def __init__(self, generator):
        self.gen = generator
        self.thread = None
        self.stop_flag = threading.Event()
        self.current = None
        self.log = []
        self._lock = threading.Lock()

    # -- control ---------------------------------------------------------
    def start(self):
        if self.thread and self.thread.is_alive():
            return False
        self.stop_flag.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return True

    def stop(self):
        self.stop_flag.set()

    def running(self):
        return bool(self.thread and self.thread.is_alive())

    def note(self, msg):
        with self._lock:
            self.log.insert(0, {"at": store.now(), "msg": msg})
            del self.log[80:]

    def state(self):
        return {
            "running": self.running(),
            "current": self.current,
            "log": self.log[:30],
            "cooldowns": getattr(self.gen, "cooldowns", lambda: {})(),
            "calls": getattr(self.gen, "calls", 0),
        }

    # -- loop ------------------------------------------------------------
    def _run(self):
        self.note("worker started")
        while not self.stop_flag.is_set():
            job = store.next_job()
            if not job:
                self.current = None
                time.sleep(1.5)
                continue
            self.current = {"id": job["id"], "kind": job["kind"], "lang": job["lang"],
                            "domain": job["domain"], "level": job["level"],
                            "done": job["done"], "want": job["want"]}
            store.update_job(job["id"], status="running")
            try:
                if job["kind"] == "generate":
                    self._generate(job)
                elif job["kind"] == "probe":
                    self._probe(job)
                else:
                    store.update_job(job["id"], status="finished",
                                     note=f"unknown kind {job['kind']}")
            except Exception as e:
                traceback.print_exc()
                self.note(f"job {job['id']} failed: {e}")
                store.update_job(job["id"], status="finished", note=str(e))
        self.current = None
        self.note("worker stopped")

    def _wait_for_capacity(self):
        """Sleep while every model is in cooldown."""
        for _ in range(60):
            if self.stop_flag.is_set():
                return False
            if getattr(self.gen, "available", lambda: [1])():
                return True
            time.sleep(2)
        return False

    def _generate(self, job):
        lang, domain, level = job["lang"], job["domain"], job["level"]
        lang_name = config.ENGLISH_NAME.get(lang, "English")
        rng = random.Random()
        done, failed = job["done"], job["failed"]

        while done < job["want"] and not self.stop_flag.is_set():
            if not self._wait_for_capacity():
                store.update_job(job["id"], status="queued", done=done, failed=failed,
                                 note="waiting on rate limits")
                return
            try:
                payload = riddles.generate(self.gen, lang, lang_name, domain, level, rng=rng)
                rid = riddles.persist(payload)
                if payload["accepted_vocab"]:
                    done += 1
                    self.note(f"{lang}/{domain}@{level}: {payload['answer']} "
                              f"— {len(payload['new'])} new, {payload['drafts']} drafts")
                    if config.PROBE_AUTOMATICALLY:
                        store.enqueue("probe", lang, domain, level, want=1, note=rid)
                else:
                    failed += 1
                    self.note(f"{lang}/{domain}@{level}: rejected — {payload['reject_reason']}")
            except Exception as e:
                failed += 1
                self.note(f"generate error: {e}")
                store.log_attempt(lang, domain, level, None, 0, "error", str(e))
                time.sleep(2)
            store.update_job(job["id"], done=done, failed=failed)
            if failed > job["want"] * 4 + 8:
                store.update_job(job["id"], status="finished", note="too many failures")
                return

        store.update_job(job["id"], status="finished", done=done, failed=failed)

    def _probe(self, job):
        """`note` carries the riddle id for a targeted probe; otherwise take
        the oldest candidate."""
        lang_name = config.ENGLISH_NAME.get(job["lang"], "English")
        rid = job.get("note")
        riddle = store.get_riddle(rid) if rid else None
        if riddle is None:
            pending = store.queue("candidate", job["lang"], limit=1)
            riddle = pending[0] if pending else None
        if riddle is None or riddle["status"] not in ("candidate", "probing"):
            store.update_job(job["id"], status="finished", note="nothing to probe")
            return

        if not self._wait_for_capacity():
            store.update_job(job["id"], status="queued", note=rid)
            return

        store.set_status(riddle["id"], "probing")
        verdict = probes.run(self.gen, riddle, lang_name)
        store.set_probe_result(riddle["id"], verdict)

        if verdict["pass"]:
            # Probes pass: the riddle is mechanically sound. A human still sees
            # it, but only to judge what models cannot.
            store.set_status(riddle["id"], "candidate")
            self.note(f"probe ok: {riddle['answer']} "
                      f"(open {verdict['solved_open']}/{verdict['solved_open_of']})")
            store.log_attempt(riddle["lang"], riddle["domain"], riddle["level"],
                              riddle["model"], riddle["drafts"], "accepted", "probe pass")
        else:
            store.set_status(riddle["id"], "rejected", "; ".join(verdict["reasons"]))
            self.note(f"probe failed: {riddle['answer']} — {verdict['reasons'][0]}")
            store.log_attempt(riddle["lang"], riddle["domain"], riddle["level"],
                              riddle["model"], riddle["drafts"], "rejected_probe",
                              verdict["reasons"][0])

        if riddle.get("subject_id") and verdict["solved_open_of"]:
            from content import subjects
            subjects.record_retellability(
                riddle["subject_id"],
                verdict["solved_open"] / verdict["solved_open_of"])

        store.update_job(job["id"], status="finished", done=1)
