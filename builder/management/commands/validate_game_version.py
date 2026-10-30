from django.core.management.base import BaseCommand, CommandError

from builder.models import GameVersion


class Command(BaseCommand):
    help = 'Validate a game version before testing or publication.'

    def add_arguments(self, parser):
        parser.add_argument('version_id', type=int)

    def handle(self, *args, **options):
        try:
            version = GameVersion.objects.get(pk=options['version_id'])
        except GameVersion.DoesNotExist as error:
            raise CommandError('Game version does not exist.') from error

        errors = version.validate_definition()
        version.save(update_fields=['validation_errors', 'definition_checksum'])
        if errors:
            for error in errors:
                self.stderr.write(error)
            raise CommandError('Game version is invalid.')

        self.stdout.write(self.style.SUCCESS('Game version is valid.'))
