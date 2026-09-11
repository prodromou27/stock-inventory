import time

from django.core.management.base import BaseCommand

from apps.core.jobs import cleanup_expired_outputs, fail_stale_jobs, process_next_job


class Command(BaseCommand):
    help = "Process durable background jobs (use --watch for a worker process)."

    def add_arguments(self, parser):
        parser.add_argument("--watch", action="store_true")
        parser.add_argument("--poll-seconds", type=float, default=2.0)

    def handle(self, *args, **options):
        stale = fail_stale_jobs()
        if stale:
            self.stdout.write(self.style.WARNING(f"Marked {stale} abandoned job(s) failed."))
        last_cleanup = 0.0
        while True:
            if time.monotonic() - last_cleanup >= 3600:
                cleanup_expired_outputs()
                last_cleanup = time.monotonic()
            job = process_next_job()
            if job:
                self.stdout.write(f"{job.pk}: {job.status}")
                continue
            if not options["watch"]:
                return
            time.sleep(max(options["poll_seconds"], 0.2))
