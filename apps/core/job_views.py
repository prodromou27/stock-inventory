from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views import View
from django.views.generic import ListView

from .authorization import is_administrator
from .models import BackgroundJob


def _visible_jobs(user):
    queryset = BackgroundJob.objects.select_related("requested_by")
    return queryset if is_administrator(user) else queryset.filter(requested_by=user)


class BackgroundJobListView(LoginRequiredMixin, ListView):
    template_name = "core/background_job_list.html"
    context_object_name = "jobs"
    paginate_by = 30

    def get_queryset(self):
        return _visible_jobs(self.request.user)


class BackgroundJobDetailView(LoginRequiredMixin, View):
    def get(self, request, pk):
        job = get_object_or_404(_visible_jobs(request.user), pk=pk)
        return render(request, "core/background_job_detail.html", {"job": job})


class BackgroundJobDownloadView(LoginRequiredMixin, View):
    def get(self, request, pk):
        job = get_object_or_404(_visible_jobs(request.user), pk=pk)
        if (
            job.status != BackgroundJob.Status.SUCCEEDED
            or not job.output_file
            or job.output_deleted_at
            or (job.output_expires_at and job.output_expires_at <= timezone.now())
        ):
            raise Http404("This job has no downloadable output.")
        try:
            stream = job.output_file.open("rb")
        except FileNotFoundError as exc:
            raise Http404("The temporary output has expired or is missing.") from exc
        return FileResponse(
            stream,
            as_attachment=True,
            filename=job.output_filename or "job-output",
            content_type=job.output_content_type or "application/octet-stream",
        )
