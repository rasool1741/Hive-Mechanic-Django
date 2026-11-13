# pylint: disable=line-too-long

from django.conf import settings
from django.middleware.locale import LocaleMiddleware


class SiteLanguageMiddleware(LocaleMiddleware):
    '''
    LocaleMiddleware that, by default, ignores the browser's Accept-Language
    header. The interface then follows settings.LANGUAGE_CODE (Persian by
    default) unless the user picked another language with the switcher, which
    is stored in the language cookie.
    '''

    def process_request(self, request):
        if getattr(settings, 'LANGUAGE_IGNORE_ACCEPT_HEADER', True):
            request.META.pop('HTTP_ACCEPT_LANGUAGE', None)

        super().process_request(request)
