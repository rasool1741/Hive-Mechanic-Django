import os

from django.core.management.base import BaseCommand

from ...models import SessionMediaSubmission, private_player_media_storage


class Command(BaseCommand):
    help = 'Report or remove player-media files not referenced by a submission.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Actually remove orphaned files.')

    def handle(self, *args, **options):
        root = private_player_media_storage.location
        referenced = set(SessionMediaSubmission.objects.values_list('file', flat=True))
        orphaned = []
        if not os.path.isdir(root):
            self.stdout.write('No player media storage directory exists.')
            return
        for directory, _, filenames in os.walk(root):
            for filename in filenames:
                absolute = os.path.join(directory, filename)
                relative = os.path.relpath(absolute, root).replace(os.sep, '/')
                if relative not in referenced:
                    orphaned.append((absolute, relative))
        for absolute, relative in orphaned:
            if options['apply']:
                os.remove(absolute)
            self.stdout.write('%s %s' % ('removed' if options['apply'] else 'orphan', relative))
        self.stdout.write('%d orphaned file(s) found.' % len(orphaned))
