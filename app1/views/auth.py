from django.contrib.auth.views import LoginView
from django.shortcuts import render

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
