import hashlib

from django.contrib.auth.views import LoginView, PasswordResetView
from django.shortcuts import redirect, render

from .. import throttle
from ..forms import LoginForm


class ThrottledLoginView(LoginView):
    """Django's LoginView (safe ``next`` handling) plus failure throttling."""

    template_name = "registration/login.html"
    form_class = LoginForm
    redirect_authenticated_user = True

    def post(self, request, *args, **kwargs):
        username = request.POST.get("username", "")
        if throttle.is_locked_out(request, username):
            form = self.get_form_class()(request)
            context = self.get_context_data(form=form, locked_out=True)
            return render(request, self.template_name, context, status=429)
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        throttle.register_failure(self.request, form.data.get("username", ""))
        return super().form_invalid(form)

    def form_valid(self, form):
        throttle.reset(self.request, form.cleaned_data.get("username", ""))
        return super().form_valid(form)


class ThrottledPasswordResetView(PasswordResetView):
    """Django's reset flow (same response whether or not the address exists), rate limited
    per client address and per submitted address. The reset e-mail is sent directly
    (not through the outbox) so the single-use link is never stored in the database."""

    template_name = "registration/password_reset_form.html"
    email_template_name = "registration/password_reset_email.txt"
    subject_template_name = "registration/password_reset_subject.txt"

    def form_valid(self, form):
        address = form.cleaned_data["email"].strip().lower()
        client = self.request.META.get("REMOTE_ADDR", "unknown")
        if not (
            throttle.allow_rate(f"pwreset:addr:{client}", 20)
            and throttle.allow_rate(f"pwreset:email:{hashlib.sha256(address.encode()).hexdigest()}", 300)
        ):
            return redirect(self.get_success_url())
        return super().form_valid(form)
