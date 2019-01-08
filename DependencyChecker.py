from svn import local
from collections import Iterable
import re
import configparser
from getpass import getpass
import keyring
from jira import JIRA


class DependencyChecker:
    def __init__(self):
        config = configparser.ConfigParser()
        config.read('DependencyChecker.conf')

        password = keyring.get_password(config['JIRA']['keyring_service_name'], config['JIRA']['username'])

        if password is None:
            keyring.set_password(config['JIRA']['keyring_service_name'],
                                 config['JIRA']['username'],
                                 getpass('Input jira password for {}: '.format(config['JIRA']['username'])))

            password = keyring.get_password(config['JIRA']['keyring_service_name'], config['JIRA']['username'])

        self.jira = JIRA(server=config['JIRA']['server'], auth=(config['JIRA']['username'], password))
        self.svn = local.LocalClient(config['SVN']['working_copy_path'])
        self.issue_regex = config['SVN']['issuekey_regex']
        self.file_extensions = config['SVN']['accepted_extensions'].split(',')
        self.statuses_to_ignore = config['JIRA']['statuses_to_ignore'].split(',')
        self.max_checked_revisions = int(config['SVN']['max_checked_revisions'])

    def get_issue_number(self, log_message):
        return re.findall(self.issue_regex, log_message)

    def func(self, log, main_issue_number):
        return ((file, [issue for issue in
                        [(issue, self.jira.issue(issue, fields='status').fields.status.name)
                         for issue in set(item
                                          for sublist in
                                          (self.get_issue_number(entry[1].msg)
                                           for entry in enumerate(self.svn.log_default(rel_filepath=file))
                                           if entry[0] < self.max_checked_revisions and
                                           main_issue_number not in self.get_issue_number(entry[1].msg))
                                          for item in sublist)]
                        if issue[1] not in self.statuses_to_ignore])
                for _, file in log.changelist
                if file[-3:] in self.file_extensions
                )

    def check_dependencies(self, revision):
        # ¯\_(ツ)_/¯
        log_entry = self.svn.log_default(revision_from=revision, revision_to=revision,
                                             limit=1, changelist=True)

        return self.func(log_entry, self.get_issue_number(log_entry.msg).pop())


if __name__ == '__main__':

    dependency_checker = DependencyChecker()

    for rev in dependency_checker.check_dependencies(int(input('Enter revision to check: '))):
        for file_in_revision in rev:
            print(f'File: {file_in_revision[0]}')
            issues = file_in_revision[1]

            issues_by_status = {status: [issue[0]
                                         for issue in issues
                                         if issue[1] == status]
                                for status in set(issue[1] for issue in issues)
                                }
            if len(issues_by_status) > 0:
                for status, issues in issues_by_status.items():
                    print(f'Status: {status}\n       {issues}')
            else:
                print(f'Should be OK')
            print()
