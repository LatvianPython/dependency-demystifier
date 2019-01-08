from svn import local
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

    def get_issues(self, log_message):
        return re.findall(self.issue_regex, log_message)

    def check_dependencies(self, revision_to_check):
        log_entry = self.svn.log_default(revision_from=revision_to_check, revision_to=revision_to_check,
                                         limit=1, changelist=True)

        files = [file for _, file in log_entry.changelist if file[-3:] in self.file_extensions]

        main_issue_number = self.get_issues(log_entry.msg).pop()

        for file in files:
            revisions = self.svn.log_default(rel_filepath=file, limit=self.max_checked_revisions)

            open_issues = set()

            for revision in revisions:
                issues_in_revision = self.get_issues(revision.msg)
                if main_issue_number not in issues_in_revision:
                    for issue in issues_in_revision:
                        issue_status = self.jira.issue(issue, fields='status').fields.status.name
                        if issue_status not in self.statuses_to_ignore:
                            issues.add((issue, issue_status))

            yield (file, open_issues)


if __name__ == '__main__':

    dependency_checker = DependencyChecker()

    for file_name, issues in dependency_checker.check_dependencies(int(input('Enter revision to check: '))):
        print(f'File: {file_name}')

        issues_by_status = {status: [issue[0]
                                     for issue in issues
                                     if issue[1] == status]
                            for status in set(issue[1] for issue in issues)}

        if len(issues_by_status) > 0:
            for status, issues_with_status in issues_by_status.items():
                print(f'Status: {status}\n       {issues_with_status}')
        else:
            print(f'Should be OK')
        print()
