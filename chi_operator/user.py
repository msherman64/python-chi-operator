from operator import attrgetter

import click
from keystoneauth1.exceptions import NotFound

from .base import BaseCommand
from .util import column_align


@click.group(help="Subcommands related to user accounts")
def user():
    pass


class UserInspectCommand(BaseCommand):
    @staticmethod
    @user.command(name='inspect')
    @click.argument('user')
    def cli(user):
        """Display a summary for a given user.

        When specifying the USER, you can user either the username or the
        Keystone user ID.

        """
        return UserInspectCommand().run(user=user)

    def run(self, user=None):
        keystone = self.keystone()

        query = {}
        try:
            ks_user = keystone.users.get(user)
            query['name'] = ks_user.name
        except NotFound:
            query['name'] = user

        # For some reason Keystone can return multiple copies of federated
        # users--this seems like an API bug. Use a map to require uniqueness.
        user_ids = set(user.id for user in keystone.users.list(**query))

        report_lines = [
            f'User: {user}',
            f'Found {len(user_ids)} user account(s).',
        ]

        for user_id in user_ids:
            user = keystone.users.get(user_id)
            is_federated = user.domain_id != 'default'
            projects = sorted(keystone.projects.list(user=user), key=attrgetter('name'))
            if projects:
                project_lines = (
                    [['ID', 'Name', 'Domain']] + [
                        [p.id, p.name, p.domain_id] for p in projects
                    ])
            else:
                project_lines = [['(none)']]
            report_lines.extend([
                '-'*60,
                f'ID: {user.id}',
                f'Name: {user.name}',
                f'Domain: {user.domain_id}',
                f'Federated: {is_federated}',
                'Projects:', '  ' + '\n  '.join(column_align(project_lines)),
            ])

        print('\n'.join(report_lines))
