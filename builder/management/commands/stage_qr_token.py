import secrets
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from builder.models import Game
from builder.urban_tools import create_qr_token


class Command(BaseCommand):
    help = 'Print the signed token to print as a QR code for one stage (graph node id) of a game.'

    def add_arguments(self, parser):
        parser.add_argument('game_slug')
        parser.add_argument('stage_id')
        parser.add_argument('--days', type=int, default=30, help='How many days the code stays valid (default 30).')

    def handle(self, *args, **options):
        if not Game.objects.filter(slug=options['game_slug']).exists():
            raise CommandError('Unknown game: %s' % options['game_slug'])
        if options['days'] < 1:
            raise CommandError('--days must be at least 1.')
        expires_at = time.time() + options['days'] * 86400
        token = create_qr_token(options['game_slug'], options['stage_id'], secrets.token_hex(8),
                                getattr(settings, 'GAME_ACTION_QR_SECRET', settings.SECRET_KEY), expires_at)
        self.stdout.write(token)
