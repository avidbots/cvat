# Copyright (C) 2021 Intel Corporation
#
# SPDX-License-Identifier: MIT

from django.core.exceptions import BadRequest
from django.utils.functional import SimpleLazyObject
from rest_framework import views
from rest_framework.exceptions import ValidationError
from django.conf import settings
from rest_framework.response import Response
from rest_auth.registration.views import RegisterView
from allauth.account import app_settings as allauth_settings
from furl import furl

from django.utils.decorators import method_decorator
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi

from .authentication import Signer

def get_context(request):
    from cvat.apps.organizations.models import Organization, Membership

    IAM_ROLES = {role:priority for priority, role in enumerate(settings.IAM_ROLES)}
    groups = list(request.user.groups.filter(name__in=list(IAM_ROLES.keys())))
    groups.sort(key=lambda group: IAM_ROLES[group.name])

    organization = None
    membership = None
    try:
        org_slug = request.GET.get('org')
        org_id = request.GET.get('org_id')
        org_header = request.headers.get('X-Organization')

        if org_id and (org_slug or org_header):
            raise BadRequest('You cannot specify "org_id" query parameter with ' +
                '"org" query parameter or "X-Organization" HTTP header at the same time.')
        if org_slug and org_header and org_slug != org_header:
            raise BadRequest('You cannot specify "org" query parameter and ' +
                '"X-Organization" HTTP header with different values.')
        org_slug = org_slug or org_header

        if org_slug:
            organization = Organization.objects.get(slug=org_slug)
            membership = Membership.objects.filter(organization=organization,
                user=request.user).first()
        elif org_id:
                organization = Organization.objects.get(id=int(org_id))
                membership = Membership.objects.filter(organization=organization,
                    user=request.user).first()
    except Organization.DoesNotExist:
        raise BadRequest(f'{org_slug or org_id} organization does not exist.')

    if membership and not membership.is_active:
        membership = None

    context = {
        "privilege": groups[0] if groups else None,
        "membership": membership,
        "organization": organization,
    }

    return context
class ContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):

        # https://stackoverflow.com/questions/26240832/django-and-middleware-which-uses-request-user-is-always-anonymous
        request.iam_context = SimpleLazyObject(lambda: get_context(request))

        return self.get_response(request)


@method_decorator(name='post', decorator=swagger_auto_schema(
    request_body=openapi.Schema(
        type=openapi.TYPE_OBJECT,
        required=[
            'url'
        ],
        properties={
            'url': openapi.Schema(type=openapi.TYPE_STRING)
        }
    ),
    responses={'200': openapi.Response(description='text URL')}
))
class SigningView(views.APIView):
    """
    This method signs URL for access to the server.

    Signed URL contains a token which authenticates a user on the server.
    Signed URL is valid during 30 seconds since signing.
    """
    def post(self, request):
        url = request.data.get('url')
        if not url:
            raise ValidationError('Please provide `url` parameter')

        signer = Signer()
        url = self.request.build_absolute_uri(url)
        sign = signer.sign(self.request.user, url)

        url = furl(url).add({Signer.QUERY_PARAM: sign}).url
        return Response(url)


class RegisterViewEx(RegisterView):
    def get_response_data(self, user):
        data = self.get_serializer(user).data
        data['email_verification_required'] = True
        data['key'] = None
        if allauth_settings.EMAIL_VERIFICATION != \
            allauth_settings.EmailVerificationMethod.MANDATORY:
            data['email_verification_required'] = False
            data['key'] = user.auth_token.key
        return data
