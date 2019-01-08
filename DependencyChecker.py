from svn import local
import re
import configparser
from getpass import getpass
import keyring
from jira import JIRA


class DependencyChecker:
    def __init__(self, jira, svn_working_copy_path, extensions_to_check, statuses_to_ignore, issue_regex,
                 max_revisions=20):
        server, username, password = jira
        self.jira = JIRA(server=server, auth=(username, password))
        self.file_extensions = extensions_to_check
        self.statuses_to_ignore = statuses_to_ignore
        self.max_checked_revisions = max_revisions
        self.issue_regex = re.compile(issue_regex)
        self.svn = local.LocalClient(svn_working_copy_path)

    def get_issues(self, log_message):
        return self.issue_regex.findall(log_message)

    def check_dependencies(self, revision_to_check):
        log_entry = self.svn.log_default(revision_from=revision_to_check, revision_to=revision_to_check,
                                         limit=1, changelist=True)

        files = [file for _, file in log_entry.changelist if file[-3:] in self.file_extensions]

        main_issue_number = self.get_issues(log_message=log_entry.msg).pop()

        for file in files:
            revisions = self.svn.log_default(rel_filepath=file, limit=self.max_checked_revisions)

            open_issues = set()

            for revision in revisions:
                issues_in_revision = self.get_issues(log_message=revision.msg)
                if main_issue_number not in issues_in_revision:
                    for issue in issues_in_revision:
                        issue_status = self.jira.issue(id=issue, fields='status').fields.status.name
                        if issue_status not in self.statuses_to_ignore:
                            open_issues.add((issue, issue_status))

            yield (file, open_issues)


def main():
    config = configparser.ConfigParser()
    config.read('DependencyChecker.conf')

    statuses_to_ignore = config['JIRA']['statuses_to_ignore'].split(',')
    service_name = config['JIRA']['keyring_service_name']
    server = config['JIRA']['server']
    username = config['JIRA']['username']
    password = keyring.get_password(service_name=service_name, username=username)

    if password is None:
        keyring.set_password(service_name=service_name, username=username,
                             password=getpass('Input jira password for {}: '.format(username)))
        password = keyring.get_password(service_name=service_name, username=username)

    issue_regex = config['SVN']['issuekey_regex']
    file_extensions = config['SVN']['accepted_extensions'].split(',')
    working_copy_path = config['SVN']['working_copy_path']

    dependency_checker = DependencyChecker(jira=(server, username, password),
                                           svn_working_copy_path=working_copy_path, extensions_to_check=file_extensions,
                                           statuses_to_ignore=statuses_to_ignore, issue_regex=issue_regex)

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


if __name__ == '__main__':
    main()
